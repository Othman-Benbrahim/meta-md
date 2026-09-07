"""Tableaux extraits du PDF lui-meme.

Quand un modele resume ou deforme une grille, le PDF garde la version
exacte : positions des cellules, spans, petits caracteres."""
from __future__ import annotations

import logging
import re
from typing import Any

from . import balises
from ..traitements.tableaux_html import RANGS_ENTETE, _cellule_html
from ..traitements.texte import _PUCE_EN_TETE_RE, _mots_cles, _texte_nu


logger = logging.getLogger("atelier.pdf.tableaux")


# Une ligne d'en-tete de colonnes annonce, elle n'expose pas : toutes ses
# cellules sont remplies, et toutes courtes. Au-dela, c'est une ligne de
# donnees — « Composition massique d'un melange. » en tete de la page 5 de ce
# programme n'est pas un en-tete, c'est la ligne coupee par la page d'avant.
LONGUEUR_ENTETE_MAX = 80


# Un indice et un exposant se composent plus petit, et sur une autre ligne de
# base que le reste de leur ligne. En dessous de ce rapport de taille, un span
# n'est pas du texte courant.
_RAPPORT_PETIT = 0.85
# Ecart de ligne de base au-dela duquel le petit caractere est declare monte
# ou descendu. Un demi-point suffit : ces PDF decalent de 1,5 point.
_ECART_LIGNE_PETITE = 0.5

_EN_EXPOSANT = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
                "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
                "+": "⁺", "-": "⁻", "(": "⁽", ")": "⁾", "n": "ⁿ", "i": "ⁱ"}
_EN_INDICE = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅",
              "6": "₆", "7": "₇", "8": "₈", "9": "₉",
              "+": "₊", "-": "₋", "(": "₍", ")": "₎", "n": "ₙ", "k": "ₖ"}
_SANS_PETITS_CARACTERES = str.maketrans(
    {v: k for table in (_EN_EXPOSANT, _EN_INDICE) for k, v in table.items()})


def _mots_du_tableau(rangs: list[list[str]]) -> set[str]:
    """Vocabulaire d'un tableau, pour comparer deux lectures de ses cellules.

    Les indices et exposants sont ramenes a leurs caracteres ordinaires : la
    relecture ecrit `10²³` la ou `extract()` ecrit `1023`, et sans cela le
    seul mot que la relecture ait rendu MIEUX se declarerait perdu.
    """
    texte = "\n".join(c for rang in rangs for c in rang)
    return _mots_cles(texte.translate(_SANS_PETITS_CARACTERES))


def _relecture_fidele(rangs: list[list[str]], relus: list[list[str]]) -> bool:
    """La relecture sur les spans rend-elle tout ce qu'`extract()` avait lu ?

    Deux conditions, l'une sur le tableau et l'autre sur chaque cellule.

    Aucun mot ne doit manquer : c'est ce qui garantit qu'aucune cellule n'est
    tombee entre les boites de `find_tables` et celles de `get_text`.

    Et aucune cellule remplie ne doit se vider : un mot peut rester dans le
    tableau tout en ayant change de colonne. Sur la page 7 du programme de NSI
    de terminale, pymupdf rend une ligne justifiee sur deux colonnes en un
    seul span, que rien ne permet de partager — « Protocoles de routage. »
    passait dans la colonne voisine sans qu'aucun mot ne se perde. Le tableau
    entier revient alors a `extract()` : mieux vaut un indice mal place qu'une
    cellule vide.
    """
    if _mots_du_tableau(rangs) - _mots_du_tableau(relus):
        return False
    for rang, relu in zip(rangs, relus):
        for avant, apres in zip(rang, relu):
            if avant and not apres:
                return False
    return True


def _en_petits_caracteres(texte: str, vers_le_bas: bool) -> str | None:
    """Le texte en indices ou en exposants Unicode, ou None s'il n'y passe pas.

    C'est la monnaie du projet pour ces caracteres : `traitements.maths` les
    connait deja et les ecrit en LaTeX quand le fragment qui les porte est
    mathematique. Rien a inventer ici, il suffit de ne pas les perdre.
    """
    table = _EN_INDICE if vers_le_bas else _EN_EXPOSANT
    rendu = "".join(table.get(c, "") for c in texte)
    return rendu if len(rendu) == len(texte) else None


