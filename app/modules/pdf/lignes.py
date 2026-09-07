"""Lignes du PDF : ce qu'elles disent, ce qu'elles reparent.

Sert a retrouver un mot avale par le modele, a reperer un debut d'item
et a ecarter les liens qu'aucune page ne portait."""
from __future__ import annotations

import re
from typing import Any

from ..traitements.texte import (_EST_PUCE_RE, _PUCE_EN_TETE_RE, _est_glyphe_de_puce,
                                 _norme_titre, _recoller_paragraphes)
from .pagination import _est_de_pagination, _lignes_de_base_de_pagination


# Un lien que le modele ecrit vient de deux endroits, et d'un seul il est
# legitime : l'adresse imprimee sur la page, qu'il transforme en lien — 13 cas
# du corpus — ou son idee de ce que l'adresse devrait etre. Le bandeau du BO
# le montre a nouveau : neuf `[Logo](https://www.education.gouv.fr/…)` dans le
# corpus, dont `favicon.ico`, aucun present nulle part dans le document.
# C'est la meme invention que pour les images, sous une forme que
# `_retirer_images_du_modele` ne voit pas — il n'y a pas de `!` devant.
#
# Le PDF tranche sans rien couter : ses annotations declarent ses liens (3
# dans tout le corpus), et sa couche texte porte les adresses imprimees. Ce
# qui n'est ni dans l'une ni dans l'autre n'a pas ete lu, il a ete devine.
_LIEN_DU_MODELE_RE = re.compile(r"\[([^\]\n]*)\]\((https?://[^)\s]*)\)")
_ESPACES_RE = re.compile(r"\s+")
_DEBUT_URL_RE = re.compile(r"^https?://(?:www\.)?", re.IGNORECASE)


def _retirer_liens_inventes(md_page: str, page: Any,
                            texte_pdf: str) -> tuple[str, int]:
    """Deballe les liens dont la cible n'est nulle part dans la page.

    Le libelle reste, seul le lien tombe : on ne perd jamais de texte, et
    « Logo » restant seul se presente enfin comme la mention de bandeau qu'il
    est, que `_retirer_bandeaux` sait recuser.
    """
    try:
        declarees = {l["uri"] for l in page.get_links() if l.get("uri")}
    except Exception:  # noqa: BLE001
        declarees = set()
    imprime = _ESPACES_RE.sub("", (texte_pdf or "")).lower()
    retires = 0

    def _un(m: "re.Match[str]") -> str:
        nonlocal retires
        url = m.group(2)
        if url in declarees:
            return m.group(0)
        nu = _ESPACES_RE.sub("", _DEBUT_URL_RE.sub("", url)).rstrip("/").lower()
        if nu and nu in imprime:
            return m.group(0)
        retires += 1
        return m.group(1)

    neuf = _LIEN_DU_MODELE_RE.sub(_un, md_page)
    return (neuf, retires) if retires else (md_page, 0)


# Le PDF compose ses apostrophes en typographie (U+2019, « d’education »)
# la ou le modele rend une apostrophe droite. Comparer les caracteres tels
# quels faisait echouer toutes les ancres : mesure sur le programme de cycle
# 1, le seul lien du document ne se reposait pas pour cette seule raison.
_APOSTROPHES = "'’‘ʼ"
_TIRETS = '-‐‑–—'


def _motif_souple(mot: str) -> str:
    """Motif d'un mot, indifferent a la forme des apostrophes et tirets."""
    morceaux = []
    for c in mot:
        if c in _APOSTROPHES:
            morceaux.append("[" + re.escape(_APOSTROPHES) + "]")
        elif c in _TIRETS:
            morceaux.append("[" + re.escape(_TIRETS) + "]")
        else:
            morceaux.append(re.escape(c))
    return "".join(morceaux)


# Une ancre trop courte se retrouverait au hasard dans la page : on ne repose
# un lien que si son texte est reconnaissable, et sans ambiguite.
_ANCRE_MIN = 4


