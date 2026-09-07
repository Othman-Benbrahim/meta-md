# -*- coding: utf-8 -*-
"""Lit la hierarchie des titres dans l'arbre de structure d'un PDF balise.

**Le niveau d'un titre est ecrit dans le fichier.** Un PDF exporte depuis
Word porte un arbre de structure : chaque paragraphe y est un `StructElem`
de type `/H1`../`/H6`, exactement le style que l'auteur a choisi. 27 des 29
PDF du corpus en ont un — ce sont des exports Word du Bulletin officiel.

C'est une meilleure source que la police. `_TitresPdf` classe des signatures
(taille, graisse, style, couleur) pour deviner une hierarchie ; sur le
programme de mathematiques de seconde, « Intentions majeures » et
« Competences mathematiques » se composent trop pareil pour se distinguer, et
sortaient au meme rang. L'arbre les donne en `H3` et `H4`, sans rien deviner.

Deux moities a recoller, car le PDF les separe :

- l'arbre donne `(niveau, page, identifiants de contenu marque)` ;
- le flux de la page donne la position de chaque contenu marque.

On ne decode pas les glyphes du flux : c'est la que sont les pieges (polices
sous-ensemble, `Identity-H`), et PyMuPDF sait deja lire le texte. On ne
releve donc que des **lignes de base**, et l'appelant apparie ses propres
lignes par la position. Un titre compose sur deux lignes se recolle ainsi de
lui-meme, ce qu'un appariement sur le texte ne saurait pas faire.

Aucune dependance nouvelle : PyMuPDF, deja la, suffit.
"""
from __future__ import annotations

import re
from typing import Any

import pymupdf  # type: ignore[import-untyped]

# `12 0 R` : une reference indirecte. Le premier nombre est le numero d'objet.
_REF_RE = re.compile(r"(\d+)\s+\d+\s+R")
_TITRE_RE = re.compile(r"H([1-6])\Z")
# Un entier isole dans un `/K` est un identifiant de contenu marque.
_ENTIER_RE = re.compile(r"(?<![\w.])(\d+)(?![\w.])")
_MCID_RE = re.compile(r"/MCID\s+(\d+)")

# Les operateurs d'un flux de contenu, et les objets qu'ils consomment.
_JETON_RE = re.compile(
    rb"(?P<chaine>\((?:\\.|[^\\()])*\))"
    rb"|(?P<hexa><[0-9A-Fa-f\s]*>)"
    rb"|(?P<ouvre_dict><<)|(?P<ferme_dict>>>)"
    rb"|(?P<crochet>[\[\]])"
    rb"|(?P<nom>/[^\s/\[\]<>(){}%]*)"
    rb"|(?P<nombre>[-+]?[0-9]*\.?[0-9]+)"
    # `\x22` est le guillemet droit : l'ecrire tel quel fermerait la chaine.
    rb"|(?P<operateur>[A-Za-z'\x22*][A-Za-z0-9'\x22*]*)")

# Au-dela, l'arbre est malforme ou cyclique : on abandonne plutot que boucler.
_PROFONDEUR_MAX = 60

# Les operateurs qui commencent une nouvelle ligne de texte.
_NOUVELLE_LIGNE = ("T*", "'", '"')


def _refs_et_mcids(texte: str | None) -> tuple[list[int], list[int]]:
    """Demele un `/K` : references aux enfants d'un cote, identifiants de
    contenu marque de l'autre.

    `/K` prend toutes les formes que le format autorise : `[ 0 ]` (des
    identifiants nus), `[ 12 0 R 13 0 R ]` (des enfants), `12 0 R` (un seul
    enfant), ou `<< /Type /MCR /MCID 3 >>`. Les references sortent d'abord,
    car `12 0 R` contient deux entiers qu'on prendrait sinon pour des
    identifiants.
    """
    if not texte:
        return [], []
    refs = [int(m.group(1)) for m in _REF_RE.finditer(texte)]
    reste = _REF_RE.sub(" ", texte)
    mcids = [int(x) for x in _ENTIER_RE.findall(reste)]
    mcids += [int(x) for x in _MCID_RE.findall(texte)]
    return refs, sorted(set(mcids))


