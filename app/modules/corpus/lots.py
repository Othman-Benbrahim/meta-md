"""Lots predefinis, auto-decouverts depuis `lots/*.json`.

Chaque lot vit dans un fichier JSON de `lots/`, a la racine du depot,
auto-descriptif :

    {
      "slug": "programmes-secondaire",
      "source": { "type": "json", "url": "https://…" },
      "presentation": [ {cle, valeur, label}, ... ],
      "metadata": [ {key, label, type, description}, ... ],
      "data": [ {identifiant, titre, cycle, source_url, ...}, ... ]
    }

Trois sections portent le lot : `presentation` dit ce qu'il est, `metadata`
decrit les champs de ses documents, `data` liste les documents eux-memes.

Le JSON est la seule source du lot : rien n'est rejoue depuis le jeu de
donnees d'origine (le bouton « Actualiser » et le registre de transforms ont
ete retires le 2026-08-08), et le bloc `source` ne sert plus qu'a dire d'ou
viennent les donnees. Ajouter un lot = deposer un JSON de cette forme ; le
mettre a jour = remplacer son fichier.

Trois dossiers en portent, et `dossiers()` ecrit l'ordre entre eux. Le
catalogue distant est telecharge par `corpus.catalogue`, qui copie des fichiers
deja prets — ce n'est pas la recuperation en ligne retiree en aout.

Seul `entries[].source_url` est appele sur le reseau, au telechargement des
PDFs, et `entries[].identifiant` designe une entree. Les autres cles sont
libres : la preview affiche toutes celles qu'elle trouve.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import chemins
from . import metadata

logger = logging.getLogger("atelier.corpus.lots")

DOWNLOAD_TIMEOUT = 60.0
DOWNLOAD_USER_AGENT = "MD-RAG-local/1.0"

# Deux dossiers portent des lots, et l'ordre entre eux est une regle : le
# premier qui porte un slug l'emporte.
#
#   data/lots/            les votres. META-MD n'y ecrit jamais et une mise a
#                         jour ne les touche pas. Vide a l'installation.
#   data/lots-catalogue/  ce que META-MD telecharge du depot de donnees.
#                         Reecrit a chaque rafraichissement, donc jamais un
#                         endroit ou poser quelque chose a soi.
#
# Deposer son `programmes-nsi.json` dans `data/lots/` est donc la facon de
# corriger un lot publie sans que le prochain telechargement ecrase la
# correction.
#
# **Aucun lot n'est livre avec l'application.** Un dossier `app/lots/` a
# existe, comme repli hors ligne ; il entrait en concurrence avec le catalogue
# de deux facons mesurees — renommer un lot dans `lots-json/` faisait
# reapparaitre la copie livree A COTE de la nouvelle, et retirer un lot le
# faisait revenir d'entre les morts. Le prix de ces regles depassait ce que
# rendait le repli. Sans reseau, la liste est donc vide, et l'interface le dit.
ORIGINE_UTILISATEUR = "utilisateur"
ORIGINE_CATALOGUE = "catalogue"

LIBELLE_ORIGINE = {
    ORIGINE_UTILISATEUR: "le votre",
    ORIGINE_CATALOGUE: "du catalogue",
}


def dossier_utilisateur() -> Path:
    return chemins.dossier_data() / "lots"


def dossier_catalogue() -> Path:
    return chemins.dossier_data() / "lots-catalogue"


def dossiers() -> list[tuple[str, Path]]:
    """Les deux sources, de la plus prioritaire a la moins."""
    return [(ORIGINE_UTILISATEUR, dossier_utilisateur()),
            (ORIGINE_CATALOGUE, dossier_catalogue())]


def lots_dir() -> Path:
    """Le dossier ou l'on depose ses propres lots."""
    return dossier_utilisateur()


