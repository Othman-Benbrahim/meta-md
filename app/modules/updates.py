"""Detection non bloquante des nouvelles versions de META-MD.

Le navigateur ne contacte jamais la Forge directement : il interroge le
serveur local, qui lit un petit manifeste JSON officiel. Aucun fichier
applicatif n'est encore remplace ici ; ce module ne fait qu'informer.

**Neutralise dans cette version.** Ce depot est un fork : le manifeste
distant decrit le projet d'origine, dont les paquets ne portent pas les
corrections faites ici. Accepter une mise a jour venue de la Forge
remplacerait `app/` en entier et effacerait ce fork sans prevenir.

`VERIFICATION_ACTIVE` commande tout : a False, aucune requete ne part, la
route `/api/update` annonce une version a jour et l'installation est
refusee. Le code de verification et d'installation est conserve intact, et
non supprime : remettre la constante a True suffit a le reactiver, une fois
les paquets republies ailleurs et `MANIFEST_URL` reecrite.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


# Le seul interrupteur. Voir le docstring du module.
VERIFICATION_ACTIVE = False

BASE_DIR = Path(__file__).parent.parent.resolve()
VERSION_FILE = BASE_DIR / "VERSION"
MANIFEST_URL = (
    "https://forge.apps.education.fr/meta-md/meta-md/-/releases/"
    "permalink/latest/downloads/update-manifest.json"
)
CACHE_SECONDS = 24 * 60 * 60
# Une panne reseau ne doit pas rendre META-MD muet pendant 24 h : on la reessaie
# bien plus tot, et on ne l'ecrit jamais sur le disque.
CACHE_ERREUR_SECONDS = 10 * 60
MANIFEST_TIMEOUT = 20.0
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
# Le paquet de mise a jour n'est plus attache a la release : la page des
# versions ne doit montrer qu'une chose a qui debute, l'installation complete.
# Il vit dans le registre de paquets, dont seule META-MD connait l'adresse.
# La forme « asset de release » reste acceptee : les manifestes deja publies
# la portent, et l'archive d'installation y demeure.
PACKAGE_URL_RE = re.compile(
    r"^https://forge\.apps\.education\.fr/(?:"
    r"meta-md/meta-md/-/releases/[^\s?#]+"
    r"|api/v4/projects/\d+/packages/generic/metamd/\d+\.\d+\.\d+/[\w.\-]+\.zip"
    r")$"
)


def _url_de_paquet_autorisee(url: str) -> bool:
    return bool(PACKAGE_URL_RE.fullmatch(url.strip()))

_cache_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_time = 0.0


def current_version() -> str:
    """Version installee, lisible meme dans une archive sans metadonnees Git."""
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
    return value or "0.0.0"


def _version_tuple(value: str) -> tuple[int, int, int]:
    """Accepte uniquement les versions publiques simples ``X.Y.Z``."""
    parts = value.strip().split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("version attendue au format X.Y.Z")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _read_manifest() -> dict[str, Any]:
    request = Request(
        MANIFEST_URL,
        headers={"User-Agent": f"META-MD/{current_version()} update-check"},
    )
    # 3 s ne suffisent pas : l'adresse est un permalink qui passe par deux
    # redirections, et la Forge repond parfois en 25 a 35 s quand ses
    # runners sont charges. La verification echouait alors en silence, et
    # une mise a jour publiee restait invisible.
    with urlopen(request, timeout=MANIFEST_TIMEOUT) as response:  # noqa: S310
        content_length = int(response.headers.get("Content-Length", "0") or 0)
        if content_length > MAX_MANIFEST_BYTES:
            raise ValueError("manifeste trop volumineux")
        raw = response.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("manifeste trop volumineux")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("le manifeste doit etre un objet JSON")
    return data


def _normalise_manifest(data: dict[str, Any]) -> dict[str, Any]:
    latest = str(data.get("latest") or "").strip()
    _version_tuple(latest)
    release_url = str(data.get("release_url") or "").strip()
    if release_url and not release_url.startswith(
            "https://forge.apps.education.fr/meta-md/meta-md/"):
        raise ValueError("adresse de version non autorisée")
    package_url = str(data.get("package_url") or "").strip()
    package_hash = str(data.get("sha256") or "").strip().lower()
    try:
        package_size = int(data.get("size") or 0)
    except (TypeError, ValueError):
        package_size = 0
    installable = bool(
        _url_de_paquet_autorisee(package_url)
        and re.fullmatch(r"[0-9a-f]{64}", package_hash)
        and 0 < package_size <= MAX_PACKAGE_BYTES
    )
    return {
        "latest": latest,
        "published_at": str(data.get("published_at") or "").strip(),
        "summary": str(data.get("summary") or "").strip()[:500],
        "release_url": release_url,
        "critical": bool(data.get("critical", False)),
        "package_url": package_url if installable else "",
        "sha256": package_hash if installable else "",
        "size": package_size if installable else 0,
        "installable": installable,
    }


def _avec_version_installee(result: dict[str, Any]) -> dict[str, Any]:
    """Recalcule ce qui depend de l'installation locale.

    `current` et `update_available` decrivent l'ici et maintenant, pas le
    manifeste distant. Les servir depuis le cache faisait proposer une version
    DEJA INSTALLEE : apres une mise a jour, le cache continuait d'affirmer que
    la version installee etait l'ancienne, et le bandeau restait affiche
    jusqu'a l'expiration des 24 h — un redemarrage n'y changeait rien, le
    cache vivant sur le disque.
    """
    sortie = dict(result)
    installee = current_version()
    sortie["current"] = installee
    latest = sortie.get("latest")
    try:
        sortie["update_available"] = bool(
            sortie.get("ok") and latest
            and _version_tuple(str(latest)) > _version_tuple(installee))
    except ValueError:
        sortie["update_available"] = False
    return sortie


def _cache_file() -> Path:
    raw = os.environ.get("METAMD_DATA_DIR")
    base = Path(raw) if raw else BASE_DIR.parent / "data"
    return base / "update-cache.json"


def _cache_frais() -> dict[str, Any] | None:
    """Cache memoire, puis cache disque.

    Le compteur de 24 h doit survivre a un redemarrage : sans cela, chaque
    ouverture de META-MD interrogeait la Forge, bien plus souvent que la regle
    annoncee.
    """
    global _cache, _cache_time
    if _cache is not None:
        duree = CACHE_SECONDS if _cache.get("ok") else CACHE_ERREUR_SECONDS
        if time.monotonic() - _cache_time < duree:
            return _avec_version_installee(_cache)
        return None
    try:
        brut = json.loads(_cache_file().read_text(encoding="utf-8"))
        age = time.time() - float(brut.get("horodatage") or 0)
        etat = brut.get("etat")
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(etat, dict) or not 0 <= age < CACHE_SECONDS:
        return None
    _cache = etat
    _cache_time = time.monotonic() - age
    return _avec_version_installee(etat)


def _ecrire_cache(result: dict[str, Any]) -> None:
    """Seuls les succes sont ecrits : persister une panne la ferait durer."""
    if not result.get("ok"):
        return
    chemin = _cache_file()
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        temporaire = chemin.with_suffix(".tmp")
        temporaire.write_text(
            json.dumps({"horodatage": time.time(), "etat": result},
                       ensure_ascii=False),
            encoding="utf-8")
        temporaire.replace(chemin)
    except OSError:
        pass                    # le cache est un confort, jamais une condition


def update_status(*, force: bool = False) -> dict[str, Any]:
    """Retourne un etat affichable ; une panne distante n'est jamais fatale."""
    global _cache, _cache_time
    # Verification desactivee : on repond « a jour » sans rien demander a
    # personne. `ok: True` est voulu — un `ok: False` ferait afficher
    # « Forge injoignable », ce qui donnerait a croire a une panne alors
    # que c'est un choix. `checked: False` dit la verite a qui lit l'API.
    if not VERIFICATION_ACTIVE:
        return {
            "ok": True,
            "current": current_version(),
            "update_available": False,
            "checked": False,
            "raison": ("Verification desactivee dans cette version : les "
                       "paquets publies en amont ne portent pas les "
                       "modifications de ce depot."),
        }
    if not force:
        with _cache_lock:
            frais = _cache_frais()
        if frais is not None:
            return frais

    # L'appel reseau a lieu HORS du verrou. Le tenir pendant les 3 s d'attente
    # serialisait toutes les autres lectures de version derriere lui.
    installed = current_version()
    try:
        manifest = _normalise_manifest(_read_manifest())
        result = {
            "ok": True,
            "current": installed,
            "update_available": (
                _version_tuple(manifest["latest"]) > _version_tuple(installed)
            ),
            **manifest,
        }
    except Exception as exc:
        result = {
            "ok": False,
            "current": installed,
            "update_available": False,
            "error": str(exc),
        }

    with _cache_lock:
        _cache = result
        _cache_time = time.monotonic()
    _ecrire_cache(result)
    return _avec_version_installee(result)