def _valeur_de(doc: Any, xref: int, cle: str) -> str | None:
    essai = doc.xref_get_key(xref, cle)
    return essai[1] if essai[0] != "null" else None


def _tous_les_mcids(doc: Any, xref: int, vus: set[int],
                    profondeur: int = 0) -> list[int]:
    """Ramasse les identifiants sous un element, a n'importe quelle profondeur.

    Word interpose parfois un `/Span` entre un element et son contenu ; s'en
    tenir au premier niveau laissait le titre — ou la cellule — sans texte.
    """
    if xref in vus or profondeur > _PROFONDEUR_MAX:
        return []
    vus.add(xref)
    refs, mcids = _refs_et_mcids(_valeur_de(doc, xref, "K"))
    for enfant in refs:
        mcids += _tous_les_mcids(doc, enfant, vus, profondeur + 1)
    return mcids


def _type_de(doc: Any, xref: int) -> str:
    return (_valeur_de(doc, xref, "S") or "").lstrip("/")


def _positions_par_mcid(page: Any) -> dict[int, list[tuple[float, float]]]:
    """Position de depart de chaque ecriture de texte, par contenu marque.

    On rejoue le flux en ne suivant que ce qui deplace le curseur — matrice
    de texte, matrice courante — et on note, a chaque operateur d'ecriture, ou
    le curseur se trouve. Le resultat est en coordonnees PyMuPDF, donc
    directement comparable aux boites de `get_text`.
    """
    try:
        flux = page.read_contents()
    except Exception:  # noqa: BLE001
        return {}
    if not flux:
        return {}
    vers_mupdf = page.transformation_matrix

    positions: dict[int, list[tuple[float, float]]] = {}
    pile_mcid: list[int | None] = []
    pile_ctm: list[Any] = []
    ctm = pymupdf.Matrix(1, 1)
    tm = pymupdf.Matrix(1, 1)       # matrice de texte
    tlm = pymupdf.Matrix(1, 1)      # matrice de debut de ligne
    interligne = 0.0
    operandes: list[bytes] = []
    dico: dict[str, float] = {}
    profondeur_dico = 0
    cle: str | None = None

    def noter() -> None:
        """Retient la position courante pour le contenu marque le plus proche."""
        mcid = next((m for m in reversed(pile_mcid) if m is not None), None)
        if mcid is None:
            return
        p = pymupdf.Point(tm.e, tm.f) * ctm * vers_mupdf
        positions.setdefault(mcid, []).append((p.x, p.y))

    for jeton in _JETON_RE.finditer(flux):
        genre = jeton.lastgroup
        brut = jeton.group()

        # Un dictionnaire en ligne : seul `/MCID` nous interesse.
        if genre == "ouvre_dict":
            profondeur_dico += 1
            if profondeur_dico == 1:
                dico = {}
            continue
        if genre == "ferme_dict":
            profondeur_dico = max(0, profondeur_dico - 1)
            continue
        if profondeur_dico:
            if genre == "nom":
                cle = brut[1:].decode("latin-1")
            elif genre == "nombre" and cle:
                try:
                    dico[cle] = float(brut)
                except ValueError:
                    pass
                cle = None
            continue

        if genre != "operateur":
            operandes.append(brut)
            continue

        op = brut.decode("latin-1")
        nombres: list[float] = []
        for o in operandes:
            try:
                nombres.append(float(o))
            except ValueError:
                pass

        if op == "BDC":
            pile_mcid.append(int(dico["MCID"]) if "MCID" in dico else None)
            dico = {}
        elif op == "BMC":
            pile_mcid.append(None)
        elif op == "EMC":
            if pile_mcid:
                pile_mcid.pop()
        elif op == "q":
            pile_ctm.append(pymupdf.Matrix(ctm))
        elif op == "Q":
            if pile_ctm:
                ctm = pile_ctm.pop()
        elif op == "cm" and len(nombres) >= 6:
            ctm = pymupdf.Matrix(*nombres[-6:]) * ctm
        elif op == "BT":
            tm = pymupdf.Matrix(1, 1)
            tlm = pymupdf.Matrix(1, 1)
        elif op == "Tm" and len(nombres) >= 6:
            tm = pymupdf.Matrix(*nombres[-6:])
            tlm = pymupdf.Matrix(tm)
        elif op in ("Td", "TD") and len(nombres) >= 2:
            if op == "TD":
                interligne = -nombres[-1]
            tlm = pymupdf.Matrix(1, 0, 0, 1, nombres[-2], nombres[-1]) * tlm
            tm = pymupdf.Matrix(tlm)
        elif op == "TL" and nombres:
            interligne = nombres[-1]
        elif op in _NOUVELLE_LIGNE:
            tlm = pymupdf.Matrix(1, 0, 0, 1, 0, -interligne) * tlm
            tm = pymupdf.Matrix(tlm)
            if op != "T*":          # `'` et `"` ecrivent, `T*` ne fait que sauter
                noter()
        elif op in ("Tj", "TJ"):
            noter()

        operandes = []

    return positions


