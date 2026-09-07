"""Reparation des tableaux, sur le Markdown seul.

Un tableau coupe par une fin de page, une continuation versee dans la
mauvaise cellule, un debris de grille reste en texte : tout se rattrape
ici sans relire le PDF."""
from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any

from .tableaux_html import (RANGS_ENTETE, _CELLULE_ENTETE_RE, _COLSPAN_RE, _LIGNE_RE,
                            _THEAD_RE, _blocs_de_tableau, _cellules, _est_tableau,
                            _fusionner_tables, _interieur, _lignes_du_tableau,
                            _nb_colonnes, _recomposer_ligne)
from .texte import (_BALISE_RE, _EST_CHROME_RE, _EST_PUCE_RE, _echapper, _mots, _mot_a_mot,
                    _mots_cles, _norme_titre, _texte_nu)


logger = logging.getLogger("atelier.traitements.tableaux_reparation")


def _rappel_tableau_ouvert(md_page_precedente: str) -> str | None:
    """Rappel des colonnes d'un tableau laisse ouvert par la page precedente.

    Une page est rendue sans voir les autres : quand un tableau court encore,
    le modele n'a aucun moyen de le savoir et prend la premiere cellule de la
    ligne coupee pour un titre, quand il ne rend pas la ligne en paragraphe.

    Le rappel est formule comme une information, jamais comme un ordre, et il
    dit explicitement quand l'ignorer : une premiere version, qui affirmait
    « reprends ces colonnes », a fait supprimer au modele le titre et le
    paragraphe d'une page qui commencait en realite une nouvelle section.
    """
    lignes = [l for l in (md_page_precedente or "").split("\n")]
    while lignes and not lignes[-1].strip():
        lignes.pop()
    # Le pied de page suit le tableau sans le fermer, et n'est retire qu'au
    # recollage des pages, donc trop tard pour nous.
    saute = 0
    while lignes and saute < 3 and not _est_tableau(lignes[-1]):
        fin = lignes[-1].strip()
        if len(fin) > 120 or fin[:1] in ("#", "-", "*", ">", "<"):
            break
        lignes.pop()
        while lignes and not lignes[-1].strip():
            lignes.pop()
        saute += 1
    if not lignes or not _est_tableau(lignes[-1]):
        return None
    tableau = lignes[-1].strip()
    rangs = _lignes_du_tableau(tableau)
    n = _nb_colonnes(tableau)
    if not rangs or n < 2:
        return None
    entete = " | ".join(_texte_nu(c) for _, _, c in _cellules(rangs[0]))
    return (
        "\n\nInformation sur le document, a n'utiliser que si elle "
        "s'applique : la page precedente se termine par un tableau de "
        f"{n} colonnes, dont l'en-tete est :\n{entete}\n"
        "- Si cette page commence par la SUITE de ce tableau (des cellules, "
        "sans titre ni paragraphe d'introduction), rends ces lignes dans un "
        f"<table> de {n} colonnes, en <tr><td>, sans reecrire l'en-tete. Une "
        "cellule laissee vide reste une colonne vide.\n"
        "- Si cette page commence autre chose (un titre, un paragraphe, un "
        "nouveau tableau avec son propre en-tete), IGNORE ce rappel et rends "
        "la page telle qu'elle est. N'omets jamais un titre ni un paragraphe "
        "pour faire entrer la page dans ce tableau.\n")


def _entete_seule(html: str) -> tuple[str, ...]:
    rangs = _lignes_du_tableau(html)
    if len(rangs) != 1:
        return ()
    cellules = _cellules(rangs[0])
    if not cellules or any(b.lower() != 'th' for b, _, _ in cellules):
        return ()
    return tuple(_norme_titre(_texte_nu(c)) for _, _, c in cellules)


def _retirer_entetes_isoles(md: str, tableaux: list[str]) -> str:
    """Un en-tete seul du modele ne doit pas doubler celui du tableau voisin."""
    lignes = md.split('\n')
    blocs = [debut for debut, _ in _blocs_de_tableau(lignes)]
    for k, debut in enumerate(blocs):
        signature = _entete_seule(lignes[debut])
        if not signature or any(_entete_seule(t) == signature for t in tableaux):
            continue
        for voisin in blocs[max(0, k-1):k] + blocs[k+1:k+2]:
            if any(l.strip() for l in lignes[min(debut, voisin)+1:max(debut, voisin)]):
                continue
            rangs = _lignes_du_tableau(lignes[voisin])
            if len(rangs) < 2 or _entete_seule('<table>' + rangs[0] + '</table>') != signature:
                continue
            mots = _mots_cles(lignes[voisin])
            if len(mots) >= 5 and any(len(mots & _mots_cles(t)) / len(mots) >= .8 for t in tableaux):
                lignes[debut] = ''
                break
    return '\n'.join(lignes)


