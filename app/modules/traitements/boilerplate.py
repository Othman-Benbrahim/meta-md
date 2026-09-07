"""Mobilier de page : en-tetes, pieds, bandeaux, images inventees.

Ce qui se repete d'une page a l'autre n'est pas du contenu. Le jugement
se fait sur les pages assemblees, jamais sur une seule."""
from __future__ import annotations

import logging
import re

from .texte import _MOT_RE, _est_titre_visuel


logger = logging.getLogger("atelier.traitements.boilerplate")


# Numeros de page classiques : "3", "3/12", "Page 3", "- 3 -", "p. 3", "[3]".
_PAGE_NUM_RE = re.compile(
    r"^\s*[-#*|\[\]]*\s*"
    r"(?:page\s*|p\.\s*)?"
    r"\d{1,4}"
    r"(?:\s*[/\-]\s*\d{1,4})?"
    r"\s*[-#*|\[\]]*\s*$",
    re.IGNORECASE,
)


def _remove_repeated_boilerplate(
    pages: list[str],
    head_lines: int = 4,
    tail_lines: int = 4,
    min_occurrence_ratio: float = 0.25,
) -> list[str]:
    """Retire les en-tetes / pieds / numeros de page repetitifs.

    Une ligne est traitee comme boilerplate si elle apparait comme l'une des
    `head_lines` premieres OU l'une des `tail_lines` dernieres lignes non-vides
    dans au moins `min_occurrence_ratio` des pages du document. Les numeros de
    page sont supprimes partout via `_PAGE_NUM_RE`.

    Le seuil est volontairement bas (25 %). Le prompt Vision demande deja au
    modele d'ignorer les en-tetes recurrents, et il obeit sur une partie des
    pages seulement : l'en-tete ne survit donc que sur quelques pages, et un
    seuil a 50 % le laissait passer justement parce que le prompt avait
    partiellement fonctionne. Le plancher `max(2, ...)` evite de supprimer une
    ligne vue une seule fois.

    Les tableaux (une ligne chacun, ouverte par `<`) sont exclus de la
    detection : la suite d'un tableau reprend son en-tete en haut des pages de
    continuation et serait supprimee a tort. Les tableaux multi-pages sont
    traites separement par `_merge_table_fragments`.
    """
    if len(pages) < 2:
        return pages

    n_pages = len(pages)
    threshold = max(2, int(round(min_occurrence_ratio * n_pages)))

    head_counts: dict[str, int] = {}
    tail_counts: dict[str, int] = {}

    def _countable(line: str) -> bool:
        return not line.startswith(("<", "|"))

    # Comptage : on ne considere que les lignes non-vides normalisees.
    for page in pages:
        non_empty = [l.strip() for l in page.split("\n") if l.strip()]
        for l in non_empty[:head_lines]:
            if _countable(l):
                head_counts[l] = head_counts.get(l, 0) + 1
        # Ne pas double-compter les lignes deja vues dans head_lines si la page
        # est tres courte.
        tail_start = max(head_lines, len(non_empty) - tail_lines)
        for l in non_empty[tail_start:]:
            if _countable(l):
                tail_counts[l] = tail_counts.get(l, 0) + 1

    head_boilerplate = {l for l, c in head_counts.items() if c >= threshold}
    tail_boilerplate = {l for l, c in tail_counts.items() if c >= threshold}

    removed_head = 0
    removed_tail = 0
    removed_page_num = 0

    cleaned: list[str] = []
    for page in pages:
        lines = page.split("\n")
        # Index des lignes non-vides dans l'ordre.
        non_empty_positions = [i for i, l in enumerate(lines) if l.strip()]
        head_indexes = set(non_empty_positions[:head_lines])
        tail_indexes = set(non_empty_positions[-tail_lines:]) if len(non_empty_positions) > head_lines else set()

        result: list[str] = []
        for i, line in enumerate(lines):
            norm = line.strip()
            if not norm:
                result.append(line)
                continue
            if _PAGE_NUM_RE.match(norm):
                removed_page_num += 1
                continue
            if i in head_indexes and norm in head_boilerplate:
                removed_head += 1
                continue
            if i in tail_indexes and norm in tail_boilerplate:
                removed_tail += 1
                continue
            result.append(line)
        # Trim des lignes vides en tete/queue.
        while result and not result[0].strip():
            result.pop(0)
        while result and not result[-1].strip():
            result.pop()
        cleaned.append("\n".join(result))

    if removed_head or removed_tail or removed_page_num:
        logger.info(
            "Boilerplate : %d en-tete(s), %d pied(s), %d num de page retires "
            "sur %d pages (seuil : %d occurrences).",
            removed_head, removed_tail, removed_page_num, n_pages, threshold,
        )
    return cleaned