def _elements_de_titre(doc: Any) -> list[tuple[int, int | None, list[int]]]:
    """Parcourt l'arbre et rend `(niveau, page, identifiants)` dans l'ordre."""
    catalogue = doc.pdf_catalog()
    valeur = doc.xref_get_key(catalogue, "StructTreeRoot")
    if valeur[0] == "null":
        return []
    depart = _REF_RE.match((valeur[1] or "").strip())
    if not depart:
        return []

    page_de_xref = {doc[i].xref: i for i in range(doc.page_count)}
    trouves: list[tuple[int, int | None, list[int]]] = []

    def valeur_de(xref: int, cle: str) -> str | None:
        return _valeur_de(doc, xref, cle)

    def descendre(xref: int, vus: set[int], page_heritee: str | None,
                  profondeur: int) -> None:
        if xref in vus or profondeur > _PROFONDEUR_MAX:
            return
        vus.add(xref)
        type_ = _type_de(doc, xref)
        page_txt = valeur_de(xref, "Pg") or page_heritee
        refs, mcids = _refs_et_mcids(valeur_de(xref, "K"))

        titre = _TITRE_RE.match(type_)
        if titre:
            ids = list(mcids)
            for enfant in refs:
                ids += _tous_les_mcids(doc, enfant, set())
            page = None
            if page_txt:
                ref = _REF_RE.match(page_txt.strip())
                if ref:
                    page = page_de_xref.get(int(ref.group(1)))
            trouves.append((int(titre.group(1)), page, sorted(set(ids))))
            return  # un titre ne contient pas d'autre titre

        for enfant in refs:
            descendre(enfant, vus, page_txt, profondeur + 1)

    descendre(int(depart.group(1)), set(), None, 0)
    return trouves


def hierarchie(doc: Any,
               ) -> dict[int, list[tuple[int, list[tuple[float, float]]]]] | None:
    """Titres declares par le PDF, page par page, avec leurs positions.

    Rend `{index de page: [(niveau, [(x, y), ...]), ...]}`, les niveaux
    ramenes a une suite continue commencant a 1 — un document qui saute de
    `H1` a `H3` ne doit pas produire de `###` sans `##` au-dessus.

    Rend `None` si le PDF n'est pas balise, ou si son arbre ne donne aucun
    titre exploitable : l'appelant retombe alors sur les polices.
    """
    try:
        elements = _elements_de_titre(doc)
    except Exception:  # noqa: BLE001
        return None
    if not elements:
        return None

    # Les niveaux employes, ramenes a 1..N sans trou.
    employes = sorted({niveau for niveau, _, _ in elements})
    echelle = {brut: rang for rang, brut in enumerate(employes, start=1)}

    positions_de_page: dict[int, dict[int, list[tuple[float, float]]]] = {}
    par_page: dict[int, list[tuple[int, list[tuple[float, float]]]]] = {}
    for niveau, page, mcids in elements:
        if page is None or not mcids:
            continue
        if page not in positions_de_page:
            try:
                positions_de_page[page] = _positions_par_mcid(doc[page])
            except Exception:  # noqa: BLE001
                positions_de_page[page] = {}
        connues = positions_de_page[page]
        points = [p for m in mcids for p in connues.get(m, [])]
        if points:
            par_page.setdefault(page, []).append((echelle[niveau], points))
    return par_page or None


