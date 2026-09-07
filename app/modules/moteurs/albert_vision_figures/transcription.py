"""Moteur `albert_vision` : transcription page par page par un VLM.

Le seul module du dossier qui appelle le reseau pour ce moteur. Tout
ce qu'il fait ensuite du Markdown vient de `traitements` et de `pdf`,
et le decoupage des figures de `moteurs.albert_vision_figures`."""
from __future__ import annotations

import base64
import logging
import re
import time
from pathlib import Path
from typing import Any

import pymupdf  # type: ignore[import-untyped]
import httpx

from ...api import ratelimit
from ...pdf import balises
from ...pdf.lignes import (_items_de_la_page, _lignes_du_pdf,
                          _reparer_depuis_le_pdf, _retirer_liens_inventes,
                          poser_liens_du_pdf)
from ...pdf.pagination import _retirer_pagination
from ...pdf import sommaire as sommaire_pdf
from ...pdf.tableaux import _tableaux_de_la_page
from ...pdf.titres import _TitresPdf, _titres_de_la_page
from ...traitements import polices
from ...traitements.boilerplate import (_remove_repeated_boilerplate, _retirer_bandeaux,
                                       _retirer_images_du_modele)
from ...traitements.listes import _caler_listes
from ...traitements.pipes import _pipes_en_html
from ...traitements.tableaux_html import (_nb_colonnes,
                                         _normaliser_tableaux_html,
                                         _retirer_clotures_de_tableaux,
                                         tableaux_eparpilles)
from ...traitements.tableaux_reparation import (_merge_table_fragments,
                                               _poser_tableaux_manquants,
                                               _rappel_tableau_ouvert,
                                               _remplacer_tableaux, _reposer_tableaux,
                                               _retirer_debris_de_tableau)
from ...traitements.texte import (_assembler_pages, _couverture,
                                 _strip_code_fence_wrapper, _texte_nu)
from ...traitements.titres import _caler_titres
from ..albert import ALBERT_BASE_URL, DEFAULT_VISION_MODEL
from .prompt import VISION_PROMPT
from .decoupage import _bandeaux_graphiques, _poser_figures


logger = logging.getLogger("atelier.moteurs.albert_vision.transcription")
VISION_DPI = 150
# Les pages denses meritent une attente longue avant tout repli. Le delai
# de lecture est distinct de celui de connexion ; les pannes transitoires
# donnent lieu a trois tentatives au total, sans boucle infinie.
VISION_PAGE_TIMEOUT = 600.0
VISION_TENTATIVES = 3
# Part minimale du texte d'une page qu'on doit retrouver dans le Markdown
# rendu, quand le PDF a une couche texte pour en juger. En dessous, la page
# est refaite sans le rappel de tableau, et on garde la meilleure des deux :
# une fausse alerte coute un appel, jamais la qualite. Le seuil laisse de la
# marge aux differences de transcription (coupures de mots, ligatures) sans
# laisser passer la disparition d'un titre et de son paragraphe, mesuree a
# 74 % sur le cas reel qui a motive ce garde-fou.
COUVERTURE_MIN = 0.85
# Ce qu'on demande a un modele qui ne voit QUE la figure. Deux exigences :
# decrire ce qui est represente, et rester bref — cette phrase part dans le
# `alt` de l'image, ou elle sert d'abord a retrouver la figure dans un index.
PROMPT_FIGURE = (
    "Decris cette illustration extraite d'un document pedagogique, en francais, "
    "en une a trois phrases. Dis ce qu'elle represente et ce qu'elle montre : "
    "type de schema, elements figures, texte lisible, relations entre eux. "
    "N'ecris que la description, sans introduction, sans guillemets, sans "
    "Markdown, et n'invente rien qui ne soit visible."
)
# Au-dela, ce n'est plus une legende : le `alt` d'une image se lit dans une
# infobulle et dans un index, pas dans un paragraphe.
FIGURE_DESCRIPTION_MAX = 400


class _VisionHTTP(Exception):
    """Reponse non-200 d'Albert, remontee jusqu'au traitement de la page."""

    def __init__(self, code: int, body: str) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code
        self.body = body