def _remplacer_tableaux(md_page: str, tableaux: list[str]) -> tuple[str, int]:
    """Substitue aux tableaux rendus par le modele ceux que declare le PDF.

    On garde la place que le modele leur a donnee — c'est lui qui sait ou ils
    tombent dans l'ordre de lecture — et on ne reprend de pymupdf que la
    grille. Le texte hors tableau n'est jamais touche.

    Chaque correspondance doit etre unique et suivre l'ordre de lecture.
    Un fragment manquant ou un en-tete surnumeraire ne bloque pas les autres
    grilles. Les correspondances ambigues restent inchangees.
    """
    if not tableaux:
        return md_page, 0
    lignes = md_page.split("\n")
    blocs = _blocs_de_tableau(lignes)
    choix = {}
    for debut, fin in blocs:
        vus = _mots_cles("\n".join(lignes[debut:fin]))
        scores = []
        for j, remplacant in enumerate(tableaux):
            attendus = _mots_cles(remplacant)
            commun = vus & attendus
            if vus and attendus and len(commun) >= 5 and len(commun) / len(vus) >= .6:
                scores.append(((len(commun) / len(vus) + len(commun) / len(attendus)) / 2, j))
        scores.sort(reverse=True)
        if scores and (len(scores) == 1 or scores[0][0] - scores[1][0] >= .1):
            choix[debut] = scores[0][1]
    # Deux blocs qui revendiquent la meme grille, ou des correspondances
    # croisees, ne fournissent pas assez de preuves pour une substitution.
    choix = {d: j for d, j in choix.items() if list(choix.values()).count(j) == 1
             and all((d - autre) * (j - rang) >= 0 for autre, rang in choix.items())}
    out: list[str] = []
    curseur = 0
    for debut, fin in blocs:
        if debut not in choix:
            out.extend(lignes[curseur:fin])
            curseur = fin
            continue
        remplacant = tableaux[choix[debut]]
        avant = lignes[curseur:debut]
        # Le modele sort parfois la premiere cellule du tableau en titre, au
        # dessus de celui-ci. Le tableau du PDF, lui, la contient : garder le
        # titre ferait doublon. On ne retire que ce qui est mot pour mot dans
        # la premiere ligne du tableau qu'on vient de poser.
        rangs = _lignes_du_tableau(remplacant)
        premiere = _mots_cles(rangs[0]) if rangs else set()
        for k in range(len(avant) - 1, -1, -1):
            ligne = avant[k].strip()
            if not ligne:
                continue
            nu = re.sub(r"^#{1,6}\s+", "", ligne)
            mots = _mots_cles(nu)
            if len(nu) <= 150 and mots and mots <= premiere:
                del avant[k]
            break
        out.extend(avant)
        out.extend(remplacant.split("\n"))
        curseur = fin
    out.extend(lignes[curseur:])
    return _retirer_entetes_isoles("\n".join(out), tableaux), len(choix)


def _est_rang_banniere(tr: str) -> bool:
    """Le rang n'est-il qu'un intitule etale sur toute la largeur ?

    Une cellule unique portant un `colspan` : c'est ainsi que le BO ecrit le
    titre d'une section de competences, en tete du tableau qui la porte. Un
    rang de donnees a autant de cellules que le tableau a de colonnes.

    Un tableau d'une seule colonne ne peut pas se juger ainsi — tous ses rangs
    y sont pleine largeur, sans qu'aucun `colspan` ne soit ecrit. Le test rend
    donc `False`, et le recollage retombe sur les autres signaux.
    """
    cellules = _cellules(tr)
    if len(cellules) != 1:
        return False
    m = _COLSPAN_RE.search(cellules[0][1])
    return bool(m) and int(m.group(1)) > 1


def _meme_tableau(html_a: str, html_b: str) -> bool:
    """Les deux fragments sont-ils deux morceaux d'un meme tableau ?

    Deux signaux, faute de pouvoir mesurer quoi que ce soit : la suite reemet
    l'en-tete du fragment precedent, mot pour mot, ou elle n'en porte pas et a
    la meme largeur.

    Un troisieme cas se presentait comme insoluble : une suite qui rouvre sur
    un NOUVEL intitule, frequent dans les programmes du BO, ou la bordure
    exterieure entoure toutes les sections de competences. Il a une signature,
    et elle est dans le HTML : **cet intitule est un rang d'une seule cellule
    qui couvre toutes les colonnes**. Une suite, elle, rouvre sur des donnees,
    donc sur autant de cellules que de colonnes.

        page 4 de spe577, faux raccord  <tr><td colspan="2">S'approprier les
                                        exigences, les notions...</td></tr>
        page 6 de spe634, vraie suite   <tr><td>Entites chimiques...</td>
                                        <td>Utiliser le terme adapte...</td></tr>

    La banniere sort en `<td colspan>` et non en `<th>` : le garde-fou des
    colonnes redeclarees, qui cherche un `<th>`, passait a cote. Mesure sur le
    corpus : six fusions abusives, dont les quatre sections de competences
    d'`ensel714_annexe1`, et aucune vraie suite perdue.

    Un signal separe pourtant les deux quand le PDF declare ses tableaux :
    **une suite ne redeclare jamais ses colonnes.** Un fragment qui porte une
    ligne d'en-tete dans ses premiers rangs — meme precedee de l'intitule
    d'une partie et de son introduction — commence un nouveau tableau. Sans
    ce garde-fou, la page 7 de ce programme, qui ouvre la partie 2, se
    recollait a la partie 1 et les six tableaux du document n'en faisaient
    plus qu'un.
    """
    lignes_a = _lignes_du_tableau(html_a)
    lignes_b = _lignes_du_tableau(html_b)
    if not lignes_a or not lignes_b:
        return False
    tete = _texte_nu(lignes_a[0])
    if tete and tete == _texte_nu(lignes_b[0]):
        return True
    if any(_CELLULE_ENTETE_RE.search(l) for l in lignes_b[:RANGS_ENTETE]):
        return False
    # Le premier rang seulement : une banniere ouvre un tableau, elle ne
    # s'intercale pas. La chercher plus loin ferait tomber les suites qui
    # portent un sous-titre en cours de grille.
    if _est_rang_banniere(lignes_b[0]):
        return False
    sans_entete = not _THEAD_RE.match(_interieur(html_b).strip())
    return sans_entete and _nb_colonnes(html_a) == _nb_colonnes(html_b)