def poser_liens_du_pdf(md_page: str, page: Any) -> tuple[str, int]:
    """Repose les liens que le PDF declare et que le modele ne peut pas voir.

    Le modele ne recoit qu'une IMAGE de la page : un hyperlien n'y laisse
    aucune trace visible. Il transcrit donc le libelle sans son adresse, et
    le lien est perdu — mesure sur le programme de cycle 1 : le PDF declare
    un lien, le Markdown n'en portait aucun. `_retirer_liens_inventes` lisait
    deja ces annotations, mais pour ecarter, jamais pour reposer.

    Un lien n'est repose que si son ancre se retrouve une fois et une seule
    dans le Markdown : en cas d'ambiguite, mieux vaut ne rien poser que lier
    le mauvais passage.
    """
    try:
        liens = [l for l in page.get_links() if l.get("uri")]
    except Exception:  # noqa: BLE001
        return md_page, 0
    poses = 0
    for lien in liens:
        uri = lien["uri"]
        if uri in md_page:
            continue                       # le modele l'a deja pose
        try:
            ancre = page.get_text(clip=lien["from"])
        except Exception:  # noqa: BLE001
            continue
        ancre = _ESPACES_RE.sub(" ", ancre or "").strip(" .,;:\r\n\t")
        if len(ancre) < _ANCRE_MIN:
            continue
        motif = re.compile(r"(?<![\[\(])" + r"\s+".join(_motif_souple(m) for m in ancre.split())
                           + r"(?![\]\)])")
        trouvees = list(motif.finditer(md_page))
        if len(trouvees) != 1:
            continue                       # absente, ou ambigue
        m = trouvees[0]
        md_page = (md_page[:m.start()] + "[" + m.group(0) + "](" + uri + ")"
                   + md_page[m.end():])
        poses += 1
    return md_page, poses


# Une ligne trop courte ne prouve rien : « Contenus », « Objectifs », un
# nombre seul se retrouvent partout dans la page et se declareraient absents
# au hasard des comparaisons.
_MOTS_MIN_POUR_JUGER = 3
# Nombre de mots compares pour reconnaitre une ligne. Assez pour ne pas
# confondre deux lignes voisines, assez peu pour survivre a une fin de ligne
# que le modele a reformulee ou coupee autrement.
_MOTS_TEMOINS = 6


def _lignes_du_pdf(page: Any) -> list[str]:
    """Lignes de la couche texte, dans l'ordre de lecture.

    Les puces de ces documents sont composees en Wingdings, et pymupdf en
    fait une ligne a elles seules ; elles sont recollees a la ligne suivante,
    en tiret Markdown, pour que le texte reinsere se lise comme le reste.

    L'en-tete et le pied de page n'en sont pas : les reinserer remettrait a
    la main ce que `_retirer_pagination` vient d'enlever.
    """
    try:
        blocs = page.get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return []
    bases = _lignes_de_base_de_pagination(page)
    lignes: list[str] = []
    puce_en_attente = False
    for bloc in blocs:
        for ligne in bloc.get("lines", []):
            if _est_de_pagination(ligne, bases):
                continue
            texte = "".join(s["text"] for s in ligne.get("spans", [])).strip()
            if not texte:
                continue
            if _est_glyphe_de_puce(texte):
                puce_en_attente = True
                continue
            if puce_en_attente:
                texte = "- " + _EST_PUCE_RE.sub("", texte)
                puce_en_attente = False
            lignes.append(texte)
    return lignes


