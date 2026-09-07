"""Depose les paquets construits dans le registre de paquets de la Forge.

Ce travail se faisait dans une etape de CI a part, sous l'image
`curlimages/curl`. Une etape, c'est une file d'attente et un tirage d'image de
plus : mesure du 2026-08-28, jusqu'a cinq minutes d'attente pour deux cents
secondes de travail. `urllib` fait le meme envoi depuis l'image Python qui
vient de construire les paquets.

Le jeton vient de `CI_JOB_TOKEN`, que GitLab exporte. L'adresse du registre
est passee en argument : une affectation `VAR=...` dans un script de CI ne
cree qu'une variable de SHELL, que le processus fils ne verrait pas.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def deposer(fichier: Path, base: str, jeton: str) -> None:
    """Envoie un fichier au registre, sous son propre nom."""
    url = f"{base.rstrip('/')}/{fichier.name}"
    requete = urllib.request.Request(
        url, data=fichier.read_bytes(), method="PUT",
        headers={"JOB-TOKEN": jeton,
                 "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(requete, timeout=180) as reponse:
            code = reponse.status
    except urllib.error.HTTPError as exc:
        # Le corps porte le motif du refus : sans lui, un 403 ne dit pas si
        # c'est le jeton, le registre desactive, ou le nom du paquet.
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"Envoi refuse ({exc.code}) pour {fichier.name} : {detail}")
    print(f"{fichier.name} -> {url} ({code}, {fichier.stat().st_size} octets)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deposer les paquets au registre")
    parser.add_argument("--registre", required=True,
                        help="adresse de base du paquet generique")
    parser.add_argument("fichiers", type=Path, nargs="+")
    args = parser.parse_args()

    base = args.registre.strip()
    jeton = os.environ.get("CI_JOB_TOKEN", "").strip()
    if not base:
        raise SystemExit("--registre est requis")
    if not jeton:
        raise SystemExit("CI_JOB_TOKEN est requis (exporte par GitLab)")

    manquants = [str(f) for f in args.fichiers if not f.is_file()]
    if manquants:
        raise SystemExit("fichiers introuvables : " + ", ".join(manquants))

    for fichier in args.fichiers:
        deposer(fichier, base, jeton)
    return 0


if __name__ == "__main__":
    sys.exit(main())
