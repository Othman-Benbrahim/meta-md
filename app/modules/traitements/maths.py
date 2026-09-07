"""Ce qui est mathematique s'ecrit en LaTeX.

Les cinq moteurs rendaient les maths de cinq facons. Le prompt de Vision les
reclame en LaTeX et les obtient le plus souvent ; l'OCR de Document AI en pose
une partie et laisse le reste en Unicode ; l'extraction hors ligne n'en pose
aucune et rend ce que la couche texte porte — `x²`, `√2`, `≤`, `α`, `U = RI`.
Le meme PDF se lisait donc differemment selon le moteur, et deux conversions
rangees cote a cote sous `2-Conversions/` ne se comparaient plus sur ce qu'on
voulait comparer.

Ce module pose la derniere main, apres le moteur, sur le Markdown assemble :
il reconnait les fragments mathematiques restes en texte et les ecrit en
LaTeX, `$…$` en ligne, `$$…$$` quand le fragment occupe toute la ligne. Il ne
connait ni PDF ni corpus — il lit du Markdown et rend du Markdown — et c'est
pour cela qu'il sert les cinq moteurs depuis `convert_one_pdf`.

La regle qui le gouverne est celle du reste du projet : **ne rien inventer**.
Un fragment n'est pris que s'il porte un signe non ambigu — un symbole
(`√ ≤ ∈ × ∑`), un exposant, un nom de fonction — ou une relation posee entre
deux atomes (`U = RI`). Un texte qui n'en porte aucun reste du texte : « de 8
a 10 », « 2019-2020 », « page 3 » ne sont pas des formules, et le doute
profite toujours au texte. Ce qui est mal equilibre — parentheses ou barres
depareillees, racine sans argument — est laisse tel quel : mieux vaut une
formule restee en Unicode qu'un LaTeX que KaTeX afficherait en rouge.

Le rendu accepte les quatre delimiteurs (`assets/md-rendu.js`) : ce module n'a
donc pas a reecrire les `\\(…\\)` que rend l'OCR, et il ne le fait pas. Il ne
touche qu'a ce qui n'etait pas encore des maths.
"""
from __future__ import annotations

import re
import unicodedata


# --- Ce a quoi on ne touche pas ---------------------------------------------
#
# Une formule deja posee, du code, une balise, une URL : les relire n'ajoute
# rien et peut casser quelque chose. Les unites sont dans la meme liste, et
# c'est le point le moins evident : `12 m²` est une mesure, pas le carre de la
# variable m, et `°C` une temperature. Rien dans la phrase ne les distingue
# d'une expression ; seule cette liste le fait.

_UNITES = ("mol", "dam", "µm", "cm", "dm", "hm", "km", "mm", "nm",
           "kg", "Hz", "Pa", "m", "s", "g", "L", "l", "A", "N", "J", "W", "V")

_PROTEGE_RE = re.compile(
    r"```[\s\S]*?```"                       # bloc de code
    r"|~~~[\s\S]*?~~~"
    r"|`[^`\n]+`"                           # code en ligne
    r"|\$\$[\s\S]*?\$\$"                    # maths deja posees, les 4 formes
    r"|\$[^$\n]+\$"
    r"|\\\([\s\S]*?\\\)"
    r"|\\\[[\s\S]*?\\\]"
    r"|\\[A-Za-z]+"                         # commande LaTeX isolee
    r"|<!--[\s\S]*?-->"                     # repere de page, commentaire
    r"|<[^<>\n]+>"                          # balise HTML : attributs, URL
    r"|!?\[[^\]\n]*\]\([^)\n]*\)"           # lien, image
    r"|https?://\S+"
    r"|&[A-Za-z]+;|&#\d+;"                  # entite HTML
    r"|(?<![A-Za-zÀ-ÿ])(?:" + "|".join(_UNITES) + r")[²³](?![A-Za-zÀ-ÿ])"
    r"|°\s?[CFK](?![A-Za-zÀ-ÿ])"
)

