"""A tiny local spool so short-lived hooks never block an agent.

Hooks run inside the agent's tool loop. Writing one small JSON file and exiting
is faster and more robust than connecting to a long-running daemon.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


class Spool:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.directory = home / "events"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def emit(self, source: str, payload: dict[str, Any]) -> Path:
        entry = {
            "source": source,
            "received_at": time.time(),
            "payload": payload,
        }
        name = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
        path = self.directory / name
        temp = path.with_suffix(".tmp")
        data = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        temp.write_text(data, encoding="utf-8")
        os.replace(temp, path)
        return path

    def purge(self) -> int:
        count = 0
        try:
            paths = list(self.directory.glob("*.json"))
        except OSError:
            return 0
        for path in paths:
            try:
                path.unlink()
                count += 1
            except OSError:
                continue
        return count


class SpoolWatcher:
    def __init__(
        self,
        home: Path,
        callback: Callable[[str, dict[str, Any]], None],
        poll_s: float = 0.15,
        sources: set[str] | None = None,
    ) -> None:
        self.spool = Spool(home)
        self._callback = callback
        self._poll_s = poll_s
        self._sources = sources
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reezyn-spool", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def poll_once(self) -> None:
        now = time.time()
        try:
            paths = sorted(self.spool.directory.glob("*.json"))
        except OSError:
            return
        for path in paths:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                path.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            if not isinstance(raw, dict):
                continue
            received = raw.get("received_at")
            if isinstance(received, (int, float)) and now - received > 120:
                continue
            source = raw.get("source")
            payload = raw.get("payload")
            if not isinstance(source, str) or not isinstance(payload, dict):
                continue
            if self._sources is not None and source not in self._sources:
                continue
            try:
                self._callback(source, payload)
            except Exception:
                continue

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._poll_s)
