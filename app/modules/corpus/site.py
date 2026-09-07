"""Ce qu'un depot dit de son propre site : quelles pages, dans quel ordre.

Un depot contient plus de Markdown que le site n'en publie — un README, des
modeles de tickets, parfois des notes. La config du site tranche : `mkdocs.yml`
declare son dossier de contenu et sa navigation, une config 11ty declare son
dossier d'entree.

La lecture reste un conseil, jamais une contrainte. Quand la config manque ou
resiste, tous les `.md` sont proposes et l'utilisateur decoche : c'est la regle
« config si trouvee, sinon tout ». La liste est modifiable dans les deux cas,
donc une lecture imparfaite ne bloque personne.
"""
from __future__ import annotations

import logging
import posixpath
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import yaml

from . import depot, frontmatter

logger = logging.getLogger("atelier.corpus.site")

CONFIGS_MKDOCS = ("mkdocs.yml", "mkdocs.yaml")
CONFIGS_11TY = (".eleventy.js", "eleventy.config.js", "eleventy.config.mjs",
                ".eleventy.cjs", "eleventy.config.cjs")
# Dossiers d'entree usuels d'un projet 11ty, quand la config ne se laisse pas lire.
ENTREES_11TY_USUELLES = ("src", "content", "site", "posts")

# Appels de gabarit Nunjucks ou Liquid. Ce sont des directives de rendu, pas du
# texte : `{% image "x.png", "…" %}` devient une balise <img> sur le site et ne
# dit rien a qui cherche dans le corpus.
SHORTCODE_RE = re.compile(r"{%.*?%}|{{.*?}}", re.S)


class _YamlTolerant(yaml.SafeLoader):
    """Lecteur YAML qui ne bute pas sur les tags d'un mkdocs.yml.

    Une config mkdocs-material porte couramment `!!python/name:...` ou `!ENV` :
    `safe_load` s'y arrete net. Ces valeurs ne nous interessent pas — on ne lit
    que `docs_dir`, `nav` et `site_url` — donc un tag inconnu devient None
    plutot qu'une erreur qui priverait de toute la navigation.
    """


def _tag_ignore(loader: yaml.Loader, suffix: str, node: yaml.Node) -> None:  # noqa: ARG001
    return None


_YamlTolerant.add_multi_constructor("!", _tag_ignore)
_YamlTolerant.add_multi_constructor("tag:yaml.org,2002:python/", _tag_ignore)


@dataclass
class Page:
    """Un Markdown du depot, vu comme une page candidate a l'import."""

    chemin: str                 # chemin dans le depot
    titre: str = ""
    url_publique: str = ""
    dans_nav: bool = False
    ordre: int = 0
    octets: int = 0
    urls_possibles: list[str] = field(default_factory=list)
    url_verifiee: bool = False  # l'adresse a repondu 200 sur le site reel
    propose: bool = True        # coche par defaut dans la liste
    raison: str = ""            # pourquoi elle ne l'est pas, le cas echeant
    champs: dict[str, Any] = field(default_factory=dict)   # front matter du site


@dataclass
class PlanDuSite:
    """Lecture d'un depot : ce qu'on propose d'importer, et sur quelle base."""

    generateur: str             # '11ty' | 'mkdocs'
    config_lue: str = ""        # nom du fichier de config exploite, '' sinon
    dossier_contenu: str = ""
    site_url: str = ""
    pages: list[Page] = field(default_factory=list)

    @property
    def proposees(self) -> list[Page]:
        return [p for p in self.pages if p.propose]

    @property
    def sans_texte(self) -> list[Page]:
        """Pages ecartees faute de texte indexable."""
        return [p for p in self.pages
                if p.raison and not p.raison.startswith("fichier de depot")]

    def resume(self) -> str:
        """Une phrase pour l'entete de la page d'import."""
        if self.config_lue and any(p.dans_nav for p in self.pages):
            dans = sum(1 for p in self.pages if p.dans_nav)
            hors = len(self.pages) - dans
            return (f"{self.config_lue} lu — {dans} page(s) dans la navigation, "
                    f"{hors} fichier(s) hors navigation.")
        if self.config_lue:
            return (f"{self.config_lue} lu — dossier de contenu "
                    f"{self.dossier_contenu or '(racine)'}, "
                    f"{len(self.proposees)} page(s) proposee(s).")
        return (f"Aucune config de site exploitable : les {len(self.pages)} "
                "fichiers Markdown du depot sont proposes.")