def _elargir_a_gauche(html: str, cellule: str) -> str | None:
    """Rend son statut de cellule au texte que le modele a pris pour un titre.

    Les lignes suivantes du fragment appartiennent a la meme ligne logique :
    leur premiere cellule est donc vide.
    """
    lignes = _lignes_du_tableau(html)
    if not lignes:
        return None
    out: list[str] = []
    for rang, ligne in enumerate(lignes):
        cellules = _cellules(ligne)
        if not cellules:
            return None
        tete = ("td", "", _echapper(cellule) if rang == 0 else "")
        out.append(_recomposer_ligne([tete] + list(cellules)))
    return "<table>" + "".join(out) + "</table>"


def _joindre_cellule(contenu: str, suite: str) -> str:
    """Prolonge le dernier item, sans sortir la fin de phrase de sa liste."""
    contenu, suite = contenu.strip(), suite.strip()
    if not contenu:
        return suite
    if not suite:
        return contenu
    # Une nouvelle liste reste une liste ; une fin de phrase sans nouveau
    # bloc reprend dans le dernier item ou paragraphe deja ouvert.
    if not re.search(r"<(?:ul|ol|li|p|div)\b", suite, re.I):
        fin = re.search(r"(?:</(?:li|ul|ol|p)>\s*)+$", contenu, re.I)
        if fin:
            avant = contenu[:fin.start()].rstrip()
            return avant + " " + suite + contenu[fin.start():]
    return contenu + " " + suite


def _phrase_coupee(precedente: str, suite: str) -> bool:
    """Indice prudent d'une phrase coupee, uniquement a une fin de page.

    Une puce, un paragraphe ou un intitule nouveau ne sont pas la fin du
    precedent. Sans indice textuel de continuation, on garde deux lignes.
    """
    if re.search(r"<(?:ul|ol|li|p|div)\b", suite, re.I):
        return False
    avant, apres = _texte_nu(precedente).strip(), _texte_nu(suite).strip()
    return bool(avant and apres and apres[0].islower()
                and avant[-1] not in ".!?;:…")


def _cellule_remplie(html: str) -> bool:
    return bool(_texte_nu(html).strip() or re.search(r'<img\b', html, re.I))


def _reverser_continuations(html: str,
                            ouvertures: set[int] | None = None,
                            coupures: set[int] | None = None,
                            ) -> tuple[str, int]:
    """Reverse dans la ligne precedente une ligne qui n'en est que la suite.

    Une cellule qui contient plusieurs paragraphes est souvent rendue en
    autant de lignes, les autres colonnes restant vides. Le tableau compte
    alors trois lignes la ou le document imprime n'en a qu'une, avec des cases
    vides qui ne veulent rien dire. Une ligne dont la premiere cellule est
    vide est reversee dans la precedente, colonne par colonne.

    Le texte est conserve integralement : c'est une concatenation, jamais une
    suppression. Le risque assume est l'inverse : un tableau qui laisserait
    volontairement sa premiere colonne vide verrait ses lignes reunies. Sur un
    document imprime, cette forme designe presque toujours la suite de la
    ligne du dessus.

    `ouvertures` borne ce risque quand le PDF declare sa grille : seules les
    lignes qui OUVRENT un fragment recolle y figurent, c'est-a-dire celles
    qu'une fin de page a coupees. Les autres sont des lignes du document, et
    les recoller serait defaire ce que l'arbre vient d'etablir : 47 lignes du
    corpus laissent volontairement leur premiere colonne vide, une remarque
    posee sous la notion qu'elle commente. Sans arbre, `ouvertures` vaut None
    et la reparation reste large, faute de mieux.
    """
    interieur = _interieur(html).strip()
    tete = _THEAD_RE.match(interieur)
    entete = tete.group(0) if tete else ""
    lignes = _LIGNE_RE.findall(interieur[tete.end():] if tete else interieur)
    if len(lignes) < 2:
        return html, 0
    gardees: list[list[tuple[str, str, str]]] = []
    reversees = 0
    for rang, ligne in enumerate(lignes):
        cellules = list(_cellules(ligne))
        if not cellules:
            return html, 0  # ligne illisible : on ne touche a rien
        compatibles = (gardees and len(cellules) == len(gardees[-1])
                       and all(b.lower() == "td" and not a.strip()
                               for b, a, _ in cellules + gardees[-1]))
        suite_a_droite = (not _cellule_remplie(cellules[0][2])
                         and any(_cellule_remplie(c[2]) for c in cellules[1:]))
        # La premiere colonne peut etre coupee elle aussi. On ne l'absorbe
        # qu'a une frontiere de page explicite et quand chaque cellule non
        # vide prolonge une phrase inachevee dans la meme colonne.
        suite_de_phrase = (compatibles and coupures is not None and rang in coupures
                           and any(_cellule_remplie(c[2]) for c in cellules)
                           and all(not _cellule_remplie(c[2]) or _phrase_coupee(gardees[-1][i][2], c[2])
                                   for i, c in enumerate(cellules)))
        if (compatibles and (ouvertures is None or rang in ouvertures)
                and (suite_a_droite or suite_de_phrase)):
            precedente = gardees[-1]
            for i in range(len(cellules)):
                suite = cellules[i][2].strip()
                if _cellule_remplie(suite):
                    balise, attrs, contenu = precedente[i]
                    precedente[i] = (balise, attrs, _joindre_cellule(contenu, suite))
            reversees += 1
            continue
        gardees.append(cellules)
    if not reversees:
        return html, 0
    corps = "".join(_recomposer_ligne(c) for c in gardees)
    return f"<table>{entete}{corps}</table>", reversees


