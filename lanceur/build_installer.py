"""Construit le paquet d'installation complet, celui d'une premiere fois.

A ne pas confondre avec `build_package.py`, qui produit le paquet de mise a
jour : celui-la ne contient que la partie versionnable et ne s'installe pas
seul, faute de lanceur et de scripts. C'est le piege que ce paquet-ci ferme.

N'emporte ni le runtime Python portable, telecharge par l'installateur, ni les
donnees de travail : une installation neuve part d'un `data/` vide.
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

if __package__:
    from .build_package import APP_DIR, fichiers_application, version_application
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lanceur.build_package import (APP_DIR, fichiers_application,
                                       version_application)


ROOT = Path(__file__).resolve().parent.parent
FICHIERS_RACINE = (
    # Le README affiche le logo : il part avec lui, sinon l'image est
    # morte a l'arrivee.
    "README.md", "assets/meta-md-logo.png", "LICENSE",
    "META-MD-win-1-installer.bat", "META-MD-win-2-ouvrir.bat",
    "META-MD-linux-1-installer.sh", "META-MD-linux-2-ouvrir.sh",
    "python.bat", "python.sh",
)
# Le socle, sans son outillage de release ni ses tests : l'utilisateur ne
# construit pas de paquet, il en installe un.
FICHIERS_LANCEUR = ("__init__.py", "launch.py", "state.py", "updater.py")
FICHIERS_DATA = ("config.example.json",)


def build(output_dir: Path | None = None) -> tuple[Path, str, int]:
    version = version_application()
    output_dir = output_dir or ROOT / "lanceur" / "dist"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"metamd-installation-{version}.zip"
    # Le dossier est permanent : il survit aux mises a jour, qui ne
    # remplacent que son contenu. Tout numero dans son nom devient donc
    # faux des la premiere mise a jour — il n'en porte aucun.
    prefix = "META-MD"

    manquants = [nom for nom in FICHIERS_RACINE if not (ROOT / nom).is_file()]
    if manquants:
        raise FileNotFoundError(
            "fichiers d'installation absents : " + ", ".join(manquants))

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for nom in FICHIERS_RACINE:
            archive.write(ROOT / nom, f"{prefix}/{nom}")
        for chemin in fichiers_application():
            relative = chemin.relative_to(APP_DIR).as_posix()
            archive.write(chemin, f"{prefix}/app/{relative}")
        for nom in FICHIERS_LANCEUR:
            archive.write(ROOT / "lanceur" / nom, f"{prefix}/lanceur/{nom}")
        for nom in FICHIERS_DATA:
            source = ROOT / "data" / nom
            if source.is_file():
                archive.write(source, f"{prefix}/data/{nom}")

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return destination, digest, destination.stat().st_size


def main() -> int:
    package, digest, size = build()
    print(f"Paquet d'installation : {package}")
    print(f"SHA-256 : {digest}")
    print(f"Taille : {size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
