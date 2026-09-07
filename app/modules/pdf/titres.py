"""Hierarchie des titres, lue dans les polices du PDF.

`_TitresPdf` etablit une fois pour toutes les niveaux du document ;
`traitements.titres` s'en sert ensuite pour reecrire le Markdown."""
from __future__ import annotations

from collections import Counter
import logging
from typing import Any

from . import balises, sommaire
from ..traitements import polices
from ..traitements.numerotation import _numero_de_titre
from ..traitements.texte import (NIVEAU_MAX, _EST_PUCE_RE, _FIN_DE_PHRASE,
                                 _est_glyphe_de_puce, _est_titre_de_sommaire,
                                 _norme_titre)


logger = logging.getLogger("atelier.pdf.titres")


def _niveaux_numerotes(doc: Any, titres_pdf: Any) -> dict[int, dict[str, int]]:
    """Confirme des branches numerotees sur la typographie et leurs parents.

    Les numeros de listes, les valeurs des tableaux et les dates ne donnent
    pas de niveaux. Un candidat doit etre un libelle distingue dans le PDF,
    hors tableau et sommaire, puis participer a une branche parent/enfant.
    """
    candidats = []
    for page in doc:
        try:
            lignes = [l for b in page.get_text("dict").get("blocks", [])
                      for l in b.get("lines", [])]
        except Exception:  # noqa: BLE001
            continue
        possibles = []
        hauteurs = [l["bbox"] for l in lignes if l.get("bbox")]
        for ligne in lignes:
            spans = [s for s in ligne.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            texte = ''.join(s['text'] for s in spans).strip()
            numero = _numero_de_titre(texte)
            if not numero or len(texte) > 120:
                continue
            sig = _signature(spans)
            niveau = balises.niveau_de_la_ligne(
                ligne['bbox'], (titres_pdf.balises or {}).get(page.number, []))
            if not (niveau or sig[1] or sig[2] or sig[0] >= titres_pdf.corps + 1.5):
                continue
            if not _seule_sur_sa_ligne(ligne['bbox'], hauteurs):
                continue
            prefixe = titres_pdf.rangs.get(sig, '# ')
            possibles.append((ligne['bbox'], texte, numero[0], niveau or len(prefixe.strip())))
        if not possibles:
            continue
        try:
            boites = [t.bbox for t in page.find_tables()]
        except Exception:  # noqa: BLE001
            boites = []
        renvois = []
        if titres_pdf.a_sommaire:
            try:
                renvois = [l['from'] for l in page.get_links() if l.get('kind') == 1]
                if len(renvois) < sommaire.LIENS_MIN:
                    renvois = []
            except Exception:  # noqa: BLE001
                pass
        for bbox, texte, chemin, niveau in possibles:
            if _dans_un_tableau(bbox, boites):
                continue
            if any(r[0] - 2 <= bbox[0] <= r[2]
                   and r[1] <= (bbox[1] + bbox[3]) / 2 <= r[3] for r in renvois):
                continue
            candidats.append((page.number, _norme_titre(texte), chemin, niveau))

    # Une nouvelle racine ferme la branche precedente. On ne rattache pas
    # un numero isole au souvenir d'une section deja terminee.
    branche: dict[tuple[int, ...], int] = {}
    confirmes: set[int] = set()
    for i, (_, _, chemin, _) in enumerate(candidats):
        if len(chemin) == 1:
            branche = {chemin: i}
        elif chemin[:-1] in branche:
            parent = branche[chemin[:-1]]
            branche = {c: j for c, j in branche.items() if len(c) < len(chemin)}
            branche[chemin] = i
            confirmes.update((parent, i))
    racines = [candidats[i][3] for i in sorted(confirmes) if len(candidats[i][2]) == 1]
    if not racines:
        return {}
    base = Counter(racines).most_common(1)[0][0]
    niveaux: dict[int, dict[str, int]] = {}
    for i in sorted(confirmes):
        page, cle, chemin, _ = candidats[i]
        niveaux.setdefault(page, {})[cle] = min(base + len(chemin) - 1, NIVEAU_MAX)
    return niveaux


def _reconcilier_niveaux(titres: list[tuple[int, tuple]], corps: float,
                         couleur_corps: int) -> list[int]:
    """Repare une echelle de balises contredite par des styles repetes.

    On conserve les niveaux declares sauf si un meme niveau porte a la fois
    de grands titres et des intertitres repetes, a la taille du corps, plus
    petits que ses propres sous-titres. Ce conflit ne peut pas se resoudre
    en faisant confiance au seul numero Hn.

    Dans ce cas, les seuls titres identifies par le PDF sont ordonnes par
    leur typographie. Une pile donne leur profondeur dans la branche en
    cours : un petit intertitre sous un chapitre est son enfant direct, sans
    inventer les niveaux typographiques absents de cette branche.
    """
    compte = Counter(titres)
    contradictoire = any(
        quantite >= 2 and faible[0] <= corps + .5
        and any(n == niveau and forte[0] >= faible[0] + 1.5
                for n, forte in compte)
        and any(n > niveau and enfant[0] >= faible[0] + 1.5
                for n, enfant in compte)
        for (niveau, faible), quantite in compte.items())
    if not contradictoire:
        return [n for n, _ in titres]

    pile: list[tuple] = []
    niveaux = []
    for niveau, sig in titres:
        poids = (-sig[0], not sig[1], not sig[2], sig[3] == couleur_corps, niveau)
        while pile and poids <= pile[-1]:
            pile.pop()
        pile.append(poids)
        niveaux.append(min(len(pile), NIVEAU_MAX))
    return niveaux


def _caler_balises(doc: Any, declarees: dict, corps: float,
                   couleur_corps: int) -> tuple[dict, dict, dict]:
    """Croise chaque titre declare avec les spans qui portent son texte."""
    releves = []
    libelles = {}
    for numero, titres in sorted(declarees.items()):
        try:
            blocs = doc[numero].get_text("dict").get("blocks", [])
        except Exception:  # noqa: BLE001
            continue
        spans = [s for b in blocs for l in b.get("lines", [])
                 for s in l.get("spans", []) if s.get("text", "").strip()]
        for i, (niveau, points) in enumerate(titres):
            propres = [s for s in spans if any(
                s["bbox"][0] - 2 <= x <= s["bbox"][2] + 2
                and s["bbox"][1] <= y <= s["bbox"][3] for x, y in points)]
            if propres:
                releves.append((numero, i, niveau, _signature(propres), min(y for _, y in points)))
                libelles[numero, i] = _norme_titre(' '.join(s['text'] for s in propres))
    releves.sort(key=lambda r: (r[0], r[4], r[1]))
    niveaux = _reconcilier_niveaux([(n, sig) for _, _, n, sig, _ in releves],
                                  corps, couleur_corps)
    sortie = {p: list(titres) for p, titres in declarees.items()}
    signatures: dict[tuple, set[int]] = {}
    corrections = 0
    for (p, i, ancien, sig, _), nouveau in zip(releves, niveaux):
        sortie[p][i] = (nouveau, declarees[p][i][1])
        signatures.setdefault(sig, set()).add(nouveau)
        corrections += ancien != nouveau
    if corrections:
        logger.info("Titres : %d niveau(x) corriges apres contradiction entre balises et typographie.",
                    corrections)
    # Un style qui sert a plusieurs profondeurs ne permet pas de deviner le
    # niveau d'un titre non balise. Seuls les styles sans ambiguite servent.
    stables = ({sig: next(iter(n)) for sig, n in signatures.items() if len(n) == 1}
               if corrections else {})
    # Un dernier style reserve a des mentions repetitives en italique, a
    # la taille du corps, n'ajoute pas un niveau visuel au plan. On conserve
    # ces mentions comme paragraphes emphases. Les titres gras et les
    # branches numerotees restent des titres.
    dernier = max(niveaux, default=0)
    dernieres = [sig for (_, _, _, sig, _), n in zip(releves, niveaux) if n == dernier]
    mentions = {}
    if (dernier > 1 and len(dernieres) >= 3
            and all(not sig[1] and sig[2] and sig[0] <= corps + .5 for sig in dernieres)):
        # Une rubrique distincte en italique peut etre un vrai titre. Seuls
        # les memes libelles repetes dans les branches sont des mentions.
        comptes = Counter((sig, libelles[p, i]) for (p, i, _, sig, _), n
                          in zip(releves, niveaux) if n == dernier)
        for (sig, cle), nombre in comptes.items():
            if nombre >= 3:
                mentions.setdefault(sig, set()).add(cle)
    return sortie, stables, mentions


def _seule_sur_sa_ligne(bbox: Any, toutes: list[Any]) -> bool:
    """Rien d'autre ne partage la hauteur de cette ligne.

    C'est ce qui separe un titre d'un mot gras au milieu d'un paragraphe :
    sur la page « Le Web » du programme de SNT, `langage`, `balises` et
    `HTML` sont gras a l'interieur d'une phrase, et pymupdf en fait autant de
    lignes a lui seules. Elles partagent leur hauteur avec le reste de la
    phrase ; un titre, non.
    """
    milieu = (bbox[1] + bbox[3]) / 2
    for autre in toutes:
        if autre is bbox:
            continue
        # Le partage doit etre mutuel : deux lignes qui se suivent debordent
        # l'une sur l'autre de quelques dixiemes de point, deux fragments
        # d'une meme ligne se contiennent reciproquement.
        if autre[1] <= milieu <= autre[3] \
                and bbox[1] <= (autre[1] + autre[3]) / 2 <= bbox[3]:
            return False
    return True


def _est_titre_gras(spans: list[dict], texte: str,
                    sig: tuple | None = None) -> bool:
    """Une ligne entierement grasse ou italique, courte, sans ponctuation.

    A la taille du corps, c'est tout ce qui reste pour distinguer un titre :
    l'italique y sert autant que le gras — ce programme de mathematiques
    compose ainsi « Contenus » et « Capacites attendues ».
    """
    if sig is None:
        sig = _signature(spans)
    if not (sig[1] or sig[2]):
        return False
    if len(texte) > 60 or texte.endswith(_FIN_DE_PHRASE):
        return False
    return len(texte.split()) <= 8


def _dans_un_tableau(bbox: Any, boites: list[Any]) -> bool:
    return any(b[0] - 2 <= bbox[0] and b[1] - 2 <= bbox[1]
               and bbox[2] <= b[2] + 2 and bbox[3] <= b[3] + 2 for b in boites)


def _titres_de_la_page(page: Any, titres_pdf: Any,
                       ) -> tuple[dict[str, str], dict[str, set[int]]]:
    """Titres de la page et leur niveau, d'apres les polices du PDF.

    Le modele de vision reconnait la plupart du temps qu'une ligne est un
    titre, mais rien dans sa sortie ne dit lesquels il a manques, et il en
    decide le niveau page par page, sans vue d'ensemble : deux titres de meme
    rang dans le document ressortent l'un en `#`, l'autre en `##`. Les
    tailles de police, elles, sont ecrites dans le PDF et valent pour tout le
    document — `rangs` les a deja classees en niveaux.

    Toutes les hierarchies ne se disent pas par la taille. Le programme de
    SNT compose « Introduction » et « Exemples d'activités » en **gras** a la
    taille du corps ; celui de mathematiques de premiere compose « Contenus »
    et « Capacites attendues » en **italique**, a la taille du corps aussi.
    Aucune taille ne les distingue. `_TitresPdf` classe donc des signatures
    (taille, graisse, style) et non des tailles, et ces libelles y prennent
    leur rang comme les autres.

    Les cellules d'en-tete sont grasses elles aussi, et ne sont pas des
    titres : ce que le PDF declare comme tableau est ecarte.
    """
    titres: dict[str, str] = {}
    # Rang d'occurrence des titres, sur la page, texte par texte. Le sommaire
    # reprend chaque titre du document : sans cette position, `_caler_titres`
    # promouvait aussi bien le rappel du sommaire que la section elle-meme, et
    # le plan du document existait en double.
    positions: dict[str, set[int]] = {}
    vus: dict[str, int] = {}
    dans_le_sommaire = False
    rangs = getattr(titres_pdf, "rangs", None) or {}
    corps = getattr(titres_pdf, "corps", 0.0)
    # Les styles utilises en complement sont ancres sur les niveaux des
    # titres declares, et non sur une deuxieme echelle independante.
    declarees = getattr(titres_pdf, "balises", None)
    declares = None if declarees is None else declarees.get(page.number, [])
    if declares is None and not rangs:
        return titres, positions
    try:
        blocs = page.get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return titres, positions
    try:
        boites = [t.bbox for t in page.find_tables()]
    except Exception:  # noqa: BLE001
        boites = []
    # Un sommaire peut continuer sur la page suivante, sans repeter son
    # intitule. Les zones de ses liens internes ne sont pas des sections.
    renvois = []
    if getattr(titres_pdf, "a_sommaire", False):
        try:
            renvois = [l["from"] for l in page.get_links()
                       if l.get("kind") == 1 and l.get("page", -1) >= 0]
            if len(renvois) < sommaire.LIENS_MIN:
                renvois = []
        except Exception:  # noqa: BLE001
            renvois = []
    hauteurs = [
        ligne["bbox"] for b in blocs for ligne in b.get("lines", [])
        if ligne.get("bbox")
        and not _est_glyphe_de_puce(
            "".join(s["text"] for s in ligne.get("spans", [])))
        and any(s["text"].strip() for s in ligne.get("spans", []))]
    for b in blocs:
        for ligne in b.get("lines", []):
            spans = [s for s in ligne.get("spans", []) if s["text"].strip()]
            if not spans:
                continue
            texte = "".join(s["text"] for s in ligne.get("spans", [])).strip()
            # Un titre est court : une phrase de corps composee plus grand
            # reste une phrase, et on ne veut pas la promouvoir.
            if not (3 <= len(texte) <= 120):
                continue
            bbox = ligne.get("bbox", (0,) * 4)
            niveau_declare = (balises.niveau_de_la_ligne(bbox, declares)
                              if declares is not None else None)
            # Les cellules sont rendues en HTML, que le calage Markdown ne
            # parcourt pas. Leur libelle ne doit pas consommer l'occurrence
            # d'un vrai titre identique situe apres le tableau.
            if niveau_declare is None and _dans_un_tableau(bbox, boites):
                continue
            cle = _norme_titre(texte)
            rang_occurrence = vus.get(cle, 0)
            vus[cle] = rang_occurrence + 1
            if any(r[0] - 2 <= bbox[0] <= r[2]
                   and r[1] <= (bbox[1] + bbox[3]) / 2 <= r[3] for r in renvois):
                positions.setdefault(cle, set())
                continue
            if _est_titre_de_sommaire(texte):
                dans_le_sommaire = True
                continue
            numerote = getattr(titres_pdf, "numerotes", {}).get(page.number, {}).get(cle)
            if numerote and not dans_le_sommaire:
                titres[cle] = '#' * numerote + ' '
                positions.setdefault(cle, set()).add(rang_occurrence)
                continue
            if cle in getattr(titres_pdf, "mentions_italiques", {}).get(_signature(spans), set()):
                positions.setdefault(cle, set())
                continue
            if declares is not None:
                # Sous un titre de sommaire, rien n'est un titre : Word
                # balise le renvoi exactement comme la section qu'il annonce,
                # l'arbre ne les distingue pas. Le sommaire fermant sa page,
                # `dans_le_sommaire` n'a pas a etre releve ensuite.
                if dans_le_sommaire:
                    positions.setdefault(cle, set())
                    continue
                niveau = niveau_declare
                if niveau is None:
                    sig = _signature(spans)
                    niveau = getattr(titres_pdf, "rangs_balises", {}).get(sig)
                    if not (niveau and _est_titre_gras(spans, texte, sig)
                            and _seule_sur_sa_ligne(ligne.get("bbox", (0,) * 4), hauteurs)
                            and not _dans_un_tableau(ligne.get("bbox", (0,) * 4), boites)):
                        continue
                # L'espace finale fait partie du prefixe : `_caler_titres`
                # reecrit la ligne en `prefixe + texte`, sans en ajouter.
                titres[cle] = "#" * niveau + " "
                positions.setdefault(cle, set()).add(rang_occurrence)
                continue
            sig = _signature(spans)
            prefixe = rangs.get(sig)
            # Sous un titre de sommaire, rien n'est un titre : ce sont les
            # sections annoncees, et la plupart vivent sur d'autres pages, ou
            # leur vraie occurrence sera reconnue. L'ensemble vide dit
            # « jamais un titre sur cette page ».
            if dans_le_sommaire and not prefixe:
                positions.setdefault(cle, set())
                continue
            if not prefixe:
                continue
            bbox = ligne.get("bbox", (0, 0, 0, 0))
            # Un rang qui ne tient qu'a la graisse ou a l'italique se merite :
            # a la taille du corps, ces attributs servent aussi a mettre un
            # mot en valeur au fil d'une phrase, ou a coiffer un tableau.
            if sig[0] < corps + 1.5 and not (
                    _est_titre_gras(spans, texte, sig)
                    and _seule_sur_sa_ligne(bbox, hauteurs)
                    and not _dans_un_tableau(bbox, boites)):
                continue
            titres[cle] = prefixe
            positions.setdefault(cle, set()).add(rang_occurrence)
            # Un vrai titre clot la zone du sommaire.
            dans_le_sommaire = False
    return titres, positions


def _taille_du_corps(doc: Any) -> float:
    """Taille de police dominante du document, en volume de texte."""
    return _dominant(doc, lambda s: round(s["size"], 1)) or 0.0


def _couleur_du_corps(doc: Any) -> int:
    """Couleur dominante du texte : celle qui ne veut rien dire.

    Un document qui se sert de la couleur s'en sert pour **distinguer** : le
    corps garde la couleur ordinaire, un titre s'en ecarte. C'est ce qui
    permet de ranger deux titres de meme taille et de meme graisse — celui
    qui porte une couleur a lui passe devant celui qui garde celle du corps.
    """
    couleur = _dominant(doc, lambda s: s.get("color", 0))
    return 0 if couleur is None else int(couleur)


def _dominant(doc: Any, cle: Any) -> Any:
    """Valeur la plus portee par le texte du document, au volume."""
    compte: Counter = Counter()
    for page in doc:
        try:
            blocs = page.get_text("dict")["blocks"]
        except Exception:  # noqa: BLE001
            continue
        for b in blocs:
            for ligne in b.get("lines", []):
                for s in ligne.get("spans", []):
                    if s["text"].strip():
                        compte[cle(s)] += len(s["text"])
    return compte.most_common(1)[0][0] if compte else None


def _signature(spans: list[dict]) -> tuple[float, bool, bool, int]:
    """Ce qui distingue une ligne a l'oeil : taille, graisse, style, couleur.

    **La couleur porte un niveau.** Le programme de specialite de mathematiques
    de premiere compose ses cinq grands themes — Algebre, Analyse, Geometrie,
    Probabilites et statistiques, Algorithmique — en 10 pt gras BLEU, et leurs
    sous-parties — Derivation, Trigonometrie, Geometrie reperee — en 10 pt
    gras NOIR. Meme taille, meme graisse : sans la couleur, un chapitre et sa
    subdivision se lisaient au meme rang.
    """
    return (round(max(s["size"] for s in spans), 1),
            all(s.get("flags", 0) & polices._GRAS_FLAG for s in spans),
            all(s.get("flags", 0) & polices._ITALIQUE_FLAG for s in spans),
            spans[0].get("color", 0))


class _TitresPdf:
    """Decide ce qui est un titre, d'apres la police du PDF.

    Remplace l'heuristique de pymupdf4llm, qui promeut en titre toute ligne
    composee autrement que le corps — y compris les puces, dont le glyphe
    vient d'une police symbole.

    **Un niveau ne se dit pas toujours par la taille.** Le programme de
    specialite de mathematiques de premiere compose ses libelles les plus
    repetes — « Contenus », « Capacites attendues », « Demonstrations » — en
    8,5 pt *italique*, la taille meme du corps. Classer les seules tailles les
    rendait invisibles : `_caler_titres` n'avait rien a leur opposer, et le
    modele leur donnait un rang different a chaque page — « Demonstrations »
    est sorti en `##`, `###` et `####` dans le meme document.

    Le rang porte donc sur la **signature** (taille, graisse, style), et se
    classe parmi les seules lignes qui ressemblent a des titres. C'est ce que
    faisait de meme le moteur Document AI, retire depuis, qui ne classait que
    les blocs types « titre » par l'OCR — et c'est pourquoi ce moteur, seul,
    rendait ce document juste.
    """

    def __init__(self, doc: Any) -> None:
        self.corps = _taille_du_corps(doc)
        self.couleur_du_corps = _couleur_du_corps(doc)
        # Ce qui suit devine une hierarchie ; un PDF balise, lui, la declare.
        # `None` quand le document n'en a pas — c'est le cas des deux seuls
        # PDF du corpus qui ne viennent pas de Word.
        self.balises = balises.hierarchie(doc)
        self.a_sommaire = sommaire.declare_un_sommaire(doc)
        self.rangs_balises: dict[tuple, int] = {}
        self.mentions_italiques: dict[tuple, set[str]] = {}
        self.numerotes: dict[int, dict[str, int]] = {}
        if self.balises:
            self.balises, self.rangs_balises, self.mentions_italiques = _caler_balises(
                doc, self.balises, self.corps, self.couleur_du_corps)
        # Les niveaux ne sont pas fixes d'avance : on releve les signatures
        # employees par les lignes qui se presentent comme des titres, et on
        # les classe. Le document donne ainsi sa propre hierarchie.
        self.rangs: dict[tuple, str] = {}
        # Les rangs de taille seuls, pour `get_header_id` : il ne recoit qu'un
        # span, jamais la ligne, et ne peut donc pas verifier qu'un fragment
        # gras au fil d'une phrase n'est pas un titre.
        self.rangs_taille: dict[float, str] = {}
        if not self.corps:
            return
        # Signature -> textes qui la portent, et l'inverse : un sommaire
        # reprend les titres du document dans une composition plus discrete,
        # et ce rappel n'est pas un niveau de plus. Sur ce programme de
        # mathematiques, « Préambule » et « Programme » figurent en 11 pt gras
        # comme sections et en 8,5 pt gras dans le sommaire ; sans ce tri, le
        # sommaire s'offrait un rang et repoussait les vrais libelles d'un
        # cran, jusqu'a `######`.
        textes: dict[tuple, set[str]] = {}
        vues: dict[str, set[tuple]] = {}
        for page in doc:
            try:
                blocs = page.get_text("dict")["blocks"]
            except Exception:  # noqa: BLE001
                continue
            lignes = [l for b in blocs for l in b.get("lines", [])]
            hauteurs = [l["bbox"] for l in lignes
                        if l.get("bbox")
                        and not _est_glyphe_de_puce(
                            "".join(s["text"] for s in l.get("spans", [])))
                        and any(s["text"].strip() for s in l.get("spans", []))]
            for ligne in lignes:
                spans = [s for s in ligne.get("spans", []) if s["text"].strip()]
                if not spans:
                    continue
                texte = "".join(s["text"] for s in spans).strip()
                if not (3 <= len(texte) <= 120) or _EST_PUCE_RE.match(texte):
                    continue
                if _est_titre_de_sommaire(texte):
                    continue
                sig = _signature(spans)
                retenue = sig[0] >= self.corps + 1.5
                # A la taille du corps, seules la graisse et l'italique
                # peuvent faire un titre — et seulement sur une ligne courte,
                # sans ponctuation finale, posee seule. Sans ces trois
                # garde-fous, la moitie du corps deviendrait une hierarchie.
                if not retenue:
                    retenue = bool(
                        (sig[1] or sig[2])
                        and _est_titre_gras(spans, texte, sig)
                        and _seule_sur_sa_ligne(ligne.get("bbox", (0,) * 4),
                                                hauteurs))
                if retenue:
                    cle = _norme_titre(texte)
                    textes.setdefault(sig, set()).add(cle)
                    vues.setdefault(cle, set()).add(sig)

        # Du plus grand au plus petit ; a taille egale, le gras passe devant
        # l'italique, qui passe devant le maigre.
        def poids(s: tuple) -> tuple:
            # A taille, graisse et style egaux, une couleur qui n'est pas
            # celle du corps marque le rang superieur : c'est ainsi que ce
            # document distingue un grand theme de ses subdivisions.
            return (-s[0], not s[1], not s[2], s[3] == self.couleur_du_corps)

        # Une signature ne compte que si elle porte au moins un titre qu'on ne
        # trouve pas ailleurs, composé plus fort : sinon elle ne fait que
        # rappeler ce qui est deja dit — c'est un sommaire.
        signatures = {
            sig for sig, cles in textes.items()
            if any(min(vues[c], key=poids) == sig for c in cles)}
        ordre = sorted(signatures, key=poids)
        for rang, sig in enumerate(ordre[:NIVEAU_MAX],
                                   start=1):
            self.rangs[sig] = "#" * rang + " "
            if sig[0] >= self.corps + 1.5 and sig[0] not in self.rangs_taille:
                self.rangs_taille[sig[0]] = "#" * rang + " "

        self.numerotes = _niveaux_numerotes(doc, self)

    def get_header_id(self, span: dict, page: Any = None) -> str:
        texte = (span.get("text") or "").strip()
        if not texte or _EST_PUCE_RE.match(texte):
            return ""
        return self.rangs_taille.get(round(span.get("size", 0), 1), "")
