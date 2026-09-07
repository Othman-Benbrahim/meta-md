"""Scan des themes RAG presents dans CORPUS/.

Un theme est un sous-dossier direct de CORPUS/. La convention interne (refonte
doc-par-doc, 2026-07-16) :

  CORPUS/<THEME>/
    1-Sources/                    fichiers a convertir (PDF)
    2-Conversions/
        <stem>.metadata.yaml      sidecar : metadonnees + etat du document
        <stem>.md                 la conversion, une par document
        <stem>.pdf                le document dont elle vient
        _metadata/<stem>.metadata.yaml
        _anciens-moteurs/         les conversions des moteurs retires

Il n'y a plus de "run" : chaque document a ses conversions par moteur, son
moteur actif.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from . import documents, frontmatter, metadata, nature, schema

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")

SOURCES_DIR = "1-Sources"


def _fmt_ts(mtime: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(mtime))


def _scan_sources(sources_dir: Path) -> dict[str, Any]:
    if not sources_dir.exists() or not sources_dir.is_dir():
        return {"count": 0, "formats": [], "exists": False, "files": [],
                "total_pages": 0}
    all_files = [p for p in sources_dir.iterdir() if p.is_file()]
    real_sources = [p for p in all_files if not metadata.is_metadata_file(p)
                    and not p.name.startswith("_")]
    formats = sorted({p.suffix.lower().lstrip(".") for p in real_sources if p.suffix})
    files_info: list[dict[str, Any]] = []
    total_pages = 0
    excluded_count = 0
    active_pages = 0
    for p in sorted(real_sources, key=lambda x: x.name.lower()):
        try:
            size = p.stat().st_size
        except OSError:
            size = None
        pages: int | None = None
        if p.suffix.lower() == ".pdf":
            try:
                import pymupdf  # type: ignore[import-untyped]
                with pymupdf.open(str(p)) as doc:
                    pages = len(doc)
                    total_pages += pages
            except Exception:  # noqa: BLE001
                pages = None
        # Une seule lecture du sidecar : `is_excluded` et `source_url` en
        # sortent tous les deux, et l'etape 1 en affiche l'etat par ligne.
        meta = metadata.read_metadata(p)
        excluded = bool(meta.get("excluded"))
        # Titre courant du document, tel qu'il partira dans les chunks : il
        # s'edite directement dans l'inventaire de l'etape 1.
        titre_entry = (meta.get("fields") or {}).get("titre") or {}
        title = str(titre_entry.get("value") or "") if isinstance(titre_entry, dict) else ""
        if excluded:
            excluded_count += 1
        elif pages:
            active_pages += pages
        files_info.append({
            "name": p.name,
            "size": size,
            "pages": pages,
            "has_metadata": metadata.has_metadata(p),
            "excluded": excluded,
            "source_url": str(meta.get("source_url") or ""),
            "title": title,
        })
    return {
        "count": len(real_sources),
        "excluded_count": excluded_count,
        "active_count": len(real_sources) - excluded_count,
        "formats": formats,
        "exists": True,
        "files": files_info,
        "total_pages": total_pages,
        "active_pages": active_pages,
    }


def _scan_conversions(theme_dir: Path) -> dict[str, Any]:
    """Compte les documents avec au moins une conversion + les actives.

    On itere sur les sources et non sur les sous-dossiers de 2-Conversions/ :
    depuis le rangement par moteur, ces sous-dossiers sont des moteurs, pas
    des documents.
    """
    sources_dir = theme_dir / SOURCES_DIR
    docs_with_conv = 0
    docs_with_active = 0
    conversions_total = 0
    if sources_dir.is_dir():
        for p in sorted(sources_dir.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_file() or metadata.is_metadata_file(p):
                continue
            if p.name.startswith("_"):
                continue
            convs = documents.list_conversions(theme_dir, p.stem)
            if convs:
                docs_with_conv += 1
                conversions_total += len(convs)
            if documents.get_active_id(theme_dir, p.stem):
                docs_with_active += 1
    return {
        "docs_with_conversion": docs_with_conv,
        "docs_with_active": docs_with_active,
        "conversions_total": conversions_total,
    }


def _last_activity(theme_dir: Path) -> str | None:
    candidates: list[float] = []
    sources_dir = theme_dir / SOURCES_DIR
    if sources_dir.exists():
        try:
            candidates.append(sources_dir.stat().st_mtime)
            for p in sources_dir.iterdir():
                if p.is_file():
                    try:
                        candidates.append(p.stat().st_mtime)
                    except OSError:
                        continue
        except OSError:
            pass
    conv_root = theme_dir / documents.CONVERSIONS_DIR
    if conv_root.is_dir():
        try:
            candidates.append(conv_root.stat().st_mtime)
        except OSError:
            pass
    if not candidates:
        return None
    return _fmt_ts(max(candidates))


def _validation_status(theme_dir: Path) -> dict[str, Any]:
    """Etat de validation base sur les docs qui ont une conversion active."""
    readiness = frontmatter.validation_status(theme_dir)
    return {
        "total": readiness["active_total"],
        "md_valid": readiness["md_valid"],
        "metadata_valid": readiness["metadata_valid"],
        "both_valid": readiness["both_valid"],
        "invalid": readiness["active_total"] - readiness["both_valid"],
    }


def _fill_status(theme_dir: Path) -> dict[str, Any]:
    """Ce qu'il reste a soumettre au remplissage.

    Compte sur les documents qui ont un Markdown actif, seuls candidats au
    remplissage. Ce decompte est fait avant tout appel LLM : l'etape 4
    annonce ce qu'elle va faire, et le cout se lit avant de le payer.

    Un champ compte tant qu'il n'a pas d'entree dans le sidecar. Une entree
    vide signifie « demande au modele, qui n'a rien trouve » : le champ reste
    a saisir a la main, mais l'etape 4, elle, a fait son travail. Compter la
    valeur vide plutot que l'absence d'entree bloquerait le parcours sur des
    champs que le document ne contient pas.
    """
    fields = schema.autofill_fields(
        schema.read_schema(theme_dir).get("fields") or [])
    keys = [str(f.get("key") or "").strip() for f in fields]
    keys = [k for k in keys if k]
    docs = 0
    docs_incomplete = 0
    pending_fields = 0
    empty_fields = 0
    already_filled = False
    # Sur les Markdown poses, et non sur les couples (source, conversion) :
    # un document dont la source a ete renommee ou retiree garde son `.md` et
    # ses champs a remplir. Compter les couples le rendait invisible a
    # l'etape 3, qui refermait alors le bouton.
    for stem in documents.list_markdowns(theme_dir):
        src = metadata.sidecar_source_for_stem(theme_dir, stem)
        if metadata.is_excluded(src):
            continue
        docs += 1
        sidecar = metadata.read_metadata(src).get("fields") or {}
        # Une valeur marquee `auto` ne peut venir que du remplissage : c'est
        # la trace qu'il a deja tourne sur ce corpus. Elle sert a distinguer
        # « pas encore rempli » de « rempli, puis le schema a change ».
        if any(isinstance(v, dict) and v.get("source") == metadata.SOURCE_AUTO
               for v in sidecar.values()):
            already_filled = True
        pending = 0
        for key in keys:
            entry = sidecar.get(key)
            if entry is None:
                pending += 1
                continue
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value in (None, "", []):
                empty_fields += 1
        if pending:
            docs_incomplete += 1
            pending_fields += pending
    return {
        "docs": docs,
        # Ce qui ouvre l'etape : un Markdown existe, donc il y a de quoi
        # remplir. Le bouton n'attend plus qu'il reste des trous — un corpus
        # deja complet peut vouloir etre repasse apres un changement de
        # schema, et un bouton absent ne se laisse pas cliquer.
        "docs_converted": docs,
        "docs_incomplete": docs_incomplete,
        "pending_fields": pending_fields,
        "empty_fields": empty_fields,
        "already_filled": already_filled,
        "schema_fields": len(keys),
    }


def describe_topic(theme_dir: Path) -> dict[str, Any]:
    sources = _scan_sources(theme_dir / SOURCES_DIR)
    convert = _scan_conversions(theme_dir)
    schema_exists = schema.schema_path(theme_dir).is_file()
    schema_field_count = 0
    schema_locked = False
    if schema_exists:
        try:
            schema_data = schema.read_schema(theme_dir)
            schema_field_count = len(schema_data.get("fields") or [])
            schema_locked = bool(schema_data.get("locked"))
        except Exception:  # noqa: BLE001
            schema_field_count = 0
    validation = _validation_status(theme_dir)
    fiche = nature.read_corpus(theme_dir)
    source = fiche["source"]
    return {
        "name": theme_dir.name,
        # D'ou viennent les documents : `pdf` (defaut), `11ty` ou `mkdocs`.
        # C'est ce qui decide de la forme des etapes 1 et 3 dans l'interface.
        "source": source,
        "source_label": nature.source_label(source),
        "depuis_depot": source in nature.SOURCES_DEPOT,
        "sources": sources,
        "steps": {
            "convert": convert,
            "fill": _fill_status(theme_dir),
        },
        "schema": {
            "exists": schema_exists,
            "field_count": schema_field_count,
            "locked": schema_locked,
        },
        "validation": validation,
        "last_activity": _last_activity(theme_dir),
    }


def list_topics(rags_dir: Path) -> list[dict[str, Any]]:
    if not rags_dir.exists() or not rags_dir.is_dir():
        return []
    topics: list[dict[str, Any]] = []
    for entry in sorted(rags_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        if entry.name.startswith((".", "_")):
            continue
        topics.append(describe_topic(entry))
    return topics