# Le code n'est pas toujours dans une cloture. Le cours de NSI le montre :
# l'OCR et l'extraction rendent `python` en texte au lieu d'ouvrir la fence,
# et les quatre lignes qui suivent sont du Python nu. Sans ce garde-fou,
# `S = 0` devient une formule et `if n==0 :` un charabia — mesure : 12 lignes
# de code abimees sur ce seul document, aucune formule perdue en les
# ecartant.
#
# Une ligne indentee de quatre espaces est deja un bloc de code en Markdown,
# sauf si elle porte une puce : c'est alors une liste imbriquee, du texte. On
# y renonce aux maths, ce qui est le sens du doute.
_INDENTATION_CODE_RE = re.compile(r"^(?:[ ]{4,}|\t)(?![-*+][ ]|\d+[.)][ ])")
# Mots-clefs en tete de ligne, et operateurs qui n'existent qu'en
# programmation. `<=` et `>=` n'y sont pas : ils s'ecrivent aussi en maths.
_MOTS_CODE_RE = re.compile(
    r"^[ \t]*(?:>[ \t]*)*"
    r"(?:def|class|import|from|return|lambda|elif|except|print|input|while|for"
    r"|if|else)\b")
_OPERATEURS_CODE_RE = re.compile(r"==|!=|\+=|-=|\*=|/=|->|=>|\bself\b")


def _lignes_a_laisser(md: str) -> list[tuple[int, int]]:
    """Les lignes ou l'on ne pose pas de maths : du code, meme sans cloture.

    Une ligne voisine d'une ligne de code en est aussi : `S = 0` ne se
    reconnait a rien, mais elle suit `def somme_iterative(n):` et precede
    `for i in range(1, n+1):`. La contagion ne se fait qu'une fois, depuis les
    lignes reconnues d'elles-memes — sinon un document qui alterne code et
    texte finirait tout entier en code.
    """
    lignes = md.split("\n")
    bornes: list[tuple[int, int]] = []
    debut = 0
    for ligne in lignes:
        bornes.append((debut, debut + len(ligne)))
        debut += len(ligne) + 1

    code = [bool(ligne.strip()) and bool(_INDENTATION_CODE_RE.match(ligne)
                                         or _MOTS_CODE_RE.match(ligne)
                                         or _OPERATEURS_CODE_RE.search(ligne))
            for ligne in lignes]
    voisines = set()
    for i, est_code in enumerate(code):
        if not est_code:
            continue
        for pas in (-1, 1):
            j = i + pas
            # Une ligne vide separe sans rompre : le Markdown en met une entre
            # deux lignes d'un meme bloc.
            if 0 <= j < len(lignes) and not lignes[j].strip():
                j += pas
            if 0 <= j < len(lignes) and lignes[j].strip() and not code[j]:
                voisines.add(j)

    return [bornes[i] for i in range(len(lignes)) if code[i] or i in voisines]


# --- Ce qui se traduit ------------------------------------------------------

# --- Les lettres mathematiques d'Unicode ------------------------------------
#
# Un PDF de mathematiques compose ses variables dans le bloc « Mathematical
# Alphanumeric Symbols » : `𝑢` n'est pas un `u`, c'est U+1D462. Le programme de
# specialite de premiere en porte 103. Aucune n'etait reconnue comme variable,
# donc aucune formule de ce document n'etait posee.
#
# Leur decomposition de compatibilite rend la lettre ordinaire — `𝑢` donne
# `u`, `𝜋` donne `π`, que les grecques savent deja traduire — et elle tient en
# un seul caractere, donc les positions ne bougent pas. C'est ce qui permet de
# lire le texte aplati tout en decoupant le texte d'origine.
_MATH_DEBUT, _MATH_FIN = 0x1D400, 0x1D7FF
_APLATIR = {}
for _cp in range(_MATH_DEBUT, _MATH_FIN + 1):
    _plat = unicodedata.normalize("NFKD", chr(_cp))
    if len(_plat) == 1 and _plat != chr(_cp):
        _APLATIR[_cp] = _plat


def _aplatir(md: str) -> str:
    """Rend leurs lettres ordinaires aux variables composees en Unicode math."""
    return md.translate(_APLATIR) if md else md


# La fleche du vecteur se pose apres sa lettre, en caractere combinant. Elle
# reste collee au jeton : la longueur des positions est ainsi preservee.
_FLECHE = "⃗"

