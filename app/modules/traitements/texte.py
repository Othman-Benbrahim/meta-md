"""Reperes de texte partages par tous les traitements.

Rien ici ne connait le PDF ni le moteur : ce sont des fonctions de
chaine a chaine, et les expressions rationnelles qui les servent."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .numerotation import _numero_de_titre


# Longueur maximale d'une phrase d'annonce toleree avant l'emballage.
PREAMBULE_MAX = 200


def _strip_code_fence_wrapper(text: str) -> str:
    """Enleve un emballage ```markdown ... ``` que les VLMs ajoutent parfois.

    Le modele fait parfois preceder cet emballage d'une phrase d'annonce —
    « Voici la conversion en Markdown de la page fournie : ». La fonction
    abandonnait alors des sa premiere ligne, la clotûre survivait, et toute la
    page s'affichait en bloc de code, tableaux HTML compris.

    **Ce qui suit la clotûre est conserve.** Exiger que l'emballage enveloppe
    tout le reste etait trop strict : le modele ajoute volontiers sa
    description de figure APRES le bloc — « > **Figure : Exemple de
    comparaison de quantites** » — et la page entiere ressortait alors en bloc
    de code, tableaux HTML compris. Mesure sur la page 40 du programme de
    cycle 1 : deux clotûres, la seconde a 7 986 caracteres sur 8 428.

    Ce qui protege un bloc de code legitime n'est donc plus sa position mais
    son etiquette : on ne deballe que `markdown`, `md`, `html` ou rien du
    tout. Un extrait de code dans un document pedagogique porte le nom de son
    langage, et reste intact.
    """
    t = (text or "").strip()
    if not t.startswith("```"):
        tete, sep, reste = t.partition("```")
        annonce = tete.strip()
        candidat = sep + reste
        if (sep and len(annonce) <= PREAMBULE_MAX
                and annonce.count('\n') <= 1
                and re.match(r"^```(?:markdown|md|html)?\s*\n", candidat, re.I)):
            t = candidat
        else:
            return t
    # L'emballage se ferme, et quelque chose le suit : on garde les deux.
    m = re.match(r"^```(?:markdown|md|html)?[^\S\n]*\n(.*?)\n```[^\S\n]*\n(.+)$",
                 t, re.DOTALL | re.I)
    if m:
        return m.group(1).rstrip() + "\n\n" + m.group(2).lstrip()
    # Fence fermee proprement
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", t, re.DOTALL)
    if m:
        return m.group(1)
    # Fence ouverte mais pas fermee (VLM tronque)
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*)$", t, re.DOTALL)
    if m:
        return m.group(1).rstrip("`").rstrip()
    return t


# Restes de mise en page qui s'intercalent a une fin de page : une image
# seule (bandeau, logo), un filet horizontal. Ce n'est pas du contenu.
_EST_CHROME_RE = re.compile(r"^(!\[[^\]]*\]\([^)]*\)|[-*_]{3,}|<!--)")

# Repere de provenance, pose a chaque frontiere de page. Invisible dans un
# lecteur Markdown, il permet a la segmentation de citer la page d'origine
# d'un extrait — et donc d'ouvrir le PDF au bon endroit (`...pdf#page=7`).
MARQUE_PAGE = "<!-- page %d -->"
_MARQUE_PAGE_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->$")


def _assembler_pages(parts: list[str]) -> str:
    """Colle les pages en un document, chacune precedee de son repere."""
    morceaux = []
    for numero, texte in enumerate(parts, start=1):
        morceaux.append(MARQUE_PAGE % numero)
        morceaux.append((texte or "").strip())
    return "\n\n".join(morceaux)


def _est_titre_visuel(ligne: str) -> bool:
    """La ligne se presente-t-elle comme un titre : diese, gras, capitales ?"""
    nu = ligne.strip()
    if nu.startswith("<"):
        return False  # un tableau tout en capitales n'est pas un bandeau
    if re.match(r"^#{1,6}\s+\S", nu):
        return True
    if re.match(r"^(\*\*|__).+(\*\*|__)$", nu):
        return True
    lettres = [c for c in nu if c.isalpha()]
    return bool(lettres) and all(c.isupper() for c in lettres)
# **Une vraie balise commence par une lettre.** `<[^>]+>` paraissait suffire,
# mais ces programmes ecrivent des inegalites : `$f(x) < k$`. Le `<` ouvrait
# une fausse balise que la regex refermait au `>` suivant — sur la page 12 du
# programme de mathematiques de seconde, 2 242 caracteres de texte
# disparaissaient d'un coup. `_couverture` n'y voyait plus que 24 mots sur
# 205 et criait a la page perdue, sur une page complete a 100 %.
_BALISE_RE = re.compile(r"<!--.*?-->|</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>",
                        re.DOTALL)


def _texte_nu(html: str) -> str:
    """Texte d'un fragment HTML, pour comparer deux en-tetes."""
    return re.sub(r"\s+", " ", _BALISE_RE.sub(" ", html or "")).strip().lower()


