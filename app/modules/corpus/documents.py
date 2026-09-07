"""Gestion des conversions PDF -> Markdown.

Layout disque, depuis la mise a plat du 2026-08-23 :

    CORPUS/<THEME>/
        1-Sources/
            doc1.pdf                          # l'input, jamais corrige
        2-Conversions/
            doc1.md                           # la conversion
            doc1.pdf                          # copie de la source, meme nom
            _metadata/
                doc1.metadata.yaml            # metadonnees + etat du document
            _anciens-moteurs/
                mistral_document_ai/doc1.md   # ce que les moteurs retires ont produit

**Une conversion par document, et elle est a la racine de `2-Conversions/`.**
L'axe de rangement etait le moteur (2026-08-05) ; il ne l'est plus depuis
qu'un seul moteur subsiste — le niveau `<moteur>/` etait un dossier a un seul
enfant pour 59 documents sur 66.

**Un dossier de conversion s'ouvre tel quel** : chaque Markdown est pose a
cote du document dont il vient, sous le meme nom, sans rien a rapprocher.
C'est deja la forme de l'archive d'export ; elle vaut maintenant aussi sur le
disque. La copie ne se fait pas quand la source est elle-meme un `.md` (corpus
entres par un depot) : deux fichiers de meme nom au contenu different dans un
meme dossier seraient un piege, pas un service. Voir `source_a_cote`.

Les deux dossiers prefixes `_` ne sont scannes par rien : le prefixe suffit,
aucune exception n'a ete ajoutee nulle part.

- `_metadata/` porte les sidecars, sortis de la racine ou ils se melaient aux
  couples document/conversion.
- `_anciens-moteurs/` garde ce que `pymupdf`, `albert_vision`,
  `mistral_document_ai` et `albert_ocr` avaient produit. Ces fichiers ne sont
  plus proposes nulle part — il n'y a plus de conversion a choisir — mais ils
  ne sont pas perdus, et une suppression de document les emporte avec le
  reste.

Reconvertir ecrase le fichier precedent (l'appelant doit demander
confirmation : voir `conversion_exists`). Le moteur qui a produit la
conversion est note dans le sidecar (`active_engine`) et dans le front matter
(`_engine`) ; `enregistrer_le_moteur` l'ecrit et remet `md_valid` a false,
puisque le Markdown vient d'etre reecrit et n'a donc pas ete relu.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from . import metadata

logger = logging.getLogger("atelier.corpus.documents")

CONVERSIONS_DIR = "2-Conversions"
SOURCES_DIR = "1-Sources"
# Les conversions des moteurs retires, gardees hors du chemin. Le prefixe
# `_` les rend invisibles de tout ce qui scanne `2-Conversions/`.
ANCIENS_DIRNAME = "_anciens-moteurs"

# Un nom de dossier de moteur. Le prefixe "_" est reserve (dossiers internes
# comme _archive/), et les noms sont valides avant d'etre utilises comme
# segment de chemin.
ENGINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


def slug_engine(engine: str) -> str:
    """Normalise un nom de moteur pour l'utiliser comme nom de dossier."""
    slug = re.sub(r"[^A-Za-z0-9_\-]+", "_", (engine or "").strip()).strip("_-")
    return slug or "inconnu"


def conversions_root(theme_dir: Path) -> Path:
    return theme_dir / CONVERSIONS_DIR


def anciens_dir(theme_dir: Path) -> Path:
    """Ou dorment les conversions des moteurs retires."""
    return conversions_root(theme_dir) / ANCIENS_DIRNAME


def ancienne_conversion_path(theme_dir: Path, stem: str, engine: str) -> Path:
    return anciens_dir(theme_dir) / slug_engine(engine) / f"{stem}.md"


def conversion_path(theme_dir: Path, stem: str) -> Path:
    """La conversion d'un document. Il n'y en a qu'une."""
    return conversions_root(theme_dir) / f"{stem}.md"


def conversion_exists(theme_dir: Path, stem: str) -> bool:
    return conversion_path(theme_dir, stem).is_file()


def source_a_cote(md_path: Path, source_path: Path) -> Path | None:
    """Ou se copie le document d'origine, a cote de sa conversion.

    Rend None quand la source est elle-meme un Markdown — c'est le cas des
    corpus qui entrent par un depot (`11ty`, `mkdocs`). La copier produirait
    deux fichiers de meme nom dans le meme dossier, l'un livre par le depot et
    l'autre corrige dans le viewer : le piege exact qu'on veut eviter.
    """
    if source_path.suffix.lower() == ".md":
        return None
    return md_path.with_suffix(source_path.suffix)


