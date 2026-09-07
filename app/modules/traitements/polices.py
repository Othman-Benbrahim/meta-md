"""Ce que les polices du PDF disent du texte : accents, gras, italique.

Module a part, qui ne connait ni Markdown ni corpus : il lit une page de PDF
et rend du texte. Il servait les trois moteurs quand il y en avait trois, et
ne depend d'aucun : le seul restant s'en sert sans que ce module le sache.

Le principe est celui qui gouverne deja les tableaux et les niveaux de titre :
**ce que le fichier declare vaut mieux que ce qu'un modele suppose**. Les
attributs de police disent quels caracteres sont gras ou italiques, et la
couche texte dit ou les accents ont ete poses.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


# --- Accents detaches -------------------------------------------------------
#
# Un PDF produit par LaTeX porte souvent ses accents devant leur lettre :
# `r´ecursivit´e`, `mˆeme`, `Wikip´edia`. Le lecteur ne voit rien, les deux
# glyphes se superposent ; l'extraction, elle, rend les deux caracteres.
#
# Cela ne touche que la couche texte : un modele lit l'image et ecrit
# `recursivite` correctement. Mesure sur le cours de NSI : 231 occurrences
# cote extraction, aucune cote Vision, OCR ou Document AI.

_ACCENTS_SURS = {"´": "́", "ˆ": "̂", "¨": "̈",
                 "˜": "̃", "¸": "̧"}
_GRAVE = "̀"
_VOYELLES_GRAVE = "aAeEuU"
_ATTENDUES = {"́": "eEaAuUiIoO", "̂": "aAeEiIoOuU",
              "̈": "eEiIuUoO", "̃": "nNaA", "̧": "cC",
              _GRAVE: _VOYELLES_GRAVE}
# LaTeX compose `î` avec un « i sans point », pour que l'accent ne heurte pas
# le point : `croˆıt`. La lettre reprend son point en se recomposant.
_SANS_POINT = {"ı": "i", "ȷ": "j"}

_ACCENT_SUR_RE = re.compile(
    "([" + "".join(re.escape(c) for c in _ACCENTS_SURS) + r"])([A-Za-zıȷ])")
_GRAVE_RE = re.compile(r"`([" + _VOYELLES_GRAVE + r"])")
# Un accent grave suivi d'une espace : un mot, pas une cloture de code.
_GRAVE_MOT_RE = re.compile(r"`([" + _VOYELLES_GRAVE + r"])(?=\s)")
# `fac¸on`, `commenc¸ons` : ici la cedille suit le c qu'elle marque.
_CEDILLE_APRES_RE = re.compile(r"([cC])¸")
# Un passage de code : cloture ou accents graves apparies. On n'y touche pas.
_CODE_RE = re.compile(r"```[\s\S]*?```|`[^`\n]+`")


def _composer(lettre: str, marque: str) -> str | None:
    lettre = _SANS_POINT.get(lettre, lettre)
    if lettre not in _ATTENDUES.get(marque, ""):
        return None
    compose = unicodedata.normalize("NFC", lettre + marque)
    return compose if len(compose) == 1 else None


def recomposer_accents(md: str) -> tuple[int, str]:
    """Recolle les accents que la couche texte a poses devant leur lettre."""
    n = 0

    def _sur(m: "re.Match[str]") -> str:
        nonlocal n
        compose = _composer(m.group(2), _ACCENTS_SURS[m.group(1)])
        if compose is None:
            return m.group(0)
        n += 1
        return compose

    def _grave(m: "re.Match[str]") -> str:
        nonlocal n
        compose = _composer(m.group(1), _GRAVE)
        if compose is None:
            return m.group(0)
        n += 1
        return compose

    # Avant la detection des passages de code : sans quoi deux graves d'une
    # meme phrase s'en font les delimiteurs et tout ce qui est entre eux passe
    # pour du code. `` `a mesure `` est un mot, `` `a` `` reste une variable.
    md, poses = _GRAVE_MOT_RE.subn(
        lambda m: _composer(m.group(1), _GRAVE) or m.group(0), md)
    n += poses

    def _reparer(texte: str) -> str:
        texte = _CEDILLE_APRES_RE.sub(
            lambda m: "ç" if m.group(1) == "c" else "Ç", texte)
        return _GRAVE_RE.sub(_grave, _ACCENT_SUR_RE.sub(_sur, texte))

    morceaux: list[str] = []
    position = 0
    for code in _CODE_RE.finditer(md):
        # Du vrai code ne contient pas d'accent flottant : s'il y en a un,
        # c'est du texte que deux graves ont fait passer pour du code.
        if _ACCENT_SUR_RE.search(code.group(0)):
            continue
        morceaux.append(_reparer(md[position:code.start()]))
        morceaux.append(code.group(0))
        position = code.end()
    morceaux.append(_reparer(md[position:]))
    return n + _CEDILLE_APRES_RE.subn("", md)[1], "".join(morceaux)


# --- Emphase ----------------------------------------------------------------

_GRAS_FLAG = 1 << 4
_ITALIQUE_FLAG = 1 << 1
_CHASSE_FIXE_FLAG = 1 << 3
# Le drapeau ne suffit pas : sur le cours de NSI, le listing est compose en
# `NimbusMonL-Bold`, que pymupdf marque « gras » et « a empattements » sans
# jamais poser le bit de chasse fixe. Le nom de la police, lui, ne ment pas.
_NOM_CHASSE_FIXE_RE = re.compile(
    r"mono|courier|consol|menlo|typewriter|inconsolata|cascadia|"
    r"sourcecode|nimbusmon", re.IGNORECASE)

# En dessous, un fragment est trop court pour etre retrouve sans ambiguite :
# « de », « la », « n » se rencontrent partout.
LONGUEUR_EMPHASE_MIN = 4

# Une emphase du PDF : le texte, et le rang de son occurrence sur la page.
_Emphase = tuple[str, int]

# Lignes ou l'on ne marque rien : un titre est deja mis en valeur par son
# rang, et du code n'a pas d'emphase.
#
# Le tableau en faisait partie, et n'y est plus : `poser_emphases` le traite
# avant d'arriver ici. Ce n'est pas parce qu'un tableau porte son balisage que
# ses cellules portent le leur — sur le programme de physique-chimie de
# seconde, le PDF compose ses mots-clefs en gras dans les cellules comme dans
# le texte courant, et seul le texte courant le montrait. Une cellule se
# marque donc, mais en HTML (`_marquer_dans_les_cellules`) : `**` au milieu
# d'un `<td>` ne serait pas rendu, la cellule n'etant plus du Markdown.
_LIGNE_PROTEGEE_RE = re.compile(r"^\s*(#{1,6}\s|>|\||<)")
_EST_TABLEAU_RE = re.compile(r"^\s*<table\b", re.IGNORECASE)
_CELLULE_RE = re.compile(r"(<(t[hd])\b[^>]*>)(.*?)(</\2\s*>)",
                         re.DOTALL | re.IGNORECASE)
_MARQUES_HTML = {"**": ("<b>", "</b>"), "*": ("<i>", "</i>")}

# Ou l'on ne pose pas d'emphase, meme hors tableau : dans une balise, dans une
# URL et dans un lien. Le pied de page de ce programme l'a montre — le PDF
# compose « cation » en italique (dans « anion et cation »), et l'italique
# s'est posee au milieu de l'adresse : `www.edu*cation*.gouv.fr`. L'occurrence
# est comptee comme les autres, elle n'est simplement pas marquee.
_SANS_EMPHASE_RE = re.compile(
    r"!?\[[^\]\n]*\]\([^)\n]*\)|<[^<>\n]*>|https?://\S+|www\.\S+")


def est_chasse_fixe(span: dict[str, Any]) -> bool:
    return bool(span.get("flags", 0) & _CHASSE_FIXE_FLAG
                or _NOM_CHASSE_FIXE_RE.search(span.get("font", "") or ""))


def emphases_de_la_page(page: Any) -> tuple[list[_Emphase], list[_Emphase]]:
    """Fragments que le PDF declare gras et italiques, et OU il les declare.

    Chaque fragment vient avec son rang : le numero de son occurrence parmi
    toutes celles du meme texte sur la page, dans l'ordre de lecture. Sans
    lui, un mot gras une fois se retrouvait gras partout — sur le programme
    de SNT, « données » et « algorithmes » sont gras dans la liste a puces du
    Preambule, et se posaient aussi dans le paragraphe au-dessus, qui ne les
    compose pas ainsi. Le rang tient meme si le modele a saute un bandeau ou
    un pied de page : il ne compte que les occurrences de ce texte-la.

    Le code est ecarte : un listing colore ses mots-clefs en gras, `return`
    et `def` sont declares gras sans etre de l'emphase.

    Les accents sont recolles comme ils le sont dans le texte produit, sans
    quoi `r´ecursivit´e` ne se retrouverait jamais dans `récursivité`. Le
    rang, lui, se compte sur le texte brut du PDF : le fragment et le flux y
    portent la meme ecriture, donc le compte est le meme.
    """
    try:
        blocs = page.get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return [], []

    # Le flux de la page dans l'ordre de lecture, et la position de chaque
    # fragment dedans. Un fragment ne franchit pas la fin d'une ligne.
    flux: list[str] = []
    longueur = 0
    reperes: dict[int, list[tuple[str, int]]] = {_GRAS_FLAG: [], _ITALIQUE_FLAG: []}
    for b in blocs:
        for ligne in b.get("lines", []):
            courants: dict[int, tuple[str, int] | None] = {
                _GRAS_FLAG: None, _ITALIQUE_FLAG: None}
            for s in ligne.get("spans", []):
                texte = s.get("text", "")
                for masque in (_GRAS_FLAG, _ITALIQUE_FLAG):
                    marque = (s.get("flags", 0) & masque
                              and not est_chasse_fixe(s))
                    courant = courants[masque]
                    if marque:
                        courants[masque] = ((courant[0] + texte, courant[1])
                                            if courant else (texte, longueur))
                    elif courant:
                        reperes[masque].append(courant)
                        courants[masque] = None
                flux.append(texte)
                longueur += len(texte)
            for masque, courant in courants.items():
                if courant:
                    reperes[masque].append(courant)

    flux_cmp = _pour_comparer("".join(flux))

    def situer(paires: list[tuple[str, int]]) -> list[_Emphase]:
        sortie: list[_Emphase] = []
        for brut, debut in paires:
            # Le rang se compte sur le fragment ebarbe : le flux ne porte pas
            # les espaces de bordure au meme endroit que le fragment.
            debut += len(brut) - len(brut.lstrip())
            noyau = brut.strip()
            propre = recomposer_accents(noyau)[1].strip()
            if len(propre) < LONGUEUR_EMPHASE_MIN \
                    or not re.search(r"[A-Za-zÀ-ÿ]", propre):
                continue
            rang = flux_cmp.count(_pour_comparer(noyau), 0, debut)
            sortie.append((propre, rang))
        return sortie

    return situer(reperes[_GRAS_FLAG]), situer(reperes[_ITALIQUE_FLAG])


# Le PDF ecrit `l’objet`, un modele ecrit `l'objet` : la meme phrase, deux
# apostrophes. Ces equivalences servent A COMPARER, jamais a reecrire — elles
# conservent la longueur, ce qui permet de retrouver dans le texte d'origine
# la position trouvee dans le texte normalise.
_EQUIVALENCES = str.maketrans({
    "’": "'", "‘": "'", "‛": "'", "´": "'",
    "“": '"', "”": '"', "„": '"',
    " ": " ", " ": " ", " ": " ",   # espaces insecables
    "–": "-", "—": "-", "‐": "-", "‑": "-",
})


def _pour_comparer(texte: str) -> str:
    return texte.translate(_EQUIVALENCES)


def _marquer_occurrences(ligne: str, fragment: str, marque: str,
                         rangs: set[int], deja_vues: int,
                         ferme: str | None = None,
                         ) -> tuple[str, int, int]:
    """Marque les occurrences dont le rang est demande, dans cette ligne.

    Rend (ligne, occurrences vues, marques posees). `deja_vues` est le nombre
    d'occurrences deja rencontrees plus haut dans la page : le rang se compte
    sur la page entiere, la ligne n'en voit qu'un morceau.

    `ferme` sert aux marques qui ne se referment pas comme elles s'ouvrent :
    `<b>` se ferme par `</b>`, quand `**` se ferme par lui-meme.

    Une occurrence deja mise en valeur par le modele compte comme les autres
    — le PDF l'a lue aussi — mais on ne la marque pas deux fois. Il en va de
    meme d'une occurrence tombee dans une URL, un lien ou une balise.

    La recherche se fait sur une forme normalisee — apostrophes, tirets et
    espaces insecables ramenes a une seule ecriture — mais le texte rendu
    reste celui d'origine : la normalisation conserve les longueurs, donc les
    positions se reportent telles quelles.
    """
    frag = _pour_comparer(fragment)
    if not frag:
        return ligne, 0, 0
    if ferme is None:
        ferme = marque
    ligne_cmp = _pour_comparer(ligne)
    interdits = [(m.start(), m.end()) for m in _SANS_EMPHASE_RE.finditer(ligne)]
    morceaux: list[str] = []
    position = 0
    vues = 0
    poses = 0
    i = ligne_cmp.find(frag)
    while i >= 0:
        fin = i + len(frag)
        if ligne.count("`", 0, i) % 2:      # dans un code en ligne
            i = ligne_cmp.find(frag, fin)
            continue
        deja = (ligne[max(0, i - 2):i].endswith(("*", "_"))
                or ligne[fin:fin + 2].startswith(("*", "_"))
                or ligne[max(0, i - 3):i].endswith(("<b>", "<i>"))
                or ligne[fin:fin + 4].startswith(("</b>", "</i>"))
                or any(d < fin and i < f for d, f in interdits))
        rang = deja_vues + vues
        vues += 1
        if rang in rangs and not deja:
            morceaux.append(ligne[position:i])
            morceaux.append(marque + ligne[i:fin] + ferme)
            position = fin
            poses += 1
        i = ligne_cmp.find(frag, fin)
    if not poses:
        return ligne, vues, 0
    morceaux.append(ligne[position:])
    return "".join(morceaux), vues, poses


def _marquer_dans_les_cellules(ligne: str, fragment: str, marque: str,
                               rangs: set[int], deja_vues: int,
                               ) -> tuple[str, int, int]:
    """Marque un fragment dans les cellules d'un tableau tenant sur une ligne.

    Le balisage du tableau n'est jamais traverse : la recherche se fait
    cellule par cellule, dans l'ordre ou elles se lisent, et l'emphase s'y
    ecrit en HTML. Ce qui est hors cellule — les balises — ne compte ni comme
    vu ni comme marque.

    Une cellule d'en-tete est comptee mais pas marquee : elle est deja en
    gras par son role, et un `<b>` de plus n'y ajoute rien.
    """
    ouvre, ferme = _MARQUES_HTML.get(marque, (marque, marque))
    morceaux: list[str] = []
    position = 0
    vues = 0
    poses = 0
    for cellule in _CELLULE_RE.finditer(ligne):
        voulus = set() if cellule.group(2).lower() == "th" else rangs
        contenu, n_vues, n_poses = _marquer_occurrences(
            cellule.group(3), fragment, ouvre, voulus, deja_vues + vues, ferme)
        morceaux.append(ligne[position:cellule.start()])
        morceaux.append(cellule.group(1) + contenu + cellule.group(4))
        vues += n_vues
        poses += n_poses
        position = cellule.end()
    if not poses:
        return ligne, vues, 0
    morceaux.append(ligne[position:])
    return "".join(morceaux), vues, poses


def corriger_emphases(md_page: str, gras: list[_Emphase],
                      italiques: list[_Emphase]) -> tuple[int, str]:
    """Ramene a l'italique un gras pose sur un passage que le PDF dit italique.

    Un modele de vision rend volontiers l'italique en gras : sur le cours de
    NSI, « fonction récursive » et « récursif » sortent en `**…**` alors que
    le PDF les compose en italique. Le fichier tranche, comme pour le reste.

    Un fragment declare gras ET italique n'est pas touche : les deux se
    valent, et le gras porte plus loin.
    """
    if not italiques:
        return 0, md_page
    gras_cmp = {_pour_comparer(x) for x, _ in gras}
    ital_cmp = {_pour_comparer(x) for x, _ in italiques
                if _pour_comparer(x) not in gras_cmp}
    if not ital_cmp:
        return 0, md_page
    n = 0

    def _un(m: "re.Match[str]") -> str:
        nonlocal n
        if _pour_comparer(m.group(1).strip()) in ital_cmp:
            n += 1
            return "*" + m.group(1) + "*"
        return m.group(0)

    # La substitution d'abord : dans `return n, re.sub(...)`, Python evalue
    # `n` avant d'appeler `re.sub`, et le compte rendu serait toujours nul.
    neuf = re.sub(r"\*\*([^*\n]+)\*\*", _un, md_page)
    return n, neuf


def poser_emphases(md_page: str, gras: list[_Emphase],
                   italiques: list[_Emphase]) -> tuple[int, str]:
    """Marque dans le Markdown ce que le PDF declare gras ou italique, la ou
    il le declare.

    Un fragment n'est plus marque partout ou il se rencontre, mais aux seuls
    rangs d'occurrence que le PDF designe. Le compte court sur toute la page
    et non sur une ligne : c'est la page que `emphases_de_la_page` a lue.

    Les fragments les plus longs d'abord : « Exemple 2.1 (Somme des premiers
    entiers) » doit se marquer avant « Exemple », sinon le court prend la
    place du long et le reste se retrouve hors du gras.
    """
    if not gras and not italiques:
        return 0, md_page
    voulus: dict[tuple[str, str], set[int]] = {}
    for fragment, rang in gras:
        voulus.setdefault((fragment, "**"), set()).add(rang)
    for fragment, rang in italiques:
        voulus.setdefault((fragment, "*"), set()).add(rang)
    ordre = sorted(voulus.items(), key=lambda kv: len(kv[0][0]), reverse=True)

    lignes = md_page.split("\n")
    poses = 0
    for (fragment, marque), rangs in ordre:
        vues = 0
        dans_code = False
        for i, ligne in enumerate(lignes):
            if ligne.strip().startswith("```"):
                dans_code = not dans_code
                continue
            if dans_code or not ligne.strip():
                continue
            if _EST_TABLEAU_RE.match(ligne):
                neuve, n_vues, n_poses = _marquer_dans_les_cellules(
                    ligne, fragment, marque, rangs, vues)
                lignes[i] = neuve
                vues += n_vues
                poses += n_poses
                continue
            # Une ligne protegee ne se marque pas, mais ses occurrences
            # comptent : le PDF les a lues comme les autres.
            protegee = bool(_LIGNE_PROTEGEE_RE.match(ligne))
            neuve, n_vues, n_poses = _marquer_occurrences(
                ligne, fragment, marque, set() if protegee else rangs, vues)
            lignes[i] = neuve
            vues += n_vues
            poses += n_poses
    return poses, "\n".join(lignes)


# Deux marques que seule une espace separe. Dans une cellule, le PDF compose
# souvent une meme phrase sur trois ou quatre lignes imprimees, et un fragment
# d'emphase ne franchit jamais une fin de ligne : le texte des cellules etant
# recolle, on se retrouve avec `<b>Corps purs et melanges au</b>
# <b>quotidien.</b>` la ou le document montre une seule mise en valeur. Le
# recollage des lignes de continuation d'un tableau produit le meme effet, une
# cellule plus loin.
_EMPHASES_VOISINES_RE = re.compile(r"</(b|i)>(\s*)<\1>")


def recoller_emphases(md: str) -> str:
    """Reunit les marques voisines qu'une fin de ligne imprimee separait.

    Se pose en fin de chaine, la ou les moteurs se rejoignent : les tableaux
    sont recolles, et plus rien ne viendra separer deux marques a nouveau.
    """
    ancien = None
    while ancien != md:
        ancien = md
        md = _EMPHASES_VOISINES_RE.sub(lambda m: m.group(2), md)
    return md
