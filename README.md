![](assets/meta-md-logo.png)

**DES SOURCES VERS DES MARKDOWN DOCUMENTÉS.** META-MD prend des documents — aujourd'hui des PDF — et en produit des Markdown auto-suffisants : chaque fichier porte ses métadonnées dans son front matter YAML.

Tout tourne sur votre machine : un serveur Python de la bibliothèque standard, sans framework, qui sert une interface statique sur <http://localhost:8118/>. Seuls les appels au modèle de conversion sortent du poste.

**Convertir un document**

* Déposer les PDF par glisser-déposer, et donner à chacun son titre et, si elle existe, l'URL publique de sa source.
* Définir le schéma des métadonnées attendues, champ par champ et type par type, puis le verrouiller : le front matter des Markdown produits le suivra clé pour clé.
* Convertir : chaque page part seule à un modèle de vision, puis le PDF sert à corriger ce que le modèle déforme — tableaux, niveaux de titre, bandeaux — et ses figures sont découpées et incorporées au Markdown.
* Relire dans le viewer, PDF d'un côté et Markdown de l'autre, puis cocher les deux relectures : le texte est fidèle à la source, les métadonnées sont justes.
* Emporter les fichiers, un à un ou en ZIP : le <code>.md</code> produit et le PDF dont il vient, sous le même nom, prêts à être republiés côte à côte.

**Installer**

**Windows** : <code>META-MD-win-1-installer.bat</code>, puis <code>META-MD-win-2-ouvrir.bat</code>.
**Linux / macOS** : <code>./META-MD-linux-1-installer.sh</code>, puis <code>./META-MD-linux-2-ouvrir.sh</code>.

L'installateur récupère un Python portable et les dépendances ; l'ouverture lance le serveur et le navigateur. Une clé Albert, saisie dans **Configuration**, est nécessaire à la conversion.

Les Markdown produits se reprennent dans n'importe quel outil qui lit du front matter — dont <a href="https://forge.apps.education.fr/md-rag/md-rag" target="_blank" rel="noopener noreferrer">MD-RAG</a>, qui les découpe en extraits et les envoie vers une collection. META-MD ne fait ni l'un ni l'autre : les deux projets sont indépendants et communiquent par un simple dossier de fichiers <code>.md</code>.

---

## Auteur

© 2026 **Laurent Abbal**

- Mastodon : [@laurentabbal@mastodon.social](https://mastodon.social/@laurentabbal)
- X / Twitter : [@laurentabbal](https://x.com/laurentabbal)
- ForgeEdu : [laurentabbal.forge.apps.education.fr/](https://laurentabbal.forge.apps.education.fr/)


## Licence

Ce projet est partagé sous licence libre **AGPL-3.0**. Veuillez consulter le fichier `LICENSE` pour connaître les termes précis de distribution et de réutilisation.

Ce choix est dicté par les dépendances : la conversion repose sur **PyMuPDF** et **pymupdf4llm**, tous deux en AGPL-3.0.