# --- Tableaux declares ------------------------------------------------------
#
# Un `/Table` se lit `/TR` par `/TR`, et chaque `/TR` en `/TD`. C'est la
# structure que `find_tables` doit deviner sur des filets, et qu'elle devine
# mal : sur le programme de physique-chimie de seconde, elle voit 19 lignes la
# ou le document en declare 5, parce qu'elle coupe une cellule de plusieurs
# paragraphes a chaque retour a la ligne. Sur le corpus entier, 1179 lignes
# vues pour 979 declarees.
#
# Ce que l'arbre ne dit pas, et qu'il faut continuer de deduire : aucun `/TH`
# n'existe dans ce corpus (0 sur 2057 cellules), et aucun attribut
# `ColSpan` — les 128 `/A` rencontres portent tous `/O /List`. Une ligne a une
# seule cellule EST la cellule fusionnee, mais c'est l'appelant qui en tire le
# `colspan`. Enfin Word emet un `/Table` par page : un tableau coupe par une
# fin de page reste deux fragments ici comme ailleurs.

# Ce qui separe une ligne de tableau de ses cellules. Word interpose un
# `/TBody`, parfois un `/THead`, et rien n'interdit un groupe anonyme.
_GROUPES_DE_TABLEAU = ("TBody", "THead", "TFoot", "NonStruct", "Div", "")
_CELLULES = ("TD", "TH")

# Ce qu'un segment de contenu est, du point de vue des listes :
#   - `texte` : du texte suivi, hors de toute liste ;
#   - `item`  : le corps d'un `/LI`, c'est-a-dire un item de liste ;
#   - `puce`  : le `/Lbl` d'un `/LI`, le glyphe de la puce, qui ne se recopie
#     pas — la puce est portee par la balise de liste, pas par le texte.
TEXTE, ITEM, PUCE = "texte", "item", "puce"

# Un segment : son genre, son niveau d'imbrication de liste (0 hors liste) et
# les positions du flux qui lui appartiennent. Meme monnaie que `hierarchie`,
# pour la meme raison — on ne decode pas les glyphes, on releve des positions
# et l'appelant apparie.
_Segment = tuple[str, int, list[tuple[float, float]]]
_Cellule = list[_Segment]
_Rang = list[_Cellule]
class _Tableau(list[_Rang]):
    """Lignes d'un fragment et rangs explicitement declares en en-tete."""

    def __init__(self) -> None:
        super().__init__()
        self.entetes: set[int] = set()


_SegmentMarque = tuple[str, int, str | None, list[int]]
_RangMarque = tuple[str | None, list[list[_SegmentMarque]], bool]


# Un habillage en ligne ne coupe pas le paragraphe qui le porte : `/Span` en
# est plein (1557 dans le corpus), Word en pose un des qu'une police change au
# fil d'une phrase. Un segment doit valoir un PARAGRAPHE, sinon la cellule se
# briserait a chaque mot mis en valeur.
_EN_LIGNE = ("Span", "Link", "Em", "Strong", "Code", "Sub", "Sup",
             "Reference", "Annot", "BibEntry")