# Un modele de vision ne voit qu'une image de page : il ne peut connaitre ni
# l'URL ni le nom de fichier d'une image. Toute image qu'il ecrit est donc
# inventee, et le bandeau du BO le montre sans detour — sur le programme de
# SNT il en a produit quatre cibles differentes pour un seul et meme
# bandeau, dont `https://example.com/logo.png`. Ces liens ne menent nulle
# part, et un lecteur les prend pour du document.
#
# Les seules images legitimes de ces moteurs viennent de `_poser_figures`,
# qui les decoupe dans le PDF et les incorpore en `data:` — et qui passe
# apres, ce qui rend ce menage sans danger pour elles.
_IMAGE_DU_MODELE_RE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")


def _retirer_images_du_modele(md_page: str) -> tuple[str, int]:
    """Retire les images que le modele a inventees, cible comprise.

    Se fait avant `_retirer_bandeaux` : une fois l'image partie, la mention
    du bandeau restee a cote — « **LE BULLETIN OFFICIEL…** » — se presente
    enfin comme le titre qu'elle est, et la couche texte peut la recuser.
    """
    neuf, n = _IMAGE_DU_MODELE_RE.subn("", md_page)
    if not n:
        return md_page, 0
    # Une ligne qui ne portait que l'image ne doit pas laisser de blanc.
    return "\n".join(l if l.strip() else "" for l in neuf.split("\n")), n


def _retirer_bandeaux(md_page: str, texte_pdf: str,
                      zone: int = 3) -> tuple[str, int]:
    """Retire d'une page les bandeaux absents de sa couche texte.

    Le bandeau d'un journal officiel, un logo compose en lettres : c'est une
    image, le PDF ne la declare pas comme du texte, mais le modele de vision
    la lit et la rend en titre. Le prompt lui demande de les ignorer, et il
    obeit sur la plupart des pages : l'artefact ne survit alors qu'une fois
    dans le document, la ou la detection par repetition
    (`_remove_repeated_boilerplate`) ne peut rien pour lui.

    La couche texte du PDF tranche page par page et sans rien couter : ce
    dont elle ne dit pas un mot ne vient pas du document. Trois garde-fous,
    parce qu'une suppression est definitive : la ligne doit se presenter en
    titre — un bandeau ne se lit jamais en paragraphe — se tenir parmi les
    `zone` premieres ou dernieres lignes non-vides, et la page doit avoir une
    couche texte a lui opposer.
    """
    mots_pdf = set(_MOT_RE.findall((texte_pdf or "").lower()))
    if len(mots_pdf) < 40:
        return md_page, 0  # page scannee : rien a opposer au modele
    lignes = md_page.split("\n")
    pleines = [i for i, l in enumerate(lignes) if l.strip()]
    bordure = sorted(set(pleines[:zone]) | set(pleines[-zone:]))
    retires = 0
    for i in bordure:
        nu = lignes[i].strip()
        if not _est_titre_visuel(nu):
            continue
        mots = _MOT_RE.findall(re.sub(r"^#{1,6}\s+|[*_`]", "", nu).lower())
        # En dessous de trois mots, un seul ecart de transcription suffirait a
        # condamner un vrai titre : on s'abstient.
        if len(mots) < 3:
            continue
        if sum(1 for m in mots if m in mots_pdf) / len(mots) >= 0.8:
            continue
        lignes[i] = ""
        retires += 1
    if not retires:
        return md_page, 0
    return "\n".join(lignes).strip("\n"), retires