def raison_de_ne_pas_proposer(texte: str, champs: dict[str, Any]) -> str:
    """Pourquoi ce fichier n'est pas une page, '' s'il en est une.

    Un corpus documentaire se cherche par son texte : un document sans texte y
    entre comme un extrait vide, ce qui est pire qu'absent. Trois cas se
    rencontrent dans un depot 11ty, et aucun n'est une anomalie du depot :

    - `permalink: false` dit noir sur blanc que 11ty ne publiera pas de page ;
      le fichier n'existe que pour declarer une entree de navigation ;
    - un corps vide, quand tout le contenu est dans le front matter et qu'un
      gabarit le met en page (les fiches de modeles, par exemple) ;
    - un corps qui n'est qu'un appel de gabarit.

    Ces fichiers restent listes et cochables : c'est un defaut de proposition,
    pas une exclusion.
    """
    if champs.get("permalink") is False:
        return "11ty ne publie pas cette page (permalink: false)"
    _, corps = frontmatter.split_md(texte)
    if not corps.strip():
        return "corps vide : tout le contenu est dans le front matter"
    if not SHORTCODE_RE.sub("", corps).strip():
        return "aucun texte : le corps n'est qu'un appel de gabarit"
    return ""


# ---------------------------------------------------------------------------
# MkDocs
# ---------------------------------------------------------------------------