def _diagnostic_vision(exc: Exception) -> dict:
    if isinstance(exc, _VisionHTTP):
        causes = {408: "délai dépassé côté serveur", 429: "limite de requêtes atteinte",
                  500: "erreur interne du serveur", 502: "erreur de passerelle",
                  503: "service indisponible", 504: "délai dépassé côté passerelle",
                  401: "authentification refusée", 403: "accès refusé"}
        return {"cause": causes.get(exc.code, "requête refusée par le serveur"),
                "http": exc.code, "erreur": type(exc).__name__}
    causes = ((httpx.ConnectTimeout, "délai de connexion dépassé"),
              (httpx.ReadTimeout, "délai d'attente de la réponse dépassé"),
              (httpx.WriteTimeout, "délai d'envoi de la page dépassé"),
              (httpx.PoolTimeout, "connexion locale indisponible dans le délai"),
              (httpx.ConnectError, "échec de connexion au service"),
              (httpx.ReadError, "connexion interrompue pendant la réception"))
    return {"cause": next((label for cls, label in causes if isinstance(exc, cls)),
                          "incident de transport HTTP"), "erreur": type(exc).__name__}


def _avec_reprises(appeler: Any, annoncer: Any = None, observer: Any = None) -> Any:
    """Reessaie une panne transitoire, jamais un refus d'acces ou de requete.

    Les limites 429 et leur Retry-After sont deja gerees par ratelimit.
    Une reponse complete insuffisante releve du controle de couverture.
    """
    for tentative in range(1, VISION_TENTATIVES + 1):
        if annoncer:
            annoncer(tentative)
        debut = time.monotonic()
        try:
            resultat = appeler()
        except (httpx.TransportError, _VisionHTTP) as exc:
            transitoire = (isinstance(exc, httpx.TransportError)
                           or exc.code in (408, 500, 502, 503, 504))
            reprise = transitoire and tentative < VISION_TENTATIVES
            if observer:
                observer({"tentative": tentative, "secondes": round(time.monotonic() - debut, 1),
                          "resultat": "nouvelle tentative" if reprise else "échec définitif de l'appel",
                          **_diagnostic_vision(exc)})
            if not reprise:
                raise
            attente = 5 * tentative
            logger.warning('Vision : tentative %d/%d interrompue (%s), reprise dans %ds.',
                           tentative, VISION_TENTATIVES, type(exc).__name__, attente)
            time.sleep(attente)
        else:
            if observer:
                observer({"tentative": tentative, "secondes": round(time.monotonic() - debut, 1),
                          "resultat": "réponse reçue", "http": 200})
            return resultat


def _empreinte_de_cellule(texte: str) -> str:
    """Ce qui sert a reconnaitre qu'une ligne de texte est deja dans un tableau."""
    return " ".join(re.sub(r"\s+", " ", texte or "").split()).strip().lower()


_PUCE_DE_LIGNE = re.compile(r"^\s*[-–—−•·*]\s+")
_FIN_DE_PHRASE = (".", ";", ":", "!", "?", "»")
# Au-dela, ce n'est plus un intitule mais une phrase dont la ponctuation
# manque : la ligne suivante la continue peut-etre vraiment.
_INTITULE_MAX = 90