def _nb_rangs_hors_entete(html: str) -> int:
    """Nombre de `<tr>` d'un tableau, son `<thead>` d'ouverture excepte.

    C'est l'espace d'indices dans lequel raisonne `_reverser_continuations` :
    les ouvertures de fragment doivent s'y exprimer.
    """
    interieur = _interieur(html).strip()
    tete = _THEAD_RE.match(interieur)
    return len(_LIGNE_RE.findall(interieur[tete.end():] if tete else interieur))


def _merge_table_fragments(md: str, grille_declaree: bool = False) -> str:
    """Repare les tableaux coupes par une fin de page.

    Chaque page est convertie isolement : quand un tableau se poursuit sur la
    page suivante, le moteur en reemet l'en-tete, rend la suite en tableau
    separe, ou prend la premiere cellule de la ligne coupee pour un titre.

    Trois reparations, dans cet ordre : la cellule rendue a sa ligne, les
    fragments voisins recolles, les lignes de continuation reversees.

    Une quatrieme a disparu avec le passage au HTML — la ligne `|---|` que le
    moteur oubliait sous l'en-tete d'une suite, et sans laquelle le fragment
    ne se lisait plus comme un tableau. Le HTML n'a rien de tel a oublier :
    une balise ouverte est une balise fermee, ou elle l'est d'office par
    `_normaliser_tableaux_html`.
    """
    lines = md.split("\n")
    blocks: list[dict[str, Any]] = [
        {"start": debut, "end": fin, "html": lines[debut].strip(),
         "ouvertures": set(), "coupures": set()}
        for debut, fin in _blocs_de_tableau(lines)
    ]

    if not blocks:
        return md

    # Lignes franchies par une fusion — reperes de page, bandeaux — a reposer
    # apres le bloc qui a absorbe l'autre, sous peine de les perdre.
    suffixes: dict[int, list[str]] = {}

    # 1. Une cellule prise pour un titre.
    #
    # Une page qui commence au milieu d'un tableau ne montre pas d'en-tete :
    # le modele prend alors la premiere cellule de la ligne coupee pour un
    # titre, et rend la suite de la ligne sur une colonne de moins. On
    # reconnait la forme — tableau, titre seul, tableau ampute d'exactement
    # une colonne — et `_elargir_a_gauche` rend au titre son statut de cellule.
    #
    # Repare apres coup plutot qu'en amont : rappeler au modele qu'un tableau
    # est ouvert le pousse a rendre en tableau une page qui commence en fait
    # une nouvelle section, et a en perdre le titre et le texte. Ici, au pire
    # la forme ne correspond pas et on ne touche a rien.
    recolles = 0
    # D'amont en aval, et non l'inverse : une page peut couper deux lignes de
    # suite, chacune donnant un fragment. Repare dans l'ordre, le fragment
    # elargi sert de reference au suivant ; a rebours, on comparerait deux
    # fragments amputes entre eux et la chaine s'arreterait au premier.
    for idx in range(1, len(blocks)):
        cur = blocks[idx]
        prev = next((b for b in reversed(blocks[:idx]) if b["html"]), None)
        if not cur["html"] or prev is None:
            continue
        # Les restes de mise en page (une image de bandeau, un filet) ne
        # comptent pas comme du contenu : ils s'intercalent parce que la
        # coupure est justement une fin de page.
        entre_idx = [j for j in range(prev["end"], cur["start"])
                     if lines[j].strip() and not _EST_CHROME_RE.match(lines[j].strip())]
        if len(entre_idx) != 1:
            continue
        orpheline = lines[entre_idx[0]].strip()
        # La cellule orpheline se presente en titre, ou simplement en ligne
        # de texte quand le modele n'a meme pas cru a un titre. Dans les deux
        # cas elle est courte : une vraie phrase d'introduction ne l'est pas,
        # et c'est ce qui la protege d'etre avalee.
        m = re.match(r"^#{1,6}\s+(.*?)\s*$", orpheline)
        if m:
            cellule = m.group(1)
        elif len(orpheline) <= 150 and "<" not in orpheline:
            cellule = orpheline
        else:
            continue
        if not cellule:
            continue
        n_prev = _nb_colonnes(prev["html"])
        n_cur = _nb_colonnes(cur["html"])
        if n_cur != n_prev - 1 or n_cur < 1:
            continue
        elargi = _elargir_a_gauche(cur["html"], cellule)
        if not elargi:
            continue
        ouverture = _nb_rangs_hors_entete(prev["html"])
        prev["ouvertures"].add(ouverture)
        if any(re.fullmatch(r"\s*<!--\s*page\s+\d+\s*-->\s*", l)
               for l in lines[prev["end"]:cur["start"]]):
            prev["coupures"].add(ouverture)
        prev["html"] = _fusionner_tables(prev["html"], elargi)
        cur["html"] = ""
        # Le titre redevenu cellule n'a plus a figurer comme titre.
        lines[entre_idx[0]] = ""
        reportes = [lines[j] for j in range(prev["end"], cur["start"])
                    if lines[j].strip() and _EST_CHROME_RE.match(lines[j].strip())]
        prev["end"] = cur["end"]
        if reportes:
            suffixes.setdefault(id(prev), []).extend(reportes)
        recolles += 1

    # 2. Fusion des fragments consecutifs.
    merged = 0
    for idx in range(len(blocks) - 1, 0, -1):
        cur, prev = blocks[idx], blocks[idx - 1]
        if not cur["html"] or not prev["html"]:
            continue  # bloc deja absorbe par la reparation precedente
        # Le repere de page tombe precisement ici quand un tableau est coupe
        # par une fin de page : il ne separe pas deux tableaux, il marque une
        # frontiere. On le franchit, et on le repose apres le tableau recolle
        # — celui-ci commence sur la page d'avant, c'est elle qui le porte.
        between = lines[prev["end"]:cur["start"]]
        garde = [l for l in between if l.strip() and not _EST_CHROME_RE.match(l.strip())]
        if garde:
            continue  # du contenu reel separe les deux blocs
        if not _meme_tableau(prev["html"], cur["html"]):
            continue  # tableaux differents
        reportes = [l for l in between if l.strip()]
        ouverture = _nb_rangs_hors_entete(prev["html"])
        prev["ouvertures"].add(ouverture)
        if any(re.fullmatch(r"\s*<!--\s*page\s+\d+\s*-->\s*", l) for l in between):
            prev["coupures"].add(ouverture)
        fusion = _fusionner_tables(prev["html"], cur["html"])
        decalage = _nb_rangs_hors_entete(fusion) - _nb_rangs_hors_entete(cur["html"])
        for cle in ("ouvertures", "coupures"):
            prev[cle].update(decalage + rang for rang in cur[cle])
        prev["html"] = fusion
        cur["html"] = ""
        prev["end"] = cur["end"]
        if reportes:
            suffixes.setdefault(id(prev), []).extend(reportes)
        merged += 1

    # 3. Lignes de continuation, une fois les fragments reunis : une ligne
    # coupee par la fin de page ne se reconnait comme telle qu'apres.
    continuations = 0
    for blk in blocks:
        if not blk["html"]:
            continue
        blk["html"], n = _reverser_continuations(
            blk["html"], blk["ouvertures"] if grille_declaree else None,
            blk["coupures"])
        continuations += n

    # Reconstruction : on remplace chaque bloc par son tableau final.
    out: list[str] = []
    cursor = 0
    for blk in blocks:
        if blk["start"] >= cursor:
            out.extend(lines[cursor:blk["start"]])
        if blk["html"]:
            out.append(blk["html"])
        for ligne in suffixes.get(id(blk), []):
            out.extend(("", ligne))
        cursor = max(cursor, blk["end"])
    out.extend(lines[cursor:])

    if merged or recolles or continuations:
        logger.info("Tableaux : %d fragment(s) fusionne(s), %d cellule(s) "
                    "rendue(s) a leur ligne, %d ligne(s) de continuation "
                    "reversee(s).", merged, recolles, continuations)
    # La fusion peut laisser des lignes vides consecutives la ou un fragment a
    # disparu : on les reduit a une seule.
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text)


