"""Numerotation explicite en tete d'un libelle, sans inference de titre."""
from __future__ import annotations

import re


_NUMERO = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3})*)([.)])?(\s*)(.*)$")


def _numero_de_titre(texte: str) -> tuple[tuple[int, ...], str] | None:
    """Chemin numerique et libelle pour 1., 1), 1.1., 1.1, etc.

    La presence d'un numero ne suffit pas a faire un titre. L'appelant doit
    verifier la typographie et les relations avec les sections voisines.
    """
    m = _NUMERO.fullmatch(texte.strip())
    if not m:
        return None
    chemin, fin, espace, libelle = m.groups()
    nombres = tuple(int(n) for n in chemin.split('.'))
    if not fin and (len(nombres) == 1 or not espace):
        return None
    if not libelle or not any(c.isalpha() for c in libelle):
        return None
    # Ne pas accepter le debut d'un nombre plus long que le prefixe reconnu.
    if not espace and libelle[0].isdigit():
        return None
    return nombres, libelle.strip()
