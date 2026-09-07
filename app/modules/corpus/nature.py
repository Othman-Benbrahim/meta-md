"""Nature d'un corpus : d'ou viennent ses documents.

Jusqu'ici tout corpus partait de PDF deposes dans `1-Sources/`. Un corpus peut
desormais partir d'un site statique dont les Markdown sont deja ecrits : le
depot est recupere, ses `.md` sont importes, et il n'y a rien a convertir.

Ce choix est fait a la creation et ne change plus : melanger des PDF et un site
dans un meme corpus donnerait un schema qui ne vaut pour personne, et une etape
3 qui devrait etre deux choses a la fois. Il vit dans `corpus.yaml`, a la racine
du theme, a cote de `schema.yaml`.

`pdf` est la valeur par defaut, y compris pour les corpus crees avant ce
fichier : sans `corpus.yaml`, un corpus est un corpus de PDF.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("atelier.corpus.nature")

CORPUS_FILENAME = "corpus.yaml"

SOURCE_PDF = "pdf"
SOURCE_11TY = "11ty"
SOURCE_MKDOCS = "mkdocs"

# Sources qui partent d'un depot git plutot que de fichiers deposes.
SOURCES_DEPOT = (SOURCE_11TY, SOURCE_MKDOCS)
ALLOWED_SOURCES = (SOURCE_PDF, *SOURCES_DEPOT)

# Libelle affiche. Le nom technique est celui du moteur de conversion, pour
# qu'un dossier de `2-Conversions/` se lise sans table de correspondance.
SOURCE_LABELS = {
    SOURCE_PDF: "PDF",
    SOURCE_11TY: "11ty",
    SOURCE_MKDOCS: "MkDocs",
}


def corpus_path(theme_dir: Path) -> Path:
    return theme_dir / CORPUS_FILENAME


def read_corpus(theme_dir: Path) -> dict[str, Any]:
    """Fiche d'un corpus. Toujours un dictionnaire exploitable."""
    defaut: dict[str, Any] = {"source": SOURCE_PDF}
    p = corpus_path(theme_dir)
    if not p.is_file():
        return defaut
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("corpus.yaml illisible (%s) : %s", theme_dir.name, exc)
        return defaut
    if not isinstance(data, dict):
        return defaut
    source = str(data.get("source") or "").strip()
    if source not in ALLOWED_SOURCES:
        if source:
            logger.warning("corpus.yaml (%s) : source %r inconnue, 'pdf' retenu",
                           theme_dir.name, source)
        source = SOURCE_PDF
    return {**data, "source": source}


def write_corpus(theme_dir: Path, data: dict[str, Any]) -> None:
    theme_dir.mkdir(parents=True, exist_ok=True)
    corpus_path(theme_dir).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False),
        encoding="utf-8")


def source_of(theme_dir: Path) -> str:
    return read_corpus(theme_dir)["source"]


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def vient_d_un_depot(theme_dir: Path) -> bool:
    """Vrai quand les documents arrivent d'un depot git et non de fichiers."""
    return source_of(theme_dir) in SOURCES_DEPOT