def _echapper(texte: str) -> str:
    return (texte or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_MOT_RE = re.compile(r"\w{4,}", re.UNICODE)


def _sans_diacritiques(texte: str) -> str:
    """Le texte prive de ses accents, cedilles et autres signes diacritiques.

    **Le modele modernise l'orthographe, le PDF ne la modernise pas.** Ces
    programmes sont composes en orthographe rectifiee — « boites »,
    « connaitre », « reconnaitre » — et le modele les rend accentuees :
    « boîtes », « connaître », « reconnaître ». Ce n'est pas une faute de
    transcription, les deux graphies disent le meme mot, et rien de ce qui
    compare deux lectures d'une meme ligne ne doit s'y arreter.

    Le prix de l'oubli se lit dans le document. Page 63 du programme de
    cycle 1, « Reconnaitre les pictogrammes présents sur les contenants »
    figure dans une cellule du tableau et se declarait pourtant absente de la
    page : le modele l'avait ecrit « Reconnaître », et la ligne revenait en
    double sous le tableau. Meme cause page 40, ou le paragraphe des boites
    d'allumettes restait a cote du tableau qui le porte deja.

    On ne va pas plus loin que les diacritiques : « oeuf » et « œuf » restent
    deux mots. Les rapprocher demanderait une table de translitteration, et
    aucune mesure du corpus ne la reclame.
    """
    decompose = unicodedata.normalize("NFD", texte or "")
    return unicodedata.normalize(
        "NFC", "".join(c for c in decompose if not unicodedata.combining(c)))


def _mots(texte: str) -> list[str]:
    """Les mots d'au moins quatre lettres, en minuscules et sans accents.

    Un seul passage pour toutes les comparaisons du pipeline : couverture
    d'une page, empreinte d'une ligne, vocabulaire d'un fragment. Les deux
    cotes de chaque comparaison passent par ici, donc la normalisation ne
    peut pas en avantager un.
    """
    return _MOT_RE.findall(_sans_diacritiques((texte or "").lower()))


def _couverture(texte_pdf: str, md: str) -> float:
    """Part des mots de la couche texte du PDF qu'on retrouve dans le Markdown.

    Sert de garde-fou : une page rendue par un modele peut perdre un titre,
    un paragraphe, voire une section entiere, et rien dans la sortie ne le
    signale. La couche texte du PDF, elle, est gratuite et fait foi.

    Retourne 1.0 quand il n'y a rien a comparer (page scannee, sans couche
    texte) : le garde-fou se tait plutot que de crier au loup.
    """
    mots = _mots(texte_pdf)
    if len(mots) < 40:
        return 1.0
    # Comptage par occurrence, et non par vocabulaire : le paragraphe perdu
    # d'une page emploie souvent les mots du tableau qui le suit, si bien
    # qu'une simple appartenance donnait encore 85 % apres la disparition
    # d'un titre et de son introduction.
    attendus = Counter(mots)
    # Les balises sont retirees avant comptage : `table`, `thead` et `colspan`
    # sont des mots pour `_MOT_RE`, et un document qui parle de tableaux se
    # verrait crediter d'un texte qu'il n'a pas rendu.
    rendus = Counter(_mots(_BALISE_RE.sub(" ", md or "")))
    retrouves = sum(min(n, rendus.get(mot, 0)) for mot, n in attendus.items())
    return retrouves / len(mots)


# Mise en forme Markdown a ecarter pour comparer deux ecritures d'une meme
# ligne : un lien rendu en `[texte](cible)`, des marques d'emphase, un diese
# de titre, un chevron de citation.
_LIEN_MD_RE = re.compile(r"!?\[([^\]\n]*)\]\([^)\n]*\)")
_MARQUES_EMPHASE_RE = re.compile(r"[*_`]+")


def _empreinte(texte: str) -> str:
    """Signature d'une ligne, insensible a sa mise en forme et a sa typographie.

    Deux transcriptions d'un meme pied de page ne se ressemblent pas
    caractere pour caractere : l'apostrophe change de forme, le modele pose
    un lien la ou le PDF ecrit une URL nue, et `poser_emphases` a pu glisser
    une italique au milieu d'un mot (`www.edu*cation*.gouv.fr`, vu sur ce
    document). Ne restent ici que les mots d'au moins quatre lettres, en
    minuscules : ce qui subsiste d'une ligne quand on lui retire sa mise en
    forme.
    """
    nu = _LIEN_MD_RE.sub(r"\1", texte or "")
    nu = _MARQUES_EMPHASE_RE.sub("", nu)
    return " ".join(_mots(nu))


def _recoller_paragraphes(bloc: list[str]) -> list[str]:
    """Rend au texte du PDF ses paragraphes, coupes ligne a ligne.

    pymupdf rend une ligne par ligne composee : le paragraphe reinsere
    arrivait hache, quand le reste de la page tient chaque paragraphe sur une
    ligne. Le rendu ne changeait pas — Markdown recolle les lignes d'un meme
    paragraphe — mais la source se lisait mal, et un `.md` se relit.

    Une ligne qui ne finit pas sur une ponctuation de fin de phrase appelle la
    suivante. Le critere est prudent : au pire deux phrases d'un meme
    paragraphe se retrouvent separees, ce qui se lit encore.
    """
    recolle: list[str] = []
    for ligne in bloc:
        nu = ligne.strip()
        if not nu:
            continue
        if (recolle and not nu.startswith("- ")
                and not recolle[-1].endswith(_FIN_DE_PHRASE)
                and not recolle[-1].startswith("- ")):
            recolle[-1] = recolle[-1] + " " + nu
        else:
            recolle.append(nu)
    return recolle


# Un titre en gras a la taille du corps ne se distingue d'une phrase mise en
# valeur que par sa forme : court, sans ponctuation finale, et seul sur sa
# ligne. Aucune de ces conditions ne suffit ; les trois ensemble, oui.
_FIN_DE_PHRASE = (".", ",", ";", ":", "!", "?")


def _est_glyphe_de_puce(texte: str) -> bool:
    """Une puce seule : `-`, `•`, ou le glyphe d'une police symbole.

    Ces documents composent leurs puces en Wingdings, dont les caracteres
    tombent dans la zone privee d'Unicode (``). Une puce occupe sa
    propre ligne aux yeux de pymupdf ; sans cela, le sous-titre qu'elle
    annonce ne serait jamais « seul sur sa ligne ».
    """
    nu = texte.strip()
    return bool(nu) and len(nu) <= 2 and all(
        c in "-•●▪*·" or 0xE000 <= ord(c) <= 0xF8FF for c in nu)


# Les marques d'emphase sortent avant la comparaison. `\w` compte le souligne
# comme une lettre : `_Contenus_` se normalisait en `_contenus_` et ne
# retrouvait jamais le `contenus` lu dans le PDF. L'extraction rend justement
# les libelles italiques de ce programme sous cette forme.
_MARQUES_MD_RE = re.compile(r"[*_`]")


def _norme_titre(texte: str) -> str:
    nu = _MARQUES_MD_RE.sub("", texte)
    # Un nombre ou un petit mot distingue des titres (4 ans / 5 ans,
    # avec / sans). Le vocabulaire filtre employe pour la couverture des
    # paragraphes n'est donc pas une cle de titre. Les spans PDF peuvent
    # aussi coller un nombre a son unite sans espace.
    nu = re.sub(r'(?<=\d)(?=[^\W\d_])', ' ', nu)
    numero = _numero_de_titre(nu)
    if numero:
        chemin, libelle = numero
        return '.'.join(map(str, chemin)) + '. ' + _mot_a_mot(libelle)
    return _mot_a_mot(nu)


# La puce en tete d'un item. `/Lbl` la porte quand Word l'a balisee — 435
# items du corpus — mais les 3333 autres la gardent dans leur texte, ou la
# composent en artefact. Elle part dans tous les cas : c'est `<li>` qui la
# rend, et « <li>- texte</li> » affiche deux puces.
# Les glyphes de la zone privee d'Unicode sont les puces Wingdings de ces
# documents ; tiret et points sont les puces ordinaires. Le « o » du second
# niveau n'y figure pas : c'est une lettre, et « o ordre » se rencontre.
_PUCE_EN_TETE_RE = re.compile(r"^(?:[-•●▪−–—*]|[-])\s*")


def _mots_cles(texte: str) -> set[str]:
    """Vocabulaire d'un fragment, balises HTML exclues.

    Ces ensembles servent a reconnaitre deux fois le meme contenu — un
    tableau et son double en texte courant. Compter `table`, `thead` ou
    `colspan` comme des mots ferait se ressembler deux tableaux qui n'ont
    rien a voir.
    """
    return set(_mots(_BALISE_RE.sub(" ", texte or "")))

# Puces composees dans une police symbole : le caractere tombe dans la zone
# privee d'Unicode. pymupdf y voit une police differente du corps et en fait
# un titre ; c'est une puce de liste.
# `−` (U+2212, le vrai signe moins) sert de puce dans les programmes
# officiels : 193 items du programme de mathematiques de premiere commencent
# ainsi, et aucun ne ressortait en liste. Les demi-cadratins suivent, pour la
# meme raison.
_EST_PUCE_RE = re.compile(r"^[-•●▪−–—]\s*")


# Titres qui coiffent une table des matieres. Le bloc qu'ils annoncent
# reprend mot pour mot les titres du document : le compter comme une section
# lui donnait un rang a lui seul — « Sommaire » en 12 pt gras poussait tout le
# reste d'un cran, « Preambule » se retrouvait en `###` au lieu de `##`.
_TITRES_DE_SOMMAIRE = frozenset((
    "sommaire", "table des matieres", "table des matières", "plan"))
_PONCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _mot_a_mot(texte: str) -> str:
    """La ligne entiere, en minuscules, sans ponctuation ni marque Markdown."""
    nu = _PONCTUATION_RE.sub(
        " ", _sans_diacritiques(_MARQUES_MD_RE.sub("", texte or "").lower()))
    return " ".join(nu.split())


def _est_titre_de_sommaire(texte: str) -> bool:
    """Cette ligne coiffe-t-elle une table des matieres ?

    La comparaison porte sur la ligne ENTIERE, mot a mot. Passer par
    l'ancienne cle de titre, qui ne gardait que les mots d'au moins quatre lettres,
    reduisait « du plan » a « plan » : sur la page 21 du programme d'arts
    plastiques, « L'ecriture du plan » — une cellule de tableau, coupee en
    deux par la mise en page — ouvrait un sommaire imaginaire, et tous les
    titres de la page suivaient l'ecart. Un vrai sommaire s'annonce d'un mot
    et n'a rien autour.
    """
    return _mot_a_mot(texte) in _SOMMAIRES_NORMALISES


_SOMMAIRES_NORMALISES = frozenset(_mot_a_mot(x) for x in _TITRES_DE_SOMMAIRE)


# Markdown ne va pas au-dela de six niveaux ; les tailles suivantes s'y
# rangent toutes.
NIVEAU_MAX = 6