def _lignes_composees(page: Any) -> list[tuple[Any, list[tuple[Any, str]]]]:
    """Les lignes de la page, caractere par caractere : (boite, [(boite, c)]).

    `Table.extract()` rend le texte d'une cellule en triant ses morceaux par
    ordonnee : un indice, compose plus bas que sa ligne, se retrouve donc
    rejete apres elle. Sur la page 4 de ce programme, « formules O2, H2, N2,
    H2O, CO2 » sortait en « formules O , 2 H , N , H O, CO . 2 2 2 2 ».
    L'ordre juste est celui du flux, que `get_text` conserve, et c'est le meme
    passage qui rend les indices et exposants en caracteres Unicode.

    On descend au **caractere** et non au span, parce qu'un span traverse
    parfois deux colonnes : pymupdf rend en un seul morceau une ligne
    justifiee sur deux cellules, et 4,85 % des spans du corpus sont dans ce
    cas. Aucune regle geometrique ne les partage — il faut pouvoir couper
    dedans. `rawdict` coute 0,4 seconde de plus que `dict` sur les 525 pages
    du corpus, ce qui ne se discute pas.
    """
    try:
        blocs = page.get_text("rawdict")["blocks"]
    except Exception:  # noqa: BLE001
        return []
    sortie: list[tuple[Any, list[tuple[Any, str]]]] = []
    for bloc in blocs:
        for ligne in bloc.get("lines", []):
            spans = [s for s in ligne.get("spans", []) if s.get("chars")]
            if not spans:
                continue
            corps = max(s.get("size", 0) for s in spans)
            base = max(spans, key=lambda s: s.get("size", 0))["origin"][1]
            morceaux: list[tuple[Any, str]] = []
            for s in spans:
                ecart = s["origin"][1] - base
                petit = (s.get("size", 0) < corps * _RAPPORT_PETIT
                         and abs(ecart) > _ECART_LIGNE_PETITE)
                for c in s["chars"]:
                    texte = c.get("c", "")
                    if petit:
                        rendu = _en_petits_caracteres(texte, ecart > 0)
                        if rendu:
                            texte = rendu
                    morceaux.append((c["bbox"], texte))
            if morceaux:
                sortie.append((ligne["bbox"], morceaux))
    return sortie


def _texte_de_la_cellule(boite: Any,
                         lignes: list[tuple[Any, list[tuple[Any, str]]]]) -> str:
    """Texte d'une cellule delimitee par une boite, caractere par caractere.

    Sert le chemin de repli, quand le PDF ne declare pas ses tableaux : c'est
    alors `find_tables` qui donne les boites. Le centre d'un caractere
    tranche, plutot que son inclusion : les filets du tableau passent au
    travers des boites de `find_tables`.
    """
    if boite is None:
        return ""
    gx0, gy0, gx1, gy1 = boite[0], boite[1], boite[2], boite[3]
    lues: list[str] = []
    for _, morceaux in lignes:
        pris = [texte for (x0, y0, x1, y1), texte in morceaux
                if gx0 <= (x0 + x1) / 2 <= gx1 and gy0 <= (y0 + y1) / 2 <= gy1]
        if pris:
            lues.append("".join(pris))
    return _cellule_html("\n".join(lues))


# Tolerance entre le debut d'un contenu marque, releve dans le flux, et le
# bord gauche du premier caractere qu'il ecrit. Les deux viennent du meme
# nombre ; la demi-unite absorbe l'arrondi.
_MARGE_REPERE = 0.5