def _en_blocs(lignes: list[str]) -> str:
    """Recolle les lignes de la couche texte en blocs Markdown lisibles.

    Ces lignes sont celles de la MISE EN PAGE, coupees en plein mot : « …dont
    la regularite leur permet de develop » puis « fine ; ». Les rendre telles
    quelles, separees d'un simple retour, donnait un unique pave — Markdown
    recolle les lignes d'un meme paragraphe — ou les items d'une liste et les
    intitules de section se retrouvaient noyes dans la prose.

    Trois regles, et rien de plus : une ligne qui commence par une puce ouvre
    un item ; une ligne qui suit une phrase achevee et commence par une
    majuscule ouvre un bloc ; tout le reste continue le bloc en cours, recolle
    par une espace.
    """
    blocs: list[tuple[bool, str]] = []
    courant = ""
    est_item = False

    def poser() -> None:
        if courant:
            blocs.append((est_item, ("- " + courant) if est_item else courant))

    for ligne in lignes:
        nu = (ligne or "").strip()
        if not nu:
            continue
        if _PUCE_DE_LIGNE.match(nu):
            poser()
            courant, est_item = _PUCE_DE_LIGNE.sub("", nu), True
            continue
        # Un bloc court et sans ponctuation finale est un intitule, pas une
        # phrase en cours : la ligne suivante ne le continue pas. Sans cette
        # regle, les trois bandes de titre de la page 32 — « A aborder avant
        # 4 ans », « A partir de 4 ans… », « A partir de 5 ans… » — se
        # retrouvaient collees sur une seule ligne.
        intitule = (courant and len(courant) <= _INTITULE_MAX
                    and not courant.rstrip().endswith(_FIN_DE_PHRASE))
        suite = courant and not intitule and not (
            courant.rstrip().endswith(_FIN_DE_PHRASE) and nu[:1].isupper())
        if suite:
            courant += " " + nu
            continue
        poser()
        courant, est_item = nu, False
    poser()

    # Deux items qui se suivent restent colles : une ligne vide entre eux en
    # ferait une liste aeree, ou chaque item porte son paragraphe.
    sortie = ""
    for i, (item, texte) in enumerate(blocs):
        if i:
            sortie += "\n" if (item and blocs[i - 1][0]) else "\n\n"
        sortie += texte
    return sortie