AIDE_UTILISATEUR = """# Vos lots

Deposez ici vos propres lots, un fichier `.json` par lot. Ils apparaitront
dans META-MD a la prochaine ouverture, a cote de ceux du catalogue.

Ce dossier est a vous : META-MD n'y ecrit jamais rien et une mise a jour ne le
touche pas. Un lot que vous posez ici **masque celui du catalogue** s'il porte
le meme nom de fichier : c'est la facon de corriger un lot publie sans que le
prochain telechargement efface votre correction.

Le nom du fichier designe le lot. `mon-lot.json` :

    {
      "presentation": [
        { "cle": "titre", "label": "Titre", "valeur": "Mon lot" },
        { "cle": "description", "label": "Description", "valeur": "…" }
      ],
      "metadata": [
        { "key": "titre", "label": "Titre", "type": "text",
          "description": "Le titre du document." }
      ],
      "data": [
        { "identifiant": "doc-1",
          "titre": "Mon document",
          "source_url": "https://exemple.fr/doc.pdf",
          "licence": "Licence Ouverte" }
      ]
    }

Trois sections : `presentation` dit ce qu'est le lot, `metadata` decrit les
champs qui deviendront le schema du corpus, `data` liste les documents.

Dans `data`, deux cles ont un role fixe : `identifiant` designe l'entree, et
`source_url` est la seule adresse appelee au telechargement. Toutes les autres
cles sont libres et deviennent des champs — declarez-les dans `metadata` pour
qu'elles portent un libelle.
"""


def poser_le_dossier_utilisateur() -> None:
    """Cree `data/lots/` et son mode d'emploi, une seule fois.

    Un dossier vide qu'on ne voit pas ne sert a personne : sans cela, « posez
    vos lots dans data/lots » demanderait de creer soi-meme un dossier au bon
    nom, au bon endroit. Le fichier d'aide n'est ecrit que s'il manque — on
    peut donc y ajouter ses propres notes.
    """
    dossier = dossier_utilisateur()
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        aide = dossier / "LISEZ-MOI.md"
        if not aide.exists():
            aide.write_text(AIDE_UTILISATEUR, encoding="utf-8")
    except OSError as exc:
        logger.warning("Dossier de lots personnels indisponible : %s", exc)


def _localiser(slug: str) -> tuple[str, Path] | None:
    """Le premier des trois dossiers qui porte ce slug, et lequel c'est."""
    for origine, dossier in dossiers():
        candidat = dossier / f"{slug}.json"
        if candidat.is_file():
            return origine, candidat
    return None


def _lot_path(slug: str) -> Path | None:
    trouve = _localiser(slug)
    return trouve[1] if trouve else None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _load_lot_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Lot %s illisible : %s", path.name, exc)
        return None


def _write_lot_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _all_lots() -> list[dict[str, Any]]:
    vus: dict[str, dict[str, Any]] = {}
    for origine, dossier in dossiers():
        if not dossier.is_dir():
            continue
        for p in sorted(dossier.glob("*.json")):
            # Le catalogue range son inventaire a cote des lots ; le prefixe
            # `_` le tient hors de la decouverte, comme ailleurs dans META-MD.
            if p.name.startswith("_"):
                continue
            if p.stem in vus:
                logger.info("Lot %s : la copie %s masque celle %s",
                            p.stem, LIBELLE_ORIGINE[vus[p.stem]["origine"]],
                            LIBELLE_ORIGINE[origine])
                continue
            data = _load_lot_file(p)
            if not data or not isinstance(data, dict):
                continue
            data["origine"] = origine
            vus[p.stem] = _renommer_slug(data, p)
    return [vus[cle] for cle in sorted(vus)]


def _renommer_slug(data: dict[str, Any], p: Path) -> dict[str, Any]:
    """Le nom du fichier fait foi, meme quand le JSON porte un `slug`.

    `_lot_path` retrouve le lot en recomposant `<slug>.json`, donc un slug
    interne qui diverge du nom du fichier rend un lot fantome — liste dans
    l'interface, introuvable a l'ouverture (404 « Lot inconnu »).
    """
    declare = str(data.get("slug") or "").strip()
    if declare and declare != p.stem:
        logger.warning("Lot %s : slug declare %r ignore, le nom du fichier fait foi",
                       p.name, declare)
    data["slug"] = p.stem
    return data