def _est_continuation(html: str) -> bool:
    """Ce tableau est-il la fin d'un tableau ouvert par la page precedente ?

    Un tableau qui commence sur sa page annonce ses colonnes ; une suite n'a
    rien a annoncer, elle reprend au milieu d'une ligne. **L'absence
    d'en-tete est donc la signature d'une continuation**, la meme que
    `_meme_tableau` retient deja pour decider d'un recollage.

    Le rang compte autant que la forme, et l'appelant s'en charge : seule la
    PREMIERE grille declaree d'une page peut continuer la page d'avant. Une
    grille sans en-tete au milieu d'une page est une grille dont le PDF n'a
    pas balise l'en-tete, pas une suite ; la remonter en tete de page
    mettrait son contenu avant ce qui l'annonce.
    """
    return bool(_lignes_du_tableau(html)) and not _THEAD_RE.match(
        _interieur(html).strip())


def _deja_rendu(declare: str, mots_rendus: set[str]) -> bool:
    """Ce tableau declare est-il deja dans la page, sous une forme ou une autre ?

    On compare par les mots, pas par la structure : le modele rend rarement la
    meme grille que le PDF — colonnes fusionnees, lignes regroupees — mais il
    en rend le texte. Un tableau dont presque tous les mots sont deja dans une
    grille de la page y est ; le reposer le mettrait en double.
    """
    mots = _mots_cles(declare)
    if not mots:
        return True   # rien a poser
    return len(mots & mots_rendus) / len(mots) >= 0.8


