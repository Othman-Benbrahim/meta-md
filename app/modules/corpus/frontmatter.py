"""Front matter des Markdown actifs : materialisation continue.

Les champs metier d'un document vivent dans son sidecar
`2-Conversions/<stem>.metadata.yaml`, qui reste la source de verite. Ce
module les reporte dans le front matter YAML du Markdown actif a chaque
changement (edition des metadonnees, auto-fill, conversion, bascule de
moteur, edition du MD, changement de schema).

Consequence voulue : le `.md` sur disque est en permanence auto-suffisant,
directement exploitable dans un lecteur Markdown externe qui lit le front
matter. Corollaire : editer une cle metier a la main dans le MD est annule
a la synchronisation suivante.

Chaque synchronisation reconstruit integralement les cles metier depuis le
sidecar (ce qui purge les champs retires du schema) et preserve les cles
techniques ecrites par la conversion.

Le front matter porte TOUTES les metadonnees du document : les champs du
schema, et `source_url`, l'URL publique d'origine. Un `.md` sorti du
dossier reste donc complet, sans son sidecar.

Il porte aussi `_schema`, la description des champs du corpus (type, label,
description, vocabulaire d'une liste fermee). Les valeurs seules ne suffisent
pas : YAML ne distingue qu'une liste, un nombre et un booleen, la ou le
schema compte huit types. Un lecteur de Markdown y trouve donc de quoi
construire ses filtres sans rien connaitre du corpus.

Et `_valide`, qui dit si le document a ete relu — Markdown et metadonnees.
C'est ce qui permet a un lecteur de distinguer un document verifie d'une
conversion brute, une fois le fichier sorti de META-MD.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("atelier.corpus.frontmatter")

# Cles techniques du front matter des MDs (ecrites par pipeline).
# On les preserve toujours : elles decrivent l'extraction, pas le metier.
TECHNICAL_FM_KEYS = frozenset({"_source", "_engine", "_extracted_at", "_pages"})

# Description des champs, ecrite sous les valeurs. Hors de TECHNICAL_FM_KEYS
# a dessein : elle n'est pas preservee mais reecrite a chaque synchronisation,
# donc jamais en retard sur le schema.
SCHEMA_FM_KEY = "_schema"

# Etat de relecture du document, ecrit a chaque synchronisation comme
# `_schema` et pour la meme raison : recopie du sidecar, il ne peut pas
# rester en retard sur lui.
#
# Un `.md` sorti du dossier dit ainsi de lui-meme s'il a ete relu. Sans cette
# cle, un lecteur (EduMD) ne peut pas distinguer un document verifie d'une
# conversion brute, et rien dans le fichier ne l'indique.
#
# `_valide` est vrai quand les DEUX relectures sont faites — le Markdown est
# fidele au PDF, et les valeurs des champs sont justes. C'est la granularite
# de la case unique du viewer. Les deux drapeaux restent distincts dans le
# sidecar, ou ils se posent independamment.
VALIDE_FM_KEY = "_valide"

# Licence de reutilisation, recopiee du sidecar comme `_valide`. Prefixee car
# elle decrit la provenance du document et non son contenu : elle n'est pas un
# champ du schema, elle ne se saisit pas dans le viewer et elle ne facette
# rien — elle vaut la meme chose pour tout un lot. Un `.md` sorti du dossier
# dit ainsi sous quelles conditions il se reutilise.
LICENCE_FM_KEY = "_licence"

# D'ou vient un document importe d'un site : son chemin dans le depot, au
# commit recupere. Prefixe pour la meme raison que `_licence` — une provenance,
# pas un champ du schema. Le `.md` sorti du dossier dit ainsi de quel fichier
# il est la copie, ce que `source_url` ne dit pas : celle-ci pointe la page
# publiee, lisible, tandis que celle-la pointe la source exacte.
DEPOT_FM_KEY = "_depot"

_FRONT_MATTER_RE = re.compile(r"^---\s*\r?\n(.*?\n)---\s*\r?\n?", re.DOTALL)


def split_md(md_text: str) -> tuple[dict[str, Any], str]:
    """Separe le front matter (parse) du corps."""
    m = _FRONT_MATTER_RE.match(md_text)
    if not m:
        return {}, md_text
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, md_text[m.end():]
    if not isinstance(data, dict):
        data = {}
    return data, md_text[m.end():]


def _serialize_md(front_matter: dict[str, Any], body: str) -> str:
    if not front_matter:
        return body.lstrip("\r\n")
    yaml_text = yaml.safe_dump(front_matter, allow_unicode=True, sort_keys=False,
                               default_flow_style=False).rstrip()
    return f"---\n{yaml_text}\n---\n\n{body.lstrip(chr(10))}"


def schema_block(theme_dir: Path) -> dict[str, Any]:
    """Description des champs du corpus, telle qu'elle part dans le `.md`.

    Le front matter ne porte que des valeurs, et YAML n'en dit le type que
    pour trois des huit : une liste, un nombre, un booleen. Un `text`, un
    `keyword`, un `enum`, une `date` et une `url` y sont cinq chaines
    identiques, alors qu'elles se filtrent de cinq facons differentes. Ce
    bloc rend donc le fichier auto-descriptif : un lecteur de Markdown y lit
    de quoi construire ses filtres sans connaitre le corpus.

    Tous les champs du schema y figurent, y compris ceux que CE document ne
    renseigne pas : le bloc decrit le corpus, pas l'exemplaire. Sans quoi la
    liste des filtres possibles changerait d'un fichier a l'autre.

    `filtre: true` marque les champs a proposer comme facette. Il n'est jamais
    deduit : un champ ne devient filtrable que coche a la main dans le schema.
    """
    from . import schema  # cycle
    bloc: dict[str, Any] = {}
    for f in (schema.read_schema(theme_dir).get("fields") or []):
        if not isinstance(f, dict):
            continue
        cle = str(f.get("key") or "").strip()
        if not cle:
            continue
        entree: dict[str, Any] = {"type": f.get("type") or schema.DEFAULT_FIELD_TYPE}
        label = str(f.get("label") or "").strip()
        if label:
            entree["label"] = label
        description = str(f.get("description") or "").strip()
        if description:
            entree["description"] = description
        # Le vocabulaire d'une liste fermee est ce qui permet d'offrir des
        # cases a cocher plutot qu'un champ de recherche.
        options = [str(o) for o in (f.get("options") or []) if str(o).strip()]
        if options:
            entree["options"] = options
        # Ce champ doit-il etre propose comme filtre par un lecteur ? Le type
        # ne suffit pas a le dire : `discipline` et `texte_officiel` sont tous
        # deux du texte, seul le premier facette utilement. C'est donc une
        # decision du corpus, cochee a la main dans le schema, et publiee ici
        # pour qu'EDU-MD n'ait pas a la deviner.
        if f.get("filtre"):
            entree["filtre"] = True
        bloc[cle] = entree
    return bloc


def write_business_front_matter(md_path: Path, business: dict[str, Any],
                                schema: dict[str, Any] | None = None,
                                valide: bool | None = None,
                                licence: str | None = None,
                                depot: str | None = None) -> None:
    """Reecrit les cles metier du front matter d'un MD, cles techniques gardees.

    `schema` est recalcule a chaque synchronisation plutot que repris du
    fichier : un champ retire du schema doit disparaitre du `.md`, et une
    description corrigee doit y descendre.
    """
    if not md_path.is_file():
        return
    try:
        raw = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Lecture MD KO : %s : %s", md_path, exc)
        return
    existing_fm, body = split_md(raw)
    technical = {k: v for k, v in existing_fm.items() if k in TECHNICAL_FM_KEYS}
    new_fm: dict[str, Any] = {}
    for k, v in business.items():
        # Avec un schema, l'appelant a deja arrete la liste des cles et leur
        # ordre : on ecrit tout, valeurs vides comprises, pour que tous les
        # `.md` d'un corpus portent exactement les memes cles. Sans schema,
        # on s'en tient a ce qui a une valeur.
        if not schema and v in (None, "", []):
            continue
        new_fm[k] = v
    if schema:
        new_fm[SCHEMA_FM_KEY] = schema
    if valide is not None:
        new_fm[VALIDE_FM_KEY] = bool(valide)
    if licence:
        new_fm[LICENCE_FM_KEY] = licence
    if depot:
        new_fm[DEPOT_FM_KEY] = depot
    new_fm.update(technical)
    try:
        md_path.write_text(_serialize_md(new_fm, body), encoding="utf-8")
    except OSError as exc:
        logger.warning("Ecriture MD KO : %s : %s", md_path, exc)


def sync_front_matter(theme_dir: Path, source_path: Path) -> bool:
    """Reporte le sidecar d'un document dans son MD actif.

    Retourne True si un MD a ete touche (False si le doc n'a pas de
    conversion active).
    """
    from . import documents, metadata  # cycle
    active = documents.get_active_path(theme_dir, source_path.stem)
    if active is None:
        return False
    from . import schema  # cycle
    sidecar = metadata.read_metadata(source_path)
    valeurs = metadata.flatten_for_chunk(sidecar)
    # `source_url` est un champ du schema, mais sa valeur vit a part dans le
    # sidecar plutot que parmi les champs metier.
    valeurs["source_url"] = str(sidecar.get("source_url") or "").strip()
    bloc_schema = schema_block(theme_dir)

    if bloc_schema:
        # Le front matter suit le schema, cle pour cle et dans son ordre :
        # tous les `.md` d'un corpus portent alors les memes cles, et une
        # colonne manquante ne se confond pas avec une valeur absente. Un
        # champ retire du schema sort du `.md` mais garde sa valeur dans le
        # sidecar : on ne detruit pas une saisie pour un aller-retour de
        # configuration.
        business: dict[str, Any] = {}
        for cle, decrit in bloc_schema.items():
            valeur = valeurs.get(cle)
            if valeur in (None, "", []):
                # Le vide garde la forme du type : une liste reste une liste,
                # sinon un lecteur devrait traiter deux cas par champ.
                valeur = [] if decrit.get("type") in schema.LIST_FIELD_TYPES else ""
            business[cle] = valeur
    else:
        # Schema vide (corpus neuf, fichier illisible) : on n'ampute rien.
        business = {k: v for k, v in valeurs.items() if v not in (None, "", [])}

    # Relu veut dire les deux : le Markdown est fidele au PDF ET les valeurs
    # des champs sont justes. Un document a moitie valide n'est pas valide.
    valide = bool(sidecar.get("md_valid")) and bool(sidecar.get("metadata_valid"))
    depot = sidecar.get("depot") if isinstance(sidecar.get("depot"), dict) else {}
    write_business_front_matter(active, business, bloc_schema, valide=valide,
                                licence=str(sidecar.get("licence") or "").strip(),
                                depot=str(depot.get("url") or "").strip())
    return True


def sync_all_front_matter(theme_dir: Path) -> int:
    """Resynchronise le front matter de tous les MDs actifs du theme."""
    touched = 0
    for src, _active in iter_active_pairs(theme_dir):
        if sync_front_matter(theme_dir, src):
            touched += 1
    return touched


def iter_active_pairs(theme_dir: Path) -> list[tuple[Path, Path]]:
    """Retourne [(source, md_actif)] pour chaque doc ayant une conversion active.

    Les documents marques `excluded` dans leur sidecar sont ignores.
    """
    from . import documents, metadata  # cycle
    sources_dir = theme_dir / documents.SOURCES_DIR
    pairs: list[tuple[Path, Path]] = []
    if not sources_dir.is_dir():
        return pairs
    for src in sorted(sources_dir.iterdir(), key=lambda p: p.name.lower()):
        if not src.is_file() or metadata.is_metadata_file(src):
            continue
        if src.name.startswith("_"):
            continue
        if metadata.is_excluded(src):
            continue
        active = documents.get_active_path(theme_dir, src.stem)
        if active is None:
            continue
        pairs.append((src, active))
    return pairs


def validation_status(theme_dir: Path) -> dict[str, Any]:
    """Compte les docs actifs et leur etat de validation.

    Retourne {active_total, md_valid, metadata_valid, both_valid, missing}.
    `both_valid` est le nombre de documents segmentables.
    """
    from . import metadata  # cycle
    pairs = iter_active_pairs(theme_dir)
    total = len(pairs)
    md_ok = 0
    meta_ok = 0
    both_ok = 0
    missing: list[dict[str, Any]] = []
    for src, _active in pairs:
        sidecar = metadata.read_metadata(src)
        m_ok = bool(sidecar.get("md_valid"))
        d_ok = bool(sidecar.get("metadata_valid"))
        if m_ok:
            md_ok += 1
        if d_ok:
            meta_ok += 1
        if m_ok and d_ok:
            both_ok += 1
        else:
            missing.append({"name": src.name,
                            "md_valid": m_ok, "metadata_valid": d_ok})
    return {
        "active_total": total,
        "md_valid": md_ok,
        "metadata_valid": meta_ok,
        "both_valid": both_ok,
        "missing": missing,
    }