_SYMBOLES = {
    # `‖` est la norme : deux barres, pas deux fois une barre.
    "‖": r"\|",
    # La typographie francaise ecrit « inferieur ou egal » avec une barre
    # penchee : `⩽`, et non `≤`. Les manuels scolaires n'utilisent qu'elle,
    # mesure faite sur le corpus. Les deux se rendent par le meme `\leq`.
    "≤": r"\leq", "⩽": r"\leq", "≦": r"\leq",
    "≥": r"\geq", "⩾": r"\geq", "≧": r"\geq",
    "≠": r"\neq", "≈": r"\approx",
    "≡": r"\equiv", "∼": r"\sim", "≃": r"\simeq",
    "∈": r"\in", "∉": r"\notin", "⊂": r"\subset", "⊃": r"\supset",
    "⊆": r"\subseteq", "⊇": r"\supseteq", "∪": r"\cup", "∩": r"\cap",
    "⋃": r"\bigcup", "⋂": r"\bigcap",
    "∅": r"\emptyset", "∞": r"\infty",
    "±": r"\pm", "∓": r"\mp", "×": r"\times", "÷": r"\div",
    "·": r"\cdot", "⋅": r"\cdot",
    "∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∂": r"\partial",
    "∀": r"\forall", "∃": r"\exists", "¬": r"\neg",
    "→": r"\to", "←": r"\leftarrow", "↦": r"\mapsto",
    "⟶": r"\longrightarrow",
    "⇒": r"\Rightarrow", "⇔": r"\Leftrightarrow",
    "∥": r"\parallel", "⊥": r"\perp", "∠": r"\angle",
}
_GRECQUES = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\varepsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
    "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu",
    "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "φ": r"\varphi", "χ": r"\chi",
    "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi",
    "Ψ": r"\Psi", "Ω": r"\Omega",
}
_ENSEMBLES = {"ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}",
              "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}"}
_FRACTIONS = {"½": r"\frac{1}{2}", "⅓": r"\frac{1}{3}", "⅔": r"\frac{2}{3}",
              "¼": r"\frac{1}{4}", "¾": r"\frac{3}{4}", "⅕": r"\frac{1}{5}",
              "⅖": r"\frac{2}{5}", "⅗": r"\frac{3}{5}", "⅘": r"\frac{4}{5}",
              "⅙": r"\frac{1}{6}", "⅚": r"\frac{5}{6}", "⅛": r"\frac{1}{8}",
              "⅜": r"\frac{3}{8}", "⅝": r"\frac{5}{8}", "⅞": r"\frac{7}{8}"}
# `min` et `max` n'y sont pas : « 5 min » est une duree, et le seul nom d'une
# fonction suffit a declarer un fragment mathematique. Le risque n'est pas
# symetrique — les rater ne coute qu'une formule non posee.
_FONCTIONS = {"arccos": r"\arccos", "arcsin": r"\arcsin", "arctan": r"\arctan",
              "cos": r"\cos", "sin": r"\sin", "tan": r"\tan",
              "log": r"\log", "ln": r"\ln", "exp": r"\exp",
              "pgcd": r"\operatorname{pgcd}", "ppcm": r"\operatorname{ppcm}"}

# Les exposants et indices Unicode. `ᵉ` et `ʳ` n'y sont pas : `1ᵉʳ` et `XXᵉ`
# sont des ordinaux francais, pas des puissances.
_EXPOSANTS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
              "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
              "⁺": "+", "⁻": "-", "ⁿ": "n", "ⁱ": "i", "⁽": "(", "⁾": ")"}
_INDICES = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
            "₆": "6", "₇": "7", "₈": "8", "₉": "9",
            "₊": "+", "₋": "-", "ₙ": "n", "ᵢ": "i", "ₖ": "k",
            "₍": "(", "₎": ")"}

# Mots francais de deux ou trois lettres. Sans eux, « x = 3 et y = 4 » devient
# une seule formule ou `et` s'affiche en variables italiques. Ils coupent le
# fragment au lieu de l'etendre ; la liste se complete sans risque.
_MOTS_COURTS = frozenset("""
ai an as au aux ce ces cet ci de des du en es est et eu eux fut il ils je la
le les lui ma me mes moi mon ne ni nos on ont ou par pas peu pu qu que qui sa
se ses si soi son sur ta te tel tes toi ton tu un vas vos vu ans cas cf ex
etc art vol ici pre bis ter mis via non oui deux fin bas haut
cm mm km dm hm kg mg ml cl dl ha mn hL kWh
""".split())


# --- Le decoupage en jetons -------------------------------------------------
#
# `°` ne compte que derriere un chiffre : `30°` est un angle, `n° 5` un
# numero. `*` et `_` sont volontairement absents — ce sont les marques
# d'emphase du Markdown, pas des operateurs.

