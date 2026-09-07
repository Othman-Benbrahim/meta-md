"""Chef d'orchestre de la conversion : le moteur, puis quoi apres.

Un seul point d'entree public, `convert_one_pdf`, appele par le serveur pour
un document a la fois. Il lance le moteur, puis applique les traitements qui
ne lui appartiennent pas : emphases, maths, aeration, front matter technique.

Un seul moteur depuis le 2026-08-23 : `albert_vision_figures`. `pymupdf`
(extraction hors ligne), `albert_vision` (transcription sans les figures),
`mistral_document_ai` et `albert_ocr` ont ete retires. Ce qui ne servait
qu'a eux est parti avec eux : la cle Mistral, le sidecar
`<stem>.mistral.json` et ses `blocs`, et la lecture des champs du schema
pendant la conversion.

Les corpus qui entrent par un depot (`11ty`, `mkdocs`) ne passent pas par
ici : ils n'ont rien a convertir, leurs `.md` sont deja ecrits. Voir
`corpus.importation`.

Le fichier produit ne porte qu'un front matter technique ; les champs metier
y sont ajoutes ensuite par `corpus.frontmatter.sync_front_matter`.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pymupdf  # type: ignore[import-untyped]

from .moteurs.albert_vision_figures import NOM_MOTEUR_VISION_FIGURES
from .moteurs.albert_vision_figures.transcription import _convert_vision
from .traitements import maths
from .traitements import polices
from .traitements import rapport as rapport_conversion
from .traitements.aeration import aerer_tableaux
from .traitements.texte import MARQUE_PAGE


logger = logging.getLogger("atelier.pipeline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# `MARQUE_PAGE` est importe ci-dessus sans etre utilise ici : c'est voulu. Le
# serveur le lit sur `pipeline`, seul module de conversion qu'il connaisse.

# Le serveur valide le moteur recu contre cet ensemble avant de lancer une
# conversion. Un moteur inconnu — ou retire — tomberait sinon en silence sur
# autre chose, tout en creant un dossier a son nom dans `2-Conversions/`.
SUPPORTED_ENGINES = (NOM_MOTEUR_VISION_FIGURES,)


def _build_front_matter(fields: dict[str, Any]) -> str:
    """Serialise un dict en front matter YAML delimite par `---`.

    Utilise pyyaml pour un rendu propre et robuste (guillemets, echappement).
    Retourne une chaine se terminant par une ligne vide.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        # Fallback minimaliste sans dependance : ecrit "cle: valeur" naif.
        lines = []
        for k, v in fields.items():
            if v is None:
                continue
            lines.append(f"{k}: {v}")
        body = "\n".join(lines)
        return f"---\n{body}\n---\n\n"
    # Retire les cles a valeur None pour un front matter propre.
    clean = {k: v for k, v in fields.items() if v is not None and v != ""}
    body = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False,
                          default_flow_style=False).strip()
    return f"---\n{body}\n---\n\n"


def poser_les_maths(corps: str) -> str:
    """Ecrit en LaTeX les maths restees en texte.

    C'est ici, et non dans le moteur, parce que le traitement ne depend pas de
    lui : le prompt reclame deja les formules au modele, et il en laisse
    passer. Le jour ou un second moteur revient, il en beneficie sans rien
    avoir a reprendre.
    """
    n, nouveau = maths.poser_latex(corps)
    if n:
        logger.info("%d fragment(s) mathematique(s) ecrit(s) en LaTeX.", n)
    return nouveau


def convert_one_pdf(pdf: Path, dest_md: Path, engine: str,
                    albert_key: str | None = None,
                    progress_callback: Any = None) -> dict[str, Any]:
    """Convertit un unique PDF vers un chemin cible precis.

    N'ecrit qu'un front matter technique minimal ; les champs metier y sont
    ajoutes ensuite par `corpus.frontmatter.sync_front_matter`.

    `engine` reste un parametre bien qu'un seul moteur soit accepte : c'est
    le nom du dossier ecrit dans `2-Conversions/`, il part dans le front
    matter (`_engine`), et il garde la porte ouverte a un second moteur sans
    changer la signature.

    Retourne {ok, bytes?, duration_s?, pages?, error?}.
    """
    if engine not in SUPPORTED_ENGINES:
        return {"ok": False, "error": f"Moteur inconnu : {engine}"}
    if not albert_key:
        return {"ok": False, "error": "Clé Albert requise pour ce moteur"}

    dest_md.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    journal: list[dict[str, Any]] = []
    try:
        md = _convert_vision(pdf, api_key=albert_key, journal=journal,
                             progress_callback=progress_callback)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Conversion KO : %s", pdf.name)
        return {"ok": False, "error": str(exc)}

    # La pose des maths passe avant l'aeration : elle lit encore un tableau
    # tenant sur une ligne, ou une cellule est un `<td>…</td>` que rien ne
    # coupe.
    #
    # Le recollage des emphases passe avant elle, et surtout apres la
    # reparation des tableaux : une cellule recollee de deux lignes de
    # continuation remet bout a bout deux marques que le PDF ne separait pas.
    md = polices.recoller_emphases(md)
    md = poser_les_maths(md)

    # Derniere etape avant l'ecriture : plus aucune reparation ne relit le
    # Markdown, la convention « un tableau, une ligne » a fini de servir.
    md = aerer_tableaux(md)

    pdf_meta = _pdf_metadata(pdf)
    fm_fields = {
        "_source": pdf.name,
        "_engine": engine,
        "_extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "_pages": pdf_meta.get("pages"),
    }
    dest_md.write_text(_build_front_matter(fm_fields) + md, encoding="utf-8")

    # Le rapport se pose a cote de la conversion et du document dont elle
    # vient : les trois se lisent ensemble, et un dossier de conversion se
    # consulte sans aller chercher ailleurs ce qui s'est passe. Une conversion
    # qui echoue n'en laisse pas, il n'y aurait rien a raconter.
    duree = time.monotonic() - started
    texte = rapport_conversion.rendre(journal, source=pdf.name, moteur=engine,
                                      duree=duree)
    chemin_rapport = None
    if texte:
        chemin_rapport = dest_md.with_suffix(".rapport.md")
        try:
            chemin_rapport.parent.mkdir(parents=True, exist_ok=True)
            chemin_rapport.write_text(texte, encoding="utf-8")
        except OSError as exc:
            logger.warning("Rapport de conversion non ecrit : %s", exc)
            chemin_rapport = None
        a_relire = [p["page"] for p in journal
                    if rapport_conversion._souci(p)]
        if a_relire:
            logger.warning("Conversion %s : %d page(s) a relire : %s",
                           pdf.name, len(a_relire),
                           ", ".join(str(n) for n in a_relire))

    return {
        "ok": True,
        "bytes": dest_md.stat().st_size,
        "duration_s": round(time.monotonic() - started, 2),
        "pages": pdf_meta.get("pages"),
        "rapport": chemin_rapport.name if chemin_rapport else None,
        "a_relire": [p["page"] for p in journal
                     if rapport_conversion._souci(p)],
    }