def _repli_couche_texte(page: Any, idx: int, motif: str,
                        declares: list[Any] | None = None,
                        titres_pdf: Any = None,
                        ) -> tuple[str, bool, int]:
    """Ce qu'on garde d'une page que le modele n'a pas rendue.

    Une page perdue l'etait entierement : il ne restait qu'un commentaire, et
    son contenu disparaissait du document. Mesure sur le programme de cycle 1 :
    la page 2 est une table des matieres portant 1 228 suites de points de
    conduite, que le modele tente de reproduire une a une jusqu'a depasser les
    240 s — alors que sa couche texte, elle, porte 6 149 caracteres lisibles.

    **Les tableaux que le PDF declare sont repris aussi.** La premiere version
    de ce repli les jetait, au motif qu'il n'avait pas la finesse du modele —
    mais sur ces programmes la page EST un tableau : les pages 29 et 32 en
    declarent 3 et 4, et elles ressortaient en texte au fil de l'eau. Or rien
    n'a besoin du modele ici, l'arbre de structure porte les lignes et les
    cellules, et `_tableaux_de_la_page` sait deja les rendre.

    Il faut en revanche eviter de tout ecrire deux fois : la couche texte
    contient AUSSI le texte des cellules. Les lignes que les tableaux
    reprennent sont donc retirees du fil, par comparaison sur une empreinte
    sans casse ni espaces multiples.

    Les tableaux retrouvent leur place grace a leur texte, puis les titres
    et les emphases sont retablis depuis les informations du PDF. Un repli
    reste signale au rapport : la couche texte peut elle-meme etre incomplete.
    """
    def habiller(texte: str) -> str:
        if titres_pdf is not None:
            texte, _ = _caler_titres(texte, _titres_de_la_page(page, titres_pdf))
        gras, ital = polices.emphases_de_la_page(page)
        _, texte = polices.corriger_emphases(texte, gras, ital)
        _, texte = polices.poser_emphases(texte, gras, ital)
        return texte

    try:
        lignes = _lignes_du_pdf(page)
    except Exception:  # noqa: BLE001
        lignes = []
    try:
        tableaux = _tableaux_de_la_page(page, declares)
    except Exception:  # noqa: BLE001
        tableaux = []

    if not lignes and not tableaux:
        return "", False, 0

    if not tableaux:
        return habiller(_en_blocs(lignes)), True, 0

    # Chaque tableau se pose LA OU SON CONTENU SE TROUVAIT, pas a la fin.
    # Les mettre tous en queue les rendait adjacents, et `_merge_table_fragments`
    # n'y voyait plus qu'un tableau coupe : les quatre grilles de la page 32
    # n'en faisaient qu'une, et les bandes de titre qui les separent avaient
    # remonte en bloc au-dessus. On repere donc, pour chaque grille, la
    # premiere ligne de texte qu'elle reprend — c'est sa place.
    cellules = [{_empreinte_de_cellule(_texte_nu(c))
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", t, re.S)} - {""}
                for t in tableaux]
    gardees: list[str] = []
    ou_poser: dict[int, int] = {}
    for ligne in lignes:
        nue = _empreinte_de_cellule(re.sub(r"^[-*]\s+", "", ligne))
        rang = None
        if nue:
            for i, cel in enumerate(cellules):
                if any(nue in c for c in cel):
                    rang = i
                    break
        if rang is not None:
            ou_poser.setdefault(rang, len(gardees))
            continue
        gardees.append(ligne)
    # Une grille dont aucune ligne n'a ete reconnue se pose en queue, faute
    # de mieux : c'est le cas d'un tableau que la couche texte ne porte pas.
    for i in range(len(tableaux)):
        ou_poser.setdefault(i, len(gardees))

    morceaux: list[str] = []
    precedent = 0
    for i in sorted(ou_poser, key=lambda k: (ou_poser[k], k)):
        segment = _en_blocs(gardees[precedent:ou_poser[i]])
        if segment:
            morceaux.append(segment)
        morceaux.append(tableaux[i])
        precedent = ou_poser[i]
    reste = _en_blocs(gardees[precedent:])
    if reste:
        morceaux.append(reste)
    return habiller("\n\n".join(m for m in morceaux if m)), True, len(tableaux)


def _convert_vision(pdf: Path, api_key: str,
                    model: str = DEFAULT_VISION_MODEL,
                    journal: list[dict[str, Any]] | None = None,
                    progress_callback: Any = None) -> str:
    """Rend chaque page en PNG puis appelle Albert Vision (chat completions).

    Les figures du PDF sont decoupees et incorporees en `data:` dans la
    foulee : le modele les decrit, il ne sait pas les decouper, et le PDF
    porte leur boite. C'est ce que ce moteur ajoute a une transcription
    nue, et il l'ajoute sans un appel reseau de plus.

    `progress_callback` recoit la page attaquee. Sans lui, la conversion d'un
    document restait a « 0/1 » pendant vingt-cinq minutes : le job ne comptait
    que des documents, et celui-ci etait seul.
    """
    doc = pymupdf.open(str(pdf))
    n_pages = len(doc)
    # Hierarchie des titres du document, lue dans les polices : le modele voit
    # une page a la fois, elle vaut pour toutes. Les tailles donnent les
    # premiers rangs, la graisse celui qui suit.
    titres_pdf = _TitresPdf(doc)
    # Meme chose pour les tableaux : l'arbre de structure vaut pour tout le
    # document, et se lit donc avant la premiere page.
    tableaux_pdf = balises.tableaux(doc) or {}
    listes_pdf = balises.listes(doc) or {}
    # Meme raison, pour les figures : ce qui trahit un bandeau, c'est qu'il
    # revient page apres page. Il faut donc avoir lu le document avant de
    # decouper la premiere page.
    bandeaux = _bandeaux_graphiques(doc)
    # Un document qui declare un `/TOC` porte son sommaire ; on n'a alors
    # aucune raison de le faire deviner sur une image.
    avec_sommaire = sommaire_pdf.declare_un_sommaire(doc)
    parts: list[str] = []
    logger.info("Vision : %s (%d pages, modele=%s)", pdf.name, n_pages, model)
    try:
        with httpx.Client(timeout=httpx.Timeout(
                connect=30, read=VISION_PAGE_TIMEOUT, write=120, pool=30)) as client:
            for idx, page in enumerate(doc, start=1):
                if progress_callback:
                    # Pas de `current` ici : la barre affiche deja « 3 / 63 »
                    # en bout de ligne, et « page 3 sur 63 » repetait la meme
                    # chose une ligne plus bas.
                    progress_callback(processed=idx - 1, total=n_pages)
                t0 = time.monotonic()
                appels = 0
                incidents: list[dict] = []
                # Le sommaire se lit dans le fichier, il ne se devine pas.
                # Celui du programme de cycle 1 aligne 1 228 suites de points
                # de conduite que le modele reproduisait une a une jusqu'a
                # depasser les 240 s : la page ressortait perdue, pour un
                # contenu que le PDF declare — un `/TOC`, et un lien par
                # entree portant sa page cible. On saute donc l'appel.
                if avec_sommaire:
                    rendu = sommaire_pdf.rendre(page, titres_pdf)
                    if rendu:
                        entrees = rendu.count("- p. ")
                        logger.info("Vision page %d/%d : sommaire repris du PDF "
                                    "(%d entrees), sans appel au modele.",
                                    idx, n_pages, entrees)
                        if journal is not None:
                            # Les memes champs que les autres pages, sinon le
                            # rapport formate des None. `couverture` reste
                            # absente : cette page ne vient pas du modele, il
                            # n'y a rien a comparer.
                            journal.append({
                                "page": idx,
                                "sommaire": entrees,
                                "secondes": time.monotonic() - t0,
                                "appels": 0,
                                "caracteres": len(rendu),
                                "tableaux_pdf": 0,
                                "figures": 0,
                            })
                        parts.append(rendu)
                        continue
                pix = page.get_pixmap(dpi=VISION_DPI)
                png = pix.tobytes("png")
                b64 = base64.b64encode(png).decode("ascii")
                logger.info("Vision page %d/%d (%d KB) -> POST %s",
                            idx, n_pages, len(png) // 1024, ALBERT_BASE_URL)
                rappel = _rappel_tableau_ouvert(parts[-1]) if parts else None
                texte_pdf = page.get_text()

                def _envoyer(payload: dict, estimation: int, objet: str) -> Any:
                    nonlocal appels

                    def annoncer(tentative: int) -> None:
                        nonlocal appels
                        appels += 1
                        if progress_callback:
                            message = f"{objet} : lecture par le modèle"
                            if tentative > 1:
                                message += f" (tentative {tentative}/{VISION_TENTATIVES})"
                            progress_callback(processed=idx - 1, total=n_pages, current=message)

                    def poster() -> Any:
                        def limitation(tentative, maximum, attente, secondes):
                            nonlocal appels
                            if tentative < maximum:
                                appels += 1
                            observer({"tentative": tentative, "secondes": round(secondes, 1),
                                      "http": 429, "erreur": "HTTP429",
                                      "cause": "limite de requêtes atteinte",
                                      "reprise_limitation": tentative < maximum,
                                      "resultat": (f"attente de {attente:.0f}s avant reprise (limitation {tentative}/{maximum})"
                                                   if tentative < maximum else "limitation persistante, tentatives épuisées")})
                            if progress_callback:
                                progress_callback(processed=idx - 1, total=n_pages,
                                                  current=f"{objet} : service limité, reprise après {attente:.0f}s")

                        r = ratelimit.call_albert_chat_completions(
                            client, f"{ALBERT_BASE_URL}/v1/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}"},
                            payload=payload, estimated_input_chars=estimation,
                            on_rate_limit=limitation)
                        if r.status_code != 200:
                            raise _VisionHTTP(r.status_code, r.text[:400])
                        return r

                    def observer(evenement: dict) -> None:
                        incidents.append({"objet": objet, "modele": model,
                                          "delai_lecture": VISION_PAGE_TIMEOUT, **evenement})

                    return _avec_reprises(poster, annoncer, observer)

                def _appel(consigne: str) -> str:
                    payload = {
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": consigne},
                                {"type": "image_url",
                                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            ],
                        }],
                        "temperature": 0.1,
                    }
                    # Estimation tokens : un PNG 150dpi ~= 2000 tokens image,
                    # + prompt ~800 tokens. On declare 3000 par appel pour le
                    # throttler.
                    r = _envoyer(payload, 12000, f"Page {idx}")
                    data = r.json()
                    contenu = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                    # La normalisation des tableaux se fait ici, et non chez
                    # l'appelant : la page peut etre redemandee sans le rappel
                    # de tableau, et les deux reponses doivent etre comparables
                    # comme elles doivent etre reparables.
                    return _normaliser_tableaux_html(
                        _pipes_en_html(_strip_code_fence_wrapper(contenu)))

                def _decrire_figure(png: bytes, nom: str) -> str:
                    """Decrit UNE figure, en un appel qui ne voit qu'elle.

                    Le prompt de page reclame deja une description des figures,
                    et le modele l'omet : sur le programme de cycle 1, les deux
                    figures sortaient avec « Figure 1.1 » pour tout libelle. Une
                    consigne noyee dans la transcription d'une page dense passe
                    apres le reste ; seule, sur l'image decoupee, elle aboutit.
                    Le cout est faible parce que les figures sont rares — deux
                    sur soixante-trois pages ici, deux sur le cours de NSI.
                    """
                    encodee = base64.b64encode(png).decode("ascii")
                    r = _envoyer({
                            "model": model,
                            "messages": [{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": PROMPT_FIGURE},
                                    {"type": "image_url", "image_url": {
                                        "url": f"data:image/png;base64,{encodee}"}},
                                ],
                            }],
                            "temperature": 0.1,
                        }, 8000, nom)
                    contenu = ((r.json().get("choices") or [{}])[0]
                               .get("message", {}).get("content", ""))
                    dite = " ".join((contenu or "").split())
                    if len(dite) > FIGURE_DESCRIPTION_MAX:
                        dite = dite[:FIGURE_DESCRIPTION_MAX].rsplit(" ", 1)[0] + "…"
                    logger.info("Figure %s decrite en %d caracteres.", nom, len(dite))
                    return dite

                try:
                    text = _appel(VISION_PROMPT + (rappel or ""))
                    # Une page rendue par un modele peut perdre un titre, un
                    # paragraphe, voire le haut de la page, et rien dans sa
                    # sortie ne le signale. La couche texte du PDF, gratuite,
                    # sert d'arbitre : sous le seuil, on refait la page et on
                    # garde la meilleure des deux.
                    #
                    # Le controle a d'abord vise le seul rappel de tableau,
                    # qui se retourne parfois contre nous — plutot que de
                    # rendre la suite du tableau, le modele sacrifie le titre
                    # et le texte d'une page qui commencait une section. Mais
                    # le rappel n'est pas la seule cause : sur la page 5 du
                    # programme de mathematiques de seconde, appelee sans
                    # rappel, Mistral Small 3.2 n'a rendu que 64 % du texte.
                    # Mesurer ne coute rien, et on mesure donc toujours.
                    couv = _couverture(texte_pdf, text)
                    if couv < COUVERTURE_MIN and rappel:
                        # Le rappel est le seul suspect qu'on sache ecarter :
                        # le retirer change la consigne, donc la reponse.
                        logger.warning("Vision page %d : %.0f%% du texte de la page "
                                       "seulement, seconde tentative sans rappel.",
                                       idx, couv * 100)
                        sans = _appel(VISION_PROMPT)
                        couv_sans = _couverture(texte_pdf, sans)
                        if couv_sans > couv:
                            text, couv = sans, couv_sans
                    if couv < COUVERTURE_MIN:
                        # **Rejouer la meme consigne ne sert a rien.** Mesure
                        # faite sur la page 12 de ce programme : la meme image
                        # et la meme consigne rendent la meme reponse, octet
                        # pour octet, a 0,1 comme a 0,8 de temperature, et
                        # meme en modifiant la consigne d'une espace. Le
                        # decodage est deterministe ; une boucle de reprises
                        # ne ferait que consommer des appels.
                        #
                        # Reste a le dire. Une page incomplete qui ne se
                        # signale pas est un piege pour le relecteur, qui n'a
                        # aucune raison de la soupconner. Sur ce programme,
                        # trois pages sur seize sont concernees, dont une qui
                        # perd toute une section en son milieu.
                        logger.warning("Vision page %d : %.0f%% du texte de la page "
                                       "seulement, page incomplete.",
                                       idx, couv * 100)
                    # Hybride : la ou le PDF declare ses tableaux, sa grille
                    # vaut mieux que celle devinee sur une image. Sur un
                    # scan, il n'en declare aucun et rien ne change.
                    # Trois issues possibles pour les tableaux d'une page, et
                    # le modele les emprunte toutes : il rend la grille au
                    # bon endroit (on y substitue celle du PDF), il la rend
                    # en double — cellules en intertitres puis grille — ou il
                    # ne la rend pas du tout et deverse les cellules en texte.
                    declares = _titres_de_la_page(page, titres_pdf)
                    text = _retirer_clotures_de_tableaux(text, texte_pdf)
                    tableaux = _tableaux_de_la_page(
                        page, tableaux_pdf.get(idx - 1))
                    text, n_remp = _remplacer_tableaux(text, tableaux)
                    if n_remp:
                        text, _ = _reposer_tableaux(text, declares[0])
                    text, n_manquants = _poser_tableaux_manquants(
                        text, tableaux, declares[0])
                    n_remp += n_manquants
                    # La page est-elle toujours incomplete une fois la grille
                    # du PDF posee ? Alors on y remet ce qui manque, depuis la
                    # couche texte. Redemander ne servirait a rien : le modele
                    # rend deux fois la meme reponse (voir plus haut).
                    if _couverture(texte_pdf, text) < COUVERTURE_MIN:
                        text, n_rep = _reparer_depuis_le_pdf(text, page)
                        if n_rep:
                            logger.info("Vision page %d : %d ligne(s) reinserees "
                                        "depuis la couche texte, couverture %.0f%%.",
                                        idx, n_rep,
                                        _couverture(texte_pdf, text) * 100)
                    # Avant les titres : un debris de cellule reste seul sur
                    # sa ligne se presente comme un titre, et se verrait
                    # attribuer un rang au lieu d'etre retire.
                    text, n_deb = _retirer_debris_de_tableau(
                        text, declares[0], texte_pdf)
                    text, n_pag = _retirer_pagination(text, page)
                    text, n_titres = _caler_titres(text, declares)
                    text, n_puces = _caler_listes(
                        text, _items_de_la_page(page, listes_pdf.get(idx - 1)))
                    # Apres les titres : un bandeau se presente en titre, et
                    # `_caler_titres` ne le connait pas — il n'est nulle part
                    # dans le PDF, c'est precisement ce qui le trahit. Les
                    # images inventees partent juste avant, pour decouvrir la
                    # mention du bandeau qu'elles accompagnent.
                    text, n_img = _retirer_images_du_modele(text)
                    text, n_liens = _retirer_liens_inventes(
                        text, page, texte_pdf)
                    # Le modele ne voit qu'une image : il ne peut pas savoir
                    # qu'un hyperlien existe. Le PDF, lui, le declare.
                    text, n_poses = poser_liens_du_pdf(text, page)
                    if n_poses:
                        logger.info("Vision page %d : %d lien(s) repose(s) "
                                    "depuis le PDF", idx, n_poses)
                    text, n_band = _retirer_bandeaux(text, texte_pdf)
                    n_band += n_img + n_liens
                    gras, ital = polices.emphases_de_la_page(page)
                    n_cor, text = polices.corriger_emphases(text, gras, ital)
                    n_emp, text = polices.poser_emphases(text, gras, ital)
                    n_emp += n_cor
                    text, n_fig = _poser_figures(text, page, idx, bandeaux,
                                                 decrire=_decrire_figure)
                    # Derniere barriere : les etapes ci-dessus travaillent
                    # ligne a ligne et peuvent re-eclater un tableau que
                    # l'arrivee avait replie. Un tableau eparpille est
                    # invisible de tout ce qui suit, jusqu'au lecteur.
                    colonne_unique = False
                    n_epars = tableaux_eparpilles(text)
                    if n_epars:
                        text = _normaliser_tableaux_html(text)
                        reste = tableaux_eparpilles(text)
                        logger.warning(
                            "Vision page %d : %d balise(s) de tableau eparpillee(s) "
                            "apres les reparations, repliees%s.", idx, n_epars,
                            "" if not reste else f" (il en reste {reste} : A RELIRE)")
                    # Le PDF declare ses colonnes : un tableau qui en rend
                    # moins a perdu une colonne en route, et avec elle son
                    # contenu. On ne le devine pas, on le dit.
                    for html in re.findall(r"^<table.*</table>$", text, re.M):
                        if _nb_colonnes(html) < 2 and tableaux_pdf.get(idx - 1):
                            logger.warning(
                                "Vision page %d : tableau rendu sur une seule "
                                "colonne alors que le PDF en declare plusieurs "
                                "- A RELIRE.", idx)
                            colonne_unique = True
                            break
                    if n_remp or n_titres or n_band or n_fig or n_emp \
                            or n_pag or n_deb:
                        logger.info("Vision page %d : %d tableau(x) repris du PDF, "
                                    "%d titre(s) cale(s), %d bandeau(x) retire(s), "
                                    "%d figure(s) posee(s), %d emphase(s) rendue(s), "
                                    "%d ligne(s) de pagination et %d debris de "
                                    "tableau retire(s).",
                                    idx, n_remp, n_titres, n_band, n_fig, n_emp,
                                    n_pag, n_deb)
                    if n_puces:
                        logger.info("Vision page %d : %d puce(s) rendues a des "
                                    "items declares par le PDF.", idx, n_puces)
                    dt = time.monotonic() - t0
                    logger.info("Vision page %d/%d OK en %.1fs (%d chars)",
                                idx, n_pages, dt, len(text or ""))
                    if journal is not None:
                        journal.append({
                            "page": idx, "secondes": round(dt, 1),
                            "appels": appels,
                            "incidents": incidents,
                            "couverture": round(_couverture(texte_pdf, text), 3),
                            "caracteres": len(text or ""),
                            "tableaux_pdf": n_remp, "figures": n_fig,
                            "eparpilles": n_epars,
                            "colonne_unique": colonne_unique,
                        })
                    parts.append(text)
                except _VisionHTTP as exc:
                    texte_repli, repris, n_tab = _repli_couche_texte(
                        page, idx, f"HTTP {exc.code}", tableaux_pdf.get(idx - 1), titres_pdf)
                    if journal is not None:
                        journal.append({"page": idx, "echec": f"HTTP {exc.code}",
                                        "secondes": round(time.monotonic() - t0, 1),
                                        "appels": appels, "incidents": incidents,
                                        "repli": repris, "tableaux_repris": n_tab})
                    logger.error("Vision page %d : HTTP %d - %s", idx, exc.code, exc.body)
                    parts.append(texte_repli)
                except httpx.TimeoutException:
                    # Sans cette entree, une page entierement perdue n'apparait
                    # nulle part dans le rapport : ni ligne, ni « a relire ».
                    # La moyenne de couverture se calcule alors sur les seules
                    # pages qui ont reussi, et annonce 99 % sur un document
                    # ampute. Seul `_VisionHTTP` alimentait le journal.
                    motif = (f"délai dépassé après {VISION_TENTATIVES} tentatives "
                             f"(attente de lecture : {VISION_PAGE_TIMEOUT:.0f}s par tentative)")
                    texte_repli, repris, n_tab = _repli_couche_texte(
                        page, idx, motif, tableaux_pdf.get(idx - 1), titres_pdf)
                    if journal is not None:
                        journal.append({"page": idx, "echec": motif,
                                        "secondes": round(time.monotonic() - t0, 1),
                                        "appels": appels, "incidents": incidents,
                                        "repli": repris, "tableaux_repris": n_tab})
                    logger.error("Vision page %d : %s%s",
                                 idx, motif,
                                 " (couche texte reprise)" if repris else "")
                    parts.append(texte_repli)
                except Exception as exc:
                    motif = f"{type(exc).__name__}: {exc}"
                    texte_repli, repris, n_tab = _repli_couche_texte(
                        page, idx, motif, tableaux_pdf.get(idx - 1), titres_pdf)
                    if journal is not None:
                        journal.append({"page": idx, "echec": motif,
                                        "secondes": round(time.monotonic() - t0, 1),
                                        "appels": appels, "incidents": incidents,
                                        "repli": repris, "tableaux_repris": n_tab})
                    logger.error("Vision page %d : echec : %s", idx, exc)
                    parts.append(texte_repli)
    finally:
        doc.close()
    if progress_callback:
        progress_callback(processed=n_pages, total=n_pages,
                          current="assemblage des pages")
    logger.info("Vision : %s termine (%d/%d pages)", pdf.name,
                sum(1 for p in parts if not p.startswith("<!--")), n_pages)
    parts = _remove_repeated_boilerplate(parts)
    # La reparation des tableaux se fait apres la jonction : un tableau coupe
    # par une fin de page n'est reconstituable qu'une fois les pages remises
    # bout a bout.
    return _merge_table_fragments(_assembler_pages(parts),
                                  bool(tableaux_pdf))
