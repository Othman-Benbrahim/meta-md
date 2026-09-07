"""Les deux racines de l'installation, resolues au meme endroit.

`app/` est versionnable et remplace a chaque mise a jour ; `data/` est
permanent. Deux modules avaient chacun leur facon de retrouver le second, et
ils ne tombaient juste que par accident : dans une installation, une version
vit dans `app/versions/X.Y.Z/`, donc `BASE_DIR.parent / "data"` designe
`app/versions/data`, qui n'existe pas. C'est le lanceur qui rattrape, en
posant `METAMD_DATA_DIR`. Un module qui refait ce calcul sans lire la variable
ecrirait donc dans le dossier de la version — efface a la mise a jour suivante.
"""
from __future__ import annotations

import os
from pathlib import Path

# `app/` en developpement, `app/versions/X.Y.Z/` dans une installation.
RACINE_APP = Path(__file__).resolve().parent.parent
_DATA_PAR_DEFAUT = RACINE_APP.parent / "data"


def dossier_app() -> Path:
    """La partie versionnable : serveur, modules, pages, lots livres."""
    return RACINE_APP


def dossier_data() -> Path:
    """La partie permanente, que la mise a jour ne remplace jamais."""
    force = os.environ.get("METAMD_DATA_DIR")
    if force:
        return Path(force).resolve()
    if _DATA_PAR_DEFAUT.is_dir():
        return _DATA_PAR_DEFAUT.resolve()
    return RACINE_APP.resolve()
