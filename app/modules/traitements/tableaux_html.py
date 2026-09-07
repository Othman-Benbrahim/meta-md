"""Tableaux HTML : les lire, les normaliser, les recomposer.

Les moteurs rendent tous leurs tableaux en HTML — une grille pipe ne
sait porter ni cellule fusionnee ni liste a puces. Ce module tient la
convention qui vaut dans tout le pipeline : un tableau, une ligne."""
from __future__ import annotations

import re
from typing import Any

from .texte import _echapper, _texte_nu, _mots_cles


# --- Tableaux HTML ----------------------------------------------------------
#
# Convention interne, sur laquelle repose tout ce qui suit : UN TABLEAU OCCUPE
# EXACTEMENT UNE LIGNE du document. Les reparations de ce module raisonnent
# sur des lignes ; un tableau etale sur vingt d'entre elles obligerait chacune
# a savoir ou il commence et ou il finit. `_normaliser_tableaux_html` etablit
# cette forme des l'arrivee d'une page, quelle que soit la mise en page rendue
# par le moteur.
#
# Les regex ci-dessous suffisent parce que le HTML rencontre ici est plat : un
# tableau, ses lignes, ses cellules. Rien n'y est imbrique, et une balise mal
# formee doit laisser le document intact plutot que faire echouer la
# conversion — d'ou des fonctions qui rendent None ou la valeur d'entree quand
# elles ne reconnaissent pas ce qu'on leur donne.

_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.DOTALL | re.IGNORECASE)
_BALISE_TABLE_RE = re.compile(r"^\s*<table\b[^>]*>(.*)</table\s*>\s*$",
                              re.DOTALL | re.IGNORECASE)
_THEAD_RE = re.compile(r"^\s*<thead\b[^>]*>.*?</thead\s*>", re.DOTALL | re.IGNORECASE)
_LIGNE_RE = re.compile(r"<tr\b[^>]*>.*?</tr\s*>", re.DOTALL | re.IGNORECASE)
_PREMIERE_LIGNE_RE = re.compile(r"^\s*<tr\b[^>]*>.*?</tr\s*>", re.DOTALL | re.IGNORECASE)
_CELLULE_RE = re.compile(r"<(t[hd])\b([^>]*)>(.*?)</\1\s*>", re.DOTALL | re.IGNORECASE)
_CELLULE_ENTETE_RE = re.compile(r"<th\b", re.IGNORECASE)
_COLSPAN_RE = re.compile(r"""colspan\s*=\s*["']?(\d+)""", re.IGNORECASE)

# Saut de ligne dans une cellule : c'est la mise en page du PDF, pas du
# contenu, et il disparait au profit d'une espace comme le fait deja
# `_cellule_html` cote extraction.
_BR_RE = re.compile(r"\s*<br\s*/?>\s*", re.IGNORECASE)


def _est_tableau(ligne: str) -> bool:
    """La ligne est-elle un tableau entier ? (voir la convention ci-dessus)"""
    return bool(_BALISE_TABLE_RE.match(ligne.strip()))


def _blocs_de_tableau(lignes: list[str]) -> list[tuple[int, int]]:
    """Bornes (debut, fin exclue) de chaque tableau.

    Un tableau tenant sur une ligne, ces bornes sont toujours d'une ligne. La
    fonction garde sa forme d'intervalle : ses appelants situent le texte qui
    precede et qui suit chaque tableau, pas seulement les tableaux.
    """
    return [(i, i + 1) for i, l in enumerate(lignes) if _est_tableau(l)]


def _interieur(html: str) -> str:
    """Contenu d'un `<table>`, sans ses balises englobantes."""
    m = _BALISE_TABLE_RE.match((html or "").strip())
    return m.group(1) if m else (html or "")


def _lignes_du_tableau(html: str) -> list[str]:
    return _LIGNE_RE.findall(_interieur(html))


def _cellules(tr: str) -> list[tuple[str, str, str]]:
    """(balise, attributs, contenu) de chaque cellule d'une ligne."""
    return _CELLULE_RE.findall(tr)


def _recomposer_ligne(cellules: list[tuple[str, str, str]]) -> str:
    return "<tr>" + "".join(f"<{b}{a}>{c}</{b}>" for b, a, c in cellules) + "</tr>"