def _contenus_de_k(texte: str | None, page: str | None,
                   ) -> tuple[list[int], list[tuple[str | None, int]]]:
    """Enfants et contenus marques, avec la page propre a chaque MCR.

    Un MCID n'est unique que SUR SA PAGE. Une reference /Pg dans un
    dictionnaire /MCR n'est pas un enfant de l'arbre a parcourir.
    """
    enfants: list[int] = []
    contenus: list[tuple[str | None, int]] = []
    for m in re.finditer(r"<<.*?>>|\d+\s+\d+\s+R|\d+", texte or "", re.S):
        morceau = m.group()
        if morceau.startswith("<<"):
            mcid = _MCID_RE.search(morceau)
            pg = re.search(r"/Pg\s+(\d+\s+\d+\s+R)", morceau)
            if mcid:
                contenus.append((pg.group(1) if pg else page, int(mcid.group(1))))
        elif ref := _REF_RE.fullmatch(morceau):
            enfants.append(int(ref.group(1)))
        else:
            contenus.append((page, int(morceau)))
    return enfants, contenus


def _segments(doc: Any, xref: int, vus: set[int], profondeur: int = 0,
              niveau: int = 0, genre: str = TEXTE,
              page_heritee: str | None = None,
              ) -> list[_SegmentMarque]:
    """Le contenu d'un sous-arbre en segments, dans l'ordre du document.

    **Un segment vaut un paragraphe**, et c'est ce qui permet a l'appelant de
    rendre les alineas d'une cellule : la cellule d'introduction de la page 4
    du programme de physique-chimie de seconde porte sept `/P`, que le
    document imprime sur sept alineas et qu'un seul bloc de texte melangeait.

    Un `/L` fait monter le niveau d'imbrication, un `/Lbl` ouvre une puce et
    un `/LBody` un item ; le reste herite du genre de son parent.
    """
    if xref in vus or profondeur > _PROFONDEUR_MAX:
        return []
    vus.add(xref)
    page = _valeur_de(doc, xref, "Pg") or page_heritee
    type_ = _type_de(doc, xref)
    if type_ == "L":
        niveau += 1
    elif type_ == "Lbl":
        genre = PUCE
    elif type_ == "LBody":
        genre = ITEM
    refs, contenus = _contenus_de_k(_valeur_de(doc, xref, "K"), page)
    # Un MCR peut aussi etre un objet indirect, plutot qu'un dictionnaire
    # ecrit directement dans /K.
    mcid = _valeur_de(doc, xref, "MCID")
    if mcid and mcid.isdigit():
        contenus.append((page, int(mcid)))
    sortie: list[list[Any]] = []
    par_page: dict[str | None, list[int]] = {}
    for pg, identifiant in contenus:
        par_page.setdefault(pg, []).append(identifiant)
    for pg, mcids in par_page.items():
        sortie.append([genre, niveau, pg, sorted(set(mcids))])
    for enfant in refs:
        sous = _segments(doc, enfant, vus, profondeur + 1, niveau, genre, page)
        if not sous:
            continue
        if (_type_de(doc, enfant) in _EN_LIGNE and sortie
                and sous[0][0] == sortie[-1][0]
                and sous[0][1] == sortie[-1][1]
                and sous[0][2] == sortie[-1][2]):
            sortie[-1][3] = sorted(set(sortie[-1][3] + list(sous[0][3])))
            sous = sous[1:]
        sortie.extend([g, n, pg, list(m)] for g, n, pg, m in sous)
    return [(g, n, pg, m) for g, n, pg, m in sortie]


def _rangs_du_tableau(doc: Any, xref: int, page_heritee: str | None,
                      ) -> list[_RangMarque]:
    """Les `/TR` : page, cellules en segments et en-tete de colonnes explicite."""
    rangs: list[_RangMarque] = []

    def descendre(x: int, page_txt: str | None, vus: set[int],
                  profondeur: int) -> None:
        if x in vus or profondeur > _PROFONDEUR_MAX:
            return
        vus.add(x)
        page_txt = _valeur_de(doc, x, "Pg") or page_txt
        refs, _ = _refs_et_mcids(_valeur_de(doc, x, "K"))
        for enfant in refs:
            type_ = _type_de(doc, enfant)
            page_enfant = _valeur_de(doc, enfant, "Pg") or page_txt
            if type_ == "TR":
                cellules = []
                sous, _ = _refs_et_mcids(_valeur_de(doc, enfant, "K"))
                types_cellules = []
                for c in sous:
                    if _type_de(doc, c) in _CELLULES:
                        cellules.append(_segments(doc, c, set(), page_heritee=page_enfant))
                        types_cellules.append(_type_de(doc, c))
                if cellules:
                    rangs.append((page_enfant, cellules, all(t == "TH" for t in types_cellules)))
            elif type_ in _GROUPES_DE_TABLEAU:
                descendre(enfant, page_enfant, vus, profondeur + 1)

    descendre(xref, page_heritee, set(), 0)
    return rangs