def download_package(
    manifest: dict[str, Any],
    downloads: Path,
    progress_callback: Any = None,
) -> Path:
    """Telecharge un paquet annonce, avec taille et empreinte obligatoires."""
    # Deuxieme verrou, independant de `update_status` : ce qui remplace les
    # fichiers de l'application ne doit pas dependre d'un seul garde-fou.
    if not VERIFICATION_ACTIVE:
        raise ValueError(
            "installation des mises a jour desactivee dans cette version")
    package_url = str(manifest.get("package_url") or "")
    expected_hash = str(manifest.get("sha256") or "").lower()
    expected_size = int(manifest.get("size") or 0)
    version = str(manifest.get("latest") or "")
    if not manifest.get("installable") or not _url_de_paquet_autorisee(package_url):
        raise ValueError("cette mise à jour ne fournit pas de paquet installable")
    _version_tuple(version)

    downloads.mkdir(parents=True, exist_ok=True)
    destination = downloads / f"metamd-{version}.zip"
    temporary = destination.with_suffix(".part")
    digest = hashlib.sha256()
    downloaded = 0
    request = Request(package_url, headers={
        "User-Agent": f"META-MD/{current_version()} updater",
    })
    try:
        with urlopen(request, timeout=30) as response, temporary.open("wb") as output:  # noqa: S310
            announced = int(response.headers.get("Content-Length", "0") or 0)
            if announced and announced != expected_size:
                raise ValueError("la taille annoncée du paquet ne correspond pas")
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                downloaded += len(block)
                if downloaded > expected_size or downloaded > MAX_PACKAGE_BYTES:
                    raise ValueError("le paquet dépasse la taille attendue")
                output.write(block)
                digest.update(block)
                if progress_callback:
                    progress_callback(
                        processed=downloaded,
                        total=expected_size,
                        current=f"Telechargement de META-MD {version}",
                    )
        if downloaded != expected_size:
            raise ValueError("le paquet téléchargé est incomplet")
        if digest.hexdigest() != expected_hash:
            raise ValueError("la somme SHA-256 du paquet ne correspond pas")
        temporary.replace(destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
