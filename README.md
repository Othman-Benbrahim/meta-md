![](assets/meta-md-logo.png)

# META-MD — version modifiée

> **Ceci est un fork.** Le projet d'origine est [META-MD](https://forge.apps.education.fr/meta-md/meta-md),
> écrit par Laurent Abbal et publié sur la Forge des communs numériques éducatifs.
> Ce dépôt en reproduit le code et y apporte les modifications listées plus bas.
> Il n'est ni maintenu ni endossé par l'auteur original.

**DES SOURCES VERS DES MARKDOWN DOCUMENTÉS.** META-MD prend des documents — aujourd'hui des PDF — ou des sites web statiques, et en produit des Markdown auto-suffisants : chaque fichier porte ses métadonnées dans son front matter YAML.

La nature d'un corpus se choisit à sa création et ne change plus : mélanger des PDF et un site donnerait un schéma qui ne vaut pour personne.

* **Corpus de PDF** — les documents sont déposés, puis convertis page à page par un modèle de vision.
* **Corpus de site** (`11ty`, `MkDocs`) — le dépôt du site est récupéré et ses Markdown, déjà écrits, sont importés. Il n'y a rien à convertir : la configuration du site (`mkdocs.yml`, `.eleventy.js`) dit quelles pages il publie, et l'on décoche le reste.

Tout tourne sur votre machine : un serveur Python de la bibliothèque standard, sans framework, qui sert une interface statique sur <http://localhost:8118/>. Seuls les appels au modèle de conversion sortent du poste.

**Convertir un document**

* Déposer les PDF par glisser-déposer, et donner à chacun son titre et, si elle existe, l'URL publique de sa source.
* Définir le schéma des métadonnées attendues, champ par champ et type par type, puis le verrouiller : le front matter des Markdown produits le suivra clé pour clé.
* Convertir, pour un corpus de PDF : chaque page part seule à un modèle de vision, puis le PDF sert à corriger ce que le modèle déforme — tableaux, niveaux de titre, bandeaux — et ses figures sont découpées et incorporées au Markdown. Pour un corpus de site, cette étape est un import : les pages choisies entrent telles quelles.
* Remplir les champs vides : une fois un document converti, le bouton de l'étape 3 demande au modèle de déduire du Markdown les métadonnées encore absentes. Les valeurs déjà posées ne sont jamais retouchées.
* Relire dans le viewer, PDF d'un côté et Markdown de l'autre, puis cocher les deux relectures : le texte est fidèle à la source, les métadonnées sont justes.
* Emporter les fichiers, un à un ou en ZIP : le <code>.md</code> produit et le document dont il vient, sous le même nom, prêts à être republiés côte à côte.

**Installer**

**Windows** : <code>META-MD-win-1-installer.bat</code>, puis <code>META-MD-win-2-ouvrir.bat</code>.
**Linux / macOS** : <code>./META-MD-linux-1-installer.sh</code>, puis <code>./META-MD-linux-2-ouvrir.sh</code>.

L'installateur récupère un Python portable et les dépendances ; l'ouverture lance le serveur et le navigateur. Une clé Albert, saisie dans **Configuration**, est nécessaire à la conversion.

Les Markdown produits se reprennent dans n'importe quel outil qui lit du front matter — dont <a href="https://forge.apps.education.fr/md-rag/md-rag" target="_blank" rel="noopener noreferrer">MD-RAG</a>, qui les découpe en extraits et les envoie vers une collection. META-MD ne fait ni l'un ni l'autre : les deux projets sont indépendants et communiquent par un simple dossier de fichiers <code>.md</code>.

---

## Modifications apportées

Conformément à l'article 5(a) de la licence AGPL-3.0, voici les changements
faits par rapport au dépôt d'origine.

**Septembre 2026 — le Markdown produit fait foi, plus l'appariement source/conversion**