_JETON_RE = re.compile(
    r"(?P<espace>[ \t]+)"
    r"|(?P<nombre>\d+(?:[.,]\d+)?)"
    r"|(?P<fonction>(?:" + "|".join(sorted(_FONCTIONS, key=len, reverse=True))
    + r")(?![A-Za-zÀ-ÿ]))"
    r"|(?P<motcourt>(?<![A-Za-zÀ-ÿ])[A-Za-z]{2,3}(?![A-Za-zÀ-ÿ]))"
    r"|(?P<variable>(?<![A-Za-zÀ-ÿ])[A-Za-z]⃗?(?![A-Za-zÀ-ÿ]))"
    r"|(?P<grecque>[" + "".join(_GRECQUES) + r"])"
    r"|(?P<ensemble>[" + "".join(_ENSEMBLES) + r"])"
    r"|(?P<fraction>[" + "".join(_FRACTIONS) + r"])"
    r"|(?P<exposant>[" + "".join(_EXPOSANTS) + r"]+)"
    r"|(?P<indice>[" + "".join(_INDICES) + r"]+)"
    r"|(?P<degre>(?<=\d)\s?°)"
    r"|(?P<racine>√)"
    r"|(?P<chapeau>\^)"
    r"|(?P<symbole>[" + "".join(_SYMBOLES) + r"])"
    r"|(?P<relation>[=<>+])"
    r"|(?P<operateur>[-−/])"
    r"|(?P<ouvre>[(\[])"
    r"|(?P<ferme>[)\]])"
    r"|(?P<barre>\|)"
    r"|(?P<virgule>,)"
    r"|(?P<points>\.{3}|…)"
    r"|(?P<autre>[\s\S])"
)

_ATOMES = frozenset(("nombre", "variable", "motcourt", "grecque", "ensemble",
                     "fraction"))
# Un seul de ces jetons suffit a declarer le fragment mathematique. Une
# fraction y est deux fois — elle est a la fois l'atome et le signe.
_SIGNES = frozenset(("symbole", "racine", "chapeau", "exposant", "indice",
                     "degre", "fonction", "fraction"))
# Ce qui rattache un atome au fragment par-dessus une espace. Sans l'un
# d'eux, l'atome isole a droite d'un fragment est plus probablement un mot :
# `√(a²) = |a| a savoir` finirait sinon par avaler le verbe.
_LIENS = frozenset(("relation", "operateur", "symbole", "racine", "chapeau",
                    "ouvre", "virgule"))
# Les deux seuls mots francais d'une lettre. Ailleurs, `a` et `y` sont des
# variables courantes : ils ne coupent le fragment que poses entre deux
# espaces sans rien qui les y rattache.
_LETTRES_MOTS = frozenset(("a", "y"))
# Les lettres qui s'elident. Devant une apostrophe elles sont un mot, pas une
# variable : `0.2 + 0.1 n’est pas egal a 0.3` finissait sinon par annexer le
# `n` de « n'est ». Les autres lettres gardent leur apostrophe, qui est alors
# le prime d'une derivee — `f'(x)`.
_ELISIONS = frozenset(("c", "d", "j", "l", "m", "n", "s", "t"))
_APOSTROPHES = ("'", "’")
# Un fragment ne commence ni ne finit sur ceux-la : un `-` ou un `→` pendant
# vaudrait un LaTeX bancal, et une parenthese seule desequilibre le tout.
_PAS_A_GAUCHE = frozenset(("espace", "virgule", "relation", "operateur",
                           "ferme", "exposant", "indice", "degre", "chapeau",
                           "points"))
_PAS_A_DROITE = frozenset(("espace", "virgule", "relation", "operateur",
                           "ouvre", "symbole", "racine", "chapeau",
                           "fonction", "points"))

_Jeton = tuple[str, str, int, int]


def _texte_hors_protege(md: str, protege: list[tuple[int, int]]) -> str:
    """Le Markdown prive de ses zones protegees, longueurs et lignes intactes.

    Le detecteur de code lit cette version-la, et non le texte brut : sinon
    le repere de page pose a chaque frontiere — `<!-- page 3 -->` — se lit
    comme du code, parce qu'il finit par `-->`, que `_OPERATEURS_CODE_RE`
    reconnait comme la fleche de Python. Mesure sur le programme de
    physique-chimie de seconde : 13 reperes de page, 36 zones ecartees par
    contagion sur 120 lignes, et pas une seule formule posee dans tout le
    document — ni `(Z ⩽ 18)`, ni `U = f(I)`, tous deux voisins d'un repere.

    Les retours a la ligne sont conserves : les bornes rendues par
    `_lignes_a_laisser` comptent les caracteres du texte d'origine. Le reste
    est remplace par un caractere nul, et non par une espace : les balises
    d'un tableau font une trentaine de caracteres avant sa premiere cellule,
    et autant d'espaces auraient fait passer chaque tableau pour un bloc de
    code indente.
    """
    nu = list(md)
    for debut, fin in protege:
        for i in range(debut, fin):
            if nu[i] != "\n":
                nu[i] = "\x00"
    return "".join(nu)


