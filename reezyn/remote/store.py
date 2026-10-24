"""Paired-phone sessions on disk.

A session token is effectively an SSH key: whoever holds it can type into an
agent. Tokens are random, shown once, and stored only as SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

FILE_NAME = "remote.json"
COOKIE_NAME = "reezyn_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RemoteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        sessions = raw.get("sessions") if isinstance(raw, dict) else None
        if not isinstance(sessions, dict):
            return
        now = time.time()
        for key, value in sessions.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            expires = value.get("expires")
            if not isinstance(expires, (int, float)) or expires <= now:
                continue
            self._sessions[key] = value

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps({"version": 1, "sessions": self._sessions}, indent=2)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(payload, encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(self.path)

    def issue(self, name: str, days: int = 30, now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[_hash(token)] = {
                "name": name[:80],
                "created": now,
                "expires": now + max(1, days) * 86400.0,
            }
            self._save()
        return token

    def validate(self, token: str, now: float | None = None) -> bool:
        if not token:
            return False
        now = time.time() if now is None else now
        key = _hash(token)
        with self._lock:
            value = self._sessions.get(key)
            if value is None:
                return False
            expires = value.get("expires")
            if not isinstance(expires, (int, float)) or expires <= now:
                self._sessions.pop(key, None)
                self._save()
                return False
            return True

    def revoke_all(self) -> int:
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            self._save()
            return count

    def count(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            stale = [
                key
                for key, value in self._sessions.items()
                if not isinstance(value.get("expires"), (int, float))
                or value["expires"] <= now
            ]
            for key in stale:
                self._sessions.pop(key, None)
            if stale:
                self._save()
            return len(self._sessions)
