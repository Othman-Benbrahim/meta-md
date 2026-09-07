"""Ce que les moteurs Albert ont en commun : l'hote et les modeles.

Une seule passerelle, plusieurs moteurs. Les isoler ici evite qu'une
URL soit ecrite deux fois et diverge."""
from __future__ import annotations


ALBERT_BASE_URL = "https://albert.api.etalab.gouv.fr"
DEFAULT_VISION_MODEL = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"

