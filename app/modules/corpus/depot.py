"""Recuperation d'un depot git public, par son archive.

Deux forges sont reconnues, GitHub et GitLab — y compris une instance GitLab
auto-hebergee, dont l'hote est quelconque. On telecharge l'archive de la
branche plutot que de cloner : `git` n'est pas garanti sur le poste, une
archive ne rapatrie pas l'historique, et `zipfile` / `tarfile` sont dans la
bibliotheque standard. Aucune dependance nouvelle.

Depots publics uniquement : aucun jeton n'est demande ni stocke. Un depot
prive repond 404 comme un depot inexistant, et le message le dit.

Le module ne touche pas au disque du corpus : il rend le contenu des `.md` en
memoire. C'est l'appelant qui decide de ce qu'il en garde.
"""
from __future__ import annotations

import io
import logging
import posixpath
import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("atelier.corpus.depot")

TIMEOUT = 60.0
USER_AGENT = "META-MD-local/1.0"
# Une archive de site raisonnable pese quelques Mo. Au-dela, c'est un depot
# qui n'a rien a faire ici (binaires, medias) et le dire vaut mieux que de
# remplir la memoire.
TAILLE_MAX = 80 * 1024 * 1024

GITHUB_HOSTS = ("github.com", "www.github.com")

# Markdown que l'on ne propose pas par defaut : ce sont des pages de depot,
# pas des pages de site.
#
# Ces noms ne comptent qu'A LA RACINE du depot. Ailleurs, ce sont des pages
# comme les autres : `content/faq/projets/licence.md` est une FAQ sur les
# licences, pas la licence du depot — le motif d'origine, qui acceptait
# n'importe quel dossier devant, l'ecartait a tort.
FICHIERS_DE_DEPOT = re.compile(
    r"^(readme|changelog|contributing|code_of_conduct|license|licence|security)\.md$",
    re.IGNORECASE)

# Ces dossiers, en revanche, sont hors site a n'importe quelle profondeur :
# rien de ce qu'ils contiennent n'est une page.
DOSSIERS_HORS_SITE = re.compile(
    r"(^|/)(\.github|\.gitlab|node_modules|\.venv|__pycache__)/",
    re.IGNORECASE)

# Dossiers de contenu : leur nom ne distingue aucun document du suivant, il ne
# sert donc a rien dans un identifiant.
DOSSIERS_DE_CONTENU = ("docs", "doc", "src", "content", "pages")

# Fichiers non-Markdown dont on garde le texte : ce sont les configs de site,
# qui disent quelles pages sont publiees. Tout le reste de l'archive n'est
# retenu que par son nom — un depot de documentation embarque des images et
# des scripts qui n'ont rien a faire en memoire.
FICHIERS_CONFIG = (
    "mkdocs.yml", "mkdocs.yaml",
    ".eleventy.js", "eleventy.config.js", "eleventy.config.mjs",
    ".eleventy.cjs", "eleventy.config.cjs",
)


class DepotError(RuntimeError):
    """Erreur lisible par l'utilisateur : URL, reseau ou archive."""


@dataclass
class Fichier:
    """Un `.md` trouve dans l'archive."""

    chemin: str          # chemin relatif a la racine du depot
    texte: str
    octets: int


@dataclass
class Depot:
    """Ce qu'on sait d'un depot apres recuperation."""

    forge: str           # 'github' ou 'gitlab'
    hote: str
    proprietaire: str
    nom: str
    ref: str
    url_web: str
    fichiers: list[Fichier] = field(default_factory=list)
    autres: list[str] = field(default_factory=list)   # chemins non-.md
    textes_annexes: dict[str, str] = field(default_factory=dict)  # configs lues

    def url_fichier(self, chemin: str) -> str:
        """Adresse web du fichier dans le depot, au ref recupere."""
        segment = "blob" if self.forge == "github" else "-/blob"
        return f"{self.url_web}/{segment}/{self.ref}/{chemin}"


def analyser_url(url: str) -> dict[str, str]:
    """Reconnait une URL de depot et en tire ses composants.

    Accepte les formes courantes : avec ou sans `https://`, avec ou sans
    `.git`, avec ou sans slash final, et l'URL d'une page du depot.
    """
    brut = (url or "").strip()
    if not brut:
        raise DepotError("Renseignez l'adresse d'un depot.")
    if "://" not in brut:
        brut = "https://" + brut

    parts = urlparse(brut)
    hote = (parts.netloc or "").lower()
    if not hote:
        raise DepotError(f"Adresse de depot illisible : {url}")

    segments = [s for s in (parts.path or "").split("/") if s]
    if len(segments) < 2:
        raise DepotError(
            "L'adresse doit designer un depot, par exemple "
            "https://github.com/proprietaire/depot.")

    proprietaire, nom = segments[0], segments[1]
    if nom.endswith(".git"):
        nom = nom[:-4]

    forge = "github" if hote in GITHUB_HOSTS else "gitlab"
    return {
        "forge": forge,
        "hote": hote,
        "proprietaire": proprietaire,
        "nom": nom,
        "url_web": f"https://{hote}/{proprietaire}/{nom}",
    }


def _urls_archive(infos: dict[str, str], ref: str) -> list[str]:
    """Adresses a essayer pour l'archive de cette reference."""
    p, n, hote = infos["proprietaire"], infos["nom"], infos["hote"]
    if infos["forge"] == "github":
        return [f"https://codeload.github.com/{p}/{n}/tar.gz/refs/heads/{ref}"]
    return [f"https://{hote}/{p}/{n}/-/archive/{ref}/{n}-{ref}.zip"]


