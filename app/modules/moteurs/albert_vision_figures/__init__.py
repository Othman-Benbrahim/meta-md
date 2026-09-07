"""Moteur `albert_vision_figures` : transcription VLM et figures du PDF.

Le seul moteur de conversion depuis le 2026-08-23.

    prompt          la consigne envoyee au modele, seule et relisible
    transcription   l'appel page par page, puis les reparations
    decoupage       les figures trouvees dans le PDF et posees dans le Markdown

Un modele de vision decrit une figure, il ne sait pas la decouper ; le PDF,
lui, porte la boite de chaque dessin. Le decoupage ne coute donc pas un appel
de plus : c'est une lecture locale ajoutee a ce que la transcription a rendu.

Ce moteur etait un drapeau (`avec_figures`) sur `albert_vision`. Ce dernier
ayant ete retire, le drapeau n'a plus de raison d'etre : les figures sont
toujours posees.
"""

from .decoupage import (NOM_MOTEUR_VISION_FIGURES, _bandeaux_graphiques,  # noqa: E402
                        _poser_figures)
from .prompt import VISION_PROMPT  # noqa: E402
from .transcription import _convert_vision  # noqa: E402
