"""Serveur local pour META-MD : des sources vers des Markdown documentes.

Lance par python.bat, sert :
  - pages/ en statique sur http://localhost:8118/
  - endpoints /api/* pour la clef, les sources, le schema et la conversion
  - /rags-static/ pour lire les PDFs / MDs d'un corpus depuis le navigateur

La cle Albert vit dans config.json (git-ignore). Aucune connexion GitLab.

La segmentation et l'envoi vers une collection Albert ne sont plus ici :
ils vivent dans le projet voisin MD-RAG, qui repart des Markdown produits.
"""
from __future__ import annotations

import io
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import traceback  # noqa: F401 (utilise dans les handlers)
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote

from modules import autofill, corpus, jobs, pipeline, updates


PORT = 8118
# Le serveur n'ecoute que la boucle locale, ce qui ne protege de rien :
# n'importe quelle page ouverte dans le navigateur peut lui poster une
# requete, et un domaine qui resout vers 127.0.0.1 peut lire ses reponses.
HOTES_AUTORISES = frozenset({
    "127.0.0.1:{}".format(PORT), "localhost:{}".format(PORT),
    "[::1]:{}".format(PORT),
})
ORIGINES_AUTORISEES = frozenset(
    "http://" + hote for hote in HOTES_AUTORISES
)
BASE_DIR = Path(__file__).parent.resolve()
# Une version peut vivre dans `app/versions/X.Y.Z`, mais les donnees
# restent dans `data/`, indique par le lanceur stable.
DEFAULT_DATA_DIR = BASE_DIR.parent / "data"
DATA_DIR = Path(
    os.environ.get("METAMD_DATA_DIR")
    or (DEFAULT_DATA_DIR if DEFAULT_DATA_DIR.is_dir() else BASE_DIR)
).resolve()
PAGES_DIR = BASE_DIR / "pages"
CORPUS_DIR = DATA_DIR / "CORPUS"
CONFIG_FILE = DATA_DIR / "config.json"

