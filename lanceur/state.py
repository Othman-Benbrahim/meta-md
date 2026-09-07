"""Etat minimal des versions, independant du code metier de META-MD."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def valid_version(value: object) -> str | None:
    version = str(value or "").strip()
    return version if VERSION_RE.fullmatch(version) else None


def selected_app(root: Path, state_path: Path) -> tuple[Path, str | None]:
    """Application active, ou sources racine pour une installation historique."""
    version = valid_version(read_state(state_path).get("active"))
    if version:
        candidate = root / "app" / "versions" / version
        if (candidate / "server.py").is_file() and (candidate / "VERSION").is_file():
            return candidate, version
    return root / "app", None
