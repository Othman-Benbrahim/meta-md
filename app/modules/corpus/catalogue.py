"""Le catalogue de lots, tenu dans un depot de donnees a part.

Les lots sont des donnees, pas du code : on les corrige quand un programme
parait, sans rapport avec le rythme des versions de META-MD. Les tenir dans le
depot de l'application obligeait a publier une version pour corriger une URL,
faisait tourner la CI pour un JSON, et alourdissait un historique qu'on venait
de purger — un lot se reecrit en entier a chaque revision, et celui du second
degre pese 87 Ko.

Ils vivent donc dans `edu-md/documents`, sous `lots-json/`, et META-MD va les
chercher. **C'est un retour en arriere assume** : la recuperation en ligne des
lots avait ete retiree le 2026-08-08, parce qu'elle servait alors a rejouer une
transformation depuis une source ouverte — un mecanisme que personne
n'utilisait. Ici, la source EST le fichier, et le telecharger revient a copier
un fichier deja pret.

**Aucun index a tenir a la main.** L'inventaire se lit dans l'arborescence du
depot par l'API de la Forge, qui donne pour chaque fichier l'empreinte de son
contenu : deposer un JSON dans `lots-json/` suffit a le publier, et rien ne se
retelecharge tant que l'empreinte ne bouge pas. Un index separe aurait fini par
mentir le jour ou on aurait oublie de le mettre a jour.

Ce que le catalogue ne fait jamais : ecrire dans `data/lots/`. Ce dossier est a
l'utilisateur, et un lot qu'il y depose masque celui du catalogue (voir
`lots.dossiers`).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from . import lots

logger = logging.getLogger("atelier.corpus.catalogue")

FORGE = "https://forge.apps.education.fr"
PROJET = "edu-md%2Fdocuments"
BRANCHE = "main"
DOSSIER_DISTANT = "lots-json"

URL_ARBRE = (f"{FORGE}/api/v4/projects/{PROJET}/repository/tree"
             f"?path={DOSSIER_DISTANT}&ref={BRANCHE}&per_page=100")
URL_FICHIER = f"{FORGE}/{PROJET.replace('%2F', '/')}/-/raw/{BRANCHE}/{DOSSIER_DISTANT}/"

TIMEOUT = 20.0
# Meme rythme que la verification de version : une fois par jour suffit pour
# des donnees qu'on revise « de temps en temps », et un demarrage ne doit pas
# dependre d'un appel reseau.
FRAICHEUR = 24 * 3600
# Un lot qui depasse cette taille n'est pas un lot : on ne l'ecrit pas.
TAILLE_MAX = 5 * 1024 * 1024

FICHIER_ETAT = "_catalogue.json"


def _fichier_etat() -> Path:
    return lots.dossier_catalogue() / FICHIER_ETAT


def lire_etat() -> dict[str, Any]:
    try:
        data = json.loads(_fichier_etat().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _ecrire_etat(etat: dict[str, Any]) -> None:
    p = _fichier_etat()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(etat, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError as exc:
        logger.warning("Catalogue : etat non ecrit (%s)", exc)


def _lot_recevable(brut: bytes, nom: str) -> dict[str, Any] | None:
    """Un fichier telecharge n'entre que s'il a la forme d'un lot.

    Sans ce controle, une page d'erreur HTML ou un JSON tronque remplacerait un
    lot valide et l'interface afficherait une liste vide sans rien expliquer.
    """
    if len(brut) > TAILLE_MAX:
        logger.warning("Catalogue : %s ignore, %d octets", nom, len(brut))
        return None
    try:
        data = json.loads(brut.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Catalogue : %s illisible (%s)", nom, exc)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        logger.warning("Catalogue : %s n'a pas la forme d'un lot", nom)
        return None
    return data


def _arbre_distant(client: httpx.Client) -> list[dict[str, Any]]:
    r = client.get(URL_ARBRE, timeout=TIMEOUT)
    r.raise_for_status()
    arbre = r.json()
    if not isinstance(arbre, list):
        raise ValueError("arborescence inattendue")
    return [e for e in arbre
            if isinstance(e, dict) and e.get("type") == "blob"
            and str(e.get("name", "")).endswith(".json")
            and not str(e.get("name", "")).startswith("_")]


def rafraichir(force: bool = False) -> dict[str, Any]:
    """Met le catalogue local au niveau du depot. Ne leve jamais.

    Rend un compte rendu : ce qui a ete pose, retire, et l'erreur eventuelle.
    Une panne reseau laisse le catalogue precedent en place — c'est tout
    l'interet de le garder sur le disque.
    """
    etat = lire_etat()
    vu = etat.get("empreintes") or {}
    age = time.time() - float(etat.get("recupere_le") or 0)
    if not force and age < FRAICHEUR:
        return {"ok": True, "ignore": "catalogue encore frais",
                "poses": [], "retires": []}

    dossier = lots.dossier_catalogue()
    poses: list[str] = []
    retires: list[str] = []
    try:
        with httpx.Client(follow_redirects=True) as client:
            distants = _arbre_distant(client)
            dossier.mkdir(parents=True, exist_ok=True)
            noms_distants = {e["name"] for e in distants}
            for entree in distants:
                nom = str(entree["name"])
                empreinte = str(entree.get("id") or "")
                cible = dossier / nom
                if cible.is_file() and vu.get(nom) == empreinte:
                    continue
                r = client.get(URL_FICHIER + nom, timeout=TIMEOUT)
                r.raise_for_status()
                if _lot_recevable(r.content, nom) is None:
                    continue
                cible.write_bytes(r.content)
                vu[nom] = empreinte
                poses.append(nom)
            # Un lot retire du depot disparait aussi d'ici : le catalogue est
            # une copie, pas une archive. Seul ce dossier est concerne, jamais
            # `data/lots/`.
            for orphelin in sorted(dossier.glob("*.json")):
                if orphelin.name.startswith("_") or orphelin.name in noms_distants:
                    continue
                orphelin.unlink(missing_ok=True)
                vu.pop(orphelin.name, None)
                retires.append(orphelin.name)
    except Exception as exc:  # noqa: BLE001 - une panne reseau ne casse rien
        logger.warning("Catalogue de lots indisponible : %s", exc)
        return {"ok": False, "erreur": str(exc), "poses": poses, "retires": retires}

    _ecrire_etat({"recupere_le": time.time(), "empreintes": vu})
    if poses or retires:
        logger.info("Catalogue de lots : %d pose(s), %d retire(s)",
                    len(poses), len(retires))
    return {"ok": True, "poses": poses, "retires": retires}