def _masque(md: str) -> bytearray:
    """Un octet par caractere : 1 la ou l'on ne touche a rien."""
    masque = bytearray(len(md))
    protege = [(m.start(), m.end()) for m in _PROTEGE_RE.finditer(md)]
    for debut, fin in (protege
                       + _lignes_a_laisser(_texte_hors_protege(md, protege))):
        masque[debut:fin] = b"\x01" * (fin - debut)
    return masque


def _jetons(md: str) -> list[_Jeton]:
    """Decoupe le Markdown, en marquant `autre` tout ce qui est protege.

    Un jeton qui mord sur une zone protegee coupe le fragment plutot que d'en
    faire partie : c'est ainsi qu'une formule deja posee, une balise ou une
    unite arretent la lecture au lieu d'etre relues.
    """
    masque = _masque(md)

    sortie: list[_Jeton] = []
    for m in _JETON_RE.finditer(md):
        genre = m.lastgroup or "autre"
        debut, fin = m.start(), m.end()
        if any(masque[debut:fin]):
            genre = "autre"
        elif genre == "motcourt" and m.group(0).lower() in _MOTS_COURTS \
                and not m.group(0).isupper():
            # `MA` n'est pas le possessif « ma » : deux capitales designent
            # deux points, et la geometrie du programme de premiere en est
            # pleine — `MA · MB`, `AB`, `AMB`.
            genre = "autre"
        elif genre == "variable" and m.group(0).lower() in _ELISIONS \
                and md[fin:fin + 1] in _APOSTROPHES:
            genre = "autre"
        sortie.append((genre, m.group(0), debut, fin))
    return _demarquer_lettres_mots(sortie)


def _demarquer_lettres_mots(jetons: list[_Jeton]) -> list[_Jeton]:
    """Rend au texte les `a` et `y` qui ne sont accroches a rien."""
    sortie = list(jetons)
    for i, (genre, valeur, debut, fin) in enumerate(sortie):
        if genre != "variable" or valeur not in _LETTRES_MOTS:
            continue
        avant = sortie[i - 1][0] if i else "autre"
        apres = sortie[i + 1][0] if i + 1 < len(sortie) else "autre"
        # Colle a un signe (`|a|`, `a²`, `2a`) : c'est une variable.
        if avant != "espace" or apres != "espace":
            continue
        voisins = (sortie[i - 2][0] if i >= 2 else "autre",
                   sortie[i + 2][0] if i + 2 < len(sortie) else "autre")
        if not any(v in _LIENS for v in voisins):
            sortie[i] = ("autre", valeur, debut, fin)
    return sortie


def _fragments(jetons: list[_Jeton]) -> list[list[_Jeton]]:
    """Regroupe les jetons en suites, puis en rogne et en equilibre les bords.

    Une virgule hors parentheses separe deux formules — `U = RI, d = vt` en
    fait deux — alors qu'a l'interieur elle appartient a la formule : `f(x, y)`
    et `(x, y)` restent d'un bloc.
    """
    suites: list[list[_Jeton]] = []
    courante: list[_Jeton] = []
    niveau = 0
    for jeton in jetons:
        genre = jeton[0]
        if genre == "autre" or (genre == "virgule" and niveau == 0):
            if courante:
                suites.append(courante)
                courante = []
            niveau = 0
            continue
        if genre == "ouvre":
            niveau += 1
        elif genre == "ferme":
            niveau = max(0, niveau - 1)
        courante.append(jeton)
    if courante:
        suites.append(courante)

    retenues: list[list[_Jeton]] = []
    for suite in suites:
        suite = _rogner(_equilibrer(_rogner(suite)))
        if suite:
            retenues.append(suite)
    return retenues


