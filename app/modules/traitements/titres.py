"""Calage des niveaux de titre sur la hierarchie lue dans le PDF.

Le module ne lit pas le PDF : il recoit la hierarchie deja etablie par
`pdf.titres` et n'en fait qu'une reecriture du Markdown."""
from __future__ import annotations

import difflib
import re

from .texte import _norme_titre
from .numerotation import _numero_de_titre


_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
# Un titre n'a pas besoin du gras que le modele lui a mis : son rang le dit.
_SANS_EMPHASE_RE = re.compile(r"^([*_]{1,2})(.+?)\1$")


def _caler_titres(md_page: str,
                  titres_et_positions: tuple[dict[str, str], dict[str, set[int]]],
                  ) -> tuple[str, int]:
    """Aligne les titres de la page sur la hierarchie de titres du PDF.

    Trois corrections d'un seul geste : une ligne que le modele a laissee en
    texte courant retrouve son statut de titre, un titre rendu au mauvais
    niveau est ramene a celui que sa police lui donne dans le document, et un
    renvoi que le modele a pris pour un titre redevient du texte. Sans quoi
    deux sections de meme rang se lisent a des profondeurs differentes, et le
    plan du document ne veut plus rien dire.

    La table des matieres est le cas qui reclame la troisieme : elle reprend
    mot pour mot chaque section, le modele l'ecrit en titres, et le plan du
    document se retrouvait en double. `positions` dit a quelles occurrences
    un texte est vraiment un titre sur cette page.
    """
    titres, positions = titres_et_positions
    if not titres and not positions:
        return md_page, 0
    lignes = md_page.split("\n")
    cales = 0
    cles = list(titres)
    # Combien de fois ce texte a deja ete rencontre dans la page.
    rencontres: dict[str, int] = {}

    def rang_de(texte: str) -> str | None:
        """Le rang d'un titre, meme transcrit a un caractere pres.

        Le modele recopie, il ne photocopie pas : sur ce programme il a ecrit
        « Algorithmiques et programmation » la ou le PDF dit
        « Algorithmique ». Un `s` suffisait a manquer l'appariement, donc a
        laisser le titre au rang que le modele avait devine. Le repli n'est
        tente qu'a defaut, et exige une quasi-identite : deux titres voisins
        d'un meme document — « Contenus » et « Contenu » — doivent rester
        distincts.
        """
        cle = _norme_titre(texte)
        if not cle:
            return None
        if cle not in titres and cle not in positions:
            numero = _numero_de_titre(cle)
            chemin = numero[0] if numero else None
            compatibles = [c for c in cles
                           if ((_numero_de_titre(c) or (None,))[0]) == chemin
                           and re.findall(r'\d+', c) == re.findall(r'\d+', cle)]
            proches = difflib.get_close_matches(cle, compatibles, n=2, cutoff=0.92)
            if len(proches) != 1:
                return None
            # Le comptage des occurrences porte sur le titre reconnu, meme
            # si une transcription approchee en change une lettre.
            cle = proches[0]
        rang_occurrence = rencontres.get(cle, 0)
        rencontres[cle] = rang_occurrence + 1
        # Le PDF dit a quelles occurrences ce texte est un titre sur cette
        # page. Les autres ne font que le citer — c'est le sommaire, qui
        # reprend chaque section et se faisait promouvoir avec elle. La chaine
        # vide dit « ce n'est pas un titre ici », ce qu'aucun rang ne sait
        # dire ; un ensemble vide dit « jamais sur cette page ».
        attendues = positions.get(cle)
        if attendues is not None and rang_occurrence not in attendues:
            return ""
        return titres.get(cle)
    cloture = None
    for i, ligne in enumerate(lignes):
        nu = ligne.strip()
        marque = re.match(r"^(`{3,}|~{3,})(.*)$", nu)
        if cloture:
            if (marque and marque.group(1)[0] == cloture[0]
                    and len(marque.group(1)) >= len(cloture) and not marque.group(2).strip()):
                cloture = None
            continue
        if marque:
            cloture = marque.group(1)
            continue
        if not nu:
            continue
        deja = re.match(r"^(#{1,6})\s+(.*?)\s*$", nu)
        if deja:
            prefixe = rang_de(deja.group(2))
            if prefixe == "":
                # Le modele a fait un titre d'un renvoi : on le rend au texte.
                lignes[i] = deja.group(2)
                cales += 1
            elif prefixe and prefixe.strip() != deja.group(1):
                lignes[i] = prefixe + deja.group(2)
                cales += 1
            continue
        if nu.startswith('>'):
            # Une citation isolee peut etre un titre mal interprete. Une
            # citation de plusieurs lignes reste un bloc de citation.
            if ((i and lignes[i-1].lstrip().startswith('>'))
                    or (i+1 < len(lignes) and lignes[i+1].lstrip().startswith('>'))):
                continue
            contenu = re.sub(r'^>\s*', '', nu)
            prefixe = rang_de(contenu)
            if prefixe:
                lignes[i] = prefixe + _SANS_EMPHASE_RE.sub(r'\2', contenu)
                cales += 1
            continue
        if nu.startswith(("<", "|", "!")):
            continue
        # Une puce peut porter un titre. Ces programmes composent leurs
        # sous-titres en items de liste — `- **Introduction**` — et le modele
        # les rend tels quels, fidelement. Si le PDF dit que c'est un titre,
        # la puce n'y change rien.
        item = _ITEM_RE.match(nu)
        contenu = (item.group(1) if item and not _numero_de_titre(nu) else nu).strip()
        if len(_SANS_EMPHASE_RE.sub(r'\2', contenu)) > 120:
            continue
        prefixe = rang_de(contenu)
        if not prefixe:     # inconnu, ou cite hors de sa section
            continue
        # Le PDF a deja identifie cette ligne et son occurrence comme titre.
        # Les lignes vides du Markdown ne sont pas une preuve supplementaire :
        # le modele peut coller le titre a la liste ou au paragraphe suivant.
        lignes[i] = prefixe + _SANS_EMPHASE_RE.sub(r"\2", contenu)
        cales += 1
    return "\n".join(lignes), cales
