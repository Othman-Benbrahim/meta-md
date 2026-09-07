"""Listes : rendre sa puce a une ligne que le PDF declare item.

La normalisation des puces Markdown vivait ici aussi ; elle ne
servait qu'au moteur `pymupdf`, retire le 2026-08-23.
"""
from __future__ import annotations

from .texte import _norme_titre


def _caler_listes(md_page: str, debuts: set[str]) -> tuple[str, int]:
    """Rend sa puce a une ligne que le PDF declare item de liste.

    **On n'ajoute jamais que ce qui manque.** Le modele rend deja plus de
    puces que l'arbre n'en declare — 1432 pour 1287 sur le corpus, hors
    tableau — et Word ne balise pas tout ce qui se lit comme une liste : lui
    retirer les siennes couterait plus que cela ne rapporterait. Restent les
    items rendus en paragraphe, que deux documents de l'ecole elementaire
    concentrent.
    """
    if not debuts:
        return md_page, 0
    lignes = md_page.split("\n")
    poses = 0
    dans_code = False
    for i, ligne in enumerate(lignes):
        nu = ligne.strip()
        if nu.startswith("```"):
            dans_code = not dans_code
            continue
        if dans_code or not nu or nu.startswith(("<", "|", ">", "#", "-", "*")):
            continue
        cle = _norme_titre(nu)
        if not cle:
            continue
        if any(cle.startswith(debut) for debut in debuts):
            lignes[i] = "- " + nu
            poses += 1
    return "\n".join(lignes), poses
