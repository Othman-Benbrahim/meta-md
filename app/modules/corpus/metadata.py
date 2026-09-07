"""Fiches de metadonnees par source (sidecar YAML par PDF).

Chaque fichier depose dans CORPUS/<THEME>/1-Sources/ peut avoir un sidecar
<nom>.metadata.yaml qui porte son front matter schema-drivé. Chaque valeur
porte un marqueur `source: auto|manual` pour distinguer les valeurs LLM
des corrections humaines. Deux flags top-level disent ce qui a ete relu.

Structure du YAML :

    md_valid: true          # le contenu Markdown extrait est fidele au PDF
    metadata_valid: true    # les champs metier ont ete relus et sont corrects
    fields:
      titre:
        value: "Programme de mathematiques"
        source: auto
      date:
        value: "2024-09-01"
        source: manual

Les deux flags de validation sont **independants** : on peut avoir un MD
nickel avec des metadonnees encore fausses, ou l'inverse. Un document
n'est segmentable (etape 4) que quand les deux sont a `true`.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("atelier.corpus.metadata")

METADATA_SUFFIX = ".metadata.yaml"

# Cles de validation dans le sidecar. Deux flags distincts depuis 2026-07-15
# pour distinguer la relecture du contenu extrait et celle des metadonnees.
VALID_KEYS = ("md_valid", "metadata_valid")


def _extract_valid_flags(data: dict[str, Any]) -> tuple[bool, bool]:
    """Retourne (md_valid, metadata_valid) d'un sidecar."""
    return bool(data.get("md_valid")), bool(data.get("metadata_valid"))

# Ou les sidecars sont ranges, RELATIF au theme. Depuis 2026-08-23 ils vivent
# dans "2-Conversions/_metadata/" et non plus a la racine de "2-Conversions/" :
# les dossiers de conversion portent desormais le `.md` et le document dont il
# vient, et s'ouvrent tels quels dans un lecteur Markdown. Un sidecar melange
# a ces couples n'y avait plus sa place.
#
# Le prefixe "_" n'est pas decoratif : `list_conversions` ignore les dossiers
# qui commencent par lui, ce dossier est donc invisible du scanner de moteurs
# sans qu'une exception ait ete ajoutee nulle part. 1-Sources/ reste input pur.
SIDECAR_SUBDIR = "2-Conversions"
SIDECAR_DIRNAME = "_metadata"
SOURCES_SUBDIR = "1-Sources"

# Valeurs possibles pour le marqueur "source" par champ.
# D'ou vient la valeur d'un champ. Trois origines, parce que « pas auto » en
# recouvrait deux qui ne se ressemblent pas : une valeur venue du jeu de
# donnees d'un lot n'a ete corrigee par personne, et l'afficher comme une
# correction humaine est faux.
SOURCE_AUTO = "auto"        # deduite par un modele (annotation ou remplissage)
SOURCE_LOT = "lot"          # portee par le jeu de donnees d'origine
SOURCE_MANUAL = "manual"    # saisie ou corrigee a la main dans le viewer
ALLOWED_SOURCES = {SOURCE_AUTO, SOURCE_LOT, SOURCE_MANUAL}

# Origines qu'un modele ne remplace jamais. `merge_annotation` ne remplit de
# toute facon que le vide, mais l'ensemble dit l'intention : ces valeurs
# viennent d'ailleurs que d'une deduction.
SOURCES_HUMAINES = {SOURCE_LOT, SOURCE_MANUAL}

# Parametres de decoupe par document (bloc `segment:` du sidecar). Absent =
# le document suit les valeurs par defaut du theme. Ce sont des reglages de
# traitement, pas du contenu : ils restent modifiables meme quand le theme
# n'affectent que la prochaine segmentation du document.
SEGMENT_DEFAULT_TARGET_CHARS = 1600
SEGMENT_DEFAULT_OVERLAP = 200
SEGMENT_TARGET_CHARS_RANGE = (400, 8000)
SEGMENT_OVERLAP_RANGE = (0, 2000)


