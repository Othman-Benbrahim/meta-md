#!/usr/bin/env bash
# META-MD - version locale
# @author    Laurent Abbal
# @copyright 2026 Laurent Abbal
# @link      https://forge.apps.education.fr/meta-md/meta-md
# @license   GNU Affero General Public License v3.0 (AGPL-3.0)
#
# Installation Linux x86_64 (portable et local).
# Python portable + dependances listees dans app/requirements.txt.
set -e

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

PY_VERSION="3.12.13"
PY_TAG="20260610"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/cpython-${PY_VERSION}+${PY_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
ARCHIVE="lanceur_python_linux_dl.tar.gz"
TARGET="lanceur/python/linux"
PY="$TARGET/bin/python3"

echo "============================================================"
echo "  META-MD - Installation Linux x86_64 (portable et local)"
echo "  Python $PY_VERSION + dependances (voir app/requirements.txt)"
echo "============================================================"

# ----- Prerequis -----
command -v curl >/dev/null || { echo "ERREUR : curl introuvable." >&2; exit 1; }
command -v tar  >/dev/null || { echo "ERREUR : tar introuvable."  >&2; exit 1; }

# Considere "vide" : dossier inexistant OU contenant uniquement instructions.txt
# (placeholder du repo). Tout autre contenu = deja installe, on refuse.
if [[ -d "$TARGET" ]]; then
    has_non_doc=0
    shopt -s nullglob dotglob
    for f in "$TARGET"/*; do
        base="$(basename "$f")"
        if [[ "$base" != "instructions.txt" ]]; then
            has_non_doc=1
            break
        fi
    done
    shopt -u nullglob dotglob
    if [[ "$has_non_doc" == "1" ]]; then
        echo "Le dossier $TARGET contient deja une installation."
        echo "Pour reinstaller : rm -rf $TARGET"
        exit 1
    fi
fi

# ----- Installation -----
mkdir -p "lanceur/python"
echo "=== 1/3 : telechargement de Python (~30 MB) ==="
curl -L --progress-bar -o "$ARCHIVE" "$URL"

echo
echo "=== 2/3 : extraction ==="
tar -xzf "$ARCHIVE"
[[ -d "python" ]] || { echo "ERREUR : pas de dossier 'python' dans l'archive." >&2; exit 1; }

# Preserve l'instructions.txt eventuel avant de remplacer le dossier
keep_instructions=""
if [[ -f "$TARGET/instructions.txt" ]]; then
    keep_instructions="$(cat "$TARGET/instructions.txt")"
fi
[[ -d "$TARGET" ]] && rm -rf "$TARGET"
mv python "$TARGET"
if [[ -n "$keep_instructions" ]]; then
    printf '%s\n' "$keep_instructions" > "$TARGET/instructions.txt"
fi
rm "$ARCHIVE"

echo
echo "=== 3/3 : installation des dependances Python ==="
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r app/requirements.txt

chmod +x "$SCRIPT_DIR"/*.sh

echo
echo "============================================================"
echo "  TERMINE."
echo "  ./python.sh utilisera desormais $TARGET."
echo
echo "  Etape suivante : ./META-MD-linux-2-ouvrir.sh"
echo "  (le serveur ecoute sur http://localhost:8118/)"
echo "============================================================"
