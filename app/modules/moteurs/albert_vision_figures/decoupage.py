"""Decoupage des figures du PDF et pose dans le Markdown.

Coeur du moteur `albert_vision_figures`. Un modele de vision decrit
une figure, il ne sait pas la decouper : le PDF, lui, porte la boite
de chaque dessin, gratuitement et sans appel reseau.

Rien ici n'appelle le reseau : ce module ne fait qu'ajouter a ce que
Vision a deja rendu."""
from __future__ import annotations

import base64
import logging
import re
from html import escape
from collections import Counter
from typing import Any

import pymupdf  # type: ignore[import-untyped]

from ...traitements.tableaux_html import _est_tableau, _LIGNE_RE, _CELLULE_RE
from ...traitements.texte import _texte_nu
from ...pdf.grilles import grilles_de_la_page, cellule_contenant, est_trace_de_grille


logger = logging.getLogger("atelier.moteurs.albert_vision_figures.decoupage")


# --- Figures : les trouver dans le PDF, les poser dans le Markdown ----------
#
# Un modele de vision decrit une figure, il ne sait pas la decouper : il ne
# rend que du texte. Document AI, lui, rend une image parce qu'il expose la
# boite de chaque figure. Le PDF porte la meme information, gratuitement : un
# schema vectoriel est un amas de traces voisins.
#
# Mesure sur le cours de NSI, contre les boites de Document AI : page 2,
# [95, 590, 229, 732] contre [92, 586, 231, 734] ; page 3, [40, 214, 537, 288]
# contre [33, 212, 562, 291]. Trois a quatre points d'ecart, sans appel
# reseau.

# Deux traces separes de moins que cela appartiennent au meme dessin. Regle
# sur le meme document : a 18 le schema de la page 3 s'eclate en trois, a 30
# il se referme, au-dela de 60 il commence a avaler le texte voisin.
ECART_FIGURE = 30.0
# En dessous, c'est un filet, un souligne, une puce : pas une figure.
AIRE_FIGURE_MIN = 2500.0
LARGEUR_FIGURE_MIN = 40.0
HAUTEUR_FIGURE_MIN = 30.0
# Resolution du decoupage. Celle de la page envoyee au modele : au-dela, on
# alourdit le `.md` sans que la figure en dise plus.
FIGURE_DPI = 150

# Un bandeau grave dans l'image occupe la meme boite sur toutes les pages.
# Meme seuil que le boilerplate textuel : deux occurrences suffisent a dire
# qu'un element est du mobilier de page et non du contenu.
SEUIL_BANDEAU_FIGURE = 2
# ... a condition qu'il se tienne en haut ou en bas de la page. C'est la
# transposition de la zone `head`/`tail` de `_remove_repeated_boilerplate` :
# une figure repetee au milieu d'une page reste une figure.
BANDE_BANDEAU = 0.15

# La citation par laquelle le prompt fait decrire une figure.
_DESCRIPTION_FIGURE_RE = re.compile(r"^>\s*\*\*Figure\s*:", re.MULTILINE)


def _citation_de_figure(lignes: list[str], depart: int) -> tuple[int, str]:
    """Fin (exclue) de la citation ouverte a `depart`, et son texte nu.

    La citation sert d'abord a placer la figure — elle dit ou le modele l'a
    vue. Elle est ensuite CONSOMMEE : depuis que la description vient d'un
    appel dedie et se range dans le `alt`, la laisser dans le corps affichait
    la meme chose deux fois, une fois en infobulle et une fois en paragraphe.
    Son texte reste utile comme description de secours quand l'appel dedie
    n'a rien rendu.
    """
    fin = depart
    morceaux: list[str] = []
    while fin < len(lignes) and lignes[fin].lstrip().startswith(">"):
        morceaux.append(lignes[fin].lstrip()[1:].strip())
        fin += 1
    texte = " ".join(m for m in morceaux if m)
    texte = re.sub(r"^\*\*Figure\s*:\s*", "", texte)
    return fin, texte.replace("**", "").strip(" :")