# En dessous, une page est consideree comme sans couche texte utile : un
# numero de page et un pied de page suffisent a atteindre quelques dizaines
# de caracteres sur une page pourtant scannee.
TEXTE_MINIMAL_PAR_PAGE = 40
# Une figure porteuse d'information occupe une part notable de la page. En
# dessous, c'est un logo, un filet, une puce.
PART_PAGE_FIGURE = 0.15


def diagnostic_pdf(pdf: Path) -> dict[str, Any]:
    """Ce que le PDF dit de lui-meme, avant de depenser un appel.

    Tant que plusieurs moteurs coexistaient, ces deux mesures servaient a en
    conseiller un. Il n'en reste qu'un : elles disent maintenant a quoi
    s'attendre, ce qui reste utile — un document deja pourvu d'une couche
    texte pourra etre relu contre son PDF, un scan depend entierement du
    modele, et les pages a figure sont celles ou le decoupage aura a faire.

    Retourne {pages, pages_texte, figures, moteur, raison}. `moteur` garde sa
    place dans la reponse, que l'interface lit deja ; il vaut desormais
    toujours le seul moteur disponible. En cas de PDF illisible, `pages` vaut
    0 et rien n'est renseigne : ce n'est pas a ce diagnostic de bloquer quoi
    que ce soit.
    """
    infos: dict[str, Any] = {"pages": 0, "pages_texte": 0, "figures": 0,
                             "moteur": None, "raison": ""}
    try:
        with pymupdf.open(str(pdf)) as doc:
            infos["pages"] = len(doc)
            for page in doc:
                if len(page.get_text().strip()) >= TEXTE_MINIMAL_PAR_PAGE:
                    infos["pages_texte"] += 1
                aire = abs(page.rect) or 1
                for image in page.get_image_info():
                    if abs(pymupdf.Rect(image["bbox"])) > PART_PAGE_FIGURE * aire:
                        infos["figures"] += 1
                        break  # une page a figure compte une fois
    except Exception as exc:  # noqa: BLE001
        logger.debug("Diagnostic PDF impossible pour %s : %s", pdf.name, exc)
        return infos

    # A partir d'ici, le texte est affiche a l'utilisateur : il s'ecrit en
    # francais accentue, contrairement aux commentaires de ce fichier.
    pages, avec_texte = infos["pages"], infos["pages_texte"]
    figures = infos["figures"]
    if not pages:
        return infos
    infos["moteur"] = NOM_MOTEUR_VISION_FIGURES

    if avec_texte == 0:
        base = ("aucune page ne porte de texte : le document est scanné, la "
                "transcription dépend entièrement du modèle")
    elif avec_texte < pages:
        manquantes = pages - avec_texte
        base = (f"{manquantes} page{'s' if manquantes > 1 else ''} sur {pages} "
                "sans couche texte : le PDF ne pourra pas servir de recours "
                "pour les relire")
    else:
        base = (f"couche texte sur {avec_texte} page"
                f"{'s' if avec_texte > 1 else ''} sur {pages} : le PDF pourra "
                "corriger ce que le modèle déforme")
    if figures:
        base += (f". {figures} page{'s portent' if figures > 1 else ' porte'} "
                 "une figure, qui sera découpée et décrite")
    infos["raison"] = base
    return infos


def _pdf_metadata(pdf: Path) -> dict[str, Any]:
    """Extrait titre et nombre de pages d'un PDF via pymupdf.

    Le titre est celui declare dans les metadonnees du PDF s'il existe,
    sinon le nom de fichier sans extension.
    """
    meta: dict[str, Any] = {"title": pdf.stem, "pages": None}
    try:
        with pymupdf.open(str(pdf)) as doc:
            meta["pages"] = len(doc)
            pdf_meta = doc.metadata or {}
            title = (pdf_meta.get("title") or "").strip()
            if title:
                meta["title"] = title
            author = (pdf_meta.get("author") or "").strip()
            if author:
                meta["author"] = author
    except Exception as exc:  # noqa: BLE001
        logger.debug("Metadonnees PDF illisibles pour %s : %s", pdf.name, exc)
    return meta