def poser_la_source(md_path: Path, source_path: Path) -> Path | None:
    """Copie le document d'origine a cote de sa conversion, sous le meme nom.

    **Un dossier de conversion doit s'ouvrir tel quel** : le Markdown et la
    piece dont il vient, cote a cote, sans rien a rapprocher. C'est deja la
    forme de l'archive d'export ; elle vaut maintenant aussi sur le disque.

    La copie, et non un deplacement : `1-Sources/` reste l'input pur, celui
    que relisent la reconversion, l'exclusion et le telechargement d'un lot.
    Un PDF ne s'edite jamais, les deux exemplaires ne peuvent pas diverger.
    """
    cible = source_a_cote(md_path, source_path)
    if cible is None or not source_path.is_file():
        return None
    try:
        if cible.is_file() and cible.stat().st_size == source_path.stat().st_size:
            return cible  # deja posee, et de la meme taille : rien a refaire
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_bytes(source_path.read_bytes())
    except OSError as exc:
        logger.warning("Source non copiee a cote de %s : %s", md_path.name, exc)
        return None
    return cible


def annexes(md_path: Path) -> list[Path]:
    """Fichiers qu'une conversion pose a cote de son `.md`.

    La copie du document d'origine en fait partie : elle appartient a la
    conversion, pas au corpus — l'original reste dans `1-Sources/`. Sans
    cela, supprimer une conversion laisserait un PDF orphelin. Le rapport de
    conversion (`<stem>.rapport.md`) suit la meme regle : il decrit une
    fabrication precise, il n'a pas de sens sans elle.

    `<stem>.mistral.json` et `<stem>.assets/` venaient de
    `mistral_document_ai`, retire le 2026-08-23 ; plus aucun moteur n'en
    produit, mais les conversions faites avant lui sont toujours sur le
    disque et ces fichiers doivent suivre le sort de leur `.md`.
    """
    return [md_path.with_suffix(".pdf"),
            md_path.with_suffix(".rapport.md"),
            md_path.with_suffix(".mistral.json"),
            md_path.with_name(f"{md_path.stem}.assets")]


def _supprimer_annexes(md_path: Path) -> list[Path]:
    """Retire les annexes d'un `.md`. Rend la liste de ce qui a disparu."""
    retires: list[Path] = []
    for annexe in annexes(md_path):
        try:
            if annexe.is_file():
                annexe.unlink()
                retires.append(annexe)
            elif annexe.is_dir():
                for enfant in sorted(annexe.rglob("*"), reverse=True):
                    enfant.unlink() if enfant.is_file() else enfant.rmdir()
                annexe.rmdir()
                retires.append(annexe)
        except OSError as exc:
            logger.warning("Annexe non supprimee (%s) : %s", annexe.name, exc)
    return retires


def list_engines(theme_dir: Path) -> list[str]:
    """Moteurs dont une ancienne conversion dort encore sous `_anciens-moteurs/`."""
    root = anciens_dir(theme_dir)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and ENGINE_RE.match(p.name))


def list_conversions(theme_dir: Path, stem: str) -> list[dict[str, Any]]:
    """La conversion d'un document, dans une liste d'au plus un element.

    La liste est conservee — l'interface la lit deja — mais elle ne peut plus
    porter qu'une entree : il n'y a qu'une conversion par document depuis que
    les fichiers vivent a la racine de `2-Conversions/`. Les conversions des
    moteurs retires ne sont pas listees : elles ne sont plus proposables, et
    les faire apparaitre relancerait un choix qui n'existe plus.
    """
    p = conversion_path(theme_dir, stem)
    if not p.is_file():
        return []
    moteur = metadata.read_by_stem(theme_dir, stem).get("active_engine") or "inconnu"
    try:
        st = p.stat()
        size: int | None = st.st_size
        ts = _format_ts(st.st_mtime)
    except OSError:
        size, ts = None, ""
    return [{"id": moteur, "engine": moteur, "ts": ts, "size": size}]


def _format_ts(epoch: float) -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def get_active_id(theme_dir: Path, stem: str) -> str | None:
    """Moteur qui a produit la conversion du document, ou None s'il n'y en a pas.

    Le nom a survecu au modele qu'il decrivait : il n'y a plus de conversion
    « active » a designer parmi plusieurs, il y en a une ou il n'y en a pas.
    L'interface lit cette cle pour savoir si un document est converti, et pour
    afficher de quel moteur il vient : la valeur reste donc le nom du moteur,
    et non un booleen.
    """
    if not conversion_exists(theme_dir, stem):
        return None
    return metadata.read_by_stem(theme_dir, stem).get("active_engine") or "inconnu"


def get_active_path(theme_dir: Path, stem: str) -> Path | None:
    """Chemin du `.md` d'un document, ou None s'il n'est pas converti."""
    p = conversion_path(theme_dir, stem)
    return p if p.is_file() else None


