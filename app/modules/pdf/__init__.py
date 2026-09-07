"""Ce qui se lit dans le PDF pour reparer le Markdown.

Un modele resume, avale un mot, aplatit une grille. Le PDF, lui, porte la
version exacte : positions, tailles de police, lignes de base, boites de
cellules. Ces modules l'interrogent et rendent de quoi corriger.

Ils dependent de `traitements`, jamais l'inverse, et ne connaissent aucun
moteur : les memes reparations servent a l'extraction hors ligne comme a la
transcription par un modele.

    pagination   numeros et pieds de page, par leur ligne de base
    titres       hierarchie des niveaux, etablie une fois par document
    tableaux     grilles extraites du PDF lui-meme
    lignes       retrouver un mot avale, reperer un debut d'item
    balises      ce qu'un PDF balise declare de sa structure
"""