def _assembler_segments(segments: list[tuple[str, int, str]],
                        bande: bool = False) -> str:
    """Le contenu d'une cellule : du texte, et les items en liste a puces.

    Les items consecutifs de meme niveau font une liste ; un niveau de plus
    ouvre une liste dans le dernier item, comme le document l'imprime — 54
    listes du corpus sont imbriquees, et 4 le sont deux fois.

    **Un item seul n'est pas une liste — mais SEULEMENT dans une bande de
    titre.** Word numerote les bandes de ces programmes : « 2. Modeliser une
    action sur un systeme » est un `/LI` a lui tout seul, en travers de la
    largeur du tableau, et l'ouvrir en `<ul>` mettrait une puce devant un
    intitule de partie. La regle valait partout, et elle depouillait alors les
    cellules ordinaires : « Associer une quantite, le nom d'un nombre et une
    ecriture chiffree » perdait sa puce page 38, la ou le PDF en imprime une,
    au seul motif qu'elle etait seule dans sa cellule. `bande` restreint la
    demotion aux rangs d'une seule cellule, les seuls que Word numerote ainsi.
    Le numero, lui, est dans le texte et y reste.
    """
    items = sum(1 for genre, _, texte in segments
                if genre == balises.ITEM and texte)
    if items < 2 and bande:
        segments = [(balises.TEXTE if genre == balises.ITEM
                     else genre, niveau, texte)
                    for genre, niveau, texte in segments]
    sortie: list[str] = []
    niveaux: list[int] = []          # niveaux de liste ouverts
    for genre, niveau, texte in segments:
        if genre == balises.PUCE or not texte:
            continue
        if genre != balises.ITEM:
            while niveaux:
                sortie.append("</li></ul>")
                niveaux.pop()
            # Un alinea de plus dans la meme cellule : le document en imprime
            # sept dans sa cellule d'introduction de la page 4, et les coller
            # bout a bout melangeait le texte de la partie, ses notions de
            # college et ses formules. Le saut ne vaut qu'entre deux
            # paragraphes — a l'interieur, les retours a la ligne restent de
            # la mise en page et disparaissent comme avant.
            if sortie and not sortie[-1].endswith(">"):
                sortie.append("<br>")
            sortie.append(texte)
            continue
        if niveaux and niveau == niveaux[-1]:
            sortie.append("</li><li>")
        while niveaux and niveau < niveaux[-1]:
            sortie.append("</li></ul>")
            niveaux.pop()
            if niveaux and niveau == niveaux[-1]:
                sortie.append("</li><li>")
        while not niveaux or niveau > niveaux[-1]:
            sortie.append("<ul><li>")
            niveaux.append(niveaux[-1] + 1 if niveaux else niveau)
        sortie.append(texte)
    while niveaux:
        sortie.append("</li></ul>")
        niveaux.pop()
    # Une espace entre deux morceaux de texte, aucune de part et d'autre d'une
    # balise : `<li> texte </li>` ajouterait des blancs au contenu.
    rendu = ""
    for morceau in sortie:
        if rendu and not rendu.endswith(">") and not morceau.startswith("<"):
            rendu += " "
        rendu += morceau
    return rendu


