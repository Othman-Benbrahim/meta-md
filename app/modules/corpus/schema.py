"""Schema de metadonnees au niveau RAG (par theme).

Chaque RAG (dossier CORPUS/<THEME>/) peut definir la liste des champs que
l'utilisateur veut voir sur chaque document. Le schema n'est pas fige :
on peut ajouter/retirer/renommer des champs a tout moment, l'auto-fill
peut etre rejoue sur les documents deja extraits.

Fichier : CORPUS/<THEME>/schema.yaml

Structure :

    locked: false
    fields:
      - key: titre
        label: "Titre du document"
        description: "Titre principal, en francais."
        type: text
      - key: type_document
        label: "Type de document"
        description: "Nature du document."
        type: enum
        options: [programme, note, rapport, courrier, autre]

L'ordre des champs dans le YAML = ordre d'affichage dans l'UI.
`locked` : quand true, le schema est valide et verrouille, les inputs
UI passent en lecture seule. C'est le declencheur de fin d'etape 1
dans le tracker.

Le `type` d'un champ dit ce qu'est sa valeur, et cela pilote quatre
choses a la fois : la consigne donnee au LLM d'auto-fill, la
normalisation de ce qu'il renvoie, la forme prise dans le front matter
du Markdown (une liste reste une liste) et la saisie proposee dans le
viewer, comme le filtre qu'un visualiseur Markdown peut construire.

`options` n'a de sens que pour `enum` : c'est le vocabulaire ferme du
champ, propre a chaque corpus.

L'ordre des champs est celui de leur ecriture dans le front matter.
META-MD s'arrete la : ce qui part ensuite dans une collection, et sous
quelle contrainte de nombre, regarde le projet qui envoie.
"""
from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("atelier.corpus.schema")

SCHEMA_FILENAME = "schema.yaml"

# Types de champ. Chaque type dit comment la valeur est demandee au LLM,
# normalisee, ecrite dans le front matter, et filtrable dans un
# visualiseur Markdown.
#
#   text    texte libre               -> recherche textuelle
#   keyword valeur courte exacte      -> facette (valeurs du corpus)
#   enum    vocabulaire ferme         -> facette a cases (options connues)
#   tags    liste de mots-cles        -> facette multi-valeurs
#   date    YYYY | YYYY-MM | YYYY-MM-DD -> intervalle, tri
#   number  nombre                    -> intervalle
#   bool    vrai/faux                 -> oui/non
#   url     lien http(s)              -> pas un filtre, un lien
FIELD_TYPES = ("text", "keyword", "enum", "tags", "date", "number", "bool", "url")
DEFAULT_FIELD_TYPE = "text"

# Champs qu'on ne propose jamais comme filtre, quoi qu'on coche.
#
# Un filtre sert a reduire une liste de documents a ceux qui partagent une
# valeur. `titre` en a une par document : facetter dessus rendrait autant de
# cases que de documents, ce qui ne reduit rien. `source_url` est un lien,
# pas une categorie. Les autres champs peuvent l'etre ou non, c'est le
# corpus qui decide, champ par champ.
#
# Le TYPE ne decide rien ici : `discipline` et `niveau_d_enseignement` sont
# du texte libre et sont pourtant les meilleurs filtres du corpus, tandis que
# `texte_officiel`, du texte lui aussi, n'en est pas un. Seul le corpus sait,
# d'ou une case a cocher par champ plutot qu'une regle deduite du type.
NON_FILTRABLES = frozenset({"titre", "source_url"})

# Types dont la valeur est une liste : le front matter doit les ecrire en
# liste YAML, pas en chaine, sinon un visualiseur ne peut pas facetter.
LIST_FIELD_TYPES = ("tags",)

# Preset par defaut pose a la creation si aucun schema n'existe. L'utilisateur
# est libre d'ajouter/retirer/renommer les champs par la suite.
DEFAULT_FIELDS: list[dict[str, Any]] = [
    {
        "key": "titre",
        "label": "Titre du document",
        "description": "Titre principal du document",
        "type": "text",
    },
    {
        "key": "type_document",
        "label": "Type de document",
        "description": "Nature du document",
        # Vocabulaire volontairement generique et court : MD-RAG n'est pas
        # reserve a un domaine. Chaque corpus remplace cette liste par la
        # sienne dans l'etape 2.
        "type": "enum",
        "options": ["programme", "note", "rapport", "compte-rendu",
                    "courrier", "article", "autre"],
    },
    {
        "key": "date",
        "label": "Date",
        # Les dates partielles sont acceptees : beaucoup de documents ne
        # portent qu'une annee, et exiger le jour les laissait sans date.
        "description": "Date de production ou de publication",
        "type": "date",
    },
    {
        "key": "auteur_ou_entite",
        "label": "Auteur ou entité émettrice",
        "description": "Personne ou organisation à l'origine du document",
        "type": "keyword",
    },
    {
        "key": "tags",
        "label": "Tags",
        "description": "Mots-clés thématiques du document",
        "type": "tags",
    },
    {
        "key": "resume",
        "label": "Résumé",
        # La longueur ne se dit plus ici : elle appartient a la consigne
        # donnee au modele (`autofill.MAX_RESUME_CHARS`), pas a la
        # description du champ, qui dit ce qu'on attend et non sa taille.
        "description": "Résumé du document",
        "type": "text",
    },
]


