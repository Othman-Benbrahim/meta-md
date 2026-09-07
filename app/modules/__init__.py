"""META-MD : le code, range par ce dont il depend.

Quatre dossiers, et la regle qui les ordonne — chacun ne connait que ceux
qui sont au-dessus de lui dans cette liste :

    traitements/  Markdown vers Markdown. Ne connait ni le PDF ni le moteur.
                  Maths, polices, tableaux HTML, titres, listes, aeration.
    pdf/          Ce qui se lit dans le PDF pour reparer le Markdown :
                  pagination, hierarchie des titres, tableaux, balises.
                  Ne connait aucun moteur.
    moteurs/      Un dossier par moteur, nomme comme lui — c'est le nom que
                  porte `active_engine` dans le sidecar. Seul endroit ou
                  un appel reseau part vers un modele. Il n'en reste qu'un,
                  `albert_vision_figures` ; les quatre autres ont ete retires
                  le 2026-08-23, et avec eux la conversion sans cle.
    corpus/       Les themes, leurs documents, leurs sidecars, leur schema.
                  Ne sait pas convertir.

Et trois modules a la racine :

    pipeline.py   Lance le moteur, applique ce qui ne lui appartient pas,
                  ecrit le fichier. Seul module de conversion que le serveur
                  importe.
    autofill.py   Remplit les champs du schema par un modele de texte.
    jobs.py       Suivi des taches longues.

Le sens de lecture vaut aussi pour les bugs : un traitement qui aurait besoin
de connaitre le moteur est un traitement mal place.
"""