def _texte_des_cellules(tableau: Any,
                        lignes: list[tuple[Any, list[tuple[Any, str]]]],
                        limites: Any = None,
                        ) -> list[list[str]]:
    """Texte des cellules d'un tableau declare par l'arbre de structure.

    Les positions relevees dans le flux disent OU commence chaque segment sur
    une ligne imprimee ; un caractere appartient au dernier segment commence
    avant lui. C'est ce qui partage une ligne justifiee sur deux colonnes,
    qu'aucune boite ne saurait couper — « Protocoles de routage. » quittait
    ainsi sa colonne sur la page 7 du programme de NSI de terminale — et
    c'est aussi ce qui separe les items d'une liste a l'interieur d'une
    cellule.
    """
    reperes = [(x, y, i, j, k)
               for i, rang in enumerate(tableau)
               for j, cellule in enumerate(rang)
               for k, (_, _, points) in enumerate(cellule)
               for (x, y) in points]
    textes: dict[tuple[int, int, int], list[str]] = {}
    for (_, ly0, _, ly1), morceaux in lignes:
        if limites is not None:
            morceaux = [(boite, texte) for boite, texte in morceaux
                        if limites[0] <= (boite[0] + boite[2]) / 2 <= limites[2]
                        and limites[1] <= (boite[1] + boite[3]) / 2 <= limites[3]]
        sur_la_ligne = sorted((x, i, j, k) for (x, y, i, j, k) in reperes
                              if ly0 <= y <= ly1)
        if not sur_la_ligne:
            continue
        accumule: dict[tuple[int, int, int], list[str]] = {}
        for (cx0, _, _, _), texte in morceaux:
            proprietaire = None
            for x, i, j, k in sur_la_ligne:
                if cx0 < x - _MARGE_REPERE:
                    break
                proprietaire = (i, j, k)
            # Un caractere pose avant le premier repere de la ligne appartient
            # a celui qui l'ouvre : le flux ne recule pas.
            if proprietaire is None:
                proprietaire = sur_la_ligne[0][1:]
            accumule.setdefault(proprietaire, []).append(texte)
        for cle, morceau in accumule.items():
            textes.setdefault(cle, []).append("".join(morceau))
    def contenu(cle: tuple[int, int, int], genre: str) -> str:
        texte = _cellule_html("\n".join(textes.get(cle, [])))
        # La puce en tete d'un item ne se recopie pas : `<li>` la rend deja.
        return _PUCE_EN_TETE_RE.sub("", texte) \
            if genre == balises.ITEM else texte

    def rendu(i: int, j: int, cellule: Any) -> list[tuple[str, int, str]]:
        segments = [(genre, niveau, contenu((i, j, k), genre))
                    for k, (genre, niveau, _) in enumerate(cellule)]
        # Les balises ne sont pas toujours fideles aux glyphes : un /Lbl
        # peut porter une ligne entiere de prose a la reprise d'une page.
        # Seul un vrai marqueur se supprime ; les mots doivent rester.
        sans_puce = not any(g == balises.PUCE for g, _, _ in segments)
        sortie: list[tuple[str, int, str]] = []
        label_texte = False
        for genre, niveau, texte in segments:
            if genre == balises.PUCE:
                nu = _texte_nu(texte).strip()
                label_texte = bool(nu) and not re.fullmatch(
                    r"(?:[^\w\s]{1,4}|\(?\d+[.)]?|[a-zA-Z][.)]|[ivxlcdmIVXLCDM]+[.)])", nu)
                if not label_texte:
                    sortie.append((genre, niveau, texte))
                    continue
                genre = balises.TEXTE
            elif genre == balises.ITEM and (label_texte or sans_puce):
                genre = balises.TEXTE
                if label_texte and sortie and sortie[-1][0] == balises.TEXTE:
                    g, n, precedent = sortie.pop()
                    texte = (precedent + " " + texte).strip()
            sortie.append((genre, niveau, texte))
        return sortie

    sortie: list[list[str]] = []
    for i, rang in enumerate(tableau):
        cellules = [rendu(i, j, c) for j, c in enumerate(rang)]
        # Une seule cellule declaree peut etre une bande pleine largeur.
        # Une seule cellule REMPLIE parmi plusieurs reste une colonne :
        # c'est notamment le cas d'une fin de liste sur la page suivante.
        sortie.append([_assembler_segments(c, bande=len(cellules) == 1)
                       for c in cellules])
    return sortie


def _rangs_du_tableau(t: Any, page: Any,
                      indices: list[int] | None = None) -> list[list[str]]:
    """Cellules d'un tableau declare par le PDF, nettoyees et rectangulaires.

    Les colonnes vides de bout en bout sont retirees : elles n'existent pas
    dans le document imprime, c'est un filet vertical que `find_tables` a pris
    pour une separation de colonnes. La page 11 de ce programme en portait
    trois, la page 12 deux, et chaque ligne trainait autant de `<td></td>`.

    Le texte des cellules est relu sur les spans (`_lignes_composees`) et non
    pris a `extract()`, qui desordonne les indices. La relecture n'est retenue
    que si elle n'a pas perdu un seul mot de ce qu'`extract()` avait lu : la
    grille de `find_tables` et les boites de `get_text` ne se recouvrent pas
    toujours, et perdre une cellule couterait plus cher qu'un indice mal
    place. La comparaison porte sur le vocabulaire et non sur le nombre de
    caracteres : la relecture est plus courte de dix caracteres sur la page 4
    de ce programme, et pour cause — ce sont les espaces qu'`extract()`
    intercalait entre « H », « O » et leurs indices.
    """
    try:
        rangs = [[_cellule_html(c) for c in ligne] for ligne in t.extract()]
    except Exception:  # noqa: BLE001
        return []
    lignes = _lignes_composees(page)
    if lignes:
        try:
            relus = [[_texte_de_la_cellule(c, lignes) for c in rang.cells]
                     for rang in t.rows]
        except Exception:  # noqa: BLE001
            relus = []
        if relus and _relecture_fidele(rangs, relus):
            rangs = relus
    gardes = [i for i, r in enumerate(rangs) if any(r)]
    if indices is not None:
        indices.extend(gardes)
    rangs = [rangs[i] for i in gardes]
    if not rangs:
        return []
    largeur = max(len(r) for r in rangs)
    rangs = [r + [""] * (largeur - len(r)) for r in rangs]
    # Une seule ligne, avec une vraie boite pour chaque colonne, peut etre
    # la fin d'une cellule sur la page suivante. Une colonne vide n'est pas
    # alors un filet parasite : la retirer deplacerait le texte restant.
    if (len(rangs) == 1 and largeur > 1 and len(t.rows) == 1
            and all(c is not None for c in t.rows[0].cells)):
        return rangs
    pleines = [j for j in range(largeur) if any(r[j] for r in rangs)]
    if len(pleines) < 2:
        return []
    return [[r[j] for j in pleines] for r in rangs]


