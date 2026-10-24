"""Cursor Agent adapter.

Cursor writes one JSONL file per agent session under
``~/.cursor/projects/<project>/agent-transcripts/<session-id>/<session-id>.jsonl``.
Each line has a top-level role and Anthropic-style content blocks: text,
thinking, tool_use/tool_call, and tool results. There are no per-message
timestamps, so events use the moment they are observed.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..events import Event
from ..sanitize import sanitize_tool_input
from ..transcripts import JsonlTail

TOOL_BLOCK_TYPES = {"tool_use", "tool-use", "tool_call", "tool-call"}
THINKING_BLOCK_TYPES = {"thinking", "reasoning"}


def cursor_projects() -> Path:
    override = os.environ.get("CURSOR_PROJECTS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cursor" / "projects"


class CursorWatcher:
    def __init__(
        self,
        on_event: Callable[[Event], None],
        root: Path | None = None,
        poll_s: float = 0.35,
    ) -> None:
        self._on_event = on_event
        self._root = root or cursor_projects()
        self._poll_s = poll_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._files: dict[Path, JsonlTail] = {}
        self._seen: dict[str, float] = {}
        self._last_scan = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._scan(prime=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reezyn-cursor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def scan_now(self) -> None:
        self._scan(prime=False, force=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._scan(prime=False)
            self._stop.wait(self._poll_s)

    def _scan(self, prime: bool, force: bool = False) -> None:
        now = time.monotonic()
        if prime or force or now - self._last_scan >= 2.5:
            self._discover(prime=prime)
            self._last_scan = now

        for path, tail in list(self._files.items()):
            try:
                records = tail.read(path)
            except Exception:
                continue
            for record in records:
                for event in parse_cursor_record(record, _session_id(path, record)):
                    seen_at = self._seen.get(event.fingerprint)
                    if seen_at is not None and time.monotonic() - seen_at < 10:
                        continue
                    self._seen[event.fingerprint] = time.monotonic()
                    try:
                        self._on_event(event)
                    except Exception:
                        pass
            if len(self._seen) > 5000:
                self._seen.clear()

    def _discover(self, prime: bool) -> None:
        if not self._root.exists():
            return
        try:
            paths = [
                path
                for path in self._root.rglob("*.jsonl")
                if "agent-transcripts" in path.parts
            ]
        except OSError:
            return
        current = set(paths)
        for path in paths:
            if path in self._files:
                continue
            tail = JsonlTail()
            if prime:
                tail.prime(path)
            self._files[path] = tail
        for path in list(self._files):
            if path not in current:
                self._files.pop(path, None)
        if len(self._files) > 200:
            for path in sorted(self._files, key=_mtime)[: len(self._files) - 200]:
                self._files.pop(path, None)


def parse_cursor_record(
    record: dict[str, Any], session_id: str, now: float | None = None
) -> list[Event]:
    role = record.get("role")
    if not isinstance(role, str):
        role = record.get("type")
    role = role.lower() if isinstance(role, str) else ""
    if role not in ("assistant", "model"):
        return []

    ts = _record_ts(record) or (time.time() if now is None else now)
    message = record.get("message")
    content: Any = message.get("content") if isinstance(message, dict) else record.get("content")

    if isinstance(content, str):
        # A bare string is usually a final answer, not pre-tool reasoning.
        return []
    if not isinstance(content, list):
        return []

    reasoning: list[str] = []
    texts: list[str] = []
    tools: list[tuple[str, dict[str, Any]]] = []

    for block in content:
        if isinstance(block, str):
            texts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "").lower()
        if kind in THINKING_BLOCK_TYPES:
            value = block.get("thinking") or block.get("text") or block.get("reasoning")
            if isinstance(value, str) and value.strip():
                reasoning.append(value.strip())
        elif kind == "text":
            value = block.get("text")
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())
        elif kind in TOOL_BLOCK_TYPES:
            name = block.get("name") or block.get("tool") or "tool"
            raw_input = block.get("input")
            if raw_input is None:
                raw_input = block.get("args")
            tools.append((str(name), _tool_input(raw_input)))
        # tool_result and unknown blocks are not spoken.

    events: list[Event] = []
    for text in reasoning:
        events.append(
            Event(
                agent="cursor",
                kind="reasoning",
                text=text,
                session_id=session_id,
                ts=ts,
            )
        )
    for name, tool_input in tools:
        events.append(
            Event(
                agent="cursor",
                kind="tool",
                session_id=session_id,
                tool_name=name,
                tool_input=tool_input,
                ts=ts,
            )
        )

    # Assistant commentary is useful when there is no explicit thinking block
    # and the message goes on to call a tool. A text-only message is normally
    # the final answer and should not be read back to the user.
    if not reasoning and tools and texts:
        events.insert(
            0,
            Event(
                agent="cursor",
                kind="reasoning",
                text=texts[-1],
                session_id=session_id,
                ts=ts,
            ),
        )
    return events


def _tool_input(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return sanitize_tool_input(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"input": value[:500]}
        if isinstance(parsed, dict):
            return sanitize_tool_input(parsed)
    return {}


def _session_id(path: Path, record: dict[str, Any]) -> str:
    for key in ("session_id", "sessionId", "conversation_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return path.stem


def _record_ts(record: dict[str, Any]) -> float | None:
    for key in ("timestamp", "timestamp_ms", "created_at", "createdAt"):
        value = record.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            return number / 1000.0 if number > 10_000_000_000 else number
    return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0
