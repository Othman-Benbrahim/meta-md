"""Un dossier par moteur, nomme comme lui.

Le nom du dossier est celui qu'on lit dans le sidecar (`active_engine`) et
dans le front matter du Markdown produit (`_engine`) : la correspondance
entre ce qui est ecrit et le code se fait sans table.

    albert_vision_figures/  transcription par un VLM, plus les figures
                            decoupees dans le PDF et incorporees au Markdown

`albert.py` tient a part ce que les moteurs Albert partagent : l'hote et le
modele. Le fichier survit a un seul moteur parce que c'est la que se pose la
question « chez qui » ; le jour ou un second moteur Albert revient, il n'a
pas a reecrire l'URL.

Quatre moteurs ont ete retires le 2026-08-23 : `pymupdf` (extraction hors
ligne, sans cle), `albert_vision` (la meme transcription, sans les figures),
`mistral_document_ai` et `albert_ocr` (OCR structure). Consequence a
connaitre avant toute reprise : **il n'existe plus de chemin de conversion
sans cle ni appel reseau.** Les conversions faites par ces moteurs restent
sur le disque, elles ne sont simplement plus reproductibles.

Un moteur peut dependre de `pdf` et de `traitements` ; l'inverse serait une
erreur de rangement. Ajouter un moteur, c'est ajouter un dossier et une
entree dans `pipeline.SUPPORTED_ENGINES`.
"""