def _fusionner_rects(rects: list[Any], ecart: float) -> list[Any]:
    """Regroupe les rectangles qui se touchent ou se frolent."""
    amas: list[Any] = []
    for r in rects:
        elargi = pymupdf.Rect(r.x0 - ecart, r.y0 - ecart, r.x1 + ecart, r.y1 + ecart)
        for a in [a for a in amas if a.intersects(elargi)]:
            amas.remove(a)
            r = r | a
        amas.append(r)
    return amas


# Un encadre n'est pas une figure. Le programme de SNT borde ses « Exemples
# d'activites » d'un filet : c'est un rectangle, donc un amas de traces, et
# `find_tables` ne le reconnait pas — une grille d'une seule cellule n'est pas
# une grille. Il partait en image, alors que le modele en avait deja transcrit
# le texte, qui se retrouvait deux fois dans le document.
#
# Ce qui le trahit, c'est que son texte court d'un bord a l'autre, comme un
# paragraphe. Une figure porte des etiquettes, jamais des lignes pleines.
# Mesure sur les deux corpus : les deux encadres de SNT ont leur troisieme
# ligne la plus large a 81 % et 90 % de la boite, les deux schemas du cours de
# NSI a 40 % et 10 %. Le seuil tient au milieu, et il en faut trois : une
# legende large ne doit pas disqualifier un schema.
PART_LIGNE_PLEINE = 0.70
LIGNES_PLEINES_MIN = 3


def _est_un_encadre_de_texte(page: Any, rect: Any) -> bool:
    """La boite enferme-t-elle du texte au fil, plutot qu'une figure ?"""
    if rect.width <= 0:
        return False
    pleines = 0
    try:
        blocs = page.get_text("dict", clip=rect)["blocks"]
    except Exception:  # noqa: BLE001
        return False
    for b in blocs:
        for ligne in b.get("lines", []):
            if not any(s["text"].strip() for s in ligne.get("spans", [])):
                continue
            bbox = ligne.get("bbox")
            if bbox and (bbox[2] - bbox[0]) / rect.width >= PART_LIGNE_PLEINE:
                pleines += 1
                if pleines >= LIGNES_PLEINES_MIN:
                    return True
    return False


