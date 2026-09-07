"""Traitements du Markdown, independants du moteur comme du support.

Tout ce qui est ici prend du texte et rend du texte. Aucun module n'ouvre un
PDF, aucun n'appelle le reseau, aucun ne sait quel moteur a produit ce qu'il
relit. Il n'y a plus qu'un moteur, mais la regle tient : c'est ce qui rend
ces traitements verifiables sans document sous la main, et reutilisables si
un second moteur revient.

    texte                 reperes partages : mots, titres, empreintes, puces
    tableaux_html         lire, normaliser, recomposer une table HTML
    tableaux_reparation   recoller ce qu'une fin de page a coupe
    pipes                 grilles pipe recues d'un moteur, rendues en HTML
    boilerplate           en-tetes, pieds, bandeaux, images inventees
    titres                calage des niveaux sur la hierarchie du PDF
    listes                debuts d'item declares par le PDF
    aeration              mise en forme des tableaux a l'ecriture
    maths                 fragments mathematiques ecrits en LaTeX
    polices               ce que les polices disent : accents, gras, italique
"""