def _rogner(suite: list[_Jeton]) -> list[_Jeton]:
    """Enleve des bords ce qui ne peut ni ouvrir ni fermer une formule."""
    while suite and suite[0][0] in _PAS_A_GAUCHE:
        suite = suite[1:]
    while suite and suite[-1][0] in _PAS_A_DROITE:
        suite = suite[:-1]
    return suite


def _equilibrer(suite: list[_Jeton]) -> list[_Jeton]:
    """Coupe le fragment devant ce qui le desequilibre.

    Une parenthese ouverte avant le fragment — `(par exemple V = π r² h)` — y
    laisse une fermante orpheline. On coupe devant, plutot que d'abandonner la
    formule : le texte garde sa parenthese, la formule ce qui lui revient.
    """
    niveau = 0
    for i, jeton in enumerate(suite):
        if jeton[0] == "ouvre":
            niveau += 1
        elif jeton[0] == "ferme":
            niveau -= 1
            if niveau < 0:
                return suite[:i]
    while niveau > 0:
        derniere = max((i for i, j in enumerate(suite) if j[0] == "ouvre"),
                       default=None)
        if derniere is None:
            break
        suite = suite[:derniere]
        niveau -= 1
    if sum(1 for j in suite if j[0] == "barre") % 2:
        derniere = max((i for i, j in enumerate(suite) if j[0] == "barre"),
                       default=None)
        if derniere is not None:
            suite = suite[:derniere]
    return suite


def _est_mathematique(suite: list[_Jeton]) -> bool:
    """Un fragment porte-t-il un signe qui ne s'explique pas autrement ?"""
    genres = [g for g, _, _, _ in suite]
    if not any(g in _ATOMES for g in genres):
        return False
    # Deux operateurs colles — `==`, `+=`, `!=` — n'existent qu'en
    # programmation. C'est le dernier filet quand le code n'a ni cloture ni
    # mot-clef pour se signaler.
    for gauche, droite in zip(suite, suite[1:]):
        if gauche[0] in ("relation", "operateur") \
                and droite[0] in ("relation", "operateur") \
                and gauche[3] == droite[2]:
            return False
    if any(g in _SIGNES for g in genres):
        return True
    # `=`, `<`, `>`, `+` sont trop courants pour valoir seuls : il leur faut un
    # atome de chaque cote, ce qui ecarte « 3 + » ou « voir < 5 ».
    for i, genre in enumerate(genres):
        if genre == "relation" \
                and any(g in _ATOMES for g in genres[:i]) \
                and any(g in _ATOMES for g in genres[i + 1:]):
            return True
    return False


# --- L'ecriture LaTeX -------------------------------------------------------

def _fin_du_groupe(suite: list[_Jeton], depart: int) -> int:
    """Indice suivant la parenthese qui ferme celle ouverte en `depart`."""
    niveau = 0
    for i in range(depart, len(suite)):
        if suite[i][0] == "ouvre":
            niveau += 1
        elif suite[i][0] == "ferme":
            niveau -= 1
            if niveau == 0:
                return i + 1
    return len(suite)


def _argument(suite: list[_Jeton], depart: int) -> tuple[str, int] | None:
    """Ce sur quoi porte une racine ou un chapeau, rendu et consomme.

    Un groupe parenthese, sinon un atome et ce qui le decore. Rien d'autre :
    une racine sans argument fait echouer le fragment entier, elle ne produit
    pas un `\\sqrt` orphelin.
    """
    i = depart
    while i < len(suite) and suite[i][0] == "espace":
        i += 1
    if i >= len(suite):
        return None
    if suite[i][0] == "ouvre":
        fin = _fin_du_groupe(suite, i)
        rendu = _ecrire(suite[i + 1:fin - 1])
        return (rendu, fin) if rendu else None
    if suite[i][0] not in _ATOMES:
        return None
    morceaux = [_ecrire_jeton(suite[i][0], suite[i][1])]
    i += 1
    while i < len(suite) and suite[i][0] in ("exposant", "indice"):
        morceaux.append(_ecrire_jeton(suite[i][0], suite[i][1]))
        i += 1
    return "".join(morceaux), i