def _elements_de_tableau(doc: Any,
                         ) -> list[list[_RangMarque]]:
    """Chaque `/Table` de l'arbre, en lignes, dans l'ordre du document."""
    valeur = doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")
    if valeur[0] == "null":
        return []
    depart = _REF_RE.match((valeur[1] or "").strip())
    if not depart:
        return []

    trouves: list[list[_RangMarque]] = []

    def descendre(xref: int, vus: set[int], page_heritee: str | None,
                  profondeur: int) -> None:
        if xref in vus or profondeur > _PROFONDEUR_MAX:
            return
        vus.add(xref)
        page_txt = _valeur_de(doc, xref, "Pg") or page_heritee
        if _type_de(doc, xref) == "Table":
            rangs = _rangs_du_tableau(doc, xref, page_txt)
            if rangs:
                trouves.append(rangs)
            return  # un tableau imbrique se lit comme des lignes du parent
        refs, _ = _refs_et_mcids(_valeur_de(doc, xref, "K"))
        for enfant in refs:
            descendre(enfant, vus, page_txt, profondeur + 1)

    descendre(int(depart.group(1)), set(), None, 0)
    return trouves


def tableaux(doc: Any) -> dict[int, list[_Tableau]] | None:
    """Tableaux declares par le PDF, page par page, cellule par cellule.

    Rend `{index de page: [tableau, ...]}`, un tableau etant une liste de
    lignes et une ligne une liste de cellules, chaque cellule portant les
    positions relevees dans le flux — comme `hierarchie` rend celles de ses
    titres. L'appelant apparie son propre texte a ces positions ; ici on ne
    decode aucun glyphe.

    Un `/Table` dont les lignes tombent sur deux pages est rendu comme deux
    fragments, un par page : c'est la forme qu'attend le reste de la chaine,
    ou chaque page est convertie seule.

    Rend `None` si le PDF n'est pas balise ou n'y declare aucun tableau :
    l'appelant retombe alors sur `find_tables`.
    """
    try:
        elements = _elements_de_tableau(doc)
    except Exception:  # noqa: BLE001
        return None
    if not elements:
        return None

    page_de_xref = {doc[i].xref: i for i in range(doc.page_count)}
    positions_de_page: dict[int, dict[int, list[tuple[float, float]]]] = {}

    def positions(page: int) -> dict[int, list[tuple[float, float]]]:
        if page not in positions_de_page:
            try:
                positions_de_page[page] = _positions_par_mcid(doc[page])
            except Exception:  # noqa: BLE001
                positions_de_page[page] = {}
        return positions_de_page[page]

    def numero(page_txt: str | None) -> int | None:
        if not page_txt:
            return None
        ref = _REF_RE.match(page_txt.strip())
        return page_de_xref.get(int(ref.group(1))) if ref else None

    par_page: dict[int, list[_Tableau]] = {}
    for rangs in elements:
        # Les lignes d'un meme tableau, groupees par page, dans l'ordre.
        fragments: dict[int, _Tableau] = {}
        for page_txt, cellules, entete in rangs:
            # Une ligne, et meme une cellule, peut continuer sur une autre
            # page que celle du /TR. Conserver toutes ses colonnes sur chaque
            # page, y compris celles dont le contenu est entierement ailleurs.
            rangs_par_page: dict[int, _Rang] = {}
            for colonne, cellule in enumerate(cellules):
                for genre, niveau, pg, mcids in cellule:
                    page = numero(pg or page_txt)
                    if page is None:
                        continue
                    connues = positions(page)
                    points = [p for m in mcids for p in connues.get(m, [])]
                    if points:
                        rang = rangs_par_page.setdefault(page, [[] for _ in cellules])
                        rang[colonne].append((genre, niveau, points))
            for page, rang in rangs_par_page.items():
                fragment = fragments.setdefault(page, _Tableau())
                if entete:
                    fragment.entetes.add(len(fragment))
                fragment.append(rang)
        for page, fragment in fragments.items():
            par_page.setdefault(page, []).append(fragment)
    return par_page or None


