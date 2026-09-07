"""Grilles visibles, y compris les fragments sans texte contenant une image."""
from dataclasses import dataclass
from typing import Any

import pymupdf


@dataclass
class Grille:
    lignes: list[list[Any]]

    @property
    def bbox(self):
        return self.lignes[0][0] | self.lignes[-1][-1]


def grilles_de_la_page(page: Any) -> list[Grille]:
    """Ne retient que les matrices completes aux cellules sans fusion.

    TableFinder expose les cellules tracees meme si l'absence de texte
    l'empeche de construire un objet Table. Une bordure seule est exclue.
    """
    try:
        cellules = [pymupdf.Rect(c) for c in page.find_tables().cells if c]
    except Exception:
        return []
    groupes = []
    while cellules:
        groupe = [cellules.pop()]
        changement = True
        while changement:
            changement = False
            for c in cellules[:]:
                if any((abs(c.x0 - d.x1) < 1 or abs(c.x1 - d.x0) < 1)
                       and min(c.y1, d.y1) - max(c.y0, d.y0) > 1
                       or (abs(c.y0 - d.y1) < 1 or abs(c.y1 - d.y0) < 1)
                       and min(c.x1, d.x1) - max(c.x0, d.x0) > 1 for d in groupe):
                    groupe.append(c)
                    cellules.remove(c)
                    changement = True
        if len(groupe) < 2:
            continue
        lignes = []
        for c in sorted(groupe, key=lambda r: (r.y0, r.x0)):
            if not lignes or abs(lignes[-1][0].y0 - c.y0) > 1:
                lignes.append([])
            lignes[-1].append(c)
        modele = lignes[0]
        if all(len(l) == len(modele) and all(
                abs(c.x0 - m.x0) < 1 and abs(c.x1 - m.x1) < 1
                and abs(c.y1 - l[0].y1) < 1
                for c, m in zip(l, modele)) for l in lignes):
            groupes.append(Grille(lignes))
    # Un dessin peut lui-meme contenir un quadrillage (fractions, damier).
    # La grille englobante suffit a situer ce dessin : ses divisions internes
    # doivent rester dans l'image, sans devenir des cellules du document.
    groupes = [g for g in groupes if not any(
        autre is not g and autre.bbox.contains(g.bbox) for autre in groupes)]
    return sorted(groupes, key=lambda g: (g.bbox.y0, g.bbox.x0))


def cellule_contenant(rect: Any, grilles: list[Grille]):
    """Adresse unique d'une figure integralement contenue dans une cellule."""
    adresses = [(g, i, j) for g in grilles for i, ligne in enumerate(g.lignes)
                for j, c in enumerate(ligne) if c.contains(rect)]
    return adresses[0] if len(adresses) == 1 else None


def est_trace_de_grille(dessin: dict, grilles: list[Grille]) -> bool:
    # Les courbes d'une illustration ne sont jamais des bordures.
    if any(item[0] not in ('l', 're') for item in dessin.get('items', [])):
        return False
    r = pymupdf.Rect(dessin['rect'])
    for g in grilles:
        for c in [g.bbox] + [c for ligne in g.lignes for c in ligne]:
            if all(abs(a - b) <= 1 for a, b in zip(r, c)):
                return True  # cadre ou fond de cellule
            if (r.width <= 2 and min(abs(r.x0 - c.x0), abs(r.x0 - c.x1)) <= 1
                    and c.y0 - 1 <= r.y0 <= r.y1 <= c.y1 + 1):
                return True
            if (r.height <= 2 and min(abs(r.y0 - c.y0), abs(r.y0 - c.y1)) <= 1
                    and c.x0 - 1 <= r.x0 <= r.x1 <= c.x1 + 1):
                return True
    return False
