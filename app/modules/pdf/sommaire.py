"""Le sommaire, lu dans le PDF plutot que devine sur une image.

Un sommaire est la page ou le modele de vision se noie : celui du programme
de cycle 1 aligne 1 228 suites de points de conduite, que le modele reproduit
une par une jusqu'a depasser les 240 s de budget. La page ressortait perdue,
pour un contenu que le fichier declare pourtant sans ambiguite — un `/TOC`,
34 `/TOCI`, et 32 liens internes portant chacun sa page cible.

On le reconstruit donc, exactement comme les tableaux et les titres : **quand
le PDF declare, il fait foi**. Aucun appel reseau, aucun point de conduite, et
une entree par ligne.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import pymupdf  # type: ignore[import-untyped]
from ..traitements.texte import _norme_titre

logger = logging.getLogger("atelier.pdf.sommaire")

# En dessous, ce ne sont pas des entrees de sommaire mais des renvois au fil
# du texte : un programme en porte quelques-uns par page.
LIENS_MIN = 5
# Une suite de points de conduite, avec le numero de page qu'elle mene.
# Elle sert de SEPARATEUR, pas seulement de queue a couper : le rectangle
# d'un lien deborde souvent sur la ligne precedente, et le texte releve vaut
# alors \u00ab Les univers sonores ....... 30 Le spectacle vivant \u00bb \u2014 la fin de
# l'entree d'avant, puis celle qu'on cherche.
_CONDUITE = re.compile(r"[.\u2026\u00b7]{2,}\s*\d*")
_QUE_DES_POINTS = re.compile(r"^[\s.\u2026\u00b7\d]*$")


def declare_un_sommaire(doc: Any) -> bool:
    """Le document declare-t-il un `/TOC` dans son arbre de structure ?

    C'est ce qui autorise a reconstruire : sans lui, une page riche en renvois
    passerait pour un sommaire et perdrait sa mise en forme.
    """
    try:
        for xref in range(1, doc.xref_length()):
            valeur = doc.xref_get_key(xref, "S")
            if valeur and valeur[0] == "name" and valeur[1] == "/TOC":
                return True
    except Exception:  # noqa: BLE001 - un PDF sans arbre n'est pas une panne
        return False
    return False


def _titre_du_lien(page: Any, rect: Any) -> str:
    """Intitule associe a l'annotation, sans les lignes des liens voisins."""
    # L'annotation peut mordre sur la ligne voisine. La repartition de toutes
    # les lignes entre tous les liens prime sur le decoupage du rectangle.
    for entree in _entrees_detaillees(page):
        if pymupdf.Rect(entree['rect']) == pymupdf.Rect(rect):
            return entree['titre']
    return ''


def _nettoyer_entree(brut: str) -> str:
    morceaux = [m.strip() for m in _CONDUITE.split(brut)]
    morceaux = [m for m in morceaux if m]
    if not morceaux:
        return ""
    return morceaux[-1].strip(" .\u2026\u00b7")


def _entrees_detaillees(page: Any) -> list[dict]:
    """Chaque ligne appartient au lien le plus proche, jamais a deux liens."""
    try:
        liens = [l for l in page.get_links() if l.get('kind') == pymupdf.LINK_GOTO
                 and l.get('page', -1) >= 0]
        lignes = [l for b in page.get_text('dict').get('blocks', []) for l in b.get('lines', [])]
    except Exception:
        return []
    groupes: dict[int, list] = {}
    for ligne in lignes:
        texte = ''.join(s.get('text', '') for s in ligne.get('spans', [])).strip()
        if not texte or _QUE_DES_POINTS.fullmatch(texte):
            continue
        r = pymupdf.Rect(ligne['bbox'])
        centre = (r.y0 + r.y1) / 2
        candidats = []
        for i, lien in enumerate(liens):
            b = pymupdf.Rect(lien['from'])
            if b.y0 - 1 <= centre <= b.y1 + 1 and b.x0 - 3 <= r.x0 <= b.x1:
                candidats.append((abs(centre - (b.y0 + b.y1) / 2), i))
        if candidats:
            _, i = min(candidats)
            groupes.setdefault(i, []).append(ligne)
    entrees = []
    for i, propres in groupes.items():
        propres.sort(key=lambda l: (l['bbox'][1], l['bbox'][0]))
        texte = ' '.join(''.join(s.get('text', '') for s in l.get('spans', [])) for l in propres)
        titre = _nettoyer_entree(texte)
        if titre:
            entrees.append({'titre': titre, 'cible': liens[i]['page'] + 1,
                            'rect': liens[i]['from'], 'lignes': propres,
                            'y': propres[0]['bbox'][1]})
    return sorted(entrees, key=lambda e: (e['y'], e['rect'][0]))