# Moteur de conversion actif du document. Depuis le passage aux dossiers par
# moteur (2026-08-05), c'est ce champ qui remplace l'ancien pointeur
# 2-Conversions/<stem>/_active.yaml : le sidecar porte tout l'etat du document.
ENGINE_SLUG_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _normalise_engine(raw: Any) -> str:
    """Slug de moteur, ou "" si absent/invalide. Le slug sert de nom de
    dossier : on n'accepte donc rien qui puisse s'echapper d'un chemin."""
    value = str(raw or "").strip()
    return value if ENGINE_SLUG_RE.match(value) else ""


def _normalise_source_url(raw: Any) -> str:
    """URL publique du document d'origine, ou "" si absente/inexploitable.

    Cette cle n'est pas un champ metier : elle ne vient pas du schema, n'est
    pas auto-remplie par le LLM et n'apparait pas dans le front matter. Elle
    est posee a l'etape 1 depuis le JSON du lot (`source_url`), et sert
    uniquement a citer la source d'un extrait dans la collection.

    Seuls http/https sont acceptes : une valeur qui n'est pas une URL
    n'aiderait personne a retrouver le document.
    """
    value = str(raw or "").strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return ""


def _normalise_segment(raw: Any) -> dict[str, Any]:
    """Nettoie le bloc `segment:` d'un sidecar.

    Retourne {} si aucun override exploitable : on ne veut pas materialiser
    les valeurs par defaut dans le YAML, sinon un changement de defaut ne se
    propagerait plus aux documents qui n'ont jamais ete regles a la main.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, (lo, hi) in (
        ("target_chars", SEGMENT_TARGET_CHARS_RANGE),
        ("overlap", SEGMENT_OVERLAP_RANGE),
    ):
        value = raw.get(key)
        if value in (None, ""):
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        out[key] = max(lo, min(hi, n))
    # Un recouvrement superieur a la cible produirait des chunks degeneres.
    if "overlap" in out:
        ceiling = out.get("target_chars", SEGMENT_DEFAULT_TARGET_CHARS) // 2
        out["overlap"] = min(out["overlap"], ceiling)
    return out


def get_segment_params(source_path: Path) -> dict[str, Any]:
    """Parametres de decoupe effectifs d'un document.

    Retourne {target_chars, overlap, overridden} : `overridden` dit si au
    moins une valeur vient du sidecar plutot que des defauts.
    """
    override = _normalise_segment(read_metadata(source_path).get("segment"))
    return {
        "target_chars": override.get("target_chars", SEGMENT_DEFAULT_TARGET_CHARS),
        "overlap": override.get("overlap", SEGMENT_DEFAULT_OVERLAP),
        "overridden": bool(override),
    }


def set_segment_params(source_path: Path, raw: Any) -> dict[str, Any]:
    """Ecrit (ou efface) le bloc `segment:` d'un sidecar sans toucher au reste.

    `raw` vide / None / {} remet le document sur les valeurs par defaut.
    Retourne les parametres effectifs apres ecriture.
    """
    data = read_metadata(source_path)
    data["segment"] = _normalise_segment(raw)
    write_metadata(source_path, data)
    return get_segment_params(source_path)


def sidecar_dir(theme_dir: Path) -> Path:
    """Ou vivent les sidecars d'un theme."""
    return theme_dir / SIDECAR_SUBDIR / SIDECAR_DIRNAME


def metadata_path_for(source_path: Path) -> Path:
    """Retourne le chemin du sidecar YAML pour un fichier source donne.

    Convention : <theme>/2-Conversions/_metadata/<stem>.metadata.yaml, meme
    stem que la source et partage entre tous les moteurs. Exemple :
      /CORPUS/T/1-Sources/doc.pdf
      -> /CORPUS/T/2-Conversions/_metadata/doc.metadata.yaml
    Si la structure ne matche pas (tests, usage non standard), on retombe
    sur le meme dossier que la source.
    """
    if source_path.parent.name == SOURCES_SUBDIR:
        return sidecar_dir(source_path.parent.parent) / (source_path.stem + METADATA_SUFFIX)
    return source_path.with_name(source_path.stem + METADATA_SUFFIX)


