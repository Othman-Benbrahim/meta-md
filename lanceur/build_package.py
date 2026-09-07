"""Construit le paquet applicatif versionne publie dans une release Forge."""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

if __package__:
    from .state import valid_version
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lanceur.state import valid_version


ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
INCLUDED_FILES = ("server.py", "requirements.txt", "VERSION")
# Plus de `lots` : ils ne sont plus livres avec l'application, ils se
# telechargent depuis le depot de donnees (voir corpus.catalogue).
INCLUDED_DIRS = ("modules", "pages")
# Le socle voyage desormais avec l'application : sans cela une correction du
# lanceur — le retour arriere, par exemple — n'atteint jamais personne, et le
# numero de version unique mentirait sur la moitie de ce qui est installe.
# Seuls les fichiers d'execution partent : ni le runtime Python, ni les tests,
# ni l'outillage de release, que l'utilisateur n'a pas a construire.
FICHIERS_SOCLE = ("__init__.py", "launch.py", "state.py", "updater.py")
IGNORED_NAMES = {"__pycache__", ".DS_Store"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def version_application() -> str:
    version = valid_version((APP_DIR / "VERSION").read_text(encoding="utf-8").strip())
    if not version:
        raise ValueError("VERSION doit etre au format X.Y.Z")
    return version


def fichiers_application() -> list[Path]:
    """Ce que la partie versionnable emporte, et rien d'autre.

    Partage avec `build_installer` : les deux paquets doivent contenir
    exactement la meme application, sinon une installation neuve et une
    installation mise a jour divergent.
    """
    paths: list[Path] = [APP_DIR / name for name in INCLUDED_FILES]
    for dirname in INCLUDED_DIRS:
        paths.extend(path for path in (APP_DIR / dirname).rglob("*") if path.is_file())
    return sorted(path for path in paths
                  if not any(part in IGNORED_NAMES for part in path.parts)
                  and path.suffix.lower() not in IGNORED_SUFFIXES)


def fichiers_socle() -> list[Path]:
    """Les fichiers du lanceur que la mise a jour emporte."""
    return [ROOT / "lanceur" / nom for nom in FICHIERS_SOCLE]


def build(output_dir: Path | None = None) -> tuple[Path, str, int]:
    version = version_application()
    output_dir = output_dir or ROOT / "lanceur" / "dist"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"metamd-{version}.zip"
    prefix = f"metamd-{version}"

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        # L'application reste A LA RACINE du paquet : les installations
        # 0.8.0 a 0.8.3 portent un updater qui y cherche `server.py` et
        # `modules/`, et rejetteraient tout autre agencement. Le socle
        # s'ajoute a cote, dans un sous-dossier que ces anciens updaters
        # emportent sans le voir — inerte chez eux, installe chez les neufs.
        for path in fichiers_application():
            relative = path.relative_to(APP_DIR).as_posix()
            archive.write(path, f"{prefix}/{relative}")
        for path in fichiers_socle():
            archive.write(path, f"{prefix}/lanceur/{path.name}")

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return destination, digest, destination.stat().st_size


def main() -> int:
    package, digest, size = build()
    print(f"Paquet : {package}")
    print(f"SHA-256 : {digest}")
    print(f"Taille : {size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
