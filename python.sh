#!/usr/bin/env bash
# META-MD - version locale
# @author    Laurent Abbal
# @copyright 2026 Laurent Abbal
# @link      https://forge.apps.education.fr/meta-md/meta-md
# @link      https://laurentabbal.forge.apps.education.fr
# @license   GNU Affero General Public License v3.0 (AGPL-3.0)

# Wrapper Python pour Linux/macOS.
# Priorite 1 : runtime Python stable dans lanceur/python/linux.
# Fallback   : python3 du PATH systeme
set -e

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
LOCAL_PY="$SCRIPT_DIR/lanceur/python/linux/bin/python3"

if [[ -x "$LOCAL_PY" ]]; then
    exec "$LOCAL_PY" "$@"
fi

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$@"
fi

echo "ERREUR : Python introuvable." >&2
echo "Lancez ./META-MD-linux-1-installer.sh pour installer le runtime Python" >&2
echo "ou installez Python 3.11+ et ajoutez-le au PATH." >&2
exit 1