def is_metadata_file(path: Path) -> bool:
    """Vrai si le chemin correspond a un sidecar de metadonnees."""
    return path.name.endswith(METADATA_SUFFIX)


def has_metadata(source_path: Path) -> bool:
    """Vrai si un sidecar existe et contient au moins un champ non vide."""
    data = read_metadata(source_path)
    fields = data.get("fields") or {}
    for entry in fields.values():
        if isinstance(entry, dict) and entry.get("value") not in (None, "", []):
            return True
    return False


def is_excluded(source_path: Path) -> bool:
    """Vrai si le doc a ete marque `excluded: true` dans son sidecar.

    Un doc exclu est ignore par la conversion et la segmentation
    (mais reste visible dans la liste de l'etape 1 pour pouvoir le re-inclure).
    """
    return bool(read_metadata(source_path).get("excluded"))


def read_metadata(source_path: Path) -> dict[str, Any]:
    """Charge le sidecar YAML d'une source. Retourne un sidecar vide si absent.

    Ne leve pas d'exception : la segmentation doit continuer meme si un
    YAML est corrompu.
    """
    meta_path = metadata_path_for(source_path)
    empty: dict[str, Any] = {"md_valid": False, "metadata_valid": False,
                              "excluded": False, "segment": {},
                              "active_engine": "", "source_url": "",
                              "licence": "", "depot": {},
                              "validated_in_bulk": False, "fields": {}}
    if not meta_path.is_file():
        return empty
    try:
        raw = meta_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Lecture impossible du sidecar %s : %s", meta_path, exc)
        return empty
    if not raw.strip():
        return empty
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.warning("YAML invalide dans %s : %s", meta_path, exc)
        return empty
    if not isinstance(data, dict):
        return empty
    md_valid, meta_valid = _extract_valid_flags(data)
    excluded = bool(data.get("excluded"))
    segment = _normalise_segment(data.get("segment"))
    engine = _normalise_engine(data.get("active_engine"))
    source_url = _normalise_source_url(data.get("source_url"))
    # Portee par le lot, jamais deduite d'un document : c'est une provenance,
    # pas un champ metier. Elle vit donc a part, comme `source_url`.
    licence = str(data.get("licence") or "").strip()
    # Provenance d'un document venu d'un site : chemin dans le depot et commit.
    # Meme statut que `licence` — une provenance, jamais un champ metier.
    depot = data.get("depot") if isinstance(data.get("depot"), dict) else {}
    in_bulk = bool(data.get("validated_in_bulk"))
    fields = data.get("fields")
    if not isinstance(fields, dict):
        return {"md_valid": md_valid, "metadata_valid": meta_valid,
                "excluded": excluded, "segment": segment,
                "active_engine": engine, "source_url": source_url,
                "licence": licence, "depot": depot,
                "validated_in_bulk": in_bulk, "fields": {}}
    normalised: dict[str, Any] = {}
    for k, v in fields.items():
        key = str(k).strip()
        if not key:
            continue
        if isinstance(v, dict):
            value = v.get("value")
            source = str(v.get("source") or SOURCE_MANUAL)
            if source not in ALLOWED_SOURCES:
                source = SOURCE_MANUAL
            normalised[key] = {"value": value, "source": source}
        else:
            # Retro-compat : valeur brute traitee comme manuelle.
            normalised[key] = {"value": v, "source": SOURCE_MANUAL}
    return {"md_valid": md_valid, "metadata_valid": meta_valid,
            "excluded": excluded, "segment": segment,
            "active_engine": engine, "source_url": source_url,
            "licence": licence, "depot": depot,
            "validated_in_bulk": in_bulk, "fields": normalised}