def _nb_colonnes(html: str) -> int:
    """Largeur du tableau, colspans compris, lue sur sa premiere ligne."""
    lignes = _lignes_du_tableau(html)
    if not lignes:
        return 0
    total = 0
    for _, attrs, _ in _cellules(lignes[0]):
        m = _COLSPAN_RE.search(attrs)
        total += int(m.group(1)) if m else 1
    return total


def _sans_thead(interieur: str) -> str:
    """Retire le `<thead>` d'ouverture en gardant les lignes qu'il porte.

    Un `<thead>` au milieu d'un tableau est du HTML invalide : la bande de
    titre d'une suite se range en cellules d'en-tete ordinaires.
    """
    nu = (interieur or "").strip()
    tete = _THEAD_RE.match(nu)
    if not tete:
        return nu
    return re.sub(r"</?thead[^>]*>", "", tete.group(0)) + nu[tete.end():]


def _fusionner_tables(html_a: str, html_b: str) -> str:
    """Colle les lignes de `html_b` a la fin de `html_a`.

    Une suite de tableau ouvre sur une ligne d'en-tete dans deux cas, qui ne
    se traitent pas pareil :

     - elle REPETE l'en-tete du fragment precedent, par pure mise en page.
       Elle disparait : recopiee, elle deviendrait une ligne de donnees au
       milieu du tableau.
     - elle porte un NOUVEL intitule, une sous-partie du tableau imprime (les
       programmes du BO en sont pleins). Elle reste, degrafee de son `<thead>`.
    """
    a = _interieur(html_a)
    b = _interieur(html_b).strip()
    tete_b = _THEAD_RE.match(b) or _PREMIERE_LIGNE_RE.match(b)
    if tete_b:
        tete_a = _THEAD_RE.match(a.strip()) or _PREMIERE_LIGNE_RE.match(a.strip())
        if tete_a and _texte_nu(tete_a.group(0)) == _texte_nu(tete_b.group(0)):
            b = b[tete_b.end():]
        else:
            b = _sans_thead(b)
    return f"<table>{a}{b}</table>"


def _sur_une_ligne(html: str) -> str:
    """Ramene un tableau sur une ligne, sans blanc entre deux balises.

    Le dernier `sub` compte : replier un tableau DEJA aere laissait un espace
    la ou il y avait un retour a la ligne, et `_aerer_un_tableau` refusait
    ensuite de le rouvrir — ses jetons ne decrivent pas ces blancs, la
    couverture echouait, et le tableau restait sur une ligne. Sans lui, aerer
    puis replier n'etait pas reversible.

    Les blancs a l'interieur du texte d'une cellule sont conserves : seuls
    ceux qui separent `>` de `<` disparaissent.
    """
    plat = re.sub(r"\s{2,}", " ", _BR_RE.sub(" ", html.replace("\n", " "))).strip()
    return re.sub(r">\s+<", "><", plat)


def _fermer_tableaux(texte: str) -> str:
    """Ferme un `<table>` que le moteur a laisse ouvert.

    Ses lignes valent mieux que rien, mais la balise se pose apres le dernier
    `</tr>` du tableau, et non a la fin de la page : un tableau ouvert au
    milieu d'une page avalerait tout ce qui le suit.
    """
    insertions: list[int] = []
    ouverte = False
    dernier_tr: int | None = None
    for j in re.finditer(r"<table\b|</table\s*>|</tr\s*>", texte, re.IGNORECASE):
        mot = j.group(0).lower()
        if mot.startswith("<table"):
            if ouverte:
                insertions.append(dernier_tr if dernier_tr is not None else j.start())
            ouverte, dernier_tr = True, None
        elif mot.startswith("</table"):
            ouverte, dernier_tr = False, None
        elif ouverte:
            dernier_tr = j.end()
    if ouverte:
        insertions.append(dernier_tr if dernier_tr is not None else len(texte))
    for pos in reversed(insertions):
        texte = texte[:pos] + "</table>" + texte[pos:]
    return texte


def tableaux_eparpilles(md: str) -> int:
    """Balises de tableau restees seules sur leur ligne, hors d'un bloc replie.

    Un `<table>` sans son `</table>` sur la meme ligne n'est pas un tableau
    pour la suite du pipeline : `_est_tableau` ne le reconnait pas, donc ni le
    recollage des fragments, ni les reparations depuis le PDF, ni l'aeration
    ne le voient. Ses balises repartent telles quelles dans le Markdown, et le
    lecteur affiche un tableau en pieces detachees.
    """
    n = 0
    for ligne in (md or "").split("\n"):
        s = ligne.strip()
        if not s or _est_tableau(s):
            continue
        if re.match(r"</?(?:table|thead|tbody|tr|td|th)\b", s, re.IGNORECASE):
            n += 1
    return n


