"""Installe atomiquement un paquet META-MD deja telecharge et verifie."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import stat
import tempfile
import zipfile
import sys
from pathlib import Path
from typing import Any

if __package__:
    from .state import read_state, valid_version, write_state
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lanceur.state import read_state, valid_version, write_state


ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = ROOT / "app" / "versions"
STATE_FILE = ROOT / "app" / "active.json"
MAX_FILES = 5000
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_FILES:
        raise ValueError("le paquet contient trop de fichiers")
    if sum(item.file_size for item in members) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("le paquet décompressé est trop volumineux")
    for item in members:
        path = Path(item.filename.replace("\\", "/"))
        mode = item.external_attr >> 16
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"chemin interdit dans le paquet : {item.filename}")
        if stat.S_ISLNK(mode):
            raise ValueError(f"lien symbolique interdit : {item.filename}")
    return members


SOCLE_DIR = ROOT / "lanceur"
SAUVEGARDE_SOCLE = SOCLE_DIR / "precedent"


def _installer_socle(source: Path) -> list[str]:
    """Remplace les fichiers du lanceur, apres en avoir garde une copie.

    Le socle porte le retour arriere : s'il se casse, plus rien ne peut le
    reparer tout seul. La copie dans `lanceur/precedent/` rend la reparation
    manuelle immediate — recopier ce dossier par-dessus — sans avoir a
    retelecharger quoi que ce soit.

    Seuls les fichiers presents dans le paquet sont touches. Le runtime
    Python (`lanceur/python/`, 110 Mo), les telechargements et l'outillage
    de release restent ou ils sont.
    """
    fichiers = sorted(p for p in source.iterdir() if p.is_file())
    if not fichiers:
        return []
    SAUVEGARDE_SOCLE.mkdir(parents=True, exist_ok=True)
    poses: list[str] = []
    for fichier in fichiers:
        actuel = SOCLE_DIR / fichier.name
        if actuel.is_file():
            shutil.copy2(actuel, SAUVEGARDE_SOCLE / fichier.name)
        # Ecriture atomique : un socle a moitie ecrit ne demarrerait plus.
        temporaire = SOCLE_DIR / (fichier.name + ".nouveau")
        shutil.copy2(fichier, temporaire)
        temporaire.replace(actuel)
        poses.append(fichier.name)
    return poses

def _purger_anciennes_versions(state: dict[str, Any]) -> None:
    """Ne garde que la version active et celle sur laquelle revenir.

    Chaque mise a jour depose une copie complete de l'application. Sans purge,
    `app/versions/` grossit sans limite sur le poste, et les archives
    telechargees avec.
    """
    gardees = {state.get("active"), state.get("previous")}
    try:
        contenu = list(VERSIONS_DIR.iterdir())
    except OSError:
        return
    for dossier in contenu:
        # Un nom hors X.Y.Z est un reste d'installation en cours : pas a nous.
        if not dossier.is_dir() or not valid_version(dossier.name):
            continue
        if dossier.name in gardees:
            continue
        shutil.rmtree(dossier, ignore_errors=True)


def install(package: Path, expected_hash: str) -> str:
    actual_hash = sha256(package)
    if actual_hash.lower() != expected_hash.strip().lower():
        raise ValueError("la somme SHA-256 du paquet ne correspond pas")

    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=VERSIONS_DIR))
    try:
        with zipfile.ZipFile(package) as archive:
            members = _safe_members(archive)
            archive.extractall(temporary, members)
        roots = [item for item in temporary.iterdir()]
        app_dir = roots[0] if len(roots) == 1 and roots[0].is_dir() else temporary
        try:
            version = valid_version(
                (app_dir / "VERSION").read_text(encoding="utf-8").strip()
            )
        except OSError:
            version = None      # archive vide ou sans VERSION lisible
        if not version or not (app_dir / "server.py").is_file() or not (app_dir / "modules").is_dir():
            raise ValueError("le paquet n'est pas une version META-MD valide")
        # Le socle voyage dans le paquet depuis la 0.8.4. Il s'installe a la
        # racine, pas dans le dossier de version : c'est lui qui lance les
        # versions, il ne peut pas vivre dans l'une d'elles. Les paquets plus
        # anciens n'en portent pas, et ce bloc ne fait alors rien.
        socle_source = app_dir / "lanceur"
        socle_pose: list[str] = []
        if socle_source.is_dir():
            socle_pose = _installer_socle(socle_source)
            shutil.rmtree(socle_source, ignore_errors=True)

        destination = VERSIONS_DIR / version
        state = read_state(STATE_FILE)
        if destination.exists():
            # Une version qui n'a pas demarre reste sur le disque : le lanceur
            # a besoin de pouvoir la nommer pour ne plus l'activer. La
            # reinstaller doit rester possible, sinon l'utilisateur boucle sur
            # « deja installee » sans aucune issue depuis l'interface.
            if state.get("failed") == version:
                shutil.rmtree(destination, ignore_errors=True)
            if destination.exists():
                raise FileExistsError(f"la version {version} est déjà installée")
        if app_dir == temporary:
            temporary.replace(destination)
            temporary = destination
        else:
            app_dir.replace(destination)

        state["previous"] = state.get("active")
        state["active"] = version
        state.pop("failed", None)
        write_state(STATE_FILE, state)
        _purger_anciennes_versions(state)
        if socle_pose:
            print(f"Socle mis a jour ({len(socle_pose)} fichier(s)) ; "
                  f"copie de l'ancien dans lanceur/precedent/.")
        return version
    finally:
        if temporary.exists() and temporary.name.startswith(".install-"):
            shutil.rmtree(temporary, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Installer un paquet META-MD")
    parser.add_argument("package", type=Path)
    parser.add_argument("sha256")
    args = parser.parse_args()
    version = install(args.package.resolve(), args.sha256)
    print(f"META-MD {version} installé et activé. Relancez l'application.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