def _poser_tableaux_manquants(md_page: str, tableaux: list[str],
                              titres: dict[str, str] | None = None,
                              ) -> tuple[str, int]:
    """Retablit les tableaux que l'extraction a rendus en texte courant.

    Quand une page n'a produit aucun tableau alors que le PDF en declare, ses
    cellules ressortent en lignes de texte, colonnes entrelacees et illisibles.
    On retire ces lignes — reconnues a ce que tous leurs mots figurent dans le
    tableau — et on pose le tableau a leur place. Aucun texte n'est perdu : ce
    qui disparait des lignes se retrouve dans les cellules.

    Un titre que le PDF declare n'en fait jamais partie : voir
    `_reposer_tableaux`, ou le meme piege a coute « Mouvement et
    interactions ».

    **Une continuation se pose meme quand la page n'en dit rien.** Les regles
    ci-dessus mettent une grille a la place du texte qui la doublait ; la
    suite d'un tableau que le modele a purement et simplement sautee n'a
    aucun texte a remplacer, et restait dehors. Elle se pose alors en tete de
    page, la seule place ou une continuation se lit — et la seule d'ou
    `_merge_table_fragments` peut la recoller a la page d'avant.
    """
    if not tableaux:
        return md_page, 0
    # Un seul tableau rendu suffisait a tout arreter — `any(_est_tableau…)`
    # ne demandait pas COMBIEN le PDF en declare. Or ces pages en portent
    # souvent deux ou trois : le modele en grille un, aplatit les autres en
    # listes imbriquees, et la page gardait ses listes sans que rien ne le
    # signale. Mesure sur le programme de cycle 1 : 123 tableaux rendus sur
    # 143 declares, dont la page 39 qui en rend un et en aplatit un autre.
    # On regarde donc chaque tableau declare, et on ne pose que ceux qui
    # manquent.
    deja = [l for l in md_page.split("\n") if _est_tableau(l)]
    mots_rendus = set()
    for l in deja:
        mots_rendus |= _mots_cles(l)
    manquants = [(i, t) for i, t in enumerate(tableaux)
                 if not _deja_rendu(t, mots_rendus)]
    if not manquants:
        return md_page, 0
    # Le rang d'origine se garde : c'est lui, et non le rang parmi les
    # manquants, qui dit laquelle des grilles ouvre la page.
    rangs_declares = [i for i, _ in manquants]
    tableaux = [t for _, t in manquants]
    # Les mots de CHAQUE grille separement : c'est ce qui dit a laquelle
    # appartient une ligne, et donc ou cette grille doit se poser.
    mots_par_table = [_mots_cles(t) for t in tableaux]
    lignes = md_page.split("\n")
    garde: list[str] = []
    posees: set[int] = set()
    retirees = 0
    for ligne in lignes:
        nu = ligne.strip()
        # Une grille deja posee n'est pas du « texte rendu a la place d'un
        # tableau » : ses mots sont evidemment ceux du tableau, et elle etait
        # donc retiree puis remplacee. Sur la page 46, cela faisait 1 rendu +
        # 2 poses = 2 au lieu de 3.
        if _est_tableau(nu):
            garde.append(ligne)
            continue
        nu_sans_marque = re.sub(r"^[#>*\-]+\s*", "", nu)
        mots = _mots_cles(nu_sans_marque)
        protege = bool(titres) and _norme_titre(nu_sans_marque) in titres
        rang = None
        if mots and not protege and len(nu) > 12:
            for i, cles in enumerate(mots_par_table):
                if cles and len(mots & cles) / len(mots) >= 0.8:
                    rang = i
                    break
        if rang is None:
            garde.append(ligne)
            continue
        retirees += 1
        # CHAQUE grille se pose la ou SON contenu se trouvait, et non toutes
        # au premier endroit couvert. Posees ensemble elles etaient
        # adjacentes, et `_merge_table_fragments` n'y voyait plus qu'un
        # tableau coupe : page 15, les trois grilles n'en faisaient qu'une et
        # les deux titres qui les separent restaient seuls, suivis d'un debris.
        if rang not in posees:
            garde.extend(tableaux[rang].split("\n"))
            posees.add(rang)
    # La continuation que la page ne rend NULLE PART se pose quand meme.
    # Les regles ci-dessus mettent une grille a la place du texte qui la
    # doublait ; quand ce texte n'existe pas, il n'y a rien a remplacer et le
    # tableau restait dehors. Page 63 du programme de cycle 1, le modele a
    # saute la fin de cellule heritee de la page 62 : la grille declaree ne
    # se posait pas, la couverture tombait, et `_reparer_depuis_le_pdf`
    # reversait les lignes brutes au bas de la page — le haut d'un tableau
    # rendu en vrac tout en bas.
    #
    # Elle se pose en TETE de page, avant tout le reste : c'est la, et
    # seulement la, qu'une continuation se lit, et c'est ce qui permet a
    # `_merge_table_fragments` de la recoller au tableau de la page d'avant.
    if (0 not in posees and rangs_declares[0] == 0
            and _est_continuation(tableaux[0])):
        garde = tableaux[0].split("\n") + [""] + garde
        posees.add(0)
        logger.info("Continuation de tableau absente de la page : la grille du "
                    "PDF est posee en tete.")
    if not posees:
        return md_page, 0
    logger.info("Tableau rendu en texte courant : %d ligne(s) remplacee(s) par "
                "%d tableau(x) du PDF.", retirees, len(posees))
    return "\n".join(garde), len(posees)


# Un debris de cellule est court, et il ne porte qu'un mot ou deux : une
# phrase du document a toujours plus a dire que le bout de cellule dont elle
# est tombee, et un titre de section a des mots en propre.
_LONGUEUR_DEBRIS_MAX = 40
_MOTS_DEBRIS_MAX = 2

# Longueur minimale, en mots d'au moins quatre lettres, d'une ligne jugee sur
# son texte entier plutot que sur sa brievete. La ligne doit s'y retrouver
# ENTIERE et d'un seul tenant : quatre mots consecutifs communs a deux
# ecritures sont deja une citation, pas une coincidence. En dessous, on ne
# juge plus — « Comparer des quantites. » n'a que deux mots a offrir.
_MOTS_MIN_POUR_DOUBLON = 4


def _texte_compare(fragment: str) -> str:
    """Le texte d'un fragment, reduit a ce qui survit a deux transcriptions.

    Les mots d'au moins quatre lettres, sans accents, separes d'une espace :
    la meme monnaie que `_mots_cles`, mais dans l'ORDRE. C'est ce qui permet
    de demander si une ligne est deja, mot pour mot, dans une cellule — et
    non plus seulement si elle en emprunte le vocabulaire.
    """
    return " ".join(_mots(_BALISE_RE.sub(" ", fragment or "")))


def _occurrences(compare: str, texte: str) -> int:
    """Combien de fois cette suite de mots figure dans ce texte."""
    if not compare:
        return 0
    return (" " + texte + " ").count(" " + compare + " ")