def _ecrire_jeton(genre: str, valeur: str) -> str:
    if genre == "variable" and valeur.endswith(_FLECHE):
        return r"\vec{" + valeur[:-len(_FLECHE)] + "}"
    if genre == "grecque":
        return _GRECQUES[valeur] + " "
    if genre == "ensemble":
        return _ENSEMBLES[valeur]
    if genre == "fraction":
        return _FRACTIONS[valeur]
    if genre == "symbole":
        return " " + _SYMBOLES[valeur] + " "
    if genre == "exposant":
        return "^{" + "".join(_EXPOSANTS[c] for c in valeur) + "}"
    if genre == "indice":
        return "_{" + "".join(_INDICES[c] for c in valeur) + "}"
    if genre == "degre":
        return "^{\\circ}"
    if genre == "fonction":
        return _FONCTIONS[valeur] + " "
    if genre == "points":
        return r"\dots "
    if genre == "operateur":
        return "-" if valeur == "−" else valeur
    if genre == "relation":
        return " " + valeur + " "
    return valeur


_ESPACES_RE = re.compile(r"[ \t]+")
# Une commande LaTeX porte une espace pour ne pas se coller au mot suivant
# (`\pi r`). Devant un exposant ou une parenthese, cette espace n'a plus lieu
# d'etre : `\cos ^{2}` s'ecrit `\cos^{2}`.
_ESPACE_INUTILE_RE = re.compile(r"(\\(?:[A-Za-z]+|\|)) +(?=[\^_)\],])")
# Une parenthese ne s'ecarte pas de ce qu'elle enferme : `( + 1, + 10)`.
_PARENTHESE_LARGE_RE = re.compile(r"(?<=[(\[]) +| +(?=[)\]])")


def _ecrire(suite: list[_Jeton]) -> str | None:
    """Rend le fragment en LaTeX, ou None s'il ne s'ecrit pas proprement."""
    morceaux: list[str] = []
    i = 0
    while i < len(suite):
        genre, valeur = suite[i][0], suite[i][1]
        if genre in ("racine", "chapeau"):
            argument = _argument(suite, i + 1)
            if argument is None:
                return None
            rendu, i = argument
            morceaux.append((r"\sqrt{" if genre == "racine" else "^{")
                            + rendu + "}")
            continue
        morceaux.append(_ecrire_jeton(genre, valeur))
        i += 1
    rendu = _ESPACES_RE.sub(" ", "".join(morceaux))
    rendu = _ESPACE_INUTILE_RE.sub(r"\1", rendu)
    return _PARENTHESE_LARGE_RE.sub("", rendu).strip()


# --- Le point d'entree ------------------------------------------------------

def poser_latex(md: str) -> tuple[int, str]:
    """Ecrit en LaTeX les fragments mathematiques restes en texte.

    Rend (nombre de fragments poses, Markdown). Cette fonction rendait aussi
    les decalages de position, dont le sidecar de Document AI avait besoin
    pour survivre a la reecriture ; ce moteur a ete retire.
    """
    if not md:
        return 0, md

    # Les variables composees en Unicode mathematique sont ramenees a leurs
    # lettres pour la lecture seulement : les longueurs sont conservees, donc
    # les positions valent dans les deux textes. Ce qui n'est pas une formule
    # se recopie depuis l'original, avec ses glyphes.
    plat = _aplatir(md)
    remplacements: list[tuple[int, int, str]] = []
    for suite in _fragments(_jetons(plat)):
        if not _est_mathematique(suite):
            continue
        debut, fin = suite[0][2], suite[-1][3]
        # Un fragment ne commence pas au milieu d'un mot : `range(1, n + 1)`
        # est un appel de fonction, pas une formule qui commencerait a sa
        # parenthese.
        if debut and (plat[debut - 1].isalpha() or plat[debut - 1] == "_"):
            continue
        rendu = _ecrire(suite)
        if not rendu:
            continue
        # Un fragment qui tient toute sa ligne est une formule isolee : elle
        # s'ecrit en bloc, comme le prompt de Vision le demande deja.
        ouvre_ligne = md.rfind("\n", 0, debut) + 1
        ferme_ligne = md.find("\n", fin)
        ferme_ligne = len(md) if ferme_ligne < 0 else ferme_ligne
        seul = md[ouvre_ligne:ferme_ligne].strip() == md[debut:fin].strip()
        marque = "$$" if seul else "$"
        remplacements.append((debut, fin, marque + rendu + marque))

    if not remplacements:
        return 0, md

    morceaux: list[str] = []
    position = 0
    for debut, fin, texte in remplacements:
        morceaux.append(md[position:debut])
        morceaux.append(texte)
        position = fin
    morceaux.append(md[position:])
    return len(remplacements), "".join(morceaux)