# Champs poses par le pipeline, pas par l'utilisateur : ils accompagnent
# chaque document sans etre demandes a un modele. Leur cle et leur libelle
# sont figes, leur description reste libre puisqu'elle ne sert qu'a expliquer.
FIXED_FIELDS: list[dict[str, Any]] = [
    {
        "key": "source_url",
        "label": "URL d'origine",
        "description": "Lien public du document, posé au dépôt ou dans la révision",
        "type": "url",
        "fixed": True,
    },
]


def with_fixed_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ajoute les champs fixes absents, juste apres `titre`.

    `titre` nomme le document, `source_url` dit d'ou il vient : les deux
    lignes que l'utilisateur ne decide pas se suivent en tete de schema,
    avant les champs metier.
    """
    presentes = {str(f.get("key") or "").strip() for f in fields}
    manquants = [copy.deepcopy(f) for f in FIXED_FIELDS if f["key"] not in presentes]
    if not manquants:
        return fields
    apres_titre = next((i for i, f in enumerate(fields)
                        if str(f.get("key") or "").strip() == "titre"), -1)
    place = apres_titre + 1
    return fields[:place] + manquants + fields[place:]


def autofill_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Les champs qu'un modele peut remplir : les fixes n'en sont pas."""
    return [f for f in fields if not f.get("fixed")]


def schema_path(theme_dir: Path) -> Path:
    return theme_dir / SCHEMA_FILENAME


def read_schema(theme_dir: Path) -> dict[str, Any]:
    """Charge le schema d'un theme. Retourne le preset par defaut si absent.

    Ne cree pas le fichier ici ; l'ecriture ne se fait qu'au premier POST
    utilisateur ou lors d'un appel explicite a ensure_schema().

    """
    schema = _read_schema_raw(theme_dir)
    schema["fields"] = with_fixed_fields(schema["fields"])
    return schema


def _read_schema_raw(theme_dir: Path) -> dict[str, Any]:
    """Le schema tel qu'il est sur le disque, sans les valeurs derivees."""
    path = schema_path(theme_dir)
    if not path.is_file():
        return default_schema()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Lecture impossible du schema %s : %s", path, exc)
        return default_schema()
    if not raw.strip():
        return default_schema()
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.warning("YAML invalide dans %s : %s", path, exc)
        return default_schema()
    if not isinstance(data, dict) or not isinstance(data.get("fields"), list):
        return default_schema()
    return {
        "locked": bool(data.get("locked")),
        "fields": _normalise_fields(data["fields"]),
    }


