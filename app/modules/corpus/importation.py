"""Import des Markdown d'un site dans un corpus.

Deux temps, comme pour un lot de PDF :

  recuperer()  telecharge le depot, ecrit ses `.md` dans `1-Sources/` et la
               fiche `_depot.json`. Rien n'est encore un document du corpus.
  importer()   copie les fichiers retenus vers `2-Conversions/`,
               ecrit leur sidecar et pose leur front matter.

Le second temps est l'equivalent exact de la conversion d'un PDF, moteur
compris : `11ty` et `mkdocs` sont des moteurs comme les cinq autres, dont la
conversion se trouve etre une copie. Tout ce qui vient apres — validation,
viewer, export — ne voit donc aucune difference.

`1-Sources/` reste un input pur : la copie de travail est celle qu'on corrige
dans le viewer, l'original garde ce que le depot a livre.
"""
from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

import httpx

from . import depot, documents, frontmatter, metadata, nature, site

logger = logging.getLogger("atelier.corpus.importation")

FICHE_DEPOT = "_depot.json"

# Cles du front matter d'un site qui ne sont pas des metadonnees du document :
# elles pilotent le rendu du site et n'ont pas de sens hors de lui.
CLES_DE_RENDU = frozenset({
    "layout", "permalink", "eleventyNavigation", "eleventyExcludeFromCollections",
    "templateEngineOverride", "draft", "hide", "search", "css", "js",
})


_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


def lire_sitemap(site_url: str) -> set[str]:
    """Chemins publies par le site, d'apres son `sitemap.xml`.

    Un generateur statique publie moins de pages qu'il n'a de fichiers : sur
    docs.forge.apps.education.fr, 34 pages pour 72 Markdown. Le sitemap le dit
    de premiere main, en une requete, la ou tester chaque adresse en demande
    autant qu'il y a de pages et ne renseigne que sur celles qu'on a devinees.

    Rend un ensemble vide si le site n'en publie pas : l'appelant retombe alors
    sur la verification une a une.
    """
    if not site_url:
        return set()
    base = site_url.rstrip("/")
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True,
                          headers={"User-Agent": depot.USER_AGENT}) as client:
            reponse = client.get(base + "/sitemap.xml")
    except httpx.HTTPError as exc:
        logger.info("Site : sitemap.xml injoignable (%s).", exc)
        return set()
    if reponse.status_code != 200 or "<loc" not in reponse.text.lower():
        logger.info("Site : pas de sitemap.xml exploitable.")
        return set()

    chemins = set()
    for loc in _LOC_RE.findall(reponse.text):
        chemin = urlsplit(loc).path or "/"
        if not chemin.endswith("/") and "." not in chemin.rsplit("/", 1)[-1]:
            chemin += "/"
        chemins.add(chemin)
    logger.info("Site : sitemap.xml lu, %d page(s) publiee(s).", len(chemins))
    return chemins


_ID_RE = re.compile(r"""\bid=["']([A-Za-z0-9_\-]{3,90})["']""")


def ancres_de_la_page(client: httpx.Client, url: str) -> set[str]:
    """Ancres reellement posees par une page publiee.

    Toutes les pages n'en portent pas. Sur docs.forge.apps.education.fr, les
    tutoriels donnent un `id` a chaque titre de section, mais la FAQ est faite
    d'accordeons DSFR dont les titres n'en ont aucun : pointer
    `/faq/#comment-nommer-un-groupe` designerait une ancre qui n'existe pas.
    Le navigateur ne s'en plaint pas, il ouvre simplement le haut de la page —
    mais l'adresse ment sur ce qu'elle promet, et rien ne le signale.
    """
    try:
        reponse = client.get(url)
    except httpx.HTTPError:
        return set()
    if reponse.status_code != 200:
        return set()
    return set(_ID_RE.findall(reponse.text))