def _est_entete(rang: list[str], largeur: int | None = None) -> bool:
    """Cette ligne annonce-t-elle les colonnes du tableau ?

    Une bande pleine largeur ne les annonce jamais : elle intitule une partie.
    Sans arbre elle se reconnait a sa cellule vide, que `all(c ...)` recuse ;
    avec l'arbre elle porte moins de cellules que le tableau n'a de colonnes,
    et « 3. Signaux et capteurs », court et seul sur sa ligne, se serait
    sinon declare en-tete.
    """
    if not rang:
        return False
    if largeur is not None and len(rang) < largeur:
        return False
    return all(c and len(c) <= LONGUEUR_ENTETE_MAX
               and not re.search(r"<(?:ul|ol|li)\b", c, re.I)
               and not _PUCE_EN_TETE_RE.match(_texte_nu(c)) for c in rang)


def _rang_en_html(rang: list[str], balise: str = "td",
                  largeur: int | None = None) -> str:
    """Une ligne du tableau, la bande pleine largeur rendue en `colspan`.

    Deux facons de reconnaitre cette bande, selon ce qu'on sait du tableau.

    Quand l'arbre de structure l'a declaree (`largeur` donnee), il n'y a rien
    a reconnaitre : une ligne qui porte moins de cellules que le tableau n'a
    de colonnes EST la cellule fusionnee. C'est la seule chose que l'arbre ne
    dise pas en toutes lettres — il n'y a pas d'attribut `ColSpan` dans ce
    corpus — mais il la montre.

    Sans arbre, on la deduit : une ligne qui ne remplit que sa premiere
    colonne s'etend en fait sur toute la largeur. C'est l'intitule d'une
    partie, ou le paragraphe d'introduction qui le suit. La rendre sur une
    colonne, avec une cellule vide a cote, montre autre chose que le document
    imprime.
    """
    if largeur is None:
        if len(rang) > 1 and rang[0] and not any(rang[1:]):
            return f'<tr><{balise} colspan="{len(rang)}">{rang[0]}</{balise}></tr>'
    elif len(rang) == 1 and largeur > 1:
        return f'<tr><{balise} colspan="{largeur}">{rang[0]}</{balise}></tr>'
    elif len(rang) < largeur:
        rang = rang + [""] * (largeur - len(rang))
    return "<tr>" + "".join(f"<{balise}>{c}</{balise}>" for c in rang) + "</tr>"