def _figures_de_la_page(page: Any) -> list[Any]:
    """Boites des figures d'une page : amas de traces, et images posees.

    Ce que le PDF declare comme tableau est ecarte : une grille bordee est
    elle aussi un amas de traces, et elle est deja rendue en HTML. Un simple
    encadre de texte l'est aussi, pour la meme raison — voir
    `_est_un_encadre_de_texte`.
    """
    grilles = grilles_de_la_page(page)
    try:
        dessins = page.get_drawings()
        # Certains PDF dessinent chaque lettre comme un petit chemin. Sans
        # couche texte, ces paragraphes sont indiscernables d'une figure :
        # le decoupage par cellule les multiplierait. Conserver alors le
        # traitement global jusqu'a disposer d'une reconnaissance du texte.
        petits = sum(0 < pymupdf.Rect(d['rect']).width < 12
                     and 0 < pymupdf.Rect(d['rect']).height < 12 for d in dessins)
        if petits >= 200 and len(page.get_text('text').strip()) < 30:
            grilles = []
        rects = [pymupdf.Rect(d["rect"]) for d in dessins
                 if pymupdf.Rect(d["rect"]).get_area() > 1
                 and not est_trace_de_grille(d, grilles)]
        for info in page.get_image_info():
            rects.append(pymupdf.Rect(info["bbox"]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Figures : lecture impossible : %s", exc)
        return []
    if not rects:
        return []

    # Les frontieres de cellules restent des frontieres de figures.
    groupes: dict[tuple, list] = {}
    for r in rects:
        adresse = cellule_contenant(r, grilles)
        cle = (tuple(adresse[0].bbox), adresse[1], adresse[2]) if adresse else ()
        groupes.setdefault(cle, []).append(r)
    amas = []
    for groupe in groupes.values():
        for _ in range(4):
            groupe = _fusionner_rects(groupe, ECART_FIGURE)
        amas.extend(groupe)

    try:
        tableaux = [pymupdf.Rect(t.bbox) for t in page.find_tables().tables]
    except Exception:  # noqa: BLE001
        tableaux = []

    gardees = []
    for r in amas:
        if (r.get_area() < AIRE_FIGURE_MIN or r.width < LARGEUR_FIGURE_MIN
                or r.height < HAUTEUR_FIGURE_MIN):
            continue
        # Le recouvrement se juge sur l'ENSEMBLE des tableaux, pas sur chacun.
        # `find_tables` decoupe volontiers une meme grille en plusieurs boites
        # — six sur une page du programme de cycle 1 — alors que la fusion des
        # traces n'en rend qu'une. Aucune boite ne couvrait alors la moitie de
        # la figure, et le tableau partait en image a cote de son propre HTML.
        couvert = sum(abs(r & t) for t in tableaux if t.intersects(r))
        if couvert > 0.5 * abs(r) and cellule_contenant(r, grilles) is None:
            continue  # c'est la grille d'un tableau, pas une figure
        if _est_un_encadre_de_texte(page, r):
            continue  # c'est un filet autour d'un texte deja transcrit
        gardees.append(r)
    gardees.sort(key=lambda r: (round(r.y0), round(r.x0)))  # ordre de lecture
    return gardees


def _signature_figure(rect: Any) -> tuple[int, int, int, int]:
    """Identite d'une boite de figure d'une page a l'autre.

    Arrondie au point : le meme bandeau ne retombe pas au millieme pres selon
    les pages, et cette tolerance suffit a le reconnaitre.
    """
    return (round(rect.x0), round(rect.y0), round(rect.width), round(rect.height))


def _bandeaux_graphiques(doc: Any,
                         seuil: int = SEUIL_BANDEAU_FIGURE,
                         ) -> set[tuple[int, int, int, int]]:
    """Boites de figures qui sont en realite des bandeaux de page.

    `_figures_de_la_page` ne voit qu'une page : elle ne peut pas savoir que
    l'amas de traces qu'elle vient de trouver est le meme sur les douze
    suivantes. Un en-tete grave dans l'image passait donc au travers de tous
    les filtres — `_retirer_bandeaux` ne rattrape que celui que le modele a
    transcrit en texte — et se retrouvait decoupe puis incorpore en `data:` a
    chaque page. Mesure sur un programme de seconde : une seule image, 952x129
    px, repetee 12 fois, 640 Ko de base64 pour un bandeau.

    Le document entier tranche ce qu'une page ne peut pas : une meme boite,
    haut ou bas de page, sur au moins `seuil` pages, est du mobilier.
    """
    compte: Counter = Counter()
    for page in doc:
        hauteur = page.rect.height or 1.0
        for rect in _figures_de_la_page(page):
            en_haut = rect.y1 <= hauteur * BANDE_BANDEAU
            en_bas = rect.y0 >= hauteur * (1.0 - BANDE_BANDEAU)
            if en_haut or en_bas:
                compte[_signature_figure(rect)] += 1
    bandeaux = {sig for sig, n in compte.items() if n >= seuil}
    if bandeaux:
        logger.info("Figures : %d bandeau(x) de page ecarte(s) (repetes sur "
                    "%d page(s) ou plus).", len(bandeaux), seuil)
    return bandeaux


def _png_de_la_figure(page: Any, rect: Any) -> bytes:
    """Decoupe la figure dans le PDF et la rend en PNG."""
    return page.get_pixmap(clip=rect, dpi=FIGURE_DPI).tobytes("png")


def _figure_en_base64(page: Any, rect: Any) -> str:
    """Decoupe la figure et la rend en `data:` prete a poser dans le Markdown."""
    return ("data:image/png;base64,"
            + base64.b64encode(_png_de_la_figure(page, rect)).decode("ascii"))


def _texte_alternatif(legende: str) -> str:
    """Rend une legende utilisable entre les crochets d'une image Markdown.

    Les crochets fermeraient le libelle au milieu de la phrase, et un retour
    a la ligne casserait la syntaxe : la description arrive d'un modele, on ne
    lui fait donc pas confiance sur la forme.
    """
    plat = " ".join((legende or "").split())
    return plat.replace("[", "(").replace("]", ")")


def _place_dans_la_page(lignes: list[str], part: float) -> int:
    """Ligne ou inserer quelque chose situe a `part` de la hauteur de page.

    Faute de mieux : le Markdown n'a pas de coordonnees, mais il suit l'ordre
    de lecture. Une figure au tiers de la page se pose donc au tiers du texte,
    a la frontiere de paragraphe la plus proche — jamais au milieu d'une
    phrase, ni dans un tableau ou un bloc de code.
    """
    frontieres = [0]
    dans_code = False
    for i, l in enumerate(lignes):
        if l.strip().startswith("```"):
            dans_code = not dans_code
        if dans_code or _est_tableau(l):
            continue
        if not l.strip() and i:
            frontieres.append(i)
    if len(lignes) not in frontieres:
        frontieres.append(len(lignes))
    vise = part * len(lignes)
    return min(frontieres, key=lambda i: abs(i - vise))


def _poser_dans_tableau(lignes, page, grille, row, col, img):
    """Place l'image si une seule matrice HTML concorde avec le texte du PDF.

    Les cellules fusionnees ou les correspondances ambigues restent intactes.
    Le texte des autres cellules ancre aussi une cellule d'image sans texte.
    """
    def mots(s):
        return set(re.findall(r'\w+', _texte_nu(s).casefold()))

    attendus = [[mots(page.get_text('text', clip=c)) for c in l] for l in grille.lignes]
    candidats = []
    for i, ligne in enumerate(lignes):
        if not _est_tableau(ligne):
            continue
        rows = list(_LIGNE_RE.finditer(ligne))
        if len(rows) != len(attendus):
            continue
        cells = [list(_CELLULE_RE.finditer(r.group())) for r in rows]
        if any(len(c) != len(a) for c, a in zip(cells, attendus)):
            continue
        if any(re.search(r'\b(?:colspan|rowspan)\s*=', c.group(2), re.I)
               for l in cells for c in l):
            continue
        ancres = [(a, mots(c.group(3))) for l, cs in zip(attendus, cells)
                  for a, c in zip(l, cs) if a]
        if not ancres or not all(len(a & b) >= .8 * len(a) for a, b in ancres):
            continue
        candidats.append((i, rows[row].start() + cells[row][col].end(3)))
    if len(candidats) != 1:
        return False
    i, pos = candidats[0]
    lignes[i] = lignes[i][:pos] + img + lignes[i][pos:]
    return True


def _poser_figures(md_page: str, page: Any, numero_page: int = 0,
                   bandeaux: set[tuple[int, int, int, int]] | None = None,
                   decrire: Any = None,
                   ) -> tuple[str, int]:
    """Insere les figures de la page dans son Markdown.

    Le modele decrit parfois chaque figure en citation (`> **Figure : …**`)
    sans savoir la decouper : quand ces descriptions existent, on apparie les
    boites et les descriptions dans l'ordre de lecture — les deux suivent le
    meme fil — et l'image se pose juste avant celle qui lui repond.

    Quand elles manquent, et c'est le cas courant (le cours de NSI n'en porte
    aucune), la figure se pose a la hauteur qu'elle occupe dans la page. Le
    reperage reste approximatif : une figure peut atterrir un paragraphe trop
    tot ou trop tard, jamais tres loin.

    `bandeaux` vient de `_bandeaux_graphiques`, qui a lu tout le document :
    ces boites sont du mobilier de page et ne sont pas posees.
    """
    boites = _figures_de_la_page(page)
    if bandeaux:
        boites = [b for b in boites if _signature_figure(b) not in bandeaux]
    if not boites:
        return md_page, 0

    lignes = md_page.split("\n")
    debuts = [i for i, l in enumerate(lignes) if _DESCRIPTION_FIGURE_RE.match(l)]
    hauteur = page.rect.height or 1.0

    # Chaque figure recoit sa ligne d'insertion avant qu'aucune ne bouge.
    a_poser: list[tuple[int, int, str]] = []
    grilles = grilles_de_la_page(page)
    nouvelles: dict[tuple, tuple[int, list[list[str]]]] = {}
    posees = 0
    for rang, boite in enumerate(boites):
        try:
            png = _png_de_la_figure(page, boite)
            uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Figure %d.%d non decoupee : %s",
                           numero_page, rang + 1, exc)
            continue
        # `fin` borne ce que la figure remplace : sa citation quand elle en a
        # une, rien du tout quand elle est seulement inseree.
        du_modele = ""
        if rang < len(debuts):
            ou = debuts[rang]
            fin, du_modele = _citation_de_figure(lignes, ou)
        else:
            ou = _place_dans_la_page(lignes, boite.y0 / hauteur)
            fin = ou
        nom = f"Figure {numero_page}.{rang + 1}" if numero_page else f"Figure {rang + 1}"
        # La description va dans le `alt`, et non en citation a cote : c'est
        # la place standard, elle voyage avec l'image, elle survit au retrait
        # du base64 que MD-RAG fait avant d'indexer, et EduMD la reprend sans
        # rien changer. Sans elle, une figure indexee ne porte que « Figure
        # 52.1 » — un trou dans l'index.
        legende = nom
        if decrire is not None:
            try:
                dite = (decrire(png, nom) or "").strip()
            except Exception as exc:  # noqa: BLE001 - une figure sans texte
                logger.warning("Figure %s non decrite : %s", nom, exc)
                dite = ""
            if dite:
                legende = f"{nom} : {dite}"
        if legende == nom and du_modele:
            # L'appel dedie n'a rien rendu : ce que le modele avait ecrit en
            # citation vaut mieux que le seul numero de la figure.
            legende = f"{nom} : {du_modele}"
        img = f'<img src="{uri}" alt="{escape(legende, quote=True)}">'
        adresse = cellule_contenant(boite, grilles)
        dans_cellule = False
        if adresse:
            grille, row, col = adresse
            dans_cellule = _poser_dans_tableau(lignes, page, grille, row, col, img)
            if not dans_cellule and not page.get_text("text", clip=grille.bbox).strip():
                cle = tuple(grille.bbox)
                if cle not in nouvelles:
                    nouvelles[cle] = (ou, [["" for _ in l] for l in grille.lignes])
                nouvelles[cle][1][row][col] += img
                dans_cellule = True
        if dans_cellule:
            # Consomme la citation sans deplacer les autres indices.
            for i in range(ou, fin):
                lignes[i] = ""
        else:
            a_poser.append((ou, fin, f"![{_texte_alternatif(legende)}]({uri})\n"))
        posees += 1

    for ou, cellules in nouvelles.values():
        html = '<table>' + ''.join('<tr>' + ''.join(
            f'<td>{c}</td>' for c in l) + '</tr>' for l in cellules) + '</table>'
        a_poser.append((ou, ou, html + '\n'))

    # D'aval en amont : inserer ne decale alors pas les positions suivantes.
    for ou, fin, texte in sorted(a_poser, reverse=True):
        lignes[ou:fin] = [texte]
    return "\n".join(lignes), posees


# Nom du moteur, tel qu'il s'ecrit dans le sidecar (`active_engine`) et
# dans le front matter (`_engine`). Le serveur valide contre
# `pipeline.SUPPORTED_ENGINES` avant de convertir.
NOM_MOTEUR_VISION_FIGURES = "albert_vision_figures"
