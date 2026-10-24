"""OpenCode adapter.

OpenCode stores conversations in a per-user SQLite database rather than a
JSONL file. Current builds write one ``session_message`` row per model step;
older builds use ``message`` plus ``part`` rows. Both are read-only, and recent
assistant rows are re-scanned for a short window so streamed content and tool
state updates are picked up without replaying the whole database.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..events import Event
from ..sanitize import sanitize_tool_input

log = logging.getLogger("reezyn")

RECENT_WINDOW_MS = 180_000
SEEN_TTL_S = 900.0

EDIT_TOOL_NAMES = {
    "apply_patch",
    "delete_file",
    "edit",
    "edit_file",
    "edit_notebook",
    "multiedit",
    "notebookedit",
    "patch",
    "replace",
    "search_replace",
    "write",
    "write_file",
}
FILE_PATH_KEYS = (
    "path",
    "file_path",
    "filePath",
    "filepath",
    "file",
    "filename",
    "target_file",
    "targetFile",
    "relativePath",
    "absolute_path",
)


def opencode_home() -> Path:
    override = os.environ.get("OPENCODE_DATA")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "opencode"
    return Path.home() / ".local" / "share" / "opencode"


def opencode_db_path() -> Path:
    return opencode_home() / "opencode.db"


class OpenCodeWatcher:
    def __init__(
        self,
        on_event: Callable[[Event], None],
        db_path: Path | None = None,
        poll_s: float = 0.4,
        window_ms: int = RECENT_WINDOW_MS,
    ) -> None:
        self._on_event = on_event
        self._db_path = db_path or opencode_db_path()
        self._poll_s = poll_s
        self._window_ms = window_ms
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reezyn-opencode", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def scan_now(self) -> None:
        self._scan()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._scan()
            self._stop.wait(self._poll_s)

    def _scan(self) -> None:
        if not self._db_path.is_file():
            return
        with self._lock:
            now_ms = int(time.time() * 1000)
            cutoff = now_ms - self._window_ms
            try:
                connection = self._connect()
            except sqlite3.Error:
                return
            try:
                tables = _tables(connection)
                if "session_message" in tables:
                    rows = _current_rows(connection, cutoff)
                    if rows:
                        for row in rows:
                            self._emit_current(row)
                        self._prune_seen()
                        return
                if "part" in tables and "message" in tables:
                    for row in _legacy_rows(connection, cutoff):
                        self._emit_legacy(row)
            except (sqlite3.Error, OSError, ValueError):
                log.debug("OpenCode scan failed", exc_info=True)
            finally:
                connection.close()
            self._prune_seen()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self._db_path}?mode=ro",
            uri=True,
            timeout=0.5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 500")
        return connection

    def _emit_current(self, row: sqlite3.Row) -> None:
        try:
            data = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        session_id = str(row["session_id"] or data.get("sessionID") or "")
        message_id = str(row["id"] or "")
        message_time = _message_time(data, row["time_created"])
        content = data.get("content")
        if not isinstance(content, list):
            return

        reasoning: list[tuple[str, Event]] = []
        tools: list[tuple[str, Event]] = []
        texts: list[tuple[str, Event]] = []
        has_tools = False
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            ts = _block_time(block, message_time)
            if kind == "reasoning":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    event = Event(
                        agent="opencode",
                        kind="reasoning",
                        text=text.strip(),
                        session_id=session_id,
                        ts=ts,
                    )
                    reasoning.append((f"{message_id}:{index}:reasoning", event))
            elif kind == "tool":
                has_tools = True
                name = str(block.get("name") or "tool")
                state = block.get("state")
                state = state if isinstance(state, dict) else {}
                raw_input = state.get("input")
                tool_input = sanitize_tool_input(raw_input) if isinstance(raw_input, dict) else {}
                status = state.get("status")
                if not tool_input and status in (None, "running"):
                    continue
                tool_input = _with_metadata_path(tool_input, state)
                if _is_edit_tool(name) and not _has_file_path(tool_input):
                    # A streamed edit can briefly expose only its replacement
                    # text. Wait for OpenCode to report the target file instead
                    # of narrating "editing the files".
                    continue
                event = Event(
                    agent="opencode",
                    kind="tool",
                    session_id=session_id,
                    tool_name=name,
                    tool_input=tool_input,
                    ts=ts,
                )
                tools.append((f"{message_id}:{index}:tool", event))
            elif kind == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    event = Event(
                        agent="opencode",
                        kind="reasoning",
                        text=text.strip(),
                        session_id=session_id,
                        ts=ts,
                    )
                    texts.append((f"{message_id}:{index}:text", event))

        events: list[tuple[str, Event]] = []
        events.extend(reasoning)
        if not reasoning and has_tools and texts:
            events.append(texts[-1])
        events.extend(tools)
        self._submit(events)

    def _emit_legacy(self, row: sqlite3.Row) -> None:
        try:
            data = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        kind = data.get("type")
        session_id = str(row["session_id"] or "")
        part_id = str(row["id"] or "")
        ts = _ms_to_seconds(row["time_created"])
        if kind == "reasoning":
            text = data.get("text")
            if isinstance(text, str) and text.strip():
                self._submit(
                    [
                        (
                            f"{part_id}:reasoning",
                            Event(
                                agent="opencode",
                                kind="reasoning",
                                text=text.strip(),
                                session_id=session_id,
                                ts=ts,
                            ),
                        )
                    ]
                )
        elif kind == "tool":
            name = str(data.get("tool") or data.get("name") or "tool")
            state = data.get("state")
            state = state if isinstance(state, dict) else {}
            raw_input = state.get("input")
            tool_input = sanitize_tool_input(raw_input) if isinstance(raw_input, dict) else {}
            tool_input = _with_metadata_path(tool_input, data)
            if _is_edit_tool(name) and not _has_file_path(tool_input):
                return
            self._submit(
                [
                    (
                        f"{part_id}:tool",
                        Event(
                            agent="opencode",
                            kind="tool",
                            session_id=session_id,
                            tool_name=name,
                            tool_input=tool_input,
                            ts=ts,
                        ),
                    )
                ]
            )

    def _submit(self, entries: list[tuple[str, Event]]) -> None:
        now = time.monotonic()
        for key, event in entries:
            if key in self._seen:
                continue
            self._seen[key] = now
            try:
                self._on_event(event)
            except Exception:
                pass

    def _prune_seen(self) -> None:
        if len(self._seen) < 5000:
            return
        cutoff = time.monotonic() - SEEN_TTL_S
        for key, seen_at in list(self._seen.items()):
            if seen_at < cutoff:
                del self._seen[key]


def _is_edit_tool(name: str) -> bool:
    return name.lower() in EDIT_TOOL_NAMES


def _has_file_path(tool_input: dict[str, Any]) -> bool:
    return any(
        isinstance(tool_input.get(key), str) and tool_input[key].strip()
        for key in FILE_PATH_KEYS
    )


def _with_metadata_path(
    tool_input: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Use OpenCode's completed file list when the input only has edit text."""

    if _has_file_path(tool_input):
        return tool_input
    metadata = state.get("metadata")
    files = metadata.get("files") if isinstance(metadata, dict) else None
    if not isinstance(files, list):
        return tool_input
    for item in files:
        if not isinstance(item, dict):
            continue
        for key in ("file", "path", "filename"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                enriched = dict(tool_input)
                enriched["path"] = value.strip()
                return enriched
    return tool_input


def accessible(db_path: Path | None = None) -> bool:
    path = db_path or opencode_db_path()
    return path.is_file()


def _tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _current_rows(connection: sqlite3.Connection, cutoff_ms: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT id, session_id, time_created, data
        FROM session_message
        WHERE type = 'assistant' AND time_created >= ?
        ORDER BY time_created ASC, id ASC
        """,
        (cutoff_ms,),
    ).fetchall()


def _legacy_rows(connection: sqlite3.Connection, cutoff_ms: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT p.id, p.session_id, p.time_created, p.data
        FROM part p
        JOIN message m ON m.id = p.message_id
        WHERE m.time_created >= ?
          AND json_valid(m.data)
          AND json_extract(m.data, '$.role') = 'assistant'
        ORDER BY p.time_created ASC, p.id ASC
        """,
        (cutoff_ms,),
    ).fetchall()


def _message_time(data: dict[str, Any], fallback_ms: Any) -> int:
    value = data.get("time")
    if isinstance(value, dict):
        for key in ("created", "streamed", "completed"):
            number = value.get(key)
            if isinstance(number, (int, float)) and number > 0:
                return int(number)
    if isinstance(fallback_ms, (int, float)):
        return int(fallback_ms)
    return int(time.time() * 1000)


def _block_time(block: dict[str, Any], fallback_ms: int) -> float:
    value = block.get("time")
    if isinstance(value, dict):
        for key in ("created", "ran", "completed"):
            number = value.get(key)
            if isinstance(number, (int, float)) and number > 0:
                return _ms_to_seconds(number)
    return _ms_to_seconds(fallback_ms)


def _ms_to_seconds(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        number = float(value)
        return number / 1000.0 if number > 10_000_000_000 else number
    return time.time()