def _assembler_tableau(rangs: list[list[str]],
                       largeur: int | None = None,
                       entetes: set[int] | None = None) -> str:
    """Les lignes d'un tableau en HTML, en-tete reconnu a sa forme."""
    entete = ""
    corps: list[str] = []
    vu_entete = False
    donnees_commencees = False
    for rang_i, rang in enumerate(rangs):
        if (not vu_entete and not donnees_commencees
                and rang_i < RANGS_ENTETE
                and (rang_i in entetes if entetes is not None else _est_entete(rang, largeur))):
            vu_entete = True
            # Un en-tete pose sous l'intitule d'une partie garde ses `<th>`
            # mais pas de `<thead>` : la balise ne se place qu'en tete de
            # tableau, et l'y renvoyer separerait l'intitule de ce qu'il
            # coiffe.
            if rang_i == 0:
                entete = f"<thead>{_rang_en_html(rang, 'th', largeur)}</thead>"
            else:
                corps.append(_rang_en_html(rang, "th", largeur))
            continue
        corps.append(_rang_en_html(rang, "td", largeur))
        # Une fois une ligne de donnees rencontree, une ligne courte plus
        # bas n'annonce pas soudainement les colonnes. Seules les bandes
        # pleine largeur peuvent preceder un en-tete.
        bande = (largeur is not None and len(rang) < largeur
                 or largeur is None and len(rang) > 1 and rang[0] and not any(rang[1:]))
        if not bande:
            donnees_commencees = True
    return "<table>" + entete + "".join(corps) + "</table>"


def _grille_correspondante(tableau: Any, grilles: list[Any]) -> Any:
    """Grille visible qui couvre les contenus marques du tableau.

    On ne remplace pas le regroupement des lignes declarees par celui de
    find_tables. Seules ses bornes et ses colonnes corroborent la lecture.
    Une grille dont le nombre de colonnes differe n'est pas un arbitre sur.
    """
    points = [p for rang in tableau for cellule in rang for _, _, pts in cellule for p in pts]
    if not points:
        return None
    largeur = max(map(len, tableau), default=0)
    candidates = []
    for grille in grilles:
        if grille.col_count != largeur:
            continue
        x0, y0, x1, y1 = grille.bbox
        dedans = sum(x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1 for x, y in points)
        if dedans / len(points) >= 0.9:
            candidates.append((dedans, grille))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _caler_colonnes(tableau: Any, grille: Any) -> Any:
    """Rend aux segments la colonne dans laquelle le PDF les imprime.

    Certains exports placent la suite d'une cellule sous le TD voisin dans
    l'arbre. On peut corriger cette contradiction quand une grille de meme
    largeur donne les boites. Sans cette preuve, on conserve les balises.
    Les cellules fusionnees, sans boite propre, restent egalement intactes.
    """
    if grille is None:
        return tableau
    sortie = []
    for rang in tableau:
        if len(rang) != grille.col_count:
            sortie.append(rang)
            continue
        nouveau: list[list[Any]] = [[] for _ in rang]
        for j, cellule in enumerate(rang):
            for genre, niveau, points in cellule:
                par_colonne: dict[int, list[Any]] = {}
                for x, y in points:
                    colonnes = {col for ligne in grille.rows
                                for col, boite in enumerate(ligne.cells)
                                if boite is not None and boite[0] - 0.5 <= x <= boite[2]
                                and boite[1] <= y <= boite[3]}
                    col = next(iter(colonnes)) if len(colonnes) == 1 else j
                    par_colonne.setdefault(col, []).append((x, y))
                for col, pts in par_colonne.items():
                    nouveau[col].append((genre, niveau, pts))
        sortie.append(nouveau)
    return sortie


def _spans_en_gras(spans: list[Any]) -> bool:
    """Le gras doit porter les libelles, pas seulement leur premier mot."""
    total = sum(len(s.get("text", "").strip()) for s in spans)
    gras = sum(len(s.get("text", "").strip()) for s in spans if s.get("flags", 0) & 16)
    return total > 0 and gras / total >= 0.8