# ---------------------------------------------------------------------------
# Mapping entrée → sidecar (front matter métier)
# ---------------------------------------------------------------------------

# Cles d'une entree qui ne sont pas des champs metier : `identifiant` designe
# l'entree dans le lot, `source_url` a sa place a part dans le sidecar. Tout
# le reste est une colonne choisie par l'auteur du lot, et vaut donc un champ.
# `licence` a rejoint la liste : elle decrit d'ou vient le document et sous
# quelles conditions il se reutilise, pas ce qu'il raconte. En faire un champ
# du schema la ferait facetter dans EduMD, alors qu'elle vaut la meme chose
# pour tout le lot.
_ENTRY_RESERVED_KEYS = frozenset({"identifiant", "source_url", "licence"})


def source_url_from_entry(entry: dict[str, Any]) -> str:
    """URL publique a citer pour ce document.

    `source_url` pointe le document lui-meme : c'est ce qu'on veut donner a
    un lecteur qui veut verifier un extrait.
    """
    url = str(entry.get("source_url") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ""


def _sidecar_from_entry(entry: dict[str, Any],
                        sidecar_source: str = metadata.SOURCE_LOT) -> dict[str, Any]:
    """Champs metier d'une entree de lot.

    Les colonnes d'un lot sont libres : elles sont declarees dans sa section
    `metadata` et deviennent le schema du corpus. On reprend donc tout ce que
    porte l'entree plutot qu'une liste de cles ecrite d'avance, qui laissait
    tomber sans rien dire les colonnes qu'elle ne connaissait pas.
    """
    fields: dict[str, Any] = {}
    for key, value in entry.items():
        cle = str(key).strip()
        if not cle or cle in _ENTRY_RESERVED_KEYS:
            continue
        if value in (None, "", []):
            continue
        fields[cle] = {"value": value, "source": sidecar_source}
    return {"md_valid": False, "metadata_valid": False, "fields": fields,
            "source_url": source_url_from_entry(entry),
            "licence": str(entry.get("licence") or "").strip()}


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

# Champs du schema que le lot ne peut pas connaitre : ils se lisent dans le
# document, pas dans le jeu de donnees d'ou vient la liste.
_LLM_FIELDS = [
    {"key": "tags", "label": "Tags", "type": "tags",
     "description": "Mots-clés thématiques."},
    {"key": "resume", "label": "Résumé", "type": "text",
     "description": "Résumé du document."},
]


def lot_title(lot: dict[str, Any]) -> str:
    """Titre affiche d'un lot, pris dans sa presentation.

    La ligne `titre` fait foi ; a defaut la premiere ligne presentee, qui
    est celle que l'auteur du lot a mise en tete. Sans presentation, le nom
    du fichier reste le seul nom disponible.
    """
    lignes = [l for l in (lot.get("presentation") or []) if isinstance(l, dict)]
    for ligne in lignes:
        if str(ligne.get("cle") or "").strip().lower() == "titre":
            valeur = str(ligne.get("valeur") or "").strip()
            if valeur:
                return valeur
    for ligne in lignes:
        valeur = str(ligne.get("valeur") or "").strip()
        if valeur:
            return valeur
    return str(lot.get("slug") or "")


def lot_entries(lot: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in (lot.get("data") or []) if isinstance(e, dict)]


def list_lots() -> list[dict[str, Any]]:
    return [{
        "slug": lot.get("slug"),
        "title": lot_title(lot),
        "file": f"{lot.get('slug')}.json",
        "fetched_at": lot.get("fetched_at"),
        "count": len(lot_entries(lot)),
        "origine": lot.get("origine"),
        "origine_label": LIBELLE_ORIGINE.get(str(lot.get("origine")), ""),
    } for lot in _all_lots()]


def get_lot(slug: str) -> dict[str, Any] | None:
    trouve = _localiser(slug)
    if trouve is None:
        return None
    origine, p = trouve
    data = _load_lot_file(p)
    if data is None:
        return None
    data["origine"] = origine
    # Meme invariant que `_all_lots` : le lot rendu se designe par le slug qui
    # permet de le retrouver, jamais par celui que le fichier declare.
    data["slug"] = p.stem
    return data


def get_schema_template(slug: str) -> list[dict[str, Any]] | None:
    lot = get_lot(slug)
    if not lot:
        return None
    champs = lot.get("metadata")
    if not isinstance(champs, list):
        return None
    # `source_url` decrit d'ou vient le document, pas ce qu'il contient :
    # il devient le `source_url` du sidecar, jamais un champ du schema.
    schema = [copy.deepcopy(f) for f in champs
              if isinstance(f, dict) and str(f.get("key") or "").strip() != "source_url"]
    # Un libelle nomme une metadonnee a l'ecran : majuscule initiale, comme
    # un titre de colonne. Seule la premiere lettre est touchee.
    for champ in schema:
        libelle = str(champ.get("label") or "")
        if libelle:
            champ["label"] = libelle[0].upper() + libelle[1:]
    # `tags` et `resume` ne viennent jamais du jeu de donnees : ils se
    # lisent dans le document. On les ajoute pour que l'auto-fill ait de
    # quoi travailler des le telechargement.
    cles = {str(f.get("key") or "").strip() for f in schema if isinstance(f, dict)}
    schema += [copy.deepcopy(f) for f in _LLM_FIELDS if f["key"] not in cles]
    return schema


# Ordre d'affichage des colonnes connues, du plus general au plus technique.
# Les cles absentes sont ignorees, celles qu'on ne connait pas sont ajoutees
# a la suite : un lot d'un autre format s'affiche donc entierement, sans
# toucher a ce module.
PREVIEW_COLUMN_ORDER = [
    "titre", "cycle", "niveau", "voie", "discipline",
    "texte_officiel", "rentree_debut", "rentree_fin", "en_vigueur",
    "source_url", "lien_legifrance", "identifiant",
]


def _preview_columns(entries: list[dict[str, Any]]) -> list[str]:
    """Cles presentes dans au moins une entree, dans un ordre stable."""
    seen: list[str] = []
    for e in entries:
        for k in e:
            if k not in seen:
                seen.append(k)
    known = [k for k in PREVIEW_COLUMN_ORDER if k in seen]
    extra = sorted(k for k in seen if k not in PREVIEW_COLUMN_ORDER)
    return known + extra


def preview_lot(slug: str) -> dict[str, Any]:
    lot = get_lot(slug)
    if lot is None:
        raise FileNotFoundError(f"Lot inconnu : {slug}")
    entries = lot_entries(lot)
    # Entrees rendues telles quelles : la preview montre tout ce que le lot
    # sait d'un document, pas une selection de champs.
    preview_entries = [dict(e) for e in entries]
    return {
        "slug": slug,
        "title": lot_title(lot),
        "presentation": lot.get("presentation") or [],
        "count": len(entries),
        "fetched_at": lot.get("fetched_at"),
        "columns": _preview_columns(entries),
        "entries": preview_entries,
    }


# ---------------------------------------------------------------------------
# Download (identique à l'ancien : télécharge chaque `contenu_url` + sidecar)
# ---------------------------------------------------------------------------

def _filename_from_url(url: str, fallback_prefix: str = "doc") -> str:
    parsed = urlparse(url)
    tail = Path(parsed.path).name or ""
    if tail.lower().endswith(".pdf"):
        return tail
    if tail:
        return tail if "." in tail else tail + ".pdf"
    import hashlib
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{fallback_prefix}-{h}.pdf"


def download_lot(slug: str, theme_dir: Path,
                 progress_callback: Any = None,
                 entry_ids: list[str] | None = None) -> dict[str, Any]:
    """Telecharge les PDFs du lot.

    Si `entry_ids` est None : tout le lot.
    Sinon : uniquement les entrees dont l'`id` est dans la liste blanche.
    """
    lot = get_lot(slug)
    if lot is None:
        return {"ok": False, "error": f"Lot inconnu : {slug}"}
    all_entries = lot_entries(lot)
    if entry_ids is not None:
        wanted = set(entry_ids)
        entries = [e for e in all_entries if e.get("identifiant") in wanted]
        if not entries:
            return {"ok": False, "error": "Aucune entrée sélectionnée ne correspond au lot."}
    else:
        entries = all_entries
    sources_dir = theme_dir / "1-Sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    lot_json_path = sources_dir / f"_lot_{slug}.json"
    lot_json_path.write_text(
        json.dumps({
            "slug": slug,
            "title": lot_title(lot),
            "source": lot.get("source"),
            "fetched_at": lot.get("fetched_at"),
            "counts": lot.get("counts"),
            "data": entries,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total = sum(1 for e in entries if (e.get("source_url") or "").strip())
    downloaded = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_targets: set[str] = set()
    processed = 0

    if progress_callback:
        progress_callback(processed=0, total=total, current=None)

    started = time.monotonic()
    with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": DOWNLOAD_USER_AGENT}) as client:
        for entry in entries:
            url = (entry.get("source_url") or "").strip()
            if not url:
                skipped += 1
                continue
            if url in seen_urls:
                skipped += 1
                continue
            seen_urls.add(url)

            filename = _filename_from_url(url)
            target = sources_dir / filename
            if filename in seen_targets:
                target = sources_dir / f"{entry.get('identifiant') or 'doc'}-{filename}"
            seen_targets.add(target.name)

            if target.is_file():
                skipped += 1
            else:
                try:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        # Sans trace, l'utilisateur ne lit qu'un compteur
                        # d'erreurs et n'a aucun moyen de comprendre.
                        logger.warning("%s : HTTP %s sur %s", slug,
                                       resp.status_code, url)
                        errors.append({"url": url, "error": f"HTTP {resp.status_code}"})
                        continue
                    target.write_bytes(resp.content)
                    downloaded += 1
                    logger.info("%s : telecharge %s (%d KB)", slug,
                                target.name, len(resp.content) // 1024)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("%s : echec de %s : %s: %s", slug, url,
                                   type(exc).__name__, exc)
                    errors.append({
                        "url": url,
                        "error": f"{type(exc).__name__}: {exc}" if str(exc)
                                 else type(exc).__name__,
                    })
                    continue

            try:
                # `lot` et non `manual` : ces valeurs viennent du jeu de
                # donnees, personne ne les a saisies ni corrigees. Elles sont
                # protegees de la meme facon — un modele ne remplit que le
                # vide — mais le viewer ne les annonce plus comme des
                # corrections humaines.
                sidecar_data = _sidecar_from_entry(entry,
                                                   sidecar_source=metadata.SOURCE_LOT)
                existing = metadata.read_metadata(target)
                merged_fields = dict((existing.get("fields") or {}))
                for k, v in sidecar_data["fields"].items():
                    cur = merged_fields.get(k)
                    if isinstance(cur, dict) and cur.get("value") not in (None, "", []):
                        continue
                    merged_fields[k] = v
                # Ecriture partielle : write_metadata remplacerait le sidecar
                # entier et effacerait `excluded`, `segment` et `active_engine`
                # d'un document deja travaille qu'on retelecharge.
                patch: dict[str, Any] = {"fields": merged_fields}
                # L'URL du lot ne s'impose pas a une URL deja posee.
                if sidecar_data["source_url"] and not existing.get("source_url"):
                    patch["source_url"] = sidecar_data["source_url"]
                metadata.update_metadata(target, patch)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Sidecar KO pour %s : %s", target.name, exc)

            processed += 1
            if progress_callback:
                progress_callback(processed=processed, total=total, current=target.name)

    return {
        "ok": True,
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
        "duration_seconds": round(time.monotonic() - started, 2),
        "sources_dir": str(sources_dir),
        "lot_json": str(lot_json_path.name),
    }