def _resoudre_par_sitemap(pages: list[site.Page], site_url: str,
                          publies: set[str]) -> int:
    """Rattache chaque page a l'URL publiee qui la porte reellement.

    Quand le chemin naturel d'un fichier est publie, c'est une page a lui
    seul. Sinon, son contenu est une SECTION de l'ancetre publie le plus
    proche, et l'ancre est le slug de son titre. Faute d'ancetre publie, la
    page n'est pas en ligne et n'aura pas d'adresse : mieux vaut aucun lien
    qu'un lien mort.
    """
    # Les chemins du sitemap sont absolus et portent deja le sous-chemin du
    # site : `https://exemple.github.io/projet/` publie `/projet/page/`. Il
    # faut donc les recoller a l'ORIGINE (schema + hote), jamais a `site_url`,
    # sinon le sous-chemin apparait deux fois.
    partie = urlsplit(site_url)
    origine = f"{partie.scheme}://{partie.netloc}"
    resolues = 0
    # Ancres d'une page publiee, chargees une seule fois et partagees : une
    # page parente porte souvent des dizaines de sections.
    ancres_connues: dict[str, set[str]] = {}

    with httpx.Client(timeout=20.0, follow_redirects=True,
                      headers={"User-Agent": depot.USER_AGENT}) as client:
        for page in pages:
            naturel = ""
            for candidat in page.urls_possibles:
                if "#" not in candidat:
                    naturel = urlsplit(candidat).path
                    break
            if not naturel:
                page.url_publique, page.url_verifiee = "", False
                continue

            if naturel in publies:
                page.url_publique = origine + naturel
                page.url_verifiee = True
                resolues += 1
                continue

            # On remonte : /a/b/c/ puis /a/b/ puis /a/ puis /
            segments = [s for s in naturel.strip("/").split("/") if s]
            parent = ""
            while segments:
                segments.pop()
                candidat_parent = "/" + "/".join(segments) + "/" if segments else "/"
                if candidat_parent in publies:
                    parent = candidat_parent
                    break
            if not parent:
                page.url_publique, page.url_verifiee = "", False
                continue

            ancre = site.ancre_de_titre(page.titre)
            if ancre:
                if parent not in ancres_connues:
                    ancres_connues[parent] = ancres_de_la_page(client, origine + parent)
                if ancre not in ancres_connues[parent]:
                    ancre = ""
            page.url_publique = origine + parent + (f"#{ancre}" if ancre else "")
            page.url_verifiee = True
            resolues += 1
    return resolues


def resoudre_urls(pages: list[site.Page], site_url: str) -> int:
    """Donne a chaque page son adresse publique reelle.

    Le sitemap d'abord, parce qu'il fait autorite et ne coute qu'une requete ;
    la verification une a une ensuite, pour les sites qui n'en publient pas.
    """
    publies = lire_sitemap(site_url)
    if publies:
        return _resoudre_par_sitemap(pages, site_url, publies)
    return verifier_urls(pages)


def verifier_urls(pages: list[site.Page]) -> int:
    """Retient, pour chaque page, la premiere adresse qui repond vraiment.

    Le site est la seule autorite sur ses propres permaliens. Un HEAD par
    candidat coute quelques secondes une fois, la ou une adresse fausse
    accompagne chaque reponse du RAG pendant toute la vie du corpus.

    Aucune adresse valable : `source_url` reste vide. Mieux vaut pas de lien
    qu'un lien mort — un lecteur qui clique sur une reference et tombe sur une
    page d'authentification cesse de faire confiance aux suivantes.
    """
    candidates = [p for p in pages if p.urls_possibles]
    if not candidates:
        return 0

    verifiees = 0
    vus: dict[str, bool] = {}
    joignable = False
    with httpx.Client(timeout=15.0, follow_redirects=False,
                      headers={"User-Agent": depot.USER_AGENT}) as client:
        for page in candidates:
            page.url_verifiee = False
            retenue = ""
            for candidat in page.urls_possibles:
                base = candidat.split("#")[0]
                if base not in vus:
                    try:
                        vus[base] = client.head(base).status_code == 200
                        joignable = True
                    except httpx.HTTPError:
                        vus[base] = False
                if vus[base]:
                    retenue = candidat
                    break
            page.url_publique = retenue
            page.url_verifiee = bool(retenue)
            verifiees += 1 if retenue else 0

    # Un site injoignable — coupure, DNS, site pas encore deploye — repondrait
    # non a tout et viderait chaque adresse. Mieux vaut alors garder l'adresse
    # la plus probable, non verifiee, que rendre un corpus sans aucun lien.
    if not joignable or (verifiees == 0 and len(candidates) > 3):
        logger.warning("Site : aucune adresse n'a repondu (%d page(s)). Les "
                       "adresses calculees sont conservees, non verifiees.",
                       len(candidates))
        for page in candidates:
            page.url_publique = page.urls_possibles[0]
            page.url_verifiee = False
        return 0

    return verifiees