def listes(doc: Any) -> dict[int, list[tuple[int, list[tuple[float, float]]]]] | None:
    """Items de liste declares hors tableau, page par page : (niveau, points).

    Les items d'une cellule n'y sont pas : `tableaux` les rend deja, a leur
    place dans la grille. Ne restent que ceux du texte courant, ou le modele
    de vision se debrouille plutot bien — 1432 puces rendues sur le corpus
    pour 1287 declarees. L'arbre ne sert donc qu'a **ajouter** celles qui
    manquent, jamais a retirer celles qu'il ne declare pas : Word ne balise
    pas tout ce qui se lit comme une liste, et les deux programmes de l'ecole
    elementaire concentrent a eux seuls la moitie du manque.
    """
    valeur = doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")
    if valeur[0] == "null":
        return None
    depart = _REF_RE.match((valeur[1] or "").strip())
    if not depart:
        return None

    page_de_xref = {doc[i].xref: i for i in range(doc.page_count)}
    trouves: list[tuple[str | None, int, list[int]]] = []

    def descendre(xref: int, vus: set[int], page_txt: str | None,
                  niveau: int, profondeur: int) -> None:
        if xref in vus or profondeur > _PROFONDEUR_MAX:
            return
        vus.add(xref)
        type_ = _type_de(doc, xref)
        if type_ in _CELLULES:
            return  # la grille s'en charge
        page_txt = _valeur_de(doc, xref, "Pg") or page_txt
        if type_ == "L":
            niveau += 1
        refs, _ = _refs_et_mcids(_valeur_de(doc, xref, "K"))
        if type_ == "LBody":
            ids = _tous_les_mcids(doc, xref, set())
            if ids:
                trouves.append((page_txt, niveau, sorted(set(ids))))
        for enfant in refs:
            descendre(enfant, vus, page_txt, niveau, profondeur + 1)

    try:
        descendre(int(depart.group(1)), set(), None, 0, 0)
    except Exception:  # noqa: BLE001
        return None
    if not trouves:
        return None

    positions_de_page: dict[int, dict[int, list[tuple[float, float]]]] = {}
    par_page: dict[int, list[tuple[int, list[tuple[float, float]]]]] = {}
    for page_txt, niveau, mcids in trouves:
        ref = _REF_RE.match((page_txt or "").strip())
        page = page_de_xref.get(int(ref.group(1))) if ref else None
        if page is None:
            continue
        if page not in positions_de_page:
            try:
                positions_de_page[page] = _positions_par_mcid(doc[page])
            except Exception:  # noqa: BLE001
                positions_de_page[page] = {}
        points = [p for m in mcids for p in positions_de_page[page].get(m, [])]
        if points:
            par_page.setdefault(page, []).append((niveau, points))
    return par_page or None


def niveau_de_la_ligne(bbox: Any,
                       titres: list[tuple[int, list[tuple[float, float]]]],
                       ) -> int | None:
    """Niveau d'une ligne rendue, si le PDF la declare comme titre.

    On demande que la boite de la ligne **contienne** une position relevee :
    le flux donne des lignes de base, pas des boites, et une ligne de base
    tombe toujours dans la boite de sa propre ligne. Comparer deux boites
    demandait une tolerance, qu'il fallait regler ; ce critere-ci n'en a pas.
    """
    x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
    for niveau, points in titres:
        for x, y in points:
            if y0 <= y <= y1 and x0 - 2.0 <= x <= x1 + 2.0:
                return niveau
    return None