logger = logging.getLogger("metamd.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
RESTART_EXIT_CODE = 75
# Duree pendant laquelle on reessaie d'ouvrir le port. Le socle n'accorde
# que 12 s a une version pour repondre : rester en deca.
ATTENTE_PORT = 8.0
_SERVER: ThreadingHTTPServer | None = None
_RESTART_REQUESTED = False


LIBELLES_TACHE = {
    "convert-doc": "Conversion",
    "fill": "Remplissage des métadonnées",
    "lot-download": "Téléchargement d'un lot",
    "depot-fetch": "Récupération du dépôt",
    "depot-import": "Import du dépôt",
    "update-install": "Installation de la mise à jour",
}


def _decrire_taches(taches: list[dict[str, Any]]) -> str:
    """Nomme les taches actives et leur age.

    « Une tache est en cours » ne dit ni laquelle ni depuis quand : impossible
    de savoir s'il faut attendre une minute ou si quelque chose est reste
    bloque.
    """
    morceaux = []
    for tache in taches[:3]:
        tete, _, reste = str(tache.get("kind") or "").partition(":")
        nom = LIBELLES_TACHE.get(tete, tete or "Tâche")
        if reste:
            nom += f" — {reste.split(':')[0]}"
        debut = tache.get("started_at")
        if debut:
            minutes = int((time.time() - float(debut)) // 60)
            nom += f", depuis {minutes} min" if minutes else ", à l'instant"
        morceaux.append(nom)
    if len(taches) > 3:
        morceaux.append(f"et {len(taches) - 3} autre(s)")
    return " ; ".join(morceaux)


def _rattraper_le_socle() -> None:
    """Pose le socle qu'un ancien installateur a laisse dans le dossier de version.

    Les installations 0.8.0 a 0.8.3 lancent LEUR `lanceur/updater.py`, qui ne
    sait rien du socle : il emporte le dossier `lanceur/` du paquet dans
    `app/versions/X.Y.Z/`, ou il dort. Sans ce rattrapage leur socle ne serait
    jamais mis a jour — pas meme a la version suivante, puisque ce serait
    encore l'ancien updater qui l'installerait. Le serveur, lui, est toujours
    le neuf : c'est donc a lui de refermer la boucle, au premier demarrage.

    Ne fait rien pour les installations 0.8.4 et suivantes : leur updater pose
    le socle lui-meme et ne laisse rien derriere.
    """
    source = BASE_DIR / "lanceur"
    cible = DATA_DIR.parent / "lanceur"
    if not source.is_dir() or not cible.is_dir():
        return
    try:
        poses: list[str] = []
        sauvegarde = cible / "precedent"
        for fichier in sorted(p for p in source.iterdir() if p.is_file()):
            actuel = cible / fichier.name
            if actuel.is_file() and actuel.read_bytes() == fichier.read_bytes():
                continue
            sauvegarde.mkdir(parents=True, exist_ok=True)
            if actuel.is_file():
                shutil.copy2(actuel, sauvegarde / fichier.name)
            # Ecriture atomique : un socle a moitie ecrit ne demarrerait plus.
            temporaire = cible / (fichier.name + ".nouveau")
            shutil.copy2(fichier, temporaire)
            temporaire.replace(actuel)
            poses.append(fichier.name)
        shutil.rmtree(source, ignore_errors=True)
        if poses:
            logger.info("Socle rattrape depuis la version installee : %s. "
                        "Il prendra effet au prochain lancement complet.",
                        ", ".join(poses))
    except OSError as exc:  # noqa: BLE001
        logger.warning("Rattrapage du socle impossible : %s", exc)

def _sonde_de_presence(pid: int) -> Any:
    """Rend une fonction qui dit si `pid` vit encore, SANS rien lui envoyer.

    `os.kill(pid, 0)` est le test de presence POSIX, et il ne l'est pas sous
    Windows : CPython y aiguille sur `GenerateConsoleCtrlEvent` des que le
    signal vaut `CTRL_C_EVENT` — **qui vaut 0**. Ce qu'on prenait pour une
    question etait donc un **Ctrl+C envoye au lanceur, une fois par seconde**.

    Le lanceur le recevait comme l'utilisateur l'aurait tape : `_handle_stop`
    levait SystemExit et il s'arretait, silencieusement, laissant le serveur
    orphelin. La mise a jour s'installait bien, mais plus personne n'etait la
    pour passer le relais — c'est la panne qu'on a poursuivie de la 0.8.4 a la
    0.8.18, et qui expliquait aussi pourquoi relancer a la main marchait
    toujours. Le journal du socle l'a montre : seize « Lanceur arrete
    (Ctrl+C) » pendant qu'un serveur tournait, sans que personne ne touche au
    clavier.

    Sous Windows on ouvre donc une poignee une bonne fois et on l'interroge :
    `WaitForSingleObject` a zero milliseconde rend WAIT_TIMEOUT tant que le
    processus vit. Garder la poignee protege en prime d'un numero de processus
    recycle par le systeme, qui ferait passer un lanceur mort pour vivant.
    """
    if os.name != "nt":
        def vivant_posix() -> bool:
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            return True
        return vivant_posix

    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)

    poignee = k32.OpenProcess(SYNCHRONIZE, False, pid)
    if not poignee:
        # Le lanceur n'existe deja plus, ou il est hors de portee : on ne
        # surveille rien plutot que de fermer un serveur qui va bien.
        logger.warning("Lanceur %s introuvable : pas de surveillance.", pid)
        return lambda: True

    def vivant_windows() -> bool:
        return k32.WaitForSingleObject(poignee, 0) == WAIT_TIMEOUT

    return vivant_windows


def _watch_launcher() -> None:
    """Ferme un serveur versionne si son lanceur a ete tue brutalement."""
    raw_pid = (os.environ.get("METAMD_LAUNCHER_PID") or "").strip()
    if not raw_pid.isdigit():
        return
    vivant = _sonde_de_presence(int(raw_pid))

    def watch() -> None:
        while True:
            time.sleep(1)
            if not vivant():
                logger.warning("Lanceur arrete : fermeture du serveur META-MD.")
                os._exit(0)

    threading.Thread(target=watch, name="launcher-watch", daemon=True).start()


def _launcher_state() -> dict[str, Any]:
    """Etat ecrit par le socle : version active, precedente, et defaillante."""
    path = DATA_DIR.parent / "app" / "active.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("config.json illisible : %s", exc)
        return {}


def save_config(data: dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_api_key() -> str:
    return (load_config().get("albert_api_key") or "").strip()


def get_autofill_model() -> str:
    return (load_config().get("autofill_model") or "").strip() or autofill.ALBERT_TEXT_MODEL_DEFAULT


def get_autofill_max_chars() -> int | None:
    val = load_config().get("autofill_max_chars")
    try:
        n = int(val)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------

def _url_static(*morceaux: str) -> str:
    """URL `/rags-static/…` pour un chemin dont les morceaux sont litteraux.

    Un nom de fichier porte ici tout ce qu'un titre officiel peut porter :
    espaces, apostrophes, tirets cadratins — et parfois `&#039;`, une entite
    HTML gravee dans le nom par un import qui ne l'avait pas decodee. Or `#`
    ouvre un fragment et `&` separe des parametres : pose tel quel dans un
    `src` ou un `href`, le chemin se faisait trancher au premier `#`, et la
    page de revision rendait un 404 sur un fichier pourtant present.

    Le serveur `unquote` deja a l'entree de `/rags-static/` : c'est bien une
    URL encodee qu'il attend, elle n'etait simplement pas encodee au depart.
    Chaque morceau est un nom, jamais un chemin : `/` y est donc encode aussi.
    """
    return "/rags-static/" + "/".join(quote(m, safe="") for m in morceaux)


def _safe_join(root: Path, rel: str) -> Path | None:
    rel = rel.lstrip("/")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _timestamp_slug() -> str:
    return time.strftime("%Y-%m-%d_%H-%M-%S", time.gmtime())


def _valid_rag_name(rag: str) -> bool:
    return bool(rag) and "/" not in rag and "\\" not in rag and not rag.startswith(".")


def _corpus_slug(nom: str) -> str:
    """Nom de dossier tire d'un nom libre.

    L'utilisateur ecrit ce qu'il veut ; le dossier, lui, doit traverser un
    systeme de fichiers, une URL et une ligne de commande. On retire les
    accents, on remplace tout le reste par des tirets et on passe en
    minuscules : `Programmes du primaire` donne `programmes-du-primaire`.
    """
    plat = unicodedata.normalize("NFKD", nom.lower())
    plat = "".join(c for c in plat if not unicodedata.combining(c))
    garde = [c if (c.isalnum() and c.isascii()) else "-" for c in plat]
    return re.sub(r"-{2,}", "-", "".join(garde)).strip("-")


# Budget de metadonnees et presence d'une URL d'origine : la logique vit
# dans les modules metier, le serveur ne fait que la servir.
_theme_has_source_url = corpus.metadata.theme_has_source_url


# Cles de schema traitees comme portant le titre du document. On les cherche
# d'abord ; a defaut on prend le premier champ du schema, dont l'ordre est
# l'ordre d'affichage et donc, par convention, le titre en tete.
TITLE_KEYS = ("titre", "title")


def _document_title(sidecar: dict[str, Any], schema_fields: list[dict[str, Any]]) -> str:
    """Titre lisible d'un document, ou "" s'il n'a pas encore ete trouve.

    Sert a afficher le titre plutot que le nom de fichier dans les listes.
    """
    fields = sidecar.get("fields") or {}

    def value_of(key: str) -> str:
        entry = fields.get(key)
        if not isinstance(entry, dict):
            return ""
        value = entry.get("value")
        return value.strip() if isinstance(value, str) else ""

    for key in TITLE_KEYS:
        found = value_of(key)
        if found:
            return found
    first = (schema_fields[0].get("key") if schema_fields else "") or ""
    return value_of(first) if first else ""


# ---------------------------------------------------------------------------
# Handler HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = f"META-MD/{updates.current_version()}"

    def log_message(self, format, *args):  # noqa: A002
        logger.info("%s - %s", self.address_string(), format % args)

    # L'interface sonde l'avancement d'une conversion une fois par seconde.
    # Sur les vingt-cinq minutes d'un programme de 63 pages, cela fait 2 175
    # lignes qui recouvrent les 63 seules qui renseignent — impossible d'y
    # relire ce qu'une page a donne. Ces sondages descendent en DEBUG ; tout
    # ce qui n'est pas un 200 remonte, car un sondage qui echoue est un fait.
    ROUTES_SONDEES = ("/api/corpus/jobs",)

    def log_request(self, code="-", size="-") -> None:
        chemin = getattr(self, "path", "").partition("?")[0]
        if str(code) == "200" and any(chemin == route or chemin.startswith(route + "/")
                                      for route in self.ROUTES_SONDEES):
            logger.debug("%s - %s", self.address_string(), self.requestline)
            return
        super().log_request(code, size)

    # --- Helpers reponse ---

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "Not Found")
            return
        ctype, _ = mimetypes.guess_type(str(path))
        ctype = ctype or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _origine_sure(self) -> bool:
        """Refuse le DNS rebinding et les requetes lancees par un autre site."""
        if (self.headers.get("Host") or "").strip().lower() not in HOTES_AUTORISES:
            return False
        origine = (self.headers.get("Origin") or "").strip().lower()
        # Absente : navigation normale. Presente : elle doit etre la notre.
        return not origine or origine in ORIGINES_AUTORISEES

    # --- Router GET ---

    def do_GET(self):  # noqa: N802
        if not self._origine_sure():
            self.send_error(403, "Forbidden"); return
        path = self.path.split("?", 1)[0]

        if path in ("/", ""):
            self._send_file(PAGES_DIR / "index.html"); return

        if path == "/api/version":
            # Un seul numero depuis la 0.8.4 : le socle voyage avec
            # l'application, il n'a plus de version propre a afficher.
            self._send_json(200, {
                "ok": True,
                "name": "META-MD",
                "version": updates.current_version(),
                "managed": bool(os.environ.get("METAMD_LAUNCHER_PID")),
                # Dit a l'interface s'il y a lieu de proposer une
                # verification. A false, le numero de version reste affiche
                # mais cesse d'etre cliquable : un bouton qui ne verifie
                # rien vaut moins qu'une absence de bouton.
                "update_check": updates.VERIFICATION_ACTIVE,
            }); return

        if path == "/api/update":
            # `?force=1` ignore le cache de 24 h : sans cela, une version
            # publiee entre-temps reste invisible jusqu'au lendemain, et
            # relancer le serveur n'y change rien puisque le cache est sur
            # disque.
            force = "force=1" in (self.path.split("?", 1)[1] if "?" in self.path else "")
            status = updates.update_status(force=force)
            # Le lanceur note ici la version qui n'a pas demarre. L'interface
            # doit pouvoir le dire : sans cela elle repropose exactement la
            # meme mise a jour, comme si rien ne s'etait passe.
            status["failed"] = _launcher_state().get("failed") or ""
            self._send_json(200, status); return

        if path == "/api/config":
            akey = get_api_key()
            self._send_json(200, {
                "albert_api_key_set": bool(akey),
                "albert_api_key_hint": (akey[:7] + "…") if akey else "",
            }); return

        if path == "/api/corpus/topics":
            self._send_json(200, {"topics": corpus.topics.list_topics(CORPUS_DIR)})
            return

        if path == "/api/corpus/lots":
            # `?refresh=1` va chercher le catalogue MAINTENANT, sans attendre
            # le rythme d'un jour. C'est ce que fait l'ouverture du panneau
            # « Ajouter des documents » : le seul moment ou la liste sert, et
            # celui ou l'on accepte d'attendre une seconde pour l'avoir a
            # jour. Sans cela, un lot publie le matin n'apparaissait qu'au
            # lendemain, et relancer META-MD n'y changeait rien.
            requete = self.path.split("?", 1)[1] if "?" in self.path else ""
            etat = None
            if "refresh=1" in requete:
                etat = corpus.catalogue.rafraichir(force=True)
            reponse: dict[str, Any] = {"lots": corpus.lots.list_lots()}
            if etat is not None:
                reponse["catalogue"] = {"ok": bool(etat.get("ok")),
                                        "erreur": etat.get("erreur")}
            self._send_json(200, reponse)
            return

        if path == "/api/corpus/jobs":
            # Sans cette liste, une page rechargee pendant une conversion
            # n'avait aucun moyen de la retrouver : l'identifiant du job ne
            # vivait que dans son JavaScript. L'utilisateur restait aveugle
            # pendant les vingt-cinq minutes d'un gros document.
            self._send_json(200, {"ok": True, "jobs": jobs.active()}); return

        m = re.match(r"^/api/corpus/jobs/([\w\-]+)$", path)
        if m:
            job = jobs.get(m.group(1))
            if job is None:
                self._send_json(404, {"ok": False, "error": "Job inconnu"}); return
            self._send_json(200, {"ok": True, "job": job}); return

        m = re.match(r"^/api/corpus/lots/([\w\-]+)/preview$", path)
        if m:
            slug = m.group(1)
            try:
                data = corpus.lots.preview_lot(slug)
            except FileNotFoundError as exc:
                self._send_json(404, {"ok": False, "error": str(exc)}); return
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._send_json(500, {"ok": False, "error": f"Preview KO : {exc}"}); return
            self._send_json(200, {"ok": True, "lot": data}); return

        if path == "/api/corpus/documents":
            self._handle_get_documents(); return

        if path == "/api/corpus/depot":
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            rag = (qs.get("corpus", [""])[0] or "").strip()
            if not _valid_rag_name(rag):
                self._send_json(400, {"ok": False, "error": "rag invalide"}); return
            theme_dir = CORPUS_DIR / rag
            self._send_json(200, {
                "ok": True,
                "source": corpus.nature.source_of(theme_dir),
                "fiche": corpus.importation.lire_fiche(theme_dir),
            }); return

        if path.startswith("/api/corpus/schema"):
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            rag = (qs.get("corpus", [""])[0] or "").strip()
            if not _valid_rag_name(rag):
                self._send_json(400, {"ok": False, "error": "rag invalide"}); return
            theme_dir = CORPUS_DIR / rag
            self._send_json(200, {
                "ok": True,
                "corpus": rag,
                "exists": (theme_dir / corpus.schema.SCHEMA_FILENAME).is_file(),
                "schema": corpus.schema.read_schema(theme_dir),
                "default_fields": corpus.schema.default_schema()["fields"],
            }); return

        if path.startswith("/api/corpus/source-metadata"):
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            rag = (qs.get("corpus", [""])[0] or "").strip()
            source = (qs.get("source", [""])[0] or "").strip()
            src_path = self._resolve_source(rag, source)
            if src_path is None:
                self._send_json(400, {"ok": False, "error": "rag/source invalide"}); return
            theme_dir = CORPUS_DIR / rag
            # Au plus une conversion par document depuis qu'elles vivent a la
            # racine de `2-Conversions/`. La liste est gardee : l'interface la
            # lit deja, et une liste vide dit « pas encore converti ».
            conversions = corpus.documents.list_conversions(theme_dir, src_path.stem)
            active_id = corpus.documents.get_active_id(theme_dir, src_path.stem)
            for c in conversions:
                c["md_url"] = _url_static(
                    rag, corpus.documents.CONVERSIONS_DIR, f"{src_path.stem}.md")
            self._send_json(200, {
                "ok": True,
                "corpus": rag,
                "source": source,
                "metadata": corpus.metadata.read_metadata(src_path),
                "has_metadata": corpus.metadata.has_metadata(src_path),
                "schema": corpus.schema.read_schema(theme_dir),
                "conversions": conversions,
                "active_engine": active_id,
            }); return

        if path.startswith("/rags-static/"):
            from urllib.parse import unquote
            rel = unquote(path[len("/rags-static/"):])
            target = _safe_join(CORPUS_DIR, rel)
            if target is None:
                self.send_error(400); return
            self._send_file(target); return

        # Rapports d'upload : lecture seule, JSON uniquement.
        if path.startswith("/_reports/"):
            from urllib.parse import unquote
            rel = unquote(path[len("/_reports/"):])
            target = _safe_join(DATA_DIR / "rapports", rel)
            if target is None or target.suffix.lower() != ".json":
                self.send_error(400); return
            self._send_file(target); return

        if path.startswith("/assets/"):
            target = _safe_join(PAGES_DIR, path.lstrip("/"))
            if target is None:
                self.send_error(400); return
            self._send_file(target); return

        # /nom.html -> pages/nom.html
        m = re.match(r"^/([\w\-]+)(?:\.html)?$", path)
        if m:
            target = PAGES_DIR / f"{m.group(1)}.html"
            if target.exists():
                self._send_file(target); return

        self.send_error(404, "Not Found")

    # --- Router POST ---

    def do_POST(self):  # noqa: N802
        if not self._origine_sure():
            self.send_error(403, "Forbidden"); return
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "JSON invalide"}); return

        try:
            if path == "/api/config":
                self._handle_save_config(body); return
            if path == "/api/update/install":
                self._handle_update_install(); return
            if path == "/api/update/restart":
                self._handle_update_restart(); return
            if path == "/api/corpus/schema":
                self._handle_rag_schema(body); return
            m = re.match(r"^/api/corpus/lots/([\w\-]+)/download$", path)
            if m:
                self._handle_rag_lot_download(m.group(1), body); return
            if path == "/api/corpus/create":
                self._handle_corpus_create(body); return
            if path == "/api/corpus/depot/fetch":
                self._handle_depot_fetch(body); return
            if path == "/api/corpus/depot/import":
                self._handle_depot_import(body); return
            if path == "/api/corpus/md/zip":
                self._handle_md_zip(body); return
            if path == "/api/corpus/autofill":
                self._handle_rag_autofill(body); return
            if path == "/api/corpus/md-content":
                self._handle_rag_md_content(body); return
            if path == "/api/corpus/source-metadata":
                self._handle_rag_source_metadata(body); return
            if path == "/api/corpus/source-exclude":
                self._handle_rag_source_exclude(body); return
            if path == "/api/corpus/source-brief":
                self._handle_rag_source_brief(body); return
            if path == "/api/corpus/document/convert":
                self._handle_document_convert(body); return
            if path == "/api/corpus/sources/upload":
                self._handle_sources_upload(body); return
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"ok": False, "error": f"Erreur serveur : {exc}"})
            return

        self.send_error(404, "Not Found")

    def _handle_update_install(self) -> None:
        # Ce fork ne se met pas a jour depuis l'amont : les paquets publies
        # la-bas ne portent pas ses corrections, et les installer remplacerait
        # `app/` en entier. Voir `modules/updates.VERIFICATION_ACTIVE`.
        if not updates.VERIFICATION_ACTIVE:
            self._send_json(409, {"ok": False, "error": (
                "La mise à jour automatique est désactivée dans cette "
                "version. Les paquets publiés en amont ne contiennent pas "
                "les modifications de ce dépôt et les remplaceraient.")})
            return
        active = jobs.active()
        if active:
            self._send_json(409, {
                "ok": False,
                "error": ("Une tâche est en cours : " + _decrire_taches(active)
                          + ". Attendez sa fin avant la mise à jour."),
                "active_jobs": len(active),
            }); return
        manifest = updates.update_status(force=True)
        if not manifest.get("ok"):
            self._send_json(503, {"ok": False, "error": manifest.get("error")}); return
        if not manifest.get("update_available"):
            self._send_json(409, {"ok": False, "error": "META-MD est déjà à jour."}); return
        if not manifest.get("installable"):
            self._send_json(409, {
                "ok": False,
                "error": "Cette version ne fournit pas encore de paquet installable.",
            }); return

        job_id = jobs.create_job(kind=f"update-install:{manifest['latest']}")

        def install_update() -> dict[str, Any]:
            package = updates.download_package(
                manifest,
                DATA_DIR.parent / "lanceur" / "telechargements",
                jobs.make_progress_callback(job_id),
            )
            updater = DATA_DIR.parent / "lanceur" / "updater.py"
            if not updater.is_file():
                raise FileNotFoundError("l'installateur stable est introuvable")
            completed = subprocess.run(
                [sys.executable, str(updater), str(package), str(manifest["sha256"])],
                cwd=DATA_DIR,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if completed.returncode:
                detail = (completed.stderr or completed.stdout).strip()
                raise RuntimeError(detail or "installation du paquet impossible")
            # Le paquet est deploye : l'archive n'a plus de raison de rester.
            package.unlink(missing_ok=True)
            return {"ok": True, "version": manifest["latest"]}

        jobs.run_in_background(job_id, install_update)
        self._send_json(200, {
            "ok": True,
            "job_id": job_id,
            "version": manifest["latest"],
        })

    def _handle_update_restart(self) -> None:
        global _RESTART_REQUESTED
        if not os.environ.get("METAMD_LAUNCHER_PID"):
            self._send_json(409, {
                "ok": False,
                "error": "Relancez META-MD avec son script d'ouverture pour activer la version.",
            }); return
        if jobs.active():
            self._send_json(409, {
                "ok": False,
                "error": ("Une tâche est encore en cours : "
                          + _decrire_taches(jobs.active())
                          + ". Redémarrage refusé."),
            }); return
        _RESTART_REQUESTED = True
        self._send_json(200, {"ok": True, "restarting": True})

        def stop_after_response() -> None:
            time.sleep(0.3)
            if _SERVER is not None:
                _SERVER.shutdown()

        threading.Thread(target=stop_after_response, daemon=True).start()

    # --- Router DELETE ---

    def do_DELETE(self):  # noqa: N802
        path = self.path.split("?", 1)[0]

        # DELETE /api/corpus/source?corpus=&source=
        # Retire le PDF de 1-Sources/ et tout ce qui en derive : conversions
        # et sidecar. Les laisser ferait qu'un PDF redepose sous le meme nom
        # reprendrait l'etat de l'ancien.
        if path == "/api/corpus/source":
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            rag = (qs.get("corpus", [""])[0] or "").strip()
            source = (qs.get("source", [""])[0] or "").strip()
            src_path = self._resolve_source(rag, source)
            if src_path is None:
                self._send_json(400, {"ok": False, "error": "rag/source invalide"}); return
            if not src_path.is_file():
                self._send_json(404, {"ok": False, "error": "Document introuvable."}); return
            theme_dir = CORPUS_DIR / rag
            stem = src_path.stem
            # Les conversions d'abord : si la suppression du PDF echoue, on
            # ne laisse pas un document a moitie demonte avec son PDF en place.
            removed: list[str] = list(
                corpus.documents.delete_document(theme_dir, stem).get("removed", []))
            try:
                src_path.unlink()
            except OSError as exc:
                self._send_json(500, {"ok": False,
                    "error": f"Suppression impossible : {exc}", "removed": removed}); return
            removed.append(f"1-Sources/{src_path.name}")
            logger.info("Document supprime de %s : %s (%d fichiers)",
                        rag, src_path.name, len(removed))
            self._send_json(200, {"ok": True, "removed": removed}); return

        self.send_error(404, "Not Found")

    # --- Helpers metier ---

    def _resolve_source(self, rag: str, source: str) -> Path | None:
        if not _valid_rag_name(rag):
            return None
        if not source or "/" in source or "\\" in source or source.startswith("."):
            return None
        if source.endswith(corpus.metadata.METADATA_SUFFIX):
            return None
        theme_dir = CORPUS_DIR / rag
        sources_dir = theme_dir / "1-Sources"
        candidate = sources_dir / source
        if not candidate.is_file():
            return None
        try:
            candidate.resolve().relative_to(sources_dir.resolve())
        except ValueError:
            return None
        return candidate

    # --- Handlers ---

    def _handle_get_documents(self) -> None:
        """GET /api/corpus/documents?corpus=X : liste tous les docs avec historique + active."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        rag = (qs.get("corpus", [""])[0] or "").strip()
        if not _valid_rag_name(rag):
            self._send_json(400, {"ok": False, "error": "rag invalide"}); return
        theme_dir = CORPUS_DIR / rag
        sources_dir = theme_dir / "1-Sources"
        if not sources_dir.is_dir():
            self._send_json(200, {"ok": True, "corpus": rag, "documents": []}); return
        schema = corpus.schema.read_schema(theme_dir)
        schema_fields = schema.get("fields") or []
        docs: list[dict[str, Any]] = []
        for src in sorted(sources_dir.iterdir(), key=lambda p: p.name.lower()):
            if not src.is_file() or corpus.metadata.is_metadata_file(src):
                continue
            if src.name.startswith("_"):
                continue
            # Filtre les docs marques "a ne pas traiter" a l'etape 1 : ils
            # n'apparaissent pas dans la page conversions.
            if corpus.metadata.is_excluded(src):
                continue
            try:
                size = src.stat().st_size
            except OSError:
                size = None
            pages: int | None = None
            # Ce que le PDF dit de lui-meme : de quoi conseiller un moteur a
            # l'etape 2 sur une mesure plutot que sur une habitude. Le
            # diagnostic compte les pages, on n'ouvre donc pas deux fois.
            diagnostic: dict[str, Any] | None = None
            if src.suffix.lower() == ".pdf":
                diagnostic = pipeline.diagnostic_pdf(src)
                pages = diagnostic["pages"] or None
            conversions = corpus.documents.list_conversions(theme_dir, src.stem)
            active_id = corpus.documents.get_active_id(theme_dir, src.stem)
            sidecar = corpus.metadata.read_metadata(src)
            md_valid = bool(sidecar.get("md_valid"))
            metadata_valid = bool(sidecar.get("metadata_valid"))
            active_md_url: str | None = None
            if active_id:
                # 2-Conversions/<stem>.md
                active_md_url = _url_static(
                    rag, corpus.documents.CONVERSIONS_DIR, f"{src.stem}.md")
            docs.append({
                "source_name": src.name,
                "stem": src.stem,
                "title": _document_title(sidecar, schema_fields),
                "size": size,
                "pages": pages,
                "diagnostic": diagnostic,
                "conversions": conversions,
                "active_id": active_id,
                # Vide pour un document venu d'un site : sa source est le
                # Markdown lui-meme, et l'ouvrir dans le cadre PDF du viewer
                # n'afficherait que du texte brut. Le viewer bascule alors en
                # volet unique.
                "pdf_url": ("" if src.suffix.lower() == ".md"
                             else _url_static(rag, "1-Sources", src.name)),
                "active_md_url": active_md_url,
                "md_valid": md_valid,
                "metadata_valid": metadata_valid,
                # Dit si cette validation vient du bouton « tout valider » :
                # c'est ce qui rend l'annulation en lot possible apres une
                # fermeture du navigateur.
                "validated_in_bulk": bool(sidecar.get("validated_in_bulk")),
                "segment": corpus.metadata.get_segment_params(src),
            })
        # `orphans=1` ajoute les Markdown qui n'ont plus de source dans
        # `1-Sources/` — renommee, retiree, ou marquee « a ne pas traiter ».
        # L'etape 5 les demande : le fichier existe, il doit pouvoir partir
        # dans l'archive. Les autres pages ne les veulent pas : la conversion
        # et la validation travaillent sur des sources, et une ligne sans
        # source n'y aurait aucun bouton.
        if (qs.get("orphans", [""])[0] or "").strip() in ("1", "true", "yes"):
            connus = {d["stem"] for d in docs}
            for stem in corpus.documents.list_markdowns(theme_dir):
                if stem in connus:
                    continue
                sidecar = corpus.metadata.read_by_stem(theme_dir, stem)
                docs.append({
                    "source_name": "",
                    "stem": stem,
                    "title": _document_title(sidecar, schema_fields),
                    "size": None,
                    "pages": None,
                    "diagnostic": None,
                    "conversions": corpus.documents.list_conversions(theme_dir, stem),
                    "active_id": corpus.documents.get_active_id(theme_dir, stem),
                    "pdf_url": "",
                    "active_md_url": _url_static(
                        rag, corpus.documents.CONVERSIONS_DIR, f"{stem}.md"),
                    "md_valid": bool(sidecar.get("md_valid")),
                    "metadata_valid": bool(sidecar.get("metadata_valid")),
                    "validated_in_bulk": bool(sidecar.get("validated_in_bulk")),
                    "segment": {},
                    # Dit a l'interface que la piece d'origine est introuvable :
                    # la ligne s'affiche, sans promettre un PDF a ouvrir.
                    "sans_source": True,
                })
            docs.sort(key=lambda d: str(d["stem"]).lower())
        self._send_json(200, {
            "ok": True,
            "corpus": rag,
            "documents": docs,
            # La page des conversions en a besoin pour griser ses boutons :
            # sans schema verrouille, la conversion sera refusee, autant le
            # montrer avant le clic plutot que de le dire apres.
            "schema_locked": bool(schema.get("locked")),
        })

    def _handle_sources_upload(self, body: dict[str, Any]) -> None:
        """POST /api/corpus/sources/upload : {rag, files: [{name, data_b64, source_url?}]}.

        Depose des PDF dans `1-Sources/`, en creant le dossier au besoin.
        `source_url` est optionnelle : quand le PDF depose existe aussi en
        ligne, elle est ecrite dans son sidecar et suivra le meme chemin que
        celle d'un lot, jusqu'aux metadonnees des chunks.
        Les fichiers arrivent en base64 dans le corps JSON plutot qu'en
        multipart : le serveur est un BaseHTTPRequestHandler de la stdlib,
        et `cgi` (seul parseur multipart integre) est deprecie puis retire
        a partir de Python 3.13.
        """
        import base64
        rag = (body.get("corpus") or "").strip()
        if not _valid_rag_name(rag):
            self._send_json(400, {"ok": False, "error": "rag invalide"}); return
        raw = body.get("files")
        if not isinstance(raw, list) or not raw:
            self._send_json(400, {"ok": False, "error": "Aucun fichier fourni."}); return

        sources_dir = CORPUS_DIR / rag / "1-Sources"
        try:
            sources_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._send_json(500, {"ok": False,
                "error": f"Creation de 1-Sources/ impossible : {exc}"}); return

        saved: list[str] = []
        replaced: list[str] = []
        errors: list[dict[str, str]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = Path(str(entry.get("name") or "")).name.strip()
            # Le nom est ramene a son basename : un chemin dans le nom de
            # fichier ne doit pas permettre d'ecrire hors de 1-Sources/.
            if not name or name.startswith(".") or name.startswith("_"):
                errors.append({"name": name or "?", "error": "Nom de fichier invalide."})
                continue
            if not name.lower().endswith(".pdf"):
                errors.append({"name": name, "error": "Seuls les PDF sont acceptes."})
                continue
            try:
                data = base64.b64decode(str(entry.get("data_b64") or ""), validate=True)
            except Exception:  # noqa: BLE001
                errors.append({"name": name, "error": "Contenu illisible."})
                continue
            if not data:
                errors.append({"name": name, "error": "Fichier vide."})
                continue
            # L'extension est declarative : seul l'en-tete dit si c'en est un.
            if not data.startswith(b"%PDF-"):
                errors.append({"name": name,
                    "error": "Le contenu n'est pas un PDF (en-tete %PDF- absent)."})
                continue
            target = sources_dir / name
            existed = target.exists()
            try:
                target.write_bytes(data)
            except OSError as exc:
                errors.append({"name": name, "error": f"Ecriture impossible : {exc}"})
                continue
            (replaced if existed else saved).append(name)

            # Ecriture partielle : un PDF redepose ne doit pas perdre l'etat
            # deja accumule dans son sidecar. Une valeur vide n'efface rien
            # ici, c'est le viewer qui sert a retirer une URL ou un titre.
            patch: dict[str, Any] = {}
            url = str(entry.get("source_url") or "").strip()
            if url:
                patch["source_url"] = url
            # Titre saisi au depot : marque `manual`, donc l'auto-fill LLM
            # qui suit la conversion ne le remplacera pas (merge_autofill
            # preserve toujours les valeurs manuelles). Les autres champs du
            # sidecar sont relus puis reecrits : `fields` est remplace en
            # bloc par update_metadata.
            title = str(entry.get("title") or "").strip()
            if title:
                fields = dict(corpus.metadata.read_metadata(target).get("fields") or {})
                fields["titre"] = {"value": title, "source": corpus.metadata.SOURCE_MANUAL}
                patch["fields"] = fields
            if patch:
                try:
                    corpus.metadata.update_metadata(target, patch)
                except OSError as exc:
                    logger.warning("sidecar non ecrit pour %s : %s", name, exc)

        logger.info("Depot de sources dans %s : %d ajoutes, %d remplaces, %d erreurs",
                    rag, len(saved), len(replaced), len(errors))
        self._send_json(200, {
            "ok": bool(saved or replaced),
            "saved": saved,
            "replaced": replaced,
            "errors": errors,
        })

    def _handle_document_convert(self, body: dict[str, Any]) -> None:
        """POST /api/corpus/document/convert : {rag, source, engine, overwrite?} -> job."""
        rag = (body.get("corpus") or "").strip()
        source = (body.get("source") or "").strip()
        engine = ((body.get("engine") or "").strip().lower()
          or pipeline.SUPPORTED_ENGINES[0])
        if engine not in pipeline.SUPPORTED_ENGINES:
            self._send_json(400, {"ok": False, "error": (
                f"Moteur inconnu : {engine}. Moteurs disponibles : "
                + ", ".join(pipeline.SUPPORTED_ENGINES))}); return
        src_path = self._resolve_source(rag, source)
        if src_path is None:
            self._send_json(400, {"ok": False, "error": "rag/source invalide"}); return
        theme_dir = CORPUS_DIR / rag
        # Le schema precede la conversion, et la verrouille tant qu'il n'est
        # pas arrete. La raison tenait au moteur Mistral, qui lisait les champs
        # dans le PDF pendant la conversion ; il a ete retire, mais le verrou
        # reste, pour celle qui vaut aussi a l'import : le front matter est
        # ecrit au moment ou le document entre dans `2-Conversions/`, et il
        # suit le schema cle pour cle. Un schema decide apres coup laisserait
        # des Markdown aux cles differentes dans un meme corpus.
        schema = corpus.schema.read_schema(theme_dir)
        if not schema.get("locked"):
            self._send_json(409, {
                "ok": False, "schema_locked": False,
                "error": ("Le schéma de ce corpus n'est pas verrouillé. "
                          "Arrêtez les champs à l'étape 2, puis verrouillez : "
                          "le front matter des Markdown le suit clé pour clé."),
            }); return
        # Un seul fichier par couple (document, moteur) : reconvertir avec le
        # meme moteur ecrase le precedent, et donc les corrections manuelles
        # qui y avaient ete faites. On ne le fait pas sans accord explicite.
        if (corpus.documents.conversion_exists(theme_dir, src_path.stem)
                and not body.get("overwrite")):
            self._send_json(409, {
                "ok": False, "exists": True, "engine": engine,
                "error": (f"Une conversion {engine} existe déjà pour ce document. "
                          "La relancer ecrasera ce fichier et les corrections "
                          "manuelles qu'il contient."),
            }); return

        # Le seul moteur restant passe par Albert : sans cle, rien a tenter.
        albert_key = get_api_key()
        if not albert_key:
            self._send_json(400, {"ok": False,
                "error": "Clé Albert non configurée."}); return

        cid = corpus.documents.slug_engine(engine)
        dest = corpus.documents.conversion_path(theme_dir, src_path.stem)
        kind = f"convert-doc:{src_path.name}:{engine}"
        en_vol = jobs.deja_en_cours(kind)
        if en_vol:
            # On rend la tache en cours plutot qu'une erreur : la page se
            # rebranche dessus, et un double clic devient sans effet.
            self._send_json(200, {"ok": True, "job_id": en_vol,
                                  "deja_en_cours": True}); return
        job_id = jobs.create_job(kind=kind)

        def _do_convert() -> dict[str, Any]:
            progress_cb = jobs.make_progress_callback(job_id)
            # Le nom du document, et rien d'autre : le moteur ne renvoie plus
            # de libelle par page — la barre affiche deja « 3 / 63 » en bout
            # de ligne — donc ce texte reste affiche jusqu'a la fin. « en
            # preparation » y deviendrait faux au bout de deux secondes.
            progress_cb(processed=0, total=0, current=src_path.name)
            r = pipeline.convert_one_pdf(src_path, dest, engine,
                                         albert_key=albert_key,
                                         progress_callback=progress_cb)
            if not r.get("ok"):
                return {"ok": False, "error": r.get("error", "conversion KO")}
            # Le PDF part a cote de son Markdown : le dossier du moteur
            # s'ouvre alors tel quel, chaque document face a sa source.
            corpus.documents.poser_la_source(dest, src_path)
            # Devient la conversion active du doc. force=True : meme si le
            # moteur actif ne change pas, le Markdown vient d'etre reecrit et
            # n'a donc pas ete relu -> md_valid repasse a false.
            corpus.documents.enregistrer_le_moteur(theme_dir, src_path.stem, cid)
            # Le MD neuf ne porte qu'un front matter technique : on y reporte
            # ce que le sidecar sait deja (valeurs posees par un lot, ou
            # corrigees a la main). Le remplissage LLM, lui, est une etape a
            # part : il ne se declenche plus en douce derriere une conversion.
            corpus.frontmatter.sync_front_matter(theme_dir, src_path)
            progress_cb(processed=0, total=0, current="finalisation…")
            return {
                "ok": True,
                "conversion_id": cid,
                "bytes": r.get("bytes"),
                "duration_s": r.get("duration_s"),
                "pages": r.get("pages"),
            }

        jobs.run_in_background(job_id, _do_convert)
        self._send_json(200, {"ok": True, "job_id": job_id, "conversion_id": cid})

    def _handle_rag_lot_download(self, slug: str, body: dict[str, Any]) -> None:
        rag = (body.get("corpus") or "").strip()
        if not _valid_rag_name(rag):
            self._send_json(400, {"ok": False, "error": "rag invalide"}); return
        if corpus.lots.get_lot(slug) is None:
            self._send_json(404, {"ok": False, "error": f"Lot inconnu : {slug}"}); return
        raw_entries = body.get("entries")
        entry_ids: list[str] | None = None
        if isinstance(raw_entries, list) and raw_entries:
            entry_ids = [str(e).strip() for e in raw_entries if str(e).strip()]
            if not entry_ids:
                entry_ids = None
        theme_dir = CORPUS_DIR / rag
        theme_dir.mkdir(parents=True, exist_ok=True)
        schema_applied = self._apply_lot_schema(theme_dir, slug)
        kind = f"lot-download:{slug}"
        en_vol = jobs.deja_en_cours(kind)
        if en_vol:
            self._send_json(200, {"ok": True, "job_id": en_vol,
                                  "deja_en_cours": True}); return
        job_id = jobs.create_job(kind=kind)
        jobs.run_in_background(
            job_id,
            corpus.lots.download_lot,
            kwargs={
                "slug": slug,
                "theme_dir": theme_dir,
                "entry_ids": entry_ids,
                "progress_callback": jobs.make_progress_callback(job_id),
            },
        )
        self._send_json(200, {"ok": True, "job_id": job_id,
                              "schema_applied": schema_applied})

    def _apply_lot_schema(self, theme_dir: Path, slug: str) -> bool:
        """Pose le schema du lot sur le theme, si c'est sans risque.

        La section `metadata` d'un lot decrit exactement les champs de ses
        documents : l'appliquer au telechargement evite un geste manuel.
        `tags` et `resume` s'y ajoutent, que le lot ne peut pas connaitre.
        Un seul cas fait renoncer : un schema VERROUILLE. Verrouiller est un
        acte explicite (« Valider et verrouiller » a l'etape 2), donc une
        decision a respecter. La simple presence de `schema.yaml` n'en est
        pas une : le fichier s'ecrit des la premiere modification, souvent
        sans que rien n'ait ete vraiment choisi.

        Changer les cles remet `metadata_valid` a false sur les sidecars
        deja remplis : c'est le prix, et `write_schema` le compte.
        Retourne True si le schema du lot a ete pose.
        """
        template = corpus.lots.get_schema_template(slug)
        if not template:
            return False
        if corpus.schema.read_schema(theme_dir).get("locked"):
            logger.info("Schema du theme %s conserve : il est verrouille.",
                        theme_dir.name)
            return False
        try:
            corpus.schema.write_schema(theme_dir, {"locked": False, "fields": template})
        except OSError as exc:
            logger.warning("Ecriture du schema du lot %s KO : %s", slug, exc)
            return False
        logger.info("Schema du lot %s applique au theme %s (%d champs)",
                    slug, theme_dir.name, len(template))
        return True



    def _handle_md_zip(self, body: dict[str, Any]) -> None:
        """Renvoie une archive des documents demandes : Markdown et source.

        Le Markdown ne voyage pas seul. Ce qui le lit ensuite veut pouvoir
        remonter a la piece d'origine — un site qui publie les deux cote a
        cote, une relecture qui compare la conversion a la page imprimee —
        et le PDF sort d'ici sous le meme nom que le `.md`, ce qui suffit a
        les rapprocher a l'arrivee. L'archive est donc a plat : deux fichiers
        de meme nom par document, rien a ranger.

        L'archive se fabrique en memoire. Les Markdown pesaient quelques
        centaines de kilo-octets ; avec les PDF on passe a quelques
        mega-octets, ce qu'une reponse HTTP porte encore sans peine, la ou
        un fichier temporaire laisserait des traces a nettoyer.
        """
        rag = (body.get("corpus") or "").strip()
        if not _valid_rag_name(rag):
            self._send_json(400, {"ok": False, "error": "rag invalide"}); return
        theme_dir = CORPUS_DIR / rag

        # Ce qui existe sur le disque fait foi. On part des Markdown poses
        # dans `2-Conversions/`, et non des couples (source, conversion) :
        # un document dont la source a ete renommee ou retiree n'a plus de
        # couple, mais son Markdown est la et se telecharge.
        disponibles = corpus.documents.list_markdowns(theme_dir)
        if not disponibles:
            self._send_json(404, {"ok": False, "error": (
                "Aucun Markdown dans ce corpus : convertissez au moins un "
                "document avant de telecharger.")}); return

        # La selection filtre, elle ne commande pas. Vide, ou ne designant
        # rien qui existe, elle emporte tout ce que le corpus contient :
        # une archive complete vaut mieux qu'un refus.
        demandes = [str(x).strip() for x in (body.get("stems") or []) if str(x).strip()]
        retenus = [s for s in disponibles if s in set(demandes)] or disponibles

        tampon = io.BytesIO()
        pris = 0
        avec_source = 0
        with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
            for stem in retenus:
                md = corpus.documents.conversion_path(theme_dir, stem)
                if not md.is_file():
                    continue
                # Un nom par document : c'est la conversion active, il ne
                # peut pas y en avoir deux pour le meme stem.
                archive.write(md, arcname=f"{stem}.md")
                pris += 1
                src = corpus.documents.source_for_stem(theme_dir, stem)
                # La source est un bonus, pas une condition. Absente, le
                # Markdown part seul : c'est lui qu'on est venu chercher.
                if src is not None and src.is_file() and src.suffix.lower() != ".md":
                    # Le PDF est deja compresse : le redeflater ne gagnerait
                    # rien et couterait le temps de le relire.
                    archive.write(src, arcname=src.name,
                                  compress_type=zipfile.ZIP_STORED)
                    avec_source += 1
        if not pris:
            self._send_json(404, {"ok": False, "error": (
                "Les Markdown de ce corpus n'ont pas pu etre lus.")}); return

        data = tampon.getvalue()
        logger.info("ZIP %s : %d Markdown, %d source(s), %d Ko",
                    rag, pris, avec_source, len(data) // 1024)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{rag}-documents.zip"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


    def _handle_corpus_create(self, body: dict[str, Any]) -> None:
        """Cree le dossier d'un corpus, vide.

        Un corpus n'est rien d'autre qu'un dossier de `CORPUS/` avec son
        `1-Sources/` : le creer ici evite d'aller le faire a la main dans
        l'explorateur, et le nom saisi devient le nom du corpus.
        """
        # Le slug fait foi : c'est lui qui devient le dossier, quel que
        # soit ce qui a ete tape.
        nom = _corpus_slug((body.get("corpus") or "").strip())
        if not nom:
            self._send_json(400, {"ok": False,
                "error": "Ce nom ne laisse aucun caractere utilisable."}); return
        source = str(body.get("source") or corpus.nature.SOURCE_PDF).strip()
        if source not in corpus.nature.ALLOWED_SOURCES:
            self._send_json(400, {"ok": False,
                "error": f"Source inconnue : {source}"}); return
        theme_dir = CORPUS_DIR / nom
        if theme_dir.exists():
            self._send_json(409, {"ok": False, "error": f"Le corpus « {nom} » existe déjà."}); return
        try:
            (theme_dir / "1-Sources").mkdir(parents=True)
        except OSError as exc:
            self._send_json(500, {"ok": False, "error": f"Creation impossible : {exc}"}); return
        # La source est fixee a la creation et ne change plus : elle decide de
        # l'etape 1 et de l'etape 3, qui ne peuvent pas etre deux choses a la fois.
        corpus.nature.write_corpus(theme_dir, {"source": source})
        logger.info("Corpus cree : %s (source %s)", nom, source)
        self._send_json(200, {"ok": True, "corpus": nom, "source": source})

    def _handle_depot_fetch(self, body: dict[str, Any]) -> None:
        """Recupere un depot public et depose ses Markdown dans 1-Sources/.

        En job, comme le telechargement d'un lot : une archive de site se
        compte en dizaines de Mo et la barre de progression existante sait
        deja rendre compte d'une tache longue.
        """
        rag = (body.get("corpus") or "").strip()
        if not _valid_rag_name(rag):
            self._send_json(400, {"ok": False, "error": "rag invalide"}); return
        theme_dir = CORPUS_DIR / rag
        if not theme_dir.is_dir():
            self._send_json(400, {"ok": False, "error": f"Corpus introuvable : {rag}"}); return
        if not corpus.nature.vient_d_un_depot(theme_dir):
            self._send_json(409, {"ok": False,
                "error": "Ce corpus part de PDF, pas d'un depot."}); return

        url = str(body.get("url") or "").strip()
        if not url:
            self._send_json(400, {"ok": False, "error": "Renseignez l'adresse du depot."}); return
        # L'URL est validee tout de suite : inutile de lancer un job pour
        # decouvrir dans dix secondes qu'elle ne designe pas un depot.
        try:
            corpus.depot.analyser_url(url)
        except corpus.depot.DepotError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)}); return

        kind = f"depot-fetch:{rag}"
        en_vol = jobs.deja_en_cours(kind)
        if en_vol:
            self._send_json(200, {"ok": True, "job_id": en_vol,
                                  "deja_en_cours": True}); return
        job_id = jobs.create_job(kind=kind)
        jobs.run_in_background(
            job_id,
            corpus.importation.recuperer,
            kwargs={
                "theme_dir": theme_dir,
                "url": url,
                "ref": (str(body.get("ref") or "").strip() or None),
                "site_url": str(body.get("site_url") or "").strip(),
                "progress_callback": jobs.make_progress_callback(job_id),
            },
        )
        self._send_json(200, {"ok": True, "job_id": job_id})

    def _handle_depot_import(self, body: dict[str, Any]) -> None:
        """Copie les pages retenues vers 2-Conversions/.

        Garde par le verrou du schema, comme la conversion : le front matter
        est ecrit ici, il faut donc connaitre les champs avant de partir.
        """
        rag = (body.get("corpus") or "").strip()
        if not _valid_rag_name(rag):
            self._send_json(400, {"ok": False, "error": "rag invalide"}); return
        theme_dir = CORPUS_DIR / rag
        if not theme_dir.is_dir():
            self._send_json(400, {"ok": False, "error": f"Corpus introuvable : {rag}"}); return
        if not corpus.schema.read_schema(theme_dir).get("locked"):
            self._send_json(409, {"ok": False, "schema_locked": False,
                "error": "Verrouillez le schema a l'etape 2 avant d'importer."}); return

        raw = body.get("stems")
        stems = [str(s).strip() for s in raw if str(s).strip()] if isinstance(raw, list) else None
        kind = f"depot-import:{rag}"
        en_vol = jobs.deja_en_cours(kind)
        if en_vol:
            self._send_json(200, {"ok": True, "job_id": en_vol,
                                  "deja_en_cours": True}); return
        job_id = jobs.create_job(kind=kind)
        jobs.run_in_background(
            job_id,
            corpus.importation.importer,
            kwargs={
                "theme_dir": theme_dir,
                "stems": stems,
                "overwrite": bool(body.get("overwrite")),
                "forcer_sans_texte": bool(body.get("forcer_sans_texte")),
                "progress_callback": jobs.make_progress_callback(job_id),
            },
        )
        self._send_json(200, {"ok": True, "job_id": job_id})

    def _handle_rag_schema(self, body: dict[str, Any]) -> None:
        rag = (body.get("corpus") or "").strip()
        if not _valid_rag_name(rag):
            self._send_json(400, {"ok": False, "error": "rag invalide"}); return
        theme_dir = CORPUS_DIR / rag
        if not theme_dir.is_dir():
            self._send_json(400, {"ok": False, "error": f"Thème introuvable : {rag}"}); return
        raw = body.get("schema")
        if not isinstance(raw, dict):
            self._send_json(400, {"ok": False, "error": "schema doit etre un objet"}); return
        try:
            cascade = corpus.schema.write_schema(theme_dir, raw)
        except OSError as exc:
            self._send_json(500, {"ok": False, "error": f"Ecriture impossible : {exc}"}); return
        self._send_json(200, {
            "ok": True,
            "schema": corpus.schema.read_schema(theme_dir),
            "cascade": cascade,
        })

    def _handle_rag_autofill(self, body: dict[str, Any]) -> None:
        """POST /api/corpus/autofill : {corpus, source?, only_missing?}.

        Etape 4 du parcours. Sans `source`, tous les documents ayant un
        Markdown actif y passent, en tache de fond : un appel LLM par
        document, ca ne tient pas dans une requete HTTP.

        `only_missing` vaut vrai par defaut : l'etape s'appelle « remplir les
        champs vides », et redemander au modele ce qui est deja renseigne
        coute des jetons pour une reponse qui sera de toute facon ecartee si
        la valeur en place est manuelle.
        """
        rag = (body.get("corpus") or "").strip()
        if not _valid_rag_name(rag):
            self._send_json(400, {"ok": False, "error": "rag invalide"}); return
        theme_dir = CORPUS_DIR / rag
        if not theme_dir.is_dir():
            self._send_json(400, {"ok": False, "error": f"Thème introuvable : {rag}"}); return
        schema = corpus.schema.read_schema(theme_dir)
        # Le verrou du schema garde cette etape, et elle seule : tant que les
        # champs bougent, depenser des appels dessus n'a pas de sens.
        if not schema.get("locked"):
            self._send_json(409, {"ok": False, "error": (
                "Le schema des metadonnees n'est pas verrouille : "
                "validez-le avant de lancer le remplissage.")}); return
        fields = corpus.schema.autofill_fields(schema.get("fields") or [])
        if not fields:
            self._send_json(400, {"ok": False,
                "error": "Aucun champ a remplir dans ce schema."}); return
        if not get_api_key():
            self._send_json(400, {"ok": False,
                "error": "Clé Albert non configurée."}); return
        only_missing = body.get("only_missing")
        only_missing = True if only_missing is None else bool(only_missing)
        source = (body.get("source") or "").strip()

        if source:
            src_path = self._resolve_source(rag, source)
            if src_path is None:
                self._send_json(400, {"ok": False, "error": "source invalide"}); return
            active_md = corpus.documents.get_active_path(theme_dir, src_path.stem)
            if active_md is None:
                self._send_json(400, {"ok": False,
                    "error": "Aucune conversion active pour ce document."}); return
            result = autofill.autofill_one(
                md_path=active_md, source_path=src_path,
                schema_fields=fields,
                albert_key=get_api_key() or None,
                only_missing=only_missing,
                albert_model=get_autofill_model(),
                max_input_chars=get_autofill_max_chars(),
            )
            self._send_json(200 if result.get("ok") else 400, result); return

        # Comme le telechargement : le Markdown pose sur le disque fait foi.
        # Un document dont la source a ete renommee ou retiree garde son
        # `.md`, son sidecar et donc ses champs a remplir. `sidecar_source_
        # for_stem` donne le chemin qui mene au bon sidecar meme quand le
        # fichier d'origine n'existe plus.
        #
        # Le chemin passe ici n'est pas celui du fichier a lire : c'est celui
        # par lequel `metadata_path_for` retrouve le sidecar. Il doit donc
        # toujours pointer dans `1-Sources/`, meme quand rien ne s'y trouve —
        # la copie du PDF posee dans `2-Conversions/` menerait le sidecar a
        # s'ecrire a cote d'elle, hors de `_metadata/`.
        targets: list[tuple[Path, Path]] = []
        for stem in corpus.documents.list_markdowns(theme_dir):
            md = corpus.documents.conversion_path(theme_dir, stem)
            src = corpus.metadata.sidecar_source_for_stem(theme_dir, stem)
            if corpus.metadata.is_excluded(src):
                continue
            targets.append((src, md))
        if not targets:
            self._send_json(400, {"ok": False, "error": (
                "Aucun Markdown a remplir : convertissez au moins un "
                "document a l'etape 3.")}); return
        kind = f"fill:{rag}"
        en_vol = jobs.deja_en_cours(kind)
        if en_vol:
            self._send_json(200, {"ok": True, "job_id": en_vol,
                                  "deja_en_cours": True}); return
        job_id = jobs.create_job(kind=kind)

        def _do_fill() -> dict[str, Any]:
            progress_cb = jobs.make_progress_callback(job_id)
            total = len(targets)
            progress_cb(processed=0, total=total, current=None)
            filled = 0
            skipped = 0
            errors: list[dict[str, Any]] = []
            for done, (src, active) in enumerate(targets, start=1):
                r = autofill.autofill_one(
                    md_path=active, source_path=src,
                    schema_fields=fields,
                    albert_key=get_api_key() or None,
                    only_missing=only_missing,
                    albert_model=get_autofill_model(),
                    max_input_chars=get_autofill_max_chars(),
                )
                if r.get("ok"):
                    if r.get("filled"):
                        filled += 1
                    else:
                        skipped += 1
                else:
                    errors.append({"source": src.name, "error": r.get("error")})
                progress_cb(processed=done, total=total, current=src.name)
            return {"ok": True, "filled": filled, "skipped": skipped,
                    "errors": errors, "total": total}

        jobs.run_in_background(job_id, _do_fill)
        self._send_json(200, {"ok": True, "job_id": job_id, "total": len(targets)})

    def _handle_rag_md_content(self, body: dict[str, Any]) -> None:
        """POST /api/corpus/md-content : {rag, source, content} -> ecrit dans
        la conversion active du doc.
        """
        rag = (body.get("corpus") or "").strip()
        source = (body.get("source") or "").strip()
        content = body.get("content")
        src_path = self._resolve_source(rag, source)
        if src_path is None:
            self._send_json(400, {"ok": False, "error": "rag/source invalide"}); return
        if not isinstance(content, str):
            self._send_json(400, {"ok": False, "error": "content doit etre une chaine"}); return
        theme_dir = CORPUS_DIR / rag
        active_md = corpus.documents.get_active_path(theme_dir, src_path.stem)
        if active_md is None:
            self._send_json(400, {"ok": False, "error": "Aucune conversion active."}); return
        try:
            active_md.write_text(content, encoding="utf-8")
        except OSError as exc:
            self._send_json(500, {"ok": False, "error": f"Ecriture impossible : {exc}"}); return
        # Le sidecar reste la source de verite des champs metier : on
        # renormalise le front matter depuis lui. Une modification faite a la
        # main dans le front matter du MD est donc annulee ; c'est voulu, ces
        # champs s'editent dans le panneau metadonnees.
        corpus.frontmatter.sync_front_matter(theme_dir, src_path)
        self._send_json(200, {"ok": True, "bytes": active_md.stat().st_size})

    def _handle_rag_source_metadata(self, body: dict[str, Any]) -> None:
        rag = (body.get("corpus") or "").strip()
        source = (body.get("source") or "").strip()
        src_path = self._resolve_source(rag, source)
        if src_path is None:
            self._send_json(400, {"ok": False, "error": "rag/source invalide"}); return
        raw = body.get("metadata")
        if raw is None:
            corpus.metadata.delete_metadata(src_path)
            self._send_json(200, {"ok": True, "deleted": True}); return
        if not isinstance(raw, dict):
            self._send_json(400, {"ok": False, "error": "metadata doit etre un objet"}); return
        try:
            # Ecriture partielle : le viewer n'envoie que md_valid /
            # metadata_valid / fields, il ne doit pas effacer le reste
            # (excluded, segment, active_engine).
            metadata = corpus.metadata.update_metadata(src_path, raw)
        except OSError as exc:
            self._send_json(500, {"ok": False, "error": f"Ecriture impossible : {exc}"}); return
        corpus.frontmatter.sync_front_matter(CORPUS_DIR / rag, src_path)
        self._send_json(200, {"ok": True, "metadata": metadata})

    def _handle_rag_source_brief(self, body: dict[str, Any]) -> None:
        """POST /api/corpus/source-brief {rag, source, title?, source_url?}.

        Les deux champs qu'on peut renseigner sans avoir lu le document :
        son titre et l'URL d'origine. Edites depuis l'inventaire de l'etape 1
        et poses au depot manuel. Le titre est ecrit en `manual`, donc
        l'auto-fill ne le remplacera pas ; vide, il est retire du sidecar.

        Fusion cote serveur : `update_metadata` remplace `fields` en bloc, un
        client qui n'enverrait que le titre effacerait les autres champs.
        """
        rag = (body.get("corpus") or "").strip()
        source = (body.get("source") or "").strip()
        src_path = self._resolve_source(rag, source)
        if src_path is None:
            self._send_json(400, {"ok": False, "error": "rag/source invalide"}); return
        patch: dict[str, Any] = {}
        if "title" in body:
            title = str(body.get("title") or "").strip()
            fields = dict(corpus.metadata.read_metadata(src_path).get("fields") or {})
            if title:
                fields["titre"] = {"value": title, "source": corpus.metadata.SOURCE_MANUAL}
            else:
                fields.pop("titre", None)
            patch["fields"] = fields
        if "source_url" in body:
            # Une URL invalide n'est pas ecrite : `_normalise_source_url` la
            # rejette, la valeur deja enregistree reste en place.
            patch["source_url"] = str(body.get("source_url") or "").strip()
        if not patch:
            self._send_json(400, {"ok": False, "error": "Rien a enregistrer."}); return
        try:
            metadata = corpus.metadata.update_metadata(src_path, patch)
        except OSError as exc:
            self._send_json(500, {"ok": False, "error": f"Ecriture impossible : {exc}"}); return
        corpus.frontmatter.sync_front_matter(CORPUS_DIR / rag, src_path)
        titre_entry = (metadata.get("fields") or {}).get("titre") or {}
        self._send_json(200, {
            "ok": True,
            "source": src_path.name,
            "title": str(titre_entry.get("value") or ""),
            "source_url": str(metadata.get("source_url") or ""),
        })

    def _handle_rag_source_exclude(self, body: dict[str, Any]) -> None:
        """POST /api/corpus/source-exclude {rag, source, excluded: bool}.

        Ecrit le flag `excluded` dans le sidecar du doc. Un doc exclu n'apparait
        plus dans la page conversions et n'est pas segmente. Refuse si le theme
        est frozen (le contenu de la reference ne doit pas etre modifie).
        """
        rag = (body.get("corpus") or "").strip()
        source = (body.get("source") or "").strip()
        excluded = bool(body.get("excluded"))
        src_path = self._resolve_source(rag, source)
        if src_path is None:
            self._send_json(400, {"ok": False, "error": "rag/source invalide"}); return
        try:
            corpus.metadata.update_metadata(src_path, {"excluded": excluded})
        except OSError as exc:
            self._send_json(500, {"ok": False, "error": f"Ecriture impossible : {exc}"}); return
        self._send_json(200, {"ok": True, "excluded": excluded,
                              "source": src_path.name})

    @staticmethod
    def _refus_de_cle(cle: str) -> str | None:
        """Ce qui ne peut pas etre une cle, dit avant que l'API le refuse.

        Coller le contenu entier d'un `config.json` dans le champ est une
        erreur facile a faire et impossible a diagnostiquer ensuite : la cle
        est enregistree telle quelle, et la premiere conversion echoue sur une
        erreur d'authentification qui ne dit rien de la cause.
        """
        if cle.startswith("{") or '"' in cle or "}" in cle:
            return ("On dirait le contenu d'un fichier de configuration, pas "
                    "une clé. Ne collez que la valeur de la clé, sans "
                    "accolades ni guillemets.")
        if any(c.isspace() for c in cle):
            return "Une clé ne contient ni espace ni retour à la ligne."
        if len(cle) > 512:
            return f"Cette valeur fait {len(cle)} caractères : c'est trop long pour une clé."
        if len(cle) < 20:
            return "Cette valeur est trop courte pour une clé Albert."
        return None

    def _handle_save_config(self, body: dict[str, Any]) -> None:
        cfg = load_config()
        touched = False
        akey = (body.get("albert_api_key") or "").strip()
        if akey:
            refus = self._refus_de_cle(akey)
            if refus:
                self._send_json(400, {"ok": False, "error": refus}); return
            cfg["albert_api_key"] = akey; touched = True
        if not touched:
            self._send_json(400, {"ok": False, "error": "Aucune clé fournie"}); return
        save_config(cfg)
        self._send_json(200, {"ok": True})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

class Serveur(ThreadingHTTPServer):
    """Le serveur de META-MD, qui ne s'alarme pas d'un client parti.

    Une page qu'on quitte, un onglet qu'on ferme, un sondage que le navigateur
    annule : la connexion se coupe pendant que la reponse s'ecrit, et
    `socketserver` en tire une trace de dix lignes a l'ecran. Elle designe
    `wfile.write` comme fautif, ce qui donne a lire une panne du serveur la ou
    il ne s'est rien passe — et noie les traces qui, elles, comptent. Ces
    ruptures-la passent en DEBUG ; tout le reste garde sa trace complete.
    """

    RUPTURES = (ConnectionAbortedError, ConnectionResetError,
                BrokenPipeError, TimeoutError)

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, self.RUPTURES):
            logger.debug("Client parti avant la fin de la reponse : %s", exc)
            return
        super().handle_error(request, client_address)


def _rafraichir_le_catalogue() -> None:
    """Va chercher les lots publies, dans un fil a part.

    En fond, et jamais bloquant : la Forge injoignable ne doit pas retarder
    l'ouverture de la page d'une seconde, encore moins des vingt du timeout.
    Le catalogue de la veille reste alors en place, et les lots livres avec
    l'application restent le socle. `rafraichir` ne leve pas et respecte son
    propre rythme d'un jour ; l'appeler a chaque demarrage ne coute donc rien
    quand META-MD est ouvert plusieurs fois dans la journee.
    """
    def travail() -> None:
        corpus.catalogue.rafraichir()

    threading.Thread(target=travail, name="catalogue-lots", daemon=True).start()


def main() -> int:
    global _SERVER
    _rattraper_le_socle()
    _watch_launcher()
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    corpus.lots.poser_le_dossier_utilisateur()
    # Sous Windows, SO_REUSEADDR laisse DEUX processus se lier au meme port :
    # les deux demarrent sans rien dire et les requetes tombent au hasard sur
    # l'un ou sur l'autre. On lance alors une version du code, on en lit une
    # autre, et rien a l'ecran ne l'explique. Mieux vaut echouer franchement.
    # Ailleurs, la reutilisation reste utile : elle evite d'attendre la fin du
    # TIME_WAIT pour redemarrer.
    if os.name == "nt":
        Serveur.allow_reuse_address = False
    # Apres une mise a jour, la version suivante demarre dans la seconde qui
    # suit l'arret de la precedente. Or la reutilisation d'adresse est
    # volontairement desactivee sous Windows (voir plus haut), donc le socket
    # de celle qui vient de sortir peut tenir le port encore un instant. Un
    # seul essai faisait echouer la nouvelle version, que le lanceur declarait
    # alors defaillante : la mise a jour semblait casser META-MD alors que rien
    # n'etait casse. On attend donc, sans renoncer au message clair quand le
    # port est tenu par un programme etranger.
    server = None
    debut = time.monotonic()
    derniere: OSError | None = None
    while time.monotonic() - debut < ATTENTE_PORT:
        try:
            server = Serveur(("127.0.0.1", PORT), Handler)
            break
        except OSError as exc:
            derniere = exc
            if time.monotonic() - debut < 0.5:
                logger.info("Port %d encore occupe, on patiente…", PORT)
            time.sleep(0.4)
    if server is None:
        logger.error("Impossible d'ouvrir le port %d apres %.0f s : %s",
                     PORT, ATTENTE_PORT, derniere)
        logger.error("Un serveur META-MD tourne probablement deja. "
                     "Fermez-le, ou relancez META-MD-win-2-ouvrir.bat qui s'en charge.")
        return 1
    _SERVER = server
    _rafraichir_le_catalogue()
    logger.info("Serveur pret : http://localhost:%d/", PORT)
    logger.info("Base dir : %s", BASE_DIR)
    if DATA_DIR != BASE_DIR:
        logger.info("Data dir : %s", DATA_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Arret demande, fermeture…")
    finally:
        # Sur le chemin du redemarrage, `serve_forever` rend la main sans
        # exception et le socket restait ouvert jusqu'a la fin du processus,
        # puis en TIME_WAIT. La version suivante, lancee dans la seconde, le
        # trouvait encore tenu. Le fermer ici raccourcit d'autant l'attente.
        try:
            server.server_close()
        except OSError:
            pass
    return RESTART_EXIT_CODE if _RESTART_REQUESTED else 0


if __name__ == "__main__":
    sys.exit(main())