def entrees_de_la_page(page: Any) -> list[tuple[str, int]]:
    """Entrees du sommaire de cette page : (titre, page cible), dans l'ordre."""
    entrees = _entrees_detaillees(page)
    if len(entrees) < LIENS_MIN:
        return []
    return [(e['titre'], e['cible']) for e in entrees]


def _niveaux_des_entrees(page: Any, titres_pdf: Any) -> tuple[dict, int]:
    """Le rang du titre vise fournit celui de l'entree, y compris en suite de page."""
    if titres_pdf is None:
        return {}, 1
    cache = getattr(titres_pdf, '_niveaux_sommaire', None)
    if cache is not None:
        return cache
    from .titres import _titres_de_la_page
    niveaux, pages_cibles = {}, {}
    for p in page.parent:
        for titre, cible in entrees_de_la_page(p):
            if not 1 <= cible <= len(page.parent):
                continue
            if cible - 1 not in pages_cibles:
                pages_cibles[cible - 1] = _titres_de_la_page(page.parent[cible - 1], titres_pdf)[0]
            cle = _norme_titre(titre)
            prefixe = pages_cibles[cible - 1].get(cle)
            if prefixe:
                niveaux[cible, cle] = len(prefixe.strip())
    base = min(niveaux.values(), default=1)
    titres_pdf._niveaux_sommaire = niveaux, base
    return niveaux, base


def rendre(page: Any, titres_pdf: Any = None) -> str:
    """Le sommaire de la page en Markdown, une entree par ligne.

    Forme : `- Titre - p. 7`. Le tiret separe l'intitule de sa page, et
    remplace les points de conduite, qui ne sont qu'une facon d'occuper le
    blanc sur du papier.
    """
    entrees = _entrees_detaillees(page)
    if len(entrees) < LIENS_MIN:
        return ""
    niveaux, base = _niveaux_des_entrees(page, titres_pdf)
    if titres_pdf is not None:
        from .titres import _titres_de_la_page
        titres_page = _titres_de_la_page(page, titres_pdf)
    else:
        titres_page = ({}, {})
    occupes = {tuple(l['bbox']) for e in entrees for l in e['lignes']}
    elements = []
    parents = []
    for e in entrees:
        niveau = max(0, niveaux.get((e['cible'], _norme_titre(e['titre'])), base) - base)
        # Une suite de sommaire peut commencer par un enfant dont le parent
        # est sur la page precedente. Pas de liste orpheline en retrait :
        # Markdown la prendrait pour du code ou du texte dans le premier item.
        while parents and niveau <= parents[-1]:
            parents.pop()
        parents.append(niveau)
        retrait = len(parents) - 1
        elements.append((e['y'], 1, '    ' * retrait + f"- {e['titre']} - p. {e['cible']}"))
    # Le sommaire peut s'achever au milieu d'une page. Ses entrees et le
    # corps suivent le meme ordre geometrique ; les paragraphes restent
    # separes pour que le lecteur et le calage des titres les reconnaissent.
    for bloc in page.get_text('dict').get('blocks', []):
        paragraphe = []
        y_paragraphe = 0
        def terminer():
            if paragraphe:
                elements.append((y_paragraphe, 0, ' '.join(paragraphe)))
                paragraphe.clear()
        for ligne in bloc.get('lines', []):
            if tuple(ligne['bbox']) in occupes:
                terminer()
                continue
            texte = ''.join(s.get('text', '') for s in ligne.get('spans', [])).strip()
            if not texte or _QUE_DES_POINTS.fullmatch(texte):
                continue
            if _norme_titre(texte) in titres_page[0]:
                terminer()
                elements.append((ligne['bbox'][1], 0, texte))
                continue
            if not paragraphe:
                y_paragraphe = ligne['bbox'][1]
            paragraphe.append(texte)
        terminer()
    elements.sort(key=lambda e: e[0])
    resultat = []
    precedent = None
    for _, genre, texte in elements:
        if resultat and (genre != precedent or genre == 0):
            resultat.append('')
        resultat.append(texte)
        precedent = genre
    rendu = '\n'.join(resultat)
    if titres_pdf is not None:
        from ..traitements.titres import _caler_titres
        rendu, _ = _caler_titres(rendu, titres_page)
    return rendu