def _double_du_tableau(ligne: str, texte_table: str, texte_pdf: str) -> bool:
    """Cette ligne redit-elle ce que le tableau d'a cote dit deja ?

    **Un tableau du PDF avale la continuation que le modele a laissee
    dehors.** Une page qui commence au milieu d'un tableau porte, en haut, la
    fin d'une cellule coupee par la page precedente. Le PDF la declare comme
    une ligne de tableau et `_merge_table_fragments` la reverse dans la
    cellule d'ou elle vient — mais la version que le modele en avait faite,
    un paragraphe ou une liste a puces, restait seule sur la page, a dire une
    seconde fois ce que le tableau dit deja.

    Mesure sur le programme de cycle 1 : page 40, le paragraphe des boites
    d'allumettes ; page 44, huit puces qui reprennent une a une les cellules
    de la grille recollee ; page 63, une ligne sous le dernier tableau. Ni
    `_reposer_tableaux`, qui demande trois lignes doublees d'affilee, ni la
    regle des debris, qui n'accepte que quarante caracteres, ne les voyaient.

    Deux conditions, et la seconde est celle qui compte.

    La ligne est dans le tableau **mot pour mot et d'un seul tenant** : une
    ligne qui se contente d'emprunter le vocabulaire du tableau, dans un
    autre ordre, est le paragraphe qui l'introduit, et il reste.

    Et **c'est la couche texte du PDF qui dit si c'est un double**, en
    comptant les occurrences. Le document imprime ecrit parfois deux fois la
    meme phrase, une fois dehors et une fois dans le tableau : les « Attendus
    de fin de cycle » des programmes de cycles 2 et 3 sont rappeles en
    intitule de la grille qui les detaille. Sans ce comptage, la regle
    supprimait 26 lignes bien imprimees dans `ensel714_annexe1` et
    `ensel714_annexe2` pour en retirer une seule de trop dans le programme de
    cycle 1. Le tableau doit donc porter, a lui seul, TOUTES les occurrences
    que la page imprime : ce qui reste dehors est alors une transcription en
    trop, jamais une ligne du document.
    """
    compare = _texte_compare(ligne)
    if len(compare.split()) < _MOTS_MIN_POUR_DOUBLON:
        return False
    dans_le_tableau = _occurrences(compare, texte_table)
    if not dans_le_tableau:
        return False
    return dans_le_tableau >= _occurrences(compare, _texte_compare(texte_pdf))


def _retirer_debris_de_tableau(md_page: str,
                               titres: dict[str, str] | None = None,
                               texte_pdf: str | None = None,
                               ) -> tuple[str, int]:
    """Retire les bouts de cellule restes echoues a cote d'un tableau.

    Le modele rend une partie des cellules en intertitres et en puces avant de
    rendre la grille ; `_reposer_tableaux` retire ce double, mais seulement
    au-dela de douze caracteres. En dessous, il restait « Molecules. »,
    « - Isotopes. », « - poids ; » seuls au bas de leur page, sans rien
    autour : un lecteur ne peut pas deviner d'ou ils viennent, et ils viennent
    du tableau d'a cote, ou leur texte figure mot pour mot.

    Trois conditions, parce qu'une suppression est definitive : la ligne
    touche un tableau — lignes vides et restes de mise en page ne comptent pas
    comme une separation — elle n'a aucun mot que le tableau n'ait deja, et
    elle est courte.

    **La brievete n'est pas la seule preuve.** Une ligne longue se retire
    aussi, mais a une condition plus dure, et arbitree par la couche texte du
    PDF : voir `_double_du_tableau`. C'est le cas de la continuation qu'une
    fin de page laisse en haut de la page suivante, que le modele rend en
    paragraphe ou en puces et que la grille du PDF porte deja. Sans
    `texte_pdf`, cette seconde regle ne s'applique pas : rien ne dirait alors
    ce que la page imprime une fois et ce qu'elle imprime deux.

    **Ce que le PDF declare comme titre est epargne, diese ou pas.** Un titre
    Markdown se reconnait tout seul, mais le modele n'en pose pas toujours :
    « Mouvement et interactions », rendu en simple ligne de texte au-dessus du
    tableau de la page 9, remplissait les trois conditions — deux mots, tous
    deux dans le paragraphe d'introduction du tableau — et disparaissait du
    document, alors que l'arbre de structure le declare en titre de rang 3.
    C'est `_caler_titres` qui lui rendra son diese, juste apres.
    """
    lignes = md_page.split("\n")
    blocs = _blocs_de_tableau(lignes)
    if not blocs:
        return md_page, 0
    a_retirer: set[int] = set()
    for debut, fin in blocs:
        table = "\n".join(lignes[debut:fin])
        mots_table = _mots_cles(table)
        if not mots_table:
            continue
        texte_table = _texte_compare(table)
        table_exacte = _mot_a_mot(unescape(_texte_nu(table)))
        pdf_exact = _mot_a_mot(texte_pdf) if texte_pdf is not None else ''
        for sens, depart in ((-1, debut - 1), (1, fin)):
            i = depart
            while 0 <= i < len(lignes):
                nu = lignes[i].strip()
                if not nu or _EST_CHROME_RE.match(nu):
                    i += sens
                    continue
                if _est_tableau(nu) or nu.startswith("#"):
                    break
                if titres and _norme_titre(nu) in titres:
                    break
                # Une cellule courte peut avoir moins de quatre mots-cles.
                # Plusieurs items voisins fournissent une preuve collective,
                # en conservant tous les mots et nombres pour la comparaison.
                if texte_pdf is not None:
                    groupe = []
                    j = i
                    while 0 <= j < len(lignes):
                        item = lignes[j].strip()
                        if not item:
                            j += sens
                            continue
                        marque = re.match(r'^(?:[-*+•]|\d+[.)])\s+(.*)', item)
                        if not marque or (titres and _norme_titre(item) in titres):
                            break
                        contenu = marque.group(1)
                        exact = _mot_a_mot(unescape(contenu))
                        nb_pdf = _occurrences(exact, pdf_exact)
                        if (len(_mots_cles(contenu)) < 2 or nb_pdf == 0
                                or _occurrences(exact, table_exacte) < nb_pdf):
                            break
                        groupe.append(j)
                        j += sens
                    if len(groupe) >= 2:
                        a_retirer.update(groupe)
                        i = j
                        continue
                mots = _mots_cles(_EST_PUCE_RE.sub("", nu))
                court = (len(nu) <= _LONGUEUR_DEBRIS_MAX
                         and 0 < len(mots) <= _MOTS_DEBRIS_MAX
                         and mots <= mots_table)
                if court and texte_pdf is not None:
                    contenu = re.sub(r'^(?:[-*+•]|\d+[.)])\s+', '', nu)
                    exact = _mot_a_mot(unescape(contenu))
                    if _occurrences(exact, pdf_exact) > _occurrences(exact, table_exacte):
                        court = False
                double = (texte_pdf is not None
                          and _double_du_tableau(nu, texte_table, texte_pdf))
                if court or double:
                    a_retirer.add(i)
                    i += sens
                    continue
                break
    if not a_retirer:
        return md_page, 0
    return ("\n".join(l for i, l in enumerate(lignes) if i not in a_retirer),
            len(a_retirer))


