"""Les themes, leurs documents, leurs metadonnees.

Ce dossier ne sait pas convertir : il range, lit et ecrit. La source de
verite est le sidecar `2-Conversions/<stem>.metadata.yaml` ; le front matter
des `.md` n'en est qu'une copie, reecrite a chaque changement.

    nature        d'ou viennent les documents d'un corpus (pdf, 11ty, mkdocs)
    topics        scan des themes presents dans CORPUS/
    documents     les conversions d'un document, et laquelle est active
    metadata      lecture et ecriture du sidecar
    frontmatter   report du sidecar dans le front matter des `.md`
    schema        les champs metier d'un corpus, et leur verrou
    lots          lots predefinis, lus dans `lots/` a la racine du depot
    depot         recuperation d'une archive de depot git
    importation   entree d'un site deja ecrit, a la place de la conversion
    site          lecture d'une config mkdocs ou 11ty
"""

from . import (catalogue, depot, documents, frontmatter, importation, lots,
               metadata, nature, schema, site, topics)