def list_markdowns(theme_dir: Path) -> list[str]:
    """Les stems des Markdown reellement poses dans `2-Conversions/`.

    **Part du fichier produit, et non de la source dont il vient.** Le reste
    de l'application apparie les deux par le stem : un `.md` dont la source a
    ete renommee, retiree de `1-Sources/` ou marquee « a ne pas traiter »
    devenait alors invisible partout — telechargement compris — alors que le
    fichier etait la, sur le disque, deja converti et deja relisible.

    Le prefixe `_` reste exclu : `_metadata/` et `_anciens-moteurs/` ne sont
    pas des documents, et `list_conversions` les ignore deja pour la meme
    raison. Les fichiers annexes non plus (`<stem>.rapport.md`), qui decrivent
    une fabrication et n'ont pas de sens hors d'elle.
    """
    root = conversions_root(theme_dir)
    if not root.is_dir():
        return []
    stems: list[str] = []
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file() or p.suffix.lower() != ".md":
            continue
        if p.name.startswith("_") or p.name.endswith(".rapport.md"):
            continue
        stems.append(p.stem)
    return stems


def source_for_stem(theme_dir: Path, stem: str) -> Path | None:
    """Le document d'origine d'une conversion, s'il est encore trouvable.

    Deux endroits, dans cet ordre : la copie posee a cote du `.md` par
    `poser_la_source`, puis `1-Sources/`. La premiere suffit presque toujours
    et ne depend pas de l'etat de `1-Sources/` ; la seconde rattrape les
    conversions faites avant que la copie n'existe.

    Rend None quand la source a disparu. **C'est un cas normal, pas une
    erreur** : le Markdown se telecharge et se remplit sans elle.
    """
    md = conversion_path(theme_dir, stem)
    if md.is_file():
        for voisin in sorted(md.parent.glob(f"{glob_escape(stem)}.*")):
            if voisin.suffix.lower() != ".md" and voisin.is_file():
                return voisin
    sources_dir = theme_dir / SOURCES_DIR
    if sources_dir.is_dir():
        for src in sorted(sources_dir.glob(f"{glob_escape(stem)}.*")):
            if src.is_file() and not metadata.is_metadata_file(src):
                return src
    return None


def glob_escape(motif: str) -> str:
    """Neutralise les jokers d'un nom de fichier utilise dans un `glob`.

    Un document nomme `Fiche [2024].pdf` est courant ; `[2024]` est une
    classe de caracteres pour `glob`, qui ne le retrouverait jamais.
    """
    return re.sub(r"([\[\]*?])", r"[\1]", motif)


def enregistrer_le_moteur(theme_dir: Path, stem: str, engine: str) -> dict[str, Any]:
    """Note quel moteur vient de produire la conversion, et invalide sa relecture.

    `md_valid` retombe a false a chaque fois : le flag signifie « j'ai relu ce
    Markdown et il est fidele au document », et le fichier vient d'etre
    reecrit. Il ne peut pas survivre a sa propre reecriture.
    """
    engine = slug_engine(engine)
    if not conversion_exists(theme_dir, stem):
        return {"ok": False, "error": "Aucune conversion pour ce document."}
    previous = metadata.read_by_stem(theme_dir, stem).get("active_engine") or ""
    metadata.update_by_stem(theme_dir, stem,
                            {"active_engine": engine, "md_valid": False})
    return {"ok": True, "active": engine, "previous": previous,
            "md_valid_reset": True}


def delete_document(theme_dir: Path, stem: str) -> dict[str, Any]:
    """Supprime toutes les conversions d'un document + son sidecar.

    Utilise quand on retire le PDF source lui-meme : sans ca, le theme
    garderait des Markdown et un sidecar qui ne se rattachent plus a rien,
    et un PDF redepose sous le meme nom heriterait de l'etat de l'ancien.
    Ne touche pas a `_archive/`, qui n'est jamais lu par l'application.

    Retourne la liste des chemins supprimes, relatifs au theme.
    """
    removed: list[str] = []
    # La conversion, puis les anciennes : un document redepose sous le meme nom
    # ne doit rien heriter, ni a la racine ni dans l'archive.
    chemins = [conversion_path(theme_dir, stem)]
    chemins += [ancienne_conversion_path(theme_dir, stem, e)
                for e in list_engines(theme_dir)]
    for p in chemins:
        if not p.is_file():
            continue
        try:
            p.unlink()
        except OSError:
            continue
        removed.append(str(p.relative_to(theme_dir)).replace("\\", "/"))
        for annexe in _supprimer_annexes(p):
            removed.append(str(annexe.relative_to(theme_dir)).replace("\\", "/"))
        _prune_empty_engine_dir(p.parent)
    sidecar = metadata.sidecar_dir(theme_dir) / f"{stem}{metadata.METADATA_SUFFIX}"
    if sidecar.is_file():
        try:
            sidecar.unlink()
            removed.append(str(sidecar.relative_to(theme_dir)).replace("\\", "/"))
        except OSError:
            pass
    return {"ok": True, "removed": removed}


def _prune_empty_engine_dir(d: Path) -> None:
    """Retire un dossier d'ancien moteur quand il ne contient plus de MD."""
    if not d.is_dir() or d.name == CONVERSIONS_DIR:
        return
    if any(p.suffix.lower() == ".md" for p in d.iterdir() if p.is_file()):
        return
    try:
        d.rmdir()
    except OSError:
        pass  # dossier non vide (autre contenu) : on le laisse