def _charger_yaml(texte: str, quoi: str) -> dict[str, Any]:
    try:
        data = yaml.load(texte, Loader=_YamlTolerant)
    except yaml.YAMLError as exc:
        logger.warning("Site : %s illisible : %s", quoi, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _aplatir_nav(nav: Any, sorti: list[tuple[str, str]], titre_parent: str = "") -> None:
    """Parcourt la nav MkDocs et rend [(chemin, titre), ...] dans l'ordre.

    La nav est un arbre de listes et de dictionnaires a une cle. Le titre
    retenu est celui de la feuille ; les sections ne donnent leur nom qu'aux
    entrees qui n'en ont pas.
    """
    if isinstance(nav, str):
        sorti.append((nav, titre_parent))
        return
    if isinstance(nav, list):
        for element in nav:
            _aplatir_nav(element, sorti, titre_parent)
        return
    if isinstance(nav, dict):
        for titre, valeur in nav.items():
            if isinstance(valeur, str):
                sorti.append((valeur, str(titre)))
            else:
                _aplatir_nav(valeur, sorti, str(titre))


def _url_mkdocs(site_url: str, chemin_dans_docs: str) -> str:
    """URL publique d'une page MkDocs, en URL de repertoire (le defaut)."""
    if not site_url:
        return ""
    sans_ext = posixpath.splitext(chemin_dans_docs)[0]
    if posixpath.basename(sans_ext) == "index":
        sans_ext = posixpath.dirname(sans_ext)
    base = site_url.rstrip("/")
    return f"{base}/" if not sans_ext else f"{base}/{sans_ext}/"


def _lire_mkdocs(par_chemin: dict[str, str], octets: dict[str, int]) -> PlanDuSite | None:
    nom_config = next((c for c in CONFIGS_MKDOCS if c in par_chemin), "")
    if not nom_config:
        return None

    config = _charger_yaml(par_chemin[nom_config], nom_config)
    docs_dir = str(config.get("docs_dir") or "docs").strip("/") or "docs"
    site_url = str(config.get("site_url") or "").strip()

    ordre_nav: list[tuple[str, str]] = []
    _aplatir_nav(config.get("nav"), ordre_nav)
    titres = {}
    rangs = {}
    for rang, (chemin, titre) in enumerate(ordre_nav, start=1):
        cible = posixpath.normpath(posixpath.join(docs_dir, chemin))
        titres[cible] = titre
        rangs.setdefault(cible, rang)

    plan = PlanDuSite(generateur="mkdocs", config_lue=nom_config,
                      dossier_contenu=docs_dir, site_url=site_url)

    for chemin in sorted(par_chemin):
        dans_nav = chemin in rangs
        sous_docs = chemin.startswith(docs_dir + "/")
        # Sans nav declaree, tout le dossier de contenu fait le site.
        propose = dans_nav or (not rangs and sous_docs)
        if not (dans_nav or sous_docs or not rangs):
            propose = False
        champs, _ = frontmatter.split_md(par_chemin[chemin])
        raison = raison_de_ne_pas_proposer(par_chemin[chemin], champs)
        if not dans_nav and depot.hors_site(chemin):
            raison = raison or "fichier de depot, pas une page du site"
        # Memes candidats que pour 11ty : MkDocs non plus n'expose pas ses
        # permaliens, et un `.md` peut y devenir une page ou une ancre.
        candidats = (urls_candidates(site_url, chemin[len(docs_dir) + 1:], champs)
                     if sous_docs else [])
        plan.pages.append(Page(
            chemin=chemin,
            titre=titres.get(chemin, ""),
            url_publique=candidats[0] if candidats else "",
            urls_possibles=candidats,
            dans_nav=dans_nav,
            ordre=rangs.get(chemin, 0),
            octets=octets.get(chemin, 0),
            # Une page declaree dans la navigation du site EST une page du
            # site, quel que soit son nom : la nav fait autorite sur
            # l'heuristique du nom de fichier. Elle ne dit rien, en revanche,
            # de ce que la page contient : un corps vide reste un corps vide.
            propose=propose and not raison,
            raison=raison,
        ))

    plan.pages.sort(key=lambda p: (p.ordre == 0, p.ordre, p.chemin))
    return plan


def ancre_de_titre(titre: str) -> str:
    """Ancre HTML que markdown-it pose sous un titre.

    Meme regle que le rendu du site : accents retires, minuscules, tout ce qui
    n'est ni lettre ni chiffre devient un tiret. « Creer son projet » donne
    `creer-son-projet`, verifie contre les ancres du site.
    """
    plat = unicodedata.normalize("NFKD", (titre or "").lower())
    plat = "".join(c for c in plat if not unicodedata.combining(c))
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", plat)).strip("-")


def urls_candidates(site_url: str, relatif: str, champs: dict[str, Any]) -> list[str]:
    """Adresses possibles d'une page, de la plus probable a la moins.

    Un generateur statique n'expose pas ses regles de permalien : selon le
    gabarit, `dossier/page.md` devient une page a lui seul, ou une SECTION de
    la page du dossier. Les deux se rencontrent, parfois dans le meme depot, et
    aucune lecture du depot ne tranche a coup sur.

    On propose donc les deux formes, a charge pour l'appelant de retenir celle
    qui repond vraiment. Deviner une seule adresse est ce qui a produit 38
    liens morts sur 50 lors du premier import de docs.forge.apps.education.fr.
    """
    if not site_url:
        return []
    base = site_url.rstrip("/")

    permalink = str(champs.get("permalink") or "").strip()
    if permalink and permalink.lower() not in ("false", "none"):
        return [base + "/" + permalink.lstrip("/")]

    sans_ext = posixpath.splitext(relatif)[0]
    dossier = posixpath.dirname(sans_ext)
    nom = posixpath.basename(sans_ext)

    if nom == "index":
        return [f"{base}/{dossier}/".replace("//", "/").replace(":/", "://")
                if dossier else f"{base}/"]

    page_seule = f"{base}/{sans_ext}/"
    parent = f"{base}/{dossier}/" if dossier else f"{base}/"
    ancre = ancre_de_titre(str(champs.get("title") or champs.get("titre") or ""))
    section = f"{parent}#{ancre}" if ancre else ""

    # `.html` couvre `use_directory_urls: false` cote MkDocs. Proposer un
    # candidat de trop ne coute qu'une requete : c'est la verification qui
    # tranche, pas cette liste.
    page_html = f"{base}/{sans_ext}.html"
    return [u for u in (page_seule, page_html, section, parent) if u]


# ---------------------------------------------------------------------------
# 11ty
# ---------------------------------------------------------------------------

_DIR_INPUT_RE = re.compile(r"""input\s*:\s*["']([^"']+)["']""")


def _entree_11ty(par_chemin: dict[str, str]) -> tuple[str, str]:
    """(dossier d'entree, nom du fichier de config lu).

    La config 11ty est du JavaScript : l'executer serait deraisonnable, la
    parser entierement aussi. On y cherche seulement `input: "..."`, qui suffit
    dans l'immense majorite des projets. A defaut, un dossier usuel ; a defaut
    encore, la racine.
    """
    for nom in CONFIGS_11TY:
        if nom not in par_chemin:
            continue
        trouve = _DIR_INPUT_RE.search(par_chemin[nom])
        if trouve:
            return trouve.group(1).strip("./").strip("/"), nom
        return "", nom
    return "", ""


def _url_11ty(site_url: str, chemin_dans_entree: str, champs: dict[str, Any]) -> str:
    if not site_url:
        return ""
    base = site_url.rstrip("/")
    permalink = str(champs.get("permalink") or "").strip()
    if permalink:
        return base + "/" + permalink.lstrip("/")
    sans_ext = posixpath.splitext(chemin_dans_entree)[0]
    if posixpath.basename(sans_ext) == "index":
        sans_ext = posixpath.dirname(sans_ext)
    return f"{base}/" if not sans_ext else f"{base}/{sans_ext}/"


def _lire_11ty(par_chemin: dict[str, str], octets: dict[str, int],
               site_url: str = "") -> PlanDuSite:
    entree, nom_config = _entree_11ty(par_chemin)
    plan = PlanDuSite(generateur="11ty", config_lue=nom_config,
                      dossier_contenu=entree, site_url=site_url)

    candidats = [c for c in sorted(par_chemin)
                 if not entree or c.startswith(entree + "/")]
    # Un `input` qui ne recouvre rien est un `input` mal lu : on ne prive pas
    # l'utilisateur de son depot pour une regex.
    if entree and not candidats:
        logger.warning("Site 11ty : dossier d'entree %r vide, on reprend tout.", entree)
        entree, candidats = "", sorted(par_chemin)
        plan.dossier_contenu = ""
    elif not entree:
        # Sans `input` declare, un dossier usuel s'il existe.
        for usuel in ENTREES_11TY_USUELLES:
            sous = [c for c in sorted(par_chemin) if c.startswith(usuel + "/")]
            if sous:
                entree, candidats = usuel, sous
                plan.dossier_contenu = usuel
                break

    for chemin in candidats:
        relatif = chemin[len(entree) + 1:] if entree else chemin
        champs, _ = frontmatter.split_md(par_chemin[chemin])
        raison = raison_de_ne_pas_proposer(par_chemin[chemin], champs)
        if depot.hors_site(chemin):
            raison = raison or "fichier de depot, pas une page du site"
        candidats = urls_candidates(site_url, relatif, champs)
        plan.pages.append(Page(
            chemin=chemin,
            titre=str(champs.get("title") or champs.get("titre") or "").strip(),
            url_publique=candidats[0] if candidats else "",
            urls_possibles=candidats,
            octets=octets.get(chemin, 0),
            propose=not raison,
            raison=raison,
            champs=champs,
        ))
    return plan


# ---------------------------------------------------------------------------
# Entree publique
# ---------------------------------------------------------------------------

def _tout_le_depot(archive: depot.Depot, generateur: str,
                   site_url: str = "") -> PlanDuSite:
    plan = PlanDuSite(generateur=generateur, site_url=site_url)
    for f in archive.fichiers:
        champs, _ = frontmatter.split_md(f.texte)
        raison = raison_de_ne_pas_proposer(f.texte, champs)
        if depot.hors_site(f.chemin):
            raison = raison or "fichier de depot, pas une page du site"
        plan.pages.append(Page(chemin=f.chemin, octets=f.octets,
                               propose=not raison, raison=raison))
    return plan


def lire(archive: depot.Depot, generateur: str,
         site_url: str = "") -> PlanDuSite:
    """Plan d'import d'un depot, selon le generateur declare par le corpus."""
    par_chemin = {f.chemin: f.texte for f in archive.fichiers}
    octets = {f.chemin: f.octets for f in archive.fichiers}
    # La config n'est pas un `.md` : elle est dans les autres fichiers, qu'il
    # faut donc pouvoir relire. `recuperer` ne garde que leur nom, on repasse
    # donc par le depot pour celles qui nous interessent.
    for nom in (*CONFIGS_MKDOCS, *CONFIGS_11TY):
        if nom in archive.textes_annexes:
            par_chemin[nom] = archive.textes_annexes[nom]

    if generateur == "mkdocs":
        plan = _lire_mkdocs(par_chemin, octets)
        if plan is None:
            logger.info("Site : aucun mkdocs.yml, tous les .md sont proposes.")
            return _tout_le_depot(archive, generateur, site_url)
        if site_url and not plan.site_url:
            # `mkdocs.yml` n'a pas declare de `site_url` : celle saisie a
            # l'etape 1 prend le relais, et les candidats se recalculent.
            plan.site_url = site_url
            for page in plan.pages:
                if not page.chemin.startswith(plan.dossier_contenu + "/"):
                    continue
                champs, _ = frontmatter.split_md(par_chemin.get(page.chemin, ""))
                page.urls_possibles = urls_candidates(
                    site_url, page.chemin[len(plan.dossier_contenu) + 1:], champs)
                page.url_publique = (page.urls_possibles[0]
                                     if page.urls_possibles else "")
        # Les fichiers de config ne sont pas des pages.
        plan.pages = [p for p in plan.pages if p.chemin.lower().endswith(".md")]
        return plan

    plan = _lire_11ty(par_chemin, octets, site_url)
    plan.pages = [p for p in plan.pages if p.chemin.lower().endswith(".md")]
    return plan
