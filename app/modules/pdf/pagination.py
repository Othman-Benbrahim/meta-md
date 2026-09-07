"""Numeros de page et pieds, tels que le PDF les declare.

Un numero de page se reconnait a sa ligne de base, pas a son texte :
les artefacts du PDF le disent mieux que n'importe quelle regex."""
from __future__ import annotations

import re
from typing import Any

from ..traitements.tableaux_html import _est_tableau
from ..traitements.texte import _empreinte


# --- En-tetes et pieds de page, tels que le PDF les declare -----------------
#
# Un PDF balise range son en-tete et son pied hors de l'arbre de structure,
# dans un artefact de pagination :
#
#     /Artifact <</Attached [/Bottom]/Type/Pagination>> BDC … EMC
#
# C'est la reponse la plus sure a « d'ou vient cette ligne » : le document le
# dit lui-meme, la ou la repetition et la position se devinent. Le pied du
# programme de physique-chimie de seconde y est declare sur les treize pages,
# alors que la detection par repetition l'a laisse passer trois fois — le
# modele l'avait transcrit avec trois apostrophes differentes, et chaque
# variante ne se comptait qu'une fois.
#
# Le flux de contenu est lu directement : pymupdf ne remonte pas les
# artefacts. On n'en tire qu'une chose, la ligne de base des textes qu'ils
# portent, qui suffit a les retrouver dans `get_text("dict")` — `origin` d'un
# span EST cette ligne de base, au centieme pres.
_ARTEFACT_RE = re.compile(rb"/Artifact\s*<<([^<>]*)>>\s*BDC")
_MARQUE_CONTENU_RE = re.compile(rb"\b(?:BDC|BMC|EMC)\b")
_MATRICE_TEXTE_RE = re.compile(rb"(?:[-\d.]+\s+){5}([-\d.]+)\s+Tm")
# Tolerance entre la ligne de base lue dans le flux et celle du span : les
# deux viennent du meme nombre, arrondi differemment.
_ECART_LIGNE_DE_BASE = 1.0


def _lignes_de_base_de_pagination(page: Any) -> set[float]:
    """Lignes de base des textes ranges en artefact de pagination.

    Rend un ensemble vide quand le PDF n'est pas balise, ou n'en declare
    aucun : rien n'est alors retire, et les detections par repetition
    (`_remove_repeated_boilerplate`) et par couche texte (`_retirer_bandeaux`)
    restent seules a l'oeuvre, comme avant.
    """
    try:
        flux = b"".join(page.parent.xref_stream(x) for x in page.get_contents())
    except Exception:  # noqa: BLE001
        return set()
    hauteur = page.rect.height
    bases: set[float] = set()
    for m in _ARTEFACT_RE.finditer(flux):
        if b"/Pagination" not in m.group(1):
            continue
        # Fin de l'artefact : le EMC qui lui repond, imbrications comprises.
        i, profondeur = m.end(), 1
        while profondeur:
            marque = _MARQUE_CONTENU_RE.search(flux, i)
            if not marque:
                break
            profondeur += -1 if marque.group(0) == b"EMC" else 1
            i = marque.end()
        for q in _MATRICE_TEXTE_RE.finditer(flux[m.end():i]):
            try:
                bases.add(round(hauteur - float(q.group(1)), 2))
            except ValueError:
                continue
    return bases


def _est_de_pagination(ligne: dict, bases: set[float]) -> bool:
    """Cette ligne de `get_text("dict")` est-elle en-tete ou pied de page ?"""
    spans = ligne.get("spans", [])
    if not spans or not bases:
        return False
    y = spans[0].get("origin", (0, 0))[1]
    return any(abs(y - base) <= _ECART_LIGNE_DE_BASE for base in bases)


def _textes_de_pagination(page: Any) -> list[str]:
    """Textes de l'en-tete et du pied de page, tels qu'ils sont composes."""
    bases = _lignes_de_base_de_pagination(page)
    if not bases:
        return []
    try:
        blocs = page.get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return []
    sortie: list[str] = []
    for bloc in blocs:
        for ligne in bloc.get("lines", []):
            if not _est_de_pagination(ligne, bases):
                continue
            texte = "".join(s.get("text", "") for s in ligne.get("spans", []))
            if texte.strip():
                sortie.append(texte.strip())
    return sortie


# En dessous, une empreinte ne designe plus une ligne en particulier : deux
# mots se retrouvent dans trop de phrases pour qu'on supprime sur leur foi.
_MOTS_MIN_PAGINATION = 3


def _retirer_pagination(md_page: str, page: Any) -> tuple[str, int]:
    """Retire du Markdown l'en-tete et le pied que le PDF declare.

    La comparaison se fait sur l'empreinte : le modele ne rend pas deux fois
    la meme ligne de la meme facon, et c'est precisement ce qui la faisait
    survivre a la detection par repetition.

    Une ligne du Markdown qui n'est qu'un morceau du pied part avec lui : le
    modele coupe parfois l'URL du reste.
    """
    empreintes = {e for e in map(_empreinte, _textes_de_pagination(page))
                  if len(e.split()) >= _MOTS_MIN_PAGINATION}
    if not empreintes:
        return md_page, 0
    lignes = md_page.split("\n")
    garde: list[str] = []
    retires = 0
    for ligne in lignes:
        nu = ligne.strip()
        if nu and not _est_tableau(nu):
            empreinte = _empreinte(nu)
            if len(empreinte.split()) >= _MOTS_MIN_PAGINATION and any(
                    empreinte in ref for ref in empreintes):
                retires += 1
                continue
        garde.append(ligne)
    if not retires:
        return md_page, 0
    return "\n".join(garde).strip("\n"), retires