def _reparer_depuis_le_pdf(md_page: str, page: Any) -> tuple[str, int]:
    """Reinsere les lignes que le modele a laissees de cote.

    **Redemander la page ne sert a rien.** Mesure faite sur la page 12 du
    programme de mathematiques de seconde : la meme image et la meme consigne
    rendent la meme reponse, octet pour octet, a 0,1 comme a 0,8 de
    temperature. Le decodage est deterministe, et aucun quota ne rachete une
    page perdue. La couche texte du PDF, elle, est complete et gratuite.

    On ne remplace pas la page : on y remet ce qui manque, a sa place. Chaque
    bloc absent s'ancre sur la derniere ligne qui, elle, a ete rendue — le
    modele suit l'ordre de lecture, et cet ordre est aussi celui du PDF. Le
    bloc qui n'a pas d'ancre est celui qui OUVRE la page, et il se pose donc
    en tete et non en queue.

    **Une ligne se cherche entiere, jamais par ses mots.** Un premier essai
    jugeait une ligne absente quand ses mots manquaient au compte de la page :
    « fonction » manquant neuf fois, toute ligne le contenant se declarait
    absente, y compris celles qui etaient bien la, et la page gagnait neuf
    doublons. Un deficit de mots dit *combien* il manque, jamais *lesquels*.

    Le texte reinsere n'est pas un corps etranger : `poser_emphases` et
    `poser_latex` passent apres, sur toute la page, et `_caler_titres` donne
    leur niveau aux titres ainsi retrouves.

    Appelee **apres** la substitution des tableaux : la grille du PDF restaure
    deja son contenu, et le reinserer serait le mettre en double.
    """
    lignes_pdf = _lignes_du_pdf(page)
    if not lignes_pdf:
        return md_page, 0

    lignes_md = md_page.split("\n")
    normes_md = [_norme_titre(l) for l in lignes_md]

    def temoin(ligne: str) -> str | None:
        mots = _norme_titre(ligne).split()
        if len(mots) < _MOTS_MIN_POUR_JUGER:
            return None
        return " ".join(mots[:_MOTS_TEMOINS])

    def chercher(sonde: str, depuis: int) -> int | None:
        """Indice de la ligne du Markdown qui porte cette sonde.

        On avance avec le texte : deux lignes identiques dans la page — et
        ces programmes en ont, « Contenus » revient a chaque section — se
        reconnaissent alors dans l'ordre, et non toutes sur la premiere.
        """
        for i in range(depuis, len(normes_md)):
            if sonde in normes_md[i]:
                return i
        for i in range(0, depuis):     # le modele a pu deplacer la ligne
            if sonde in normes_md[i]:
                return i
        return None

    # Ou chaque ligne du PDF se retrouve dans le Markdown, ou rien.
    place: list[int | None] = []
    absentes: list[bool] = []
    curseur = 0
    for ligne in lignes_pdf:
        sonde = temoin(ligne)
        if sonde is None:              # trop courte pour etre jugee
            place.append(None)
            absentes.append(False)
            continue
        trouve = chercher(sonde, curseur)
        place.append(trouve)
        absentes.append(trouve is None)
        if trouve is not None and trouve >= curseur:
            curseur = trouve

    if not any(absentes):
        return md_page, 0

    # Les blocs absents et leur ancre, collectes avant toute insertion : les
    # indices du Markdown ne doivent pas bouger en cours de route.
    insertions: dict[int, list[str]] = {}
    # Un bloc sans ancre ouvre la page : c'est precisement parce que RIEN ne
    # le precede dans le PDF qu'aucune ligne rendue ne peut lui servir
    # d'ancre. Le renvoyer en fin de page le sortait de l'ordre de lecture —
    # page 63 du programme de cycle 1, le haut du tableau herite de la page
    # 62 se retrouvait tout en bas, en lignes brutes sous le dernier
    # tableau, ce que le relecteur voit d'abord.
    au_debut: list[str] = []
    i = 0
    while i < len(lignes_pdf):
        if not absentes[i]:
            i += 1
            continue
        debut = i
        while i < len(lignes_pdf) and absentes[i]:
            i += 1
        bloc = lignes_pdf[debut:i]
        ancre = next((place[j] for j in range(debut - 1, -1, -1)
                      if place[j] is not None), None)
        if ancre is None:
            au_debut.extend(bloc)
        else:
            insertions.setdefault(ancre, []).extend(bloc)

    sortie: list[str] = []
    poses = 0
    if au_debut:
        sortie.extend(_recoller_paragraphes(au_debut))
        sortie.append("")
        poses += len(au_debut)
    for i, ligne in enumerate(lignes_md):
        sortie.append(ligne)
        if i in insertions:
            sortie.append("")
            sortie.extend(_recoller_paragraphes(insertions[i]))
            sortie.append("")
            poses += len(insertions[i])
    return "\n".join(sortie), poses


def _items_de_la_page(page: Any, declares: Any) -> set[str]:
    """Debuts de ligne que le PDF declare items de liste, hors tableau.

    Un item se compose souvent sur plusieurs lignes imprimees alors que le
    modele le rend d'un trait : c'est donc sa PREMIERE ligne qu'on releve, et
    l'appelant reconnait la sienne a ce qu'elle commence par la.
    """
    if not declares:
        return set()
    try:
        blocs = page.get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return set()
    lignes = [ligne for b in blocs for ligne in b.get("lines", [])
              if any(s["text"].strip() for s in ligne.get("spans", []))]
    debuts: set[str] = set()
    for _, points in declares:
        if not points:
            continue
        depart = min(points, key=lambda p: (round(p[1], 1), p[0]))
        for ligne in lignes:
            x0, y0, x1, y1 = ligne["bbox"]
            if not (y0 <= depart[1] <= y1 and x0 - 2.0 <= depart[0] <= x1 + 2.0):
                continue
            texte = "".join(s["text"] for s in ligne.get("spans", [])).strip()
            cle = _norme_titre(_PUCE_EN_TETE_RE.sub("", texte))
            if len(cle.split()) >= _MOTS_MIN_POUR_JUGER:
                debuts.add(cle)
            break
    return debuts