def _retirer_clotures_de_tableaux(md: str, texte_pdf: str) -> str:
    """Retire un emballage de code ajoute autour d'un tableau transcrit.

    Le PDF doit corroborer le contenu et ne pas imprimer de code HTML de
    tableau. Sans couche texte, ou si le PDF montre du code, on conserve
    les clotures. Le texte qui suit le tableau dans le bloc est preserve.
    """
    if not texte_pdf or re.search(r'<table\b', texte_pdf, re.I):
        return md
    vocabulaire = _mots_cles(texte_pdf)

    def verifier(m: Any) -> str:
        corps = m.group(2)
        mots = _mots_cles(corps)
        if (not re.match(r'\s*<table\b', corps, re.I)
                or not re.search(r'</table\s*>', corps, re.I)
                or len(mots) < 3 or len(mots & vocabulaire) / len(mots) < .9):
            return m.group(0)
        return corps.strip('\n')

    return re.sub(r'^(`{3,}|~{3,})(?:html|markdown|md)[ \t]*\n(.*?)^\1[ \t]*$',
                  verifier, md, flags=re.M | re.S | re.I)


def _normaliser_tableaux_html(md: str) -> str:
    """Ramene chaque tableau de la page a une seule ligne, isolee.

    **A rejouer apres toute etape qui travaille ligne a ligne.** Le modele
    rend parfois son tableau sur une dizaine de lignes ; la normalisation le
    replie a l'arrivee de la page, mais une reparation ulterieure peut le
    re-eclater. Un tableau eparpille est invisible de tout ce qui suit :
    verifie le 2026-08-23 sur le programme d'histoire-geographie de seconde,
    dont deux pages sortaient avec leurs balises en clair.
    """
    texte = _fermer_tableaux(md or "")
    texte = _TABLE_RE.sub(
        lambda m: "\n\n" + _puces_des_cellules(_titres_des_cellules(
            _poser_le_thead(_sur_une_ligne(m.group(0))))) + "\n\n",
        texte)
    return re.sub(r"\n{3,}", "\n\n", texte).strip("\n")


# Une ligne faite uniquement de `<th>`, quelle que soit la casse ou les
# attributs portes par les cellules.
_RANG_ENTETE_SEUL = re.compile(
    r"<tr\b[^>]*>\s*(?:<th\b[^>]*>.*?</th>\s*)+</tr>", re.S | re.I)


_ITEM_EN_TEXTE = re.compile(r"(?:^|<br\s*/?>)\s*[-–−]\s+", re.IGNORECASE)


# Un titre, de n'importe quel rang, ecrit a l'interieur d'une cellule.
_TITRE_EN_CELLULE = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]\s*>",
                               re.DOTALL | re.IGNORECASE)
_DEJA_GRAS = re.compile(r"^\s*<(b|strong)\b[^>]*>(.*)</\1\s*>\s*$",
                        re.DOTALL | re.IGNORECASE)


def _titres_des_cellules(table: str) -> str:
    """Un intertitre ecrit dans une cellule redevient du gras.

    **Une cellule n'a pas de titre.** Ce que le PDF compose en gras a
    l'interieur d'une cellule — « Rechercher le tout dans un probleme de
    groupements », et les autres intertitres de la colonne des exemples du
    programme de cycle 1 — le modele le rend parfois en `<h3>`. Le lecteur
    voit alors un titre de section a taille pleine au milieu d'un tableau, et
    le plan du document gagne une entree qui n'annonce rien.

    La regle est celle qui vaut deja pour les emphases : dans une cellule on
    ecrit `<b>` et `<i>`, jamais du Markdown, et desormais jamais de titre.
    Les grilles reconstruites depuis le PDF ecrivent ces memes intertitres
    `<b>...</b><br>` ; le modele s'y aligne, et le meme document cesse de
    montrer deux formes pour la meme chose. Mesure sur le programme de
    cycle 1 : trois `<h3>` dans une cellule de la page 46, aucun ailleurs.

    Un contenu deja gras ne se remballe pas — `<h3><b>...</b></h3>` donnerait
    `<b><b>...</b></b>`.
    """
    def cellule(m: Any) -> str:
        balise, attrs, corps = m.group(1), m.group(2), m.group(3)
        if not _TITRE_EN_CELLULE.search(corps):
            return m.group(0)

        def titre(t: Any) -> str:
            texte = t.group(1).strip()
            if not texte:
                return ""
            interne = _DEJA_GRAS.match(texte)
            return f"<b>{interne.group(2).strip() if interne else texte}</b><br>"

        return f"<{balise}{attrs}>{_TITRE_EN_CELLULE.sub(titre, corps)}</{balise}>"

    return _CELLULE_RE.sub(cellule, table)


