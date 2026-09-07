#!/usr/bin/env bash
# META-MD - version locale
# @author    Laurent Abbal
# @copyright 2026 Laurent Abbal
# @link      https://forge.apps.education.fr/meta-md/meta-md
# @license   GNU Affero General Public License v3.0 (AGPL-3.0)
set -e

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

PORT=8118
URL="http://127.0.0.1:$PORT"

# ----- Le port est-il libre ? -----
# S'il ne l'est pas, deux cas tres differents : soit c'est notre propre
# serveur, reste ouvert d'une session precedente, et on le remplace ; soit
# c'est un programme etranger, et on n'y touche pas. Le depart se fait sur
# l'executable : seul un python de CE dossier est considere comme le notre.
BLOCKER_PID=""
if command -v ss >/dev/null 2>&1; then
    BLOCKER_PID="$(ss -tlnpH 2>/dev/null | awk -v p=":$PORT" '$4 ~ p"$" {print $0}' | grep -oP 'pid=\K[0-9]+' | head -n1)"
elif command -v lsof >/dev/null 2>&1; then
    BLOCKER_PID="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n1)"
fi

if [[ -n "$BLOCKER_PID" ]]; then
    BLOCKER_EXE="$(readlink -f "/proc/$BLOCKER_PID/exe" 2>/dev/null || true)"
    NOTRE_PY="$SCRIPT_DIR/lanceur/python/linux/bin/python3"
    if [[ -n "$BLOCKER_EXE" && "$BLOCKER_EXE" == "$(readlink -f "$NOTRE_PY" 2>/dev/null)" ]]; then
        echo "Un serveur META-MD tourne deja (PID $BLOCKER_PID). Fermeture..."
        kill "$BLOCKER_PID" 2>/dev/null || true
        # Laisser le temps de fermer proprement, puis insister si besoin.
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$BLOCKER_PID" 2>/dev/null || break
            sleep .3
        done
        kill -9 "$BLOCKER_PID" 2>/dev/null || true
    else
        echo "*** ERREUR : Le port $PORT est deja utilise par le PID $BLOCKER_PID. ***"
        echo
        echo "Programme en cours d'ecoute :"
        ps -p "$BLOCKER_PID" -o pid,user,cmd 2>/dev/null || true
        echo
        echo "Ce n'est pas le serveur de ce dossier : ce script n'y touche pas."
        echo "Pour liberer le port :"
        echo "    kill -9 $BLOCKER_PID"
        echo "Puis relancez ce script."
        exit 1
    fi
fi

echo "=== Demarrage du serveur META-MD ==="
echo "Interface : $URL"
echo "Ctrl+C pour arreter"
echo

# Ouvre le navigateur apres 2 s.
(
    sleep 2
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
        open "$URL" >/dev/null 2>&1 || true
    fi
) &

# Le lanceur stable choisit la version active et conserve toujours les
# Les donnees restent dans data/, les versions dans app/.
./python.sh lanceur/launch.py