def write_schema(theme_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Ecrit schema.yaml. Cree le dossier theme si besoin.

    Cascade importante : si l'ensemble des cles change (ajout/suppression
    d'un champ), on considere que les validations `metadata_valid` des
    sidecars ne sont plus fiables → on les remet a false. Les documents
    concernes ne sont donc plus segmentables tant qu'ils n'ont pas ete
    revalides.

    Le renommage de `label` ou `description` sans changer la cle
    n'invalide rien.

    Retourne un dict {invalidated: bool, sidecars_reset: N} pour informer
    l'appelant.
    """
    theme_dir.mkdir(parents=True, exist_ok=True)
    fields = _normalise_fields(data.get("fields") or [])
    old_keys = _field_keys_from_disk(theme_dir)
    new_keys = {f["key"] for f in fields}

    payload: dict[str, Any] = {
        "locked": bool(data.get("locked")),
        "fields": fields,
    }
    text = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    schema_path(theme_dir).write_text(text, encoding="utf-8")

    if old_keys is not None and old_keys != new_keys:
        return _invalidate_after_schema_change(theme_dir)
    return {"invalidated": False, "sidecars_reset": 0}


def _field_keys_from_disk(theme_dir: Path) -> set[str] | None:
    """Retourne l'ensemble des cles du schema actuellement sur disque, ou
    None si aucun schema n'existe encore (premiere ecriture)."""
    path = schema_path(theme_dir)
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    fields = data.get("fields") or []
    if not isinstance(fields, list):
        return None
    return {str(f.get("key") or "").strip() for f in fields if isinstance(f, dict)}


def _invalidate_after_schema_change(theme_dir: Path) -> dict[str, Any]:
    """Cascade quand les cles du schema changent :
    - Reset `metadata_valid: false` sur tous les sidecars de 1-Sources/.
      `md_valid` est preserve (le contenu MD lui n'a pas change).
    - Resynchronise le front matter des MDs actifs pour en purger les cles
      qui ne sont plus au schema.

    Les documents concernes redeviennent non segmentables tant que leurs
    metadonnees n'ont pas ete revalidees.
    """
    # Import tardif pour eviter les cycles.
    from . import frontmatter, metadata

    sources_dir = theme_dir / "1-Sources"
    reset = 0
    if sources_dir.is_dir():
        for pdf in sources_dir.iterdir():
            if not pdf.is_file() or metadata.is_metadata_file(pdf):
                continue
            sidecar = metadata.read_metadata(pdf)
            if not sidecar.get("metadata_valid"):
                continue
            sidecar["metadata_valid"] = False
            try:
                metadata.write_metadata(pdf, sidecar)
                reset += 1
            except OSError as exc:
                logger.warning("Reset metadata_valid KO %s : %s", pdf, exc)
    # Les cles retirees du schema doivent disparaitre du front matter des MDs
    # actifs : sync_front_matter reconstruit ces cles depuis les sidecars.
    frontmatter.sync_all_front_matter(theme_dir)
    logger.info("Schema change cascade : sidecars_reset=%d", reset)
    return {"invalidated": True, "sidecars_reset": reset}


def ensure_schema(theme_dir: Path) -> dict[str, Any]:
    """Retourne le schema du theme, en le creant avec le preset si absent."""
    path = schema_path(theme_dir)
    if not path.is_file():
        write_schema(theme_dir, {"fields": default_schema()["fields"]})
    return read_schema(theme_dir)


def default_schema() -> dict[str, Any]:
    """Retourne une copie du preset par defaut. Toujours copier pour eviter
    les mutations partagees."""
    import copy
    return {"locked": False, "fields": copy.deepcopy(DEFAULT_FIELDS)}


def _normalise_options(raw: Any) -> list[str]:
    """Vocabulaire ferme d'un champ `enum`, en liste de chaines propres.

    Accepte une liste, ou une saisie libre separee par des virgules ou des
    retours a la ligne : c'est ce que tape l'utilisateur dans l'etape 2.
    """
    if isinstance(raw, str):
        items: list[Any] = re.split(r"[,\n]", raw)
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []
    out: list[str] = []
    for item in items:
        value = str(item).strip()
        if value and value not in out:
            out.append(value)
    return out


def _normalise_fields(raw: list[Any]) -> list[dict[str, Any]]:
    """Nettoie une liste de champs reçue de l'utilisateur.

    - Supprime les entrees sans cle
    - Deduplique par cle (garde la premiere occurrence)
    - Trim les strings
    - Type absent ou inconnu : `text`
    - `options` n'est conserve que pour `enum`
    - `filtre` refuse pour les cles qui ne facettent rien (`NON_FILTRABLES`)
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        label = str(entry.get("label") or key).strip()
        description = str(entry.get("description") or "").strip()
        ftype = str(entry.get("type") or "").strip().lower()
        if ftype not in FIELD_TYPES:
            ftype = DEFAULT_FIELD_TYPE
        field: dict[str, Any] = {
            "key": key,
            "label": label,
            "description": description,
            "type": ftype,
        }
        # Un champ fixe garde sa marque : c'est elle qui interdit d'en
        # changer la cle et le libelle, et qui l'ecarte de l'auto-fill.
        if entry.get("fixed"):
            field["fixed"] = True
        if entry.get("filtre") and key not in NON_FILTRABLES:
            field["filtre"] = True
        if ftype == "enum":
            options = _normalise_options(entry.get("options"))
            if options:
                field["options"] = options
        out.append(field)
    return out


def field_types(theme_dir: Path) -> dict[str, str]:
    """{cle: type} pour le schema d'un theme. Cles inconnues : `text`."""
    return {f["key"]: f.get("type", DEFAULT_FIELD_TYPE)
            for f in read_schema(theme_dir).get("fields") or []}