def _puces_des_cellules(table: str) -> str:
    """Rend en `<ul><li>` les items qu'une cellule ecrit avec un tiret.

    Deux chemins produisent des tableaux et ne balisaient pas leurs listes
    pareil. Ceux reconstruits depuis le PDF passent par `pdf.tableaux`, qui lit
    les `/LI` de l'arbre et pose de vrais `<li>` ; ceux que le modele rend
    arrivent avec « - Articuler distinctement… » en texte, tiret compris.
    Mesure sur le programme de cycle 1 : 118 tableaux en `<li>`, 7 en tirets,
    parfois a deux pages d'ecart — le lecteur voit une puce ici et un tiret la,
    pour la meme chose.

    Une cellule qui porte deja un `<li>` n'est pas touchee, et le texte qui
    precede le premier tiret reste du texte : il introduit la liste, il n'en
    fait pas partie.
    """
    def cellule(m: Any) -> str:
        balise, attrs, corps = m.group(1), m.group(2), m.group(3)
        if "<li" in corps.lower() or not _ITEM_EN_TEXTE.search(corps):
            return m.group(0)
        morceaux = _ITEM_EN_TEXTE.split(corps)
        tete = morceaux[0].strip()
        items = [x.strip() for x in morceaux[1:] if x.strip()]
        if not items:
            return m.group(0)
        liste = "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"
        return f"<{balise}{attrs}>{tete + liste if tete else liste}</{balise}>"

    return _CELLULE_RE.sub(cellule, table)


def _poser_le_thead(table: str) -> str:
    """Enveloppe dans `<thead>` une premiere ligne faite de `<th>`.

    Deux chemins produisent des tableaux, et ils ne les balisaient pas pareil.
    Ceux que l'on construit depuis le PDF passent par `_assembler_tableau`,
    qui pose le `<thead>` ; ceux que le modele rend arrivent avec un
    `<tr><th>…</th></tr>` nu. Mesure sur le programme de cycle 1 : 92 tableaux
    avec `<thead>`, 29 sans, pour un en-tete de meme nature. Rien en aval n'a
    a connaitre deux formes.

    On n'enveloppe que la PREMIERE ligne, et seulement si elle n'est faite que
    de `<th>` : une bande de titre au milieu d'un tableau garde ses `<th>` sans
    `<thead>`, qui y serait invalide — c'est deja la regle de
    `_assembler_tableau`, et elle ne change pas.
    """
    if "<thead" in table.lower():
        return table
    ouvrant = re.match(r"\s*<table\b[^>]*>", table, re.I)
    if not ouvrant:
        return table
    reste = table[ouvrant.end():]
    premier = _RANG_ENTETE_SEUL.match(reste.lstrip())
    if not premier:
        return table
    decalage = len(reste) - len(reste.lstrip())
    rang = premier.group(0)
    return (table[:ouvrant.end()] + reste[:decalage] + "<thead>" + rang + "</thead>"
            + reste[decalage + len(rang):])


def _cellule_html(valeur: Any) -> str:
    """Contenu d'une cellule declaree par le PDF, rendu en HTML.

    Le PDF coupe le texte d'une cellule a chaque fin de ligne imprimee : ces
    retours sont de la mise en page, pas du contenu, et les recoller redonne
    le texte d'origine. Rien n'est pose a leur place, pas meme un `<br>` : la
    ponctuation suffit deja a separer deux phrases d'une meme cellule, et un
    saut de ligne de plus n'ajouterait rien a la segmentation.
    """
    texte = (valeur or "").strip()
    return _echapper(" ".join(m.strip() for m in texte.split("\n") if m.strip()))
# Rangs ou un en-tete de colonnes peut se tenir : en premier, ou sous
# l'intitule d'une partie et son paragraphe d'introduction, qui occupent
# chacun toute la largeur. Au-dela, une ligne courte et pleine est une ligne
# de donnees comme une autre.
RANGS_ENTETE = 3