def _refs_a_essayer(ref: str | None) -> list[str]:
    """`main` puis `master` quand l'appelant ne dit rien.

    Aucune des deux n'est majoritaire partout : les essayer evite de reclamer
    une branche a quelqu'un qui colle simplement l'adresse de son depot.
    """
    return [ref] if ref else ["main", "master"]


def _telecharger(url: str) -> bytes:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            reponse = client.get(url)
    except httpx.HTTPError as exc:
        raise DepotError(f"Depot injoignable : {exc}") from exc

    if reponse.status_code == 404:
        raise DepotError("Depot ou branche introuvable. Verifiez l'adresse ; "
                         "les depots prives ne sont pas pris en charge.")
    if reponse.status_code != 200:
        raise DepotError(f"La forge a repondu HTTP {reponse.status_code}.")
    if len(reponse.content) > TAILLE_MAX:
        raise DepotError(
            f"Archive trop lourde ({len(reponse.content) // (1024 * 1024)} Mo). "
            "Ce depot ne ressemble pas a un site de documentation.")
    return reponse.content


def _membres_archive(donnees: bytes) -> list[tuple[str, bytes]]:
    """(chemin sans le dossier racine, contenu) pour chaque fichier.

    Les deux forges emballent tout dans un dossier `<depot>-<ref>/` : on le
    retire pour que les chemins rendus soient ceux du depot.
    """
    sorties: list[tuple[str, bytes]] = []

    def sans_racine(chemin: str) -> str:
        return chemin.split("/", 1)[1] if "/" in chemin else ""

    if donnees[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(donnees)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    interne = sans_racine(info.filename)
                    if interne:
                        sorties.append((interne, zf.read(info)))
        except zipfile.BadZipFile as exc:
            raise DepotError(f"Archive illisible : {exc}") from exc
        return sorties

    try:
        with tarfile.open(fileobj=io.BytesIO(donnees), mode="r:gz") as tf:
            for membre in tf.getmembers():
                if not membre.isfile():
                    continue
                interne = sans_racine(membre.name)
                if not interne:
                    continue
                flux = tf.extractfile(membre)
                if flux is not None:
                    sorties.append((interne, flux.read()))
    except tarfile.TarError as exc:
        raise DepotError(f"Archive illisible : {exc}") from exc
    return sorties


def hors_site(chemin: str) -> bool:
    """Vrai pour un `.md` de depot plutot qu'une page de site.

    Deux regles distinctes : un nom de fichier de depot ne compte qu'a la
    racine, tandis qu'un dossier hors site vaut a toute profondeur.
    """
    if DOSSIERS_HORS_SITE.search(chemin):
        return True
    return bool(FICHIERS_DE_DEPOT.match(chemin))


def recuperer(url: str, ref: str | None = None) -> Depot:
    """Telecharge le depot et rend ses fichiers Markdown.

    Sans `ref`, `main` puis `master` sont essayes.
    """
    infos = analyser_url(url)
    dernier: DepotError | None = None

    for candidat in _refs_a_essayer(ref):
        for adresse in _urls_archive(infos, candidat):
            try:
                donnees = _telecharger(adresse)
            except DepotError as exc:
                dernier = exc
                continue

            membres = _membres_archive(donnees)
            fichiers: list[Fichier] = []
            autres: list[str] = []
            annexes: dict[str, str] = {}
            for chemin, contenu in membres:
                if not chemin.lower().endswith(".md"):
                    autres.append(chemin)
                    if chemin in FICHIERS_CONFIG:
                        annexes[chemin] = contenu.decode("utf-8", errors="replace")
                    continue
                try:
                    texte = contenu.decode("utf-8")
                except UnicodeDecodeError:
                    texte = contenu.decode("utf-8", errors="replace")
                    logger.warning("Depot : %s n'est pas de l'UTF-8 valide.", chemin)
                fichiers.append(Fichier(chemin=chemin, texte=texte,
                                        octets=len(contenu)))

            fichiers.sort(key=lambda f: f.chemin)
            logger.info("Depot %s/%s@%s : %d fichier(s) .md sur %d.",
                        infos["proprietaire"], infos["nom"], candidat,
                        len(fichiers), len(membres))
            return Depot(forge=infos["forge"], hote=infos["hote"],
                         proprietaire=infos["proprietaire"], nom=infos["nom"],
                         ref=candidat, url_web=infos["url_web"],
                         fichiers=fichiers, autres=autres,
                         textes_annexes=annexes)

    raise dernier or DepotError("Depot introuvable.")


def stem_depuis_chemin(chemin: str, pris: set[str] | None = None) -> str:
    """Nom de fichier plat et unique pour un chemin du depot.

    META-MD identifie un document par le `stem` de son fichier. Or un depot
    MkDocs porte couramment plusieurs `index.md` (`guide/index.md`,
    `api/index.md`) : n'en garder que le nom de base les ferait s'ecraser
    l'un l'autre. Le chemin est donc aplati — `docs/guide/index.md` devient
    `guide-index` — et le chemin d'origine reste dans le sidecar.

    Le dossier de contenu en tete est retire : il vaut pour tout le depot et
    n'y distingue donc rien.
    """
    sans_ext = posixpath.splitext(chemin)[0]
    segments = [s for s in sans_ext.split("/") if s]
    if len(segments) > 1 and segments[0].lower() in DOSSIERS_DE_CONTENU:
        segments = segments[1:]
    base = "-".join(segments) or "document"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.") or "document"

    if pris is None:
        return base
    candidat, suffixe = base, 2
    while candidat in pris:
        candidat = f"{base}-{suffixe}"
        suffixe += 1
    return candidat
