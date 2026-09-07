"""Grilles pipe recues d'un moteur, rendues en HTML.

Certains modeles rendent du pipe malgre la consigne. On le convertit a
l'entree, pour que la suite n'ait qu'une forme de tableau a connaitre."""
from __future__ import annotations

import re

from .tableaux_html import _BR_RE
from .texte import _echapper


# --- Grilles pipe reçues d'un moteur ----------------------------------------

_PIPE_SEP_RE = re.compile(r"^\|[\s:\-|]+\|$")
_PIPE_RE = re.compile(r"(?<!\\)\|")


def _est_ligne_pipe(ligne: str) -> bool:
    nu = ligne.strip()
    return nu.startswith("|") and nu.endswith("|") and len(nu) > 2


def _cellules_pipe(ligne: str) -> list[str]:
    return [c.replace("\\|", "|").strip()
            for c in _PIPE_RE.split(ligne.strip())[1:-1]]


def _contenu_cellule(texte: str) -> str:
    """Texte d'une cellule pipe rendu en HTML : echappe, gras conserve."""
    html = _echapper(_BR_RE.sub(" ", texte).strip())
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)


def _bloc_pipe_en_html(bloc: list[str]) -> str:
    corps = [l for l in bloc if not _PIPE_SEP_RE.match(l.strip())]
    if not corps:
        return ""
    # La ligne de separation ne fait un en-tete que sous la premiere ligne :
    # ailleurs, elle coupe le tableau en deux et n'est qu'un artefact.
    entete = len(bloc) > 1 and bool(_PIPE_SEP_RE.match(bloc[1].strip()))
    lignes = []
    for rang, ligne in enumerate(corps):
        balise = "th" if entete and rang == 0 else "td"
        cellules = "".join(f"<{balise}>{_contenu_cellule(c)}</{balise}>"
                           for c in _cellules_pipe(ligne))
        lignes.append(f"<tr>{cellules}</tr>")
    if entete:
        return f"<table><thead>{lignes[0]}</thead>{''.join(lignes[1:])}</table>"
    return "<table>" + "".join(lignes) + "</table>"


def _pipes_en_html(md: str) -> str:
    """Convertit en HTML les grilles pipe d'une page.

    C'est par ici que passe l'extraction hors ligne, dont pymupdf4llm rend
    tous les tableaux en pipe. Le modele de vision y revient parfois malgre
    la consigne : les deux chemins se rejoignent donc ici, avant toute
    reparation, et rien en aval n'a plus a connaitre deux syntaxes.
    """
    lignes = (md or "").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lignes):
        if not _est_ligne_pipe(lignes[i]):
            out.append(lignes[i])
            i += 1
            continue
        debut = i
        while i < len(lignes) and _est_ligne_pipe(lignes[i]):
            i += 1
        html = _bloc_pipe_en_html(lignes[debut:i])
        if html:
            out.extend(("", html, ""))
    return "\n".join(out)