Le téléchargement en ZIP et le remplissage des métadonnées partaient jusqu'ici
des couples (document source, conversion), appariés par le nom de fichier. Un
document dont la source avait été renommée, retirée de `1-Sources/` ou marquée
« à ne pas traiter » perdait son couple : son `.md` restait sur le disque, déjà
converti, mais devenait invisible des deux étapes — le téléchargement répondait
« Aucun Markdown actif dans la sélection », et le bouton de remplissage ne
s'affichait pas.

* `app/modules/corpus/documents.py` — ajout de `list_markdowns()`, qui énumère
  les Markdown réellement posés dans `2-Conversions/`, et de
  `source_for_stem()`, qui retrouve le document d'origine quand il existe
  encore et rend `None` sinon.
* `app/server.py` — la route ZIP part des Markdown présents ; la sélection
  filtre au lieu de commander, et le document d'origine devient facultatif
  dans l'archive. La route de remplissage énumère ses cibles de la même façon.
  `/api/corpus/documents` accepte `orphans=1`, que seule l'étape 5 demande.
* `app/modules/corpus/topics.py` — le décompte du remplissage se fait sur les
  Markdown, et expose `docs_converted`.
* `app/pages/assets/app.js` — le bouton « Remplir les champs vides » s'ouvre
  dès qu'un document est converti et que le schéma est verrouillé, au lieu
  d'attendre qu'il reste des champs vides. La phrase de l'étape 2 ne promet
  plus un remplissage automatique : le remplissage se déclenche au clic.
* `app/tests/test_markdowns_orphelins.py` — tests de ces comportements, corpus
  de site compris : la source y étant elle-même un `.md`, l'archive ne doit pas
  embarquer deux entrées de même nom.

**Septembre 2026 — mise à jour automatique neutralisée**

L'application interrogeait à chaque démarrage un manifeste publié sur la Forge
et proposait d'installer la version qu'il annonçait. Appliquée à ce dépôt, cette
mise à jour remplacerait `app/` en entier par le paquet du projet d'origine et
effacerait les corrections ci-dessus sans prévenir.

* `app/modules/updates.py` — constante `VERIFICATION_ACTIVE`, à `False`. Aucune
  requête ne part, `update_status()` répond « à jour » sans rien vérifier, et
  `download_package()` refuse séparément — ce qui remplace des fichiers de
  l'application ne dépend pas d'un seul garde-fou. Le code de vérification et
  d'installation est **conservé intact**, non supprimé : remettre la constante à
  `True` et réécrire `MANIFEST_URL` suffit à le réactiver.
* `app/server.py` — `/api/update/install` refuse en disant pourquoi, et
  `/api/version` expose `update_check` pour que l'interface s'aligne.
* `app/pages/assets/app.js` — le numéro de version reste affiché mais cesse
  d'être cliquable : un bouton qui ne vérifie rien vaut moins que pas de bouton.
* `lanceur/tests/test_updates.py` — les cas existants réactivent la constante le
  temps du test, pour que le mécanisme conservé reste couvert ; deux cas
  nouveaux vérifient la neutralisation telle qu'elle est livrée.

À noter : le **catalogue des lots de documents** continue, lui, d'interroger la
Forge (`edu-md/documents`). C'est une fonctionnalité de l'application, sans
rapport avec la mise à jour, et elle n'écrit rien dans `app/`.

---

## Auteur du projet d'origine

© 2026 **Laurent Abbal**

- Mastodon : [@laurentabbal@mastodon.social](https://mastodon.social/@laurentabbal)
- X / Twitter : [@laurentabbal](https://x.com/laurentabbal)
- ForgeEdu : [laurentabbal.forge.apps.education.fr/](https://laurentabbal.forge.apps.education.fr/)

## Licence

Ce projet est partagé sous licence libre **AGPL-3.0**, comme le projet dont il
dérive. Veuillez consulter le fichier `LICENSE` pour connaître les termes
précis de distribution et de réutilisation.

Ce choix est dicté par les dépendances : la conversion repose sur **PyMuPDF**
et **pymupdf4llm**, tous deux en AGPL-3.0.
