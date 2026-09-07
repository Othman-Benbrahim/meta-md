"""Mise en forme des tableaux a l'ecriture.

Derniere etape avant l'ecriture du fichier : la convention « un
tableau, une ligne » a fini de servir, le HTML peut respirer."""
from __future__ import annotations

import logging
import re


logger = logging.getLogger("atelier.traitements.aeration")


# ---------------------------------------------------------------------------
# Mise en forme des tableaux a l'ecriture
# ---------------------------------------------------------------------------
#
# Un tableau occupe une seule ligne dans tout le pipeline : les reparations de
# ce module raisonnent sur des lignes et cette convention les porte. L'aeration
# est donc faite en dernier, juste avant l'ecriture, quand plus rien n'a a
# relire le Markdown.
#
# La balise ouvrante reste a la marge : indentee de quatre espaces, elle ferait
# basculer tout le bloc en bloc de code Markdown, et le tableau s'afficherait
# en HTML brut. Une cellule n'est jamais coupee non plus — un retour a la ligne
# a l'interieur de `<td>` ajouterait des blancs dans son texte.

INDENT_TABLEAU = "    "

# Balises de structure (une par ligne) et cellules (atomiques). Les attributs
# sont acceptes : `_replier_titres_dans_tableaux` produit des `<th colspan=…>`.
# Les listes d'une cellule ne sont PAS aerees : une cellule reste
# insecable, sinon un retour a la ligne ajouterait des blancs dans son texte.
# Les <ul>/<ol> qui sont enfants directs d'un <tr> le sont, eux.
_JETON_TABLEAU = re.compile(
    r"</?(?:table|thead|tbody|tr|ul|ol)(?:\s[^>]*)?>"
    r"|<(t[dh])(?:\s[^>]*)?>.*?</\1\s*>",
    re.S)
_OUVRANTE_LISTE = re.compile(r"<[uo]l(?:\s[^>]*)?>")
# Une cellule dont le contenu porte une liste : on ouvre celle-ci.
_CELLULE_A_LISTE = re.compile(
    r"(<(?:t[dh])(?:\s[^>]*)?>)((?=.*<[uo]l\b).*?)(</(?:t[dh])\s*>)", re.S)
_OUVRANTE_TABLEAU = re.compile(r"<(?:table|thead|tbody|tr|ul|ol|t[dh])(?:\s[^>]*)?>")


def _aerer_un_tableau(html: str) -> str:
    """Repartit un tableau d'une ligne sur plusieurs, indente de 4 espaces.

    Rend le tableau inchange si le decoupage ne le couvre pas entierement :
    mieux vaut une ligne longue qu'un tableau ampute d'un fragment inattendu.
    """
    jetons = [m.group(0) for m in _JETON_TABLEAU.finditer(html)]
    if not jetons or "".join(jetons) != html:
        logger.warning("Tableau non reconnu en entier, laisse sur une ligne.")
        return html

    lignes: list[str] = []
    niveau = 0
    for jeton in jetons:
        if jeton.startswith("</"):
            niveau = max(0, niveau - 1)
        lignes.extend(_aerer_une_cellule(jeton, niveau))
        if _OUVRANTE_TABLEAU.fullmatch(jeton):
            niveau += 1
    return "\n".join(lignes)


# Une liste dans une cellule : `<ul>`, `</ul>` et chaque `<li>…</li>` sont des
# jetons ; tout le reste du contenu (`<b>…`, du texte) est laisse d'un bloc.
_JETON_LISTE = re.compile(r"</?[uo]l(?:\s[^>]*)?>|<li(?:\s[^>]*)?>.*?</li\s*>",
                          re.S)


def _aerer_une_cellule(jeton: str, niveau: int) -> list[str]:
    """Repartit le contenu d'une cellule sur plusieurs lignes s'il porte une liste.

    Une cellule reste insecable par defaut : la couper ajouterait des blancs
    dans son texte, et `_JETON_TABLEAU` la traite donc comme un atome. Mais les
    programmes rangent l'essentiel de leur contenu en listes a puces DANS les
    cellules — jusqu'a une dizaine d'items — et les laisser sur une seule ligne
    rend le `.md` illisible a la relecture.

    Le compromis : on n'ouvre que la liste. Le texte qui l'entoure garde sa
    ligne, sans retour ajoute nulle part dans une phrase.
    """
    marge = INDENT_TABLEAU * niveau
    m = _CELLULE_A_LISTE.fullmatch(jeton)
    if m is None:
        return [marge + jeton]
    ouvrante, contenu, fermante = m.group(1), m.group(2), m.group(3)

    morceaux = [x for x in _JETON_LISTE.split(contenu)]
    jetons = _JETON_LISTE.findall(contenu)
    if "".join(morceaux) + "".join(jetons) != contenu:
        return [marge + jeton]      # decoupage incomplet : on ne touche a rien

    lignes = [marge + ouvrante]
    n = niveau + 1
    reste = contenu
    for j in _JETON_LISTE.finditer(contenu):
        avant_j = reste[:j.start() - (len(contenu) - len(reste))]
        if avant_j.strip():
            lignes.append(INDENT_TABLEAU * n + avant_j.strip())
        if j.group(0).startswith("</"):
            n = max(niveau + 1, n - 1)
        lignes.append(INDENT_TABLEAU * n + j.group(0))
        if _OUVRANTE_LISTE.fullmatch(j.group(0)):
            n += 1
        reste = contenu[j.end():]
    if reste.strip():
        lignes.append(INDENT_TABLEAU * (niveau + 1) + reste.strip())
    lignes.append(marge + fermante)
    return lignes


def aerer_tableaux(corps: str) -> str:
    """Aere les tableaux du corps, juste avant l'ecriture du fichier.

    Le corps portait jusqu'ici un tableau par ligne : toutes les reparations
    raisonnent sur des lignes, et cette convention les porte. Elle a fini de
    servir ici, plus rien ne relit le Markdown apres.

    Cette fonction recalait aussi les positions du sidecar de Document AI,
    qui comptait en caracteres du corps. Ce moteur ayant ete retire, il n'y a
    plus de positions a suivre.
    """
    sorties: list[str] = []
    for ligne in corps.split("\n"):
        if ligne.startswith("<table") and ligne.endswith("</table>"):
            sorties.append(_aerer_un_tableau(ligne))
        else:
            sorties.append(ligne)
    return "\n".join(sorties)