def write_metadata(source_path: Path, data: dict[str, Any]) -> None:
    """Ecrit le sidecar YAML pour une source. Cree le dossier si besoin.

    Format en entree : {"md_valid": bool, "metadata_valid": bool,
                        "fields": {key: {"value": …, "source": auto|lot|manual}}}
    """
    meta_path = metadata_path_for(source_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    fields = data.get("fields") or {}
    clean: dict[str, Any] = {}
    for k, v in fields.items():
        key = str(k).strip()
        if not key:
            continue
        if not isinstance(v, dict):
            clean[key] = {"value": v, "source": SOURCE_MANUAL}
            continue
        value = v.get("value")
        source = str(v.get("source") or SOURCE_MANUAL)
        if source not in ALLOWED_SOURCES:
            source = SOURCE_MANUAL
        # On ecrit meme les valeurs vides : permet a l'utilisateur de
        # marquer un champ comme "vu, rien a mettre" sans que l'auto-fill
        # revienne l'ecraser. Le filtrage se fait a l'injection (flatten).
        clean[key] = {"value": value, "source": source}
    md_valid, meta_valid = _extract_valid_flags(data)
    payload: dict[str, Any] = {
        "md_valid": md_valid,
        "metadata_valid": meta_valid,
        "excluded": bool(data.get("excluded")),
        "fields": clean,
    }
    # Blocs optionnels : on ne les ecrit que s'ils portent une vraie valeur.
    segment = _normalise_segment(data.get("segment"))
    if segment:
        payload["segment"] = segment
    engine = _normalise_engine(data.get("active_engine"))
    if engine:
        payload["active_engine"] = engine
    source_url = _normalise_source_url(data.get("source_url"))
    if source_url:
        payload["source_url"] = source_url
    licence = str(data.get("licence") or "").strip()
    if licence:
        payload["licence"] = licence
    depot = data.get("depot")
    if isinstance(depot, dict) and depot:
        payload["depot"] = depot
    if data.get("validated_in_bulk"):
        payload["validated_in_bulk"] = True
    text = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    meta_path.write_text(text, encoding="utf-8")


def update_metadata(source_path: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Ecriture partielle : n'ecrase que les cles presentes dans `patch`.

    A preferer systematiquement a write_metadata depuis une requete HTTP.
    write_metadata remplace le sidecar entier : un client qui n'envoie que
    {md_valid, metadata_valid, fields} (c'est le cas du viewer) effacerait
    silencieusement `excluded`, `segment` et `active_engine`.

    Retourne le sidecar relu apres ecriture.
    """
    data = read_metadata(source_path)
    for key in ("md_valid", "metadata_valid", "excluded", "segment",
                "active_engine", "source_url", "licence", "depot",
                "validated_in_bulk", "fields"):
        if key in patch:
            data[key] = patch[key]
    # `validated_in_bulk` ne survit pas a un changement de validation venu
    # d'ailleurs : cocher la case du viewer, basculer le rond d'une ligne ou
    # reconvertir (qui remet `md_valid` a false) veut dire que l'etat du
    # document ne vient plus du bouton de validation en lot. Seul l'appelant
    # qui pose le drapeau explicitement le conserve.
    if ("md_valid" in patch or "metadata_valid" in patch)             and "validated_in_bulk" not in patch:
        data["validated_in_bulk"] = False
    write_metadata(source_path, data)
    return read_metadata(source_path)


def sidecar_source_for_stem(theme_dir: Path, stem: str) -> Path:
    """Chemin de source (hypothetique) permettant d'atteindre le sidecar
    d'un document quand on ne connait que son stem.

    Le sidecar est nomme d'apres le stem, donc l'extension exacte de la
    source n'a pas d'importance ici : metadata_path_for ne regarde que le
    stem et le nom du dossier parent.
    """
    return theme_dir / SOURCES_SUBDIR / f"{stem}.pdf"


def read_by_stem(theme_dir: Path, stem: str) -> dict[str, Any]:
    return read_metadata(sidecar_source_for_stem(theme_dir, stem))


def theme_has_source_url(theme_dir: Path) -> bool:
    """Vrai des qu'un document du theme porte une URL d'origine.

    C'est ce qui decide si `source_url` occupe reellement une des places
    des metadonnees d'un chunk : sans aucune URL, la cle n'est jamais
    emise et la place revient aux champs du schema.
    """
    conversions = theme_dir / SIDECAR_SUBDIR
    if not conversions.is_dir():
        return False
    for meta_path in conversions.glob("*" + METADATA_SUFFIX):
        stem = meta_path.name[: -len(METADATA_SUFFIX)]
        if read_by_stem(theme_dir, stem).get("source_url"):
            return True
    return False


def update_by_stem(theme_dir: Path, stem: str, patch: dict[str, Any]) -> dict[str, Any]:
    return update_metadata(sidecar_source_for_stem(theme_dir, stem), patch)


def merge_annotation(existing: dict[str, Any],
                     proposees: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Merge de valeurs proposees par un modele, quelle que soit sa source.

    **Regle unique : on ne remplit que le vide.** Une valeur deja posee
    reste, quelle que soit son origine — lot, saisie a la main, conversion,
    ou remplissage precedent. Un modele ne reprend jamais la main sur une
    case occupee.

    Cela vaut pour les deux chemins, l'annotation lue dans le PDF pendant la
    conversion (etape 3) comme le remplissage deduit du Markdown (etape 4).
    Les faire diverger reviendrait a ce que la valeur d'un champ depende du
    bouton sur lequel on a clique en dernier, ce qui est precisement ce
    qu'on ne veut pas d'une metadonnee qu'on relit et qu'on valide.

    Le resultat est reecrit tel quel par write_metadata : on repart donc du
    sidecar existant et on ne remplace que `fields`. Enumerer les cles a
    garder serait un piege — toute cle ajoutee un jour au sidecar et oubliee
    ici disparaitrait au premier remplissage, ce qui est arrive a
    `source_url`.

    Retourne (sidecar, cles_remplies).
    """
    champs = (existing.get("fields") or {}).copy()
    remplies: list[str] = []
    for key, value in (proposees or {}).items():
        if value in (None, "", []):
            continue
        actuel = champs.get(key)
        valeur_actuelle = (actuel.get("value") if isinstance(actuel, dict)
                           else actuel)
        if valeur_actuelle not in (None, "", []):
            continue
        champs[key] = {"value": value, "source": SOURCE_AUTO}
        remplies.append(key)
    fusionne = dict(existing)
    fusionne["fields"] = champs
    return fusionne, remplies


def merge_autofill(existing: dict[str, Any], autofilled: dict[str, Any]) -> dict[str, Any]:
    """Merge du remplissage de l'etape 4. Meme regle que `merge_annotation`.

    Conserve pour ses appelants, qui n'ont pas besoin de la liste des cles
    remplies. Rafraichissait autrefois les valeurs `source: auto` ; ne le
    fait plus : un champ deja rempli ne se re-remplit pas.
    """
    fusionne, _ = merge_annotation(existing, autofilled)
    return fusionne


def delete_metadata(source_path: Path) -> bool:
    """Supprime le sidecar. Retourne True si un fichier a ete supprime."""
    meta_path = metadata_path_for(source_path)
    if not meta_path.is_file():
        return False
    try:
        meta_path.unlink()
        return True
    except OSError as exc:
        logger.warning("Suppression impossible du sidecar %s : %s", meta_path, exc)
        return False


def flatten_for_chunk(data: dict[str, Any]) -> dict[str, Any]:
    """Aplati un sidecar en clefs consommables par le front matter d'un chunk.

    Chaque champ non vide devient une cle de premier niveau. Le marqueur
    `source` (auto/manual) et le flag `valid` ne sont pas propages.
    """
    out: dict[str, Any] = {}
    fields = data.get("fields") or {}
    for key, entry in fields.items():
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if value in (None, "", []):
            continue
        out[key] = value
    return out

