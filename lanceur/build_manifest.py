"""Genere le manifeste d'une release a partir du paquet construit."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

if __package__:
    from .state import valid_version
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lanceur.state import valid_version


def _version_du_paquet(package: Path) -> str:
    """La version vient du paquet lui-meme, jamais de l'arbre de travail.

    Construire un ZIP puis modifier `app/VERSION` avant de generer le manifeste
    faisait diverger les deux : le manifeste annoncait une version que le
    paquet ne contenait pas.
    """
    with zipfile.ZipFile(package) as archive:
        noms = [n for n in archive.namelist() if n.rsplit("/", 1)[-1] == "VERSION"]
        if not noms:
            raise ValueError("le paquet ne contient pas de fichier VERSION")
        brut = archive.read(min(noms, key=len)).decode("utf-8").strip()
    version = valid_version(brut)
    if not version:
        raise ValueError("VERSION doit etre au format X.Y.Z")
    return version


def build_manifest(
    package: Path,
    package_url: str,
    release_url: str,
    output: Path,
    summary: str,
    critical: bool = False,
) -> dict[str, object]:
    version = _version_du_paquet(package)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    manifest: dict[str, object] = {
        "latest": version,
        "published_at": date.today().isoformat(),
        "summary": summary.strip()[:500],
        "release_url": release_url,
        "package_url": package_url,
        "sha256": digest,
        "size": package.stat().st_size,
        "critical": bool(critical),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generer update-manifest.json")
    parser.add_argument("package", type=Path)
    parser.add_argument("package_url")
    parser.add_argument("release_url")
    parser.add_argument("output", type=Path)
    parser.add_argument("--summary", default="Nouvelle version de META-MD.")
    parser.add_argument(
        "--critical", action="store_true",
        help="version importante : la notification passera outre un report")
    args = parser.parse_args()
    build_manifest(
        args.package,
        args.package_url,
        args.release_url,
        args.output,
        args.summary,
        args.critical,
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