def fiche_path(theme_dir: Path) -> Path:
    return theme_dir / documents.SOURCES_DIR / FICHE_DEPOT


def lire_fiche(theme_dir: Path) -> dict[str, Any]:
    """Ce qu'on sait du depot deja recupere, {} si aucun."""
    p = fiche_path(theme_dir)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Fiche de depot illisible (%s) : %s", theme_dir.name, exc)
        return {}
    return data if isinstance(data, dict) else {}


def recuperer(theme_dir: Path, url: str, ref: str | None = None,
              site_url: str = "",
              progress_callback: Any = None) -> dict[str, Any]:
    """Telecharge le depot et depose ses Markdown dans `1-Sources/`.

    Rend la fiche du depot et le plan du site : la liste des pages, leur titre,
    leur URL publique et celles qui sont proposees a l'import.
    """
    generateur = nature.source_of(theme_dir)
    if generateur not in nature.SOURCES_DEPOT:
        return {"ok": False, "error": "Ce corpus ne part pas d'un depot."}

    if progress_callback:
        progress_callback(processed=0, total=3, current="telechargement")

    try:
        archive = depot.recuperer(url, ref)
    except depot.DepotError as exc:
        return {"ok": False, "error": str(exc)}

    if progress_callback:
        progress_callback(processed=1, total=3, current="lecture de la config")

    plan = site.lire(archive, generateur, site_url)

    # `plan.site_url` et non le parametre : MkDocs declare souvent son
    # `site_url` dans sa config, et le champ de l'etape 1 reste alors vide.
    if plan.site_url:
        if progress_callback:
            progress_callback(processed=2, total=4, current="verification des adresses")
        n = resoudre_urls(plan.pages, plan.site_url)
        logger.info("Site : %d adresse(s) publique(s) verifiee(s) sur %d page(s).",
                    n, len(plan.pages))

    if progress_callback:
        progress_callback(processed=2, total=3, current="ecriture des sources")

    sources_dir = theme_dir / documents.SOURCES_DIR
    sources_dir.mkdir(parents=True, exist_ok=True)

    par_chemin = {f.chemin: f for f in archive.fichiers}
    pris: set[str] = set()
    pages: list[dict[str, Any]] = []

    for page in plan.pages:
        fichier = par_chemin.get(page.chemin)
        if fichier is None:
            continue
        stem = depot.stem_depuis_chemin(page.chemin, pris)
        pris.add(stem)
        # Ecrit tel quel : `1-Sources/` garde ce que le depot a livre, front
        # matter du site compris. La copie de travail viendra ensuite.
        (sources_dir / f"{stem}.md").write_text(fichier.texte, encoding="utf-8")
        pages.append({
            "stem": stem,
            "chemin": page.chemin,
            "titre": page.titre,
            "url_publique": page.url_publique,
            "url_verifiee": page.url_verifiee,
            "url_depot": archive.url_fichier(page.chemin),
            "dans_nav": page.dans_nav,
            "ordre": page.ordre,
            "octets": page.octets,
            "propose": page.propose,
            "raison": page.raison,
        })

    fiche = {
        "url": archive.url_web,
        "forge": archive.forge,
        "ref": archive.ref,
        "generateur": generateur,
        "site_url": plan.site_url,
        "config_lue": plan.config_lue,
        "dossier_contenu": plan.dossier_contenu,
        "resume": plan.resume(),
        "recupere_le": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pages": pages,
    }
    fiche_path(theme_dir).write_text(
        json.dumps(fiche, ensure_ascii=False, indent=2), encoding="utf-8")

    if progress_callback:
        progress_callback(processed=3, total=3, current=None)

    logger.info("Depot %s@%s recupere dans %s : %d page(s), %d proposee(s).",
                archive.url_web, archive.ref, theme_dir.name, len(pages),
                sum(1 for p in pages if p["propose"]))
    return {"ok": True, "fiche": fiche}


def _champs_du_site(texte: str) -> dict[str, Any]:
    """Metadonnees exploitables du front matter d'origine.

    Un fichier 11ty porte deja `title`, `tags`, `date` : autant de cases que
    le remplissage n'aura pas a deviner. Les cles de rendu sont ecartees —
    `layout` ou `permalink` decrivent le site, pas le document.
    """
    fm, _ = frontmatter.split_md(texte)
    champs: dict[str, Any] = {}
    for cle, valeur in fm.items():
        nom = str(cle).strip()
        if not nom or nom.startswith("_") or nom in CLES_DE_RENDU:
            continue
        if valeur in (None, "", []):
            continue
        champs[nom] = valeur
    # `title` est le nom courant sur un site, `titre` celui du schema META-MD.
    if "title" in champs and "titre" not in champs:
        champs["titre"] = champs.pop("title")
    return champs


