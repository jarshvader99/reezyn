"""A small presence marker so CLI commands can tell whether Reezyn is running."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def write_running(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home / "running.json"
    path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": time.time()}),
        encoding="utf-8",
    )


def clear_running(home: Path) -> None:
    (home / "running.json").unlink(missing_ok=True)


def read_running(home: Path) -> dict[str, Any] | None:
    try:
        value = json.loads((home / "running.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def is_running(home: Path) -> bool:
    value = read_running(home)
    pid = value.get("pid") if value else None
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