def _reposer_tableaux(md_page: str,
                      titres: dict[str, str] | None = None,
                      ) -> tuple[str, int]:
    """Ramene un tableau a l'endroit ou son texte se lit dans la page.

    Un meme tableau se retrouve parfois deux fois dans la page, et les deux
    moteurs y arrivent par des chemins differents. L'extraction hors ligne
    rejette les tableaux a la suite les uns des autres tout en laissant leur
    contenu se deverser en texte courant a leur place d'origine. Le modele de
    vision, lui, transcrit d'abord les cellules en intertitres et en listes,
    puis redonne le tableau : la page dit deux fois la meme chose, une fois
    en vrac et une fois en grille.

    On reconnait le double : une suite de lignes dont presque tous les mots
    sont ceux d'un tableau de la page. Elle disparait, et le tableau prend sa
    place. Rien ne se perd, le texte retire est celui des cellules.

    Sans effet quand les tableaux sont bien places : aucune suite de lignes
    ne double alors leur contenu.

    **Un titre que le PDF declare n'est jamais un doublon**, meme quand tous
    ses mots sont dans le tableau. « Mouvement et interactions », qui coiffe
    le tableau de la page 9 de ce programme, n'a que deux mots et le
    paragraphe d'introduction du tableau les porte tous les deux : il entrait
    dans la suite doublee et disparaissait du document. Les intertitres que
    le modele tire d'une cellule, eux, ne sont declares nulle part — c'est ce
    qui les distingue.
    """
    lignes = md_page.split("\n")
    blocs = _blocs_de_tableau(lignes)
    if not blocs:
        return md_page, 0

    # La page en morceaux : chaque tableau d'un cote, chaque ligne de l'autre.
    items: list[tuple[str, Any]] = []
    curseur = 0
    for debut, fin in blocs:
        items.extend(("ligne", l) for l in lignes[curseur:debut])
        items.append(("bloc", lignes[debut:fin]))
        curseur = fin
    items.extend(("ligne", l) for l in lignes[curseur:])

    consommes: set[int] = set()
    deplacements: list[tuple[int, int, int]] = []  # (bloc, debut, fin) exclus
    for k, (genre, contenu) in enumerate(items):
        if genre != "bloc":
            continue
        mots_table = _mots_cles("\n".join(contenu))
        if not mots_table:
            continue
        premiere = derniere = None
        couvertes = 0
        for j in range(k):
            if j in consommes:
                premiere = derniere = None
                couvertes = 0
                continue
            genre_j, ligne = items[j]
            nu = ligne.strip() if genre_j == "ligne" else "<table>"
            if not nu:
                continue  # une ligne vide ne rompt pas la suite
            autre_bloc = nu.startswith(("<", "|", ">", "!"))
            titre = bool(re.match(r"^#{1,6}\s", nu))
            mots = _mots_cles(re.sub(r"^#{1,6}\s+", "", nu))
            # Un titre qui double une cellule est frequent : le modele prend
            # la premiere cellule d'une ligne pour un intertitre et deroule
            # le reste en liste. On l'accepte dans la suite, mais a condition
            # que tous ses mots soient dans le tableau — un vrai titre de
            # section, qui ne fait qu'annoncer, en a toujours en propre.
            if titres and _norme_titre(re.sub(r"^#{1,6}\s+", "", nu)) in titres:
                couverte = False
            elif titre:
                couverte = bool(mots) and mots <= mots_table
            else:
                couverte = (not autre_bloc and len(nu) > 12 and mots
                            and len(mots & mots_table) / len(mots) >= 0.8)
            if couverte:
                if premiere is None:
                    premiere = j
                derniere = j
                couvertes += 1
            elif autre_bloc or titre or len(nu) > 12:
                if couvertes >= 3:
                    break
                premiere = derniere = None
                couvertes = 0
        if couvertes < 3 or premiere is None or derniere is None:
            continue
        consommes |= set(range(premiere, derniere + 1)) | {k}
        deplacements.append((k, premiere, derniere + 1))

    if not deplacements:
        return md_page, 0

    deplaces = {k for k, _, _ in deplacements}
    vers = {d: k for k, d, _ in deplacements}
    doubles = {j for _, d, f in deplacements for j in range(d, f)}
    out: list[str] = []
    for j, (genre, contenu) in enumerate(items):
        if j in vers:  # le texte double cede la place a son tableau
            out.extend(items[vers[j]][1])
            out.append("")
        if j in doubles or j in deplaces:
            continue
        out.append(contenu if genre == "ligne" else "\n".join(contenu))
    logger.info("Tableau rejete en fin de page : %d tableau(x) remis a leur "
                "place, autant de doubles en texte courant retires.",
                len(deplacements))
    return "\n".join(out), len(deplacements)