def _tableaux_declares(page: Any, declares: list[Any],
                       grilles: list[Any] | None = None) -> list[str]:
    """Tableaux que l'arbre de structure declare pour cette page, en HTML.

    L'arbre fixe le regroupement des lignes. Une grille visible compatible
    peut corriger une colonne mal balisee. Les en-tetes reposent sur les
    `/TH` declares ou sur des libelles courts imprimes en gras ; le nombre
    de cellules de chaque ligne permet de deduire les `colspan`.
    """
    lignes = _lignes_composees(page)
    if not lignes:
        return []
    spans = [s for bloc in page.get_text("dict").get("blocks", [])
             for ligne in bloc.get("lines", []) for s in ligne.get("spans", [])]
    sortie: list[str] = []
    for tableau in declares:
        grille = _grille_correspondante(tableau, grilles or [])
        rangs = _texte_des_cellules(_caler_colonnes(tableau, grille), lignes,
                                   grille.bbox if grille is not None else None)
        entetes: set[int] = set()
        gardes = []
        for i, rang in enumerate(rangs):
            if not any(rang):
                continue
            points = [p for cellule in tableau[i] for _, _, pts in cellule for p in pts]
            # Une ligne courte n'est pas a elle seule un en-tete. On exige
            # soit des TH declares sur toute la ligne, soit des libelles
            # courts composes en gras dans le PDF.
            ligne_spans = [s for s in spans
                           if any(abs(s.get("origin", (0, 0))[1] - y) <= 0.75 for _, y in points)]
            if (i in getattr(tableau, "entetes", set())
                    or _est_entete(rang, max(map(len, rangs))) and _spans_en_gras(ligne_spans)):
                entetes.add(len(gardes))
            gardes.append(rang)
        rangs = gardes
        if not rangs:
            continue
        largeur = max(len(r) for r in rangs)
        if largeur < 2:
            continue  # une colonne unique n'est pas un tableau
        sortie.append(_assembler_tableau(rangs, largeur, entetes))
    return sortie


def _tableaux_de_la_page(page: Any, declares: list[Any] | None = None,
                         ) -> list[str]:
    """Tableaux de la page, tels que le PDF les declare, en HTML.

    C'est la structure que le modele de vision doit deviner sur une image et
    se trompe : nombre de colonnes, et surtout regroupement des lignes — une
    cellule de plusieurs paragraphes reste une seule ligne ici.

    **L'arbre de structure fixe le regroupement des lignes.** Ce que
    `find_tables` devine sur des filets, un PDF balise l'ecrit en `/Table`,
    `/TR`, `/TD` : sur le corpus, 1179 lignes vues pour 979 declarees, et sur
    la page 11 du programme de physique-chimie de seconde, quatre lignes
    imprimees — une par notion, avec ses capacites en face — se retrouvaient
    entassees en une seule. Le repli sur `find_tables` reste entier pour un
    PDF non balise (un seul dans le corpus, le cours de NSI).

    Sans arbre, `find_tables` promeut toujours sa premiere ligne en en-tete,
    ce qui n'a de sens que pour un tableau qui commence sur cette page. Sur
    une page de continuation, cet en-tete est invente : il donne un titre a
    une ligne de donnees, et surtout il empeche `_merge_table_fragments` de
    reconnaitre la suite. Un en-tete deduit doit donc porter des libelles
    courts en gras, avant le debut des donnees.

    Retourne [] si le PDF n'expose aucun tableau : page scannee, ou page sans
    tableau. Vision reste alors seul maitre a bord.
    """
    try:
        trouves = page.find_tables().tables
    except Exception as exc:  # noqa: BLE001
        logger.debug("find_tables KO : %s", exc)
        trouves = []
    if declares:
        rendus = _tableaux_declares(page, declares, trouves)
        if rendus:
            return rendus
    sortie: list[str] = []
    spans = [s for bloc in page.get_text("dict").get("blocks", [])
             for ligne in bloc.get("lines", []) for s in ligne.get("spans", [])]
    for t in trouves:
        indices: list[int] = []
        rangs = _rangs_du_tableau(t, page, indices)
        if rangs:
            entetes = set()
            for i, (rang, origine) in enumerate(zip(rangs, indices)):
                ligne = t.rows[origine]
                boites = [c for c in ligne.cells if c is not None]
                sur_le_rang = [s for s in spans if any(
                    c[0] <= s.get("origin", (0, 0))[0] <= c[2]
                    and c[1] <= s.get("origin", (0, 0))[1] <= c[3] for c in boites)]
                if _est_entete(rang) and _spans_en_gras(sur_le_rang):
                    entetes.add(i)
            largeur = (len(rangs[0]) if len(rangs) == 1 and len(t.rows) == 1
                       and all(c is not None for c in t.rows[0].cells) else None)
            sortie.append(_assembler_tableau(rangs, largeur, entetes))
    return sortie