def importer(theme_dir: Path, stems: list[str] | None = None,
             overwrite: bool = False, forcer_sans_texte: bool = False,
             progress_callback: Any = None) -> dict[str, Any]:
    """Copie les pages retenues vers `2-Conversions/`.

    Sans `stems`, toutes les pages proposees par la lecture du site.

    Une page sans texte indexable est ecartee meme si elle a ete cochee : la
    decocher par defaut ne suffisait pas, un clic sur « tout selectionner »
    filtre desactive les ramenait toutes. Un corpus documentaire se cherche par
    son texte, et un document vide y entre comme un extrait vide — pire
    qu'absent. `forcer_sans_texte` leve la garde pour qui sait ce qu'il fait.
    """
    fiche = lire_fiche(theme_dir)
    if not fiche:
        return {"ok": False, "error": "Aucun depot recupere pour ce corpus."}

    generateur = nature.source_of(theme_dir)
    pages = fiche.get("pages") or []
    if stems is None:
        retenues = [p for p in pages if p.get("propose")]
    else:
        voulus = set(stems)
        retenues = [p for p in pages if p.get("stem") in voulus]
    if not retenues:
        return {"ok": False, "error": "Aucune page sélectionnée."}

    sources_dir = theme_dir / documents.SOURCES_DIR
    # La copie de travail va a la racine de `2-Conversions/`, comme une
    # conversion. Le generateur (`11ty`, `mkdocs`) n'est plus un dossier : il
    # reste le moteur du document, note dans son sidecar.
    dest_dir = documents.conversions_root(theme_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    importees = ignorees = 0
    manquantes: list[str] = []
    sans_texte: list[str] = []
    total = len(retenues)

    for rang, page in enumerate(retenues, start=1):
        stem = page["stem"]
        if progress_callback:
            progress_callback(processed=rang - 1, total=total, current=stem)

        source = sources_dir / f"{stem}.md"
        if not source.is_file():
            manquantes.append(stem)
            continue

        texte = source.read_text(encoding="utf-8")
        champs_bruts, _ = frontmatter.split_md(texte)
        raison = site.raison_de_ne_pas_proposer(texte, champs_bruts)
        if raison and not forcer_sans_texte:
            sans_texte.append(stem)
            continue

        cible = dest_dir / f"{stem}.md"
        # Meme regle que la reconversion : on ne remplace pas une copie de
        # travail sans le dire, elle porte peut-etre des corrections.
        if cible.exists() and not overwrite:
            ignorees += 1
            continue

        _, corps = frontmatter.split_md(texte)
        cible.write_text(corps.lstrip("\n"), encoding="utf-8")

        sidecar = metadata.read_metadata(source)
        champs_site = _champs_du_site(texte)
        if not champs_site.get("titre") and page.get("titre"):
            champs_site["titre"] = page["titre"]
        fields = dict(sidecar.get("fields") or {})
        for cle, valeur in champs_site.items():
            # Un champ deja rempli ne se re-remplit jamais : la valeur du site
            # ne reprend pas la main sur une correction faite a la main.
            if cle not in fields:
                fields[cle] = {"value": valeur, "source": metadata.SOURCE_LOT}

        metadata.update_metadata(source, {
            "fields": fields,
            "active_engine": generateur,
            "source_url": page.get("url_publique") or "",
            "depot": {
                "url": page.get("url_depot") or "",
                "chemin": page.get("chemin") or "",
                "ref": fiche.get("ref") or "",
            },
            "md_valid": False,
            "metadata_valid": False,
        })
        frontmatter.sync_front_matter(theme_dir, source)
        importees += 1

    if progress_callback:
        progress_callback(processed=total, total=total, current=None)

    logger.info("Import %s : %d importee(s), %d inchangee(s), %d sans texte, "
                "%d manquante(s).", theme_dir.name, importees, ignorees,
                len(sans_texte), len(manquantes))
    return {"ok": True, "importees": importees, "ignorees": ignorees,
            "sans_texte": sans_texte, "manquantes": manquantes,
            "moteur": generateur}
