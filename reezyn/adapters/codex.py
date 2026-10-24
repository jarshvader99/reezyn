"""Codex CLI adapter.

Codex does not need an install step. It appends a JSONL rollout under
``$CODEX_HOME/sessions/YYYY/MM/DD`` and records reasoning summaries, assistant
messages, and tool calls as they happen. We tail new rollout files from the
moment Reezyn starts.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..events import Event, make_fingerprint
from ..transcripts import JsonlTail

UUID = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


class CodexWatcher:
    def __init__(
        self,
        on_event: Callable[[Event], None],
        root: Path | None = None,
        poll_s: float = 0.3,
    ) -> None:
        self._on_event = on_event
        self._root = root or (codex_home() / "sessions")
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
        self._thread = threading.Thread(target=self._run, name="reezyn-codex", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def poll_once(self) -> None:
        self._scan(prime=False)

    def scan_now(self) -> None:
        """Force discovery and ingestion; useful for tests and one-shot checks."""

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
                session_id = _session_id(path, record)
                for event in parse_rollout_record(record, session_id, path):
                    key = event.fingerprint
                    seen_at = self._seen.get(key)
                    if seen_at is not None and time.monotonic() - seen_at < 10:
                        continue
                    self._seen[key] = time.monotonic()
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
            paths = list(self._root.rglob("rollout-*.jsonl"))
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

        # Forget files that disappeared (rotation/archive), but keep the map bounded.
        for path in list(self._files):
            if path not in current:
                self._files.pop(path, None)
        if len(self._files) > 200:
            for path in sorted(self._files, key=lambda p: _mtime(p))[: len(self._files) - 200]:
                self._files.pop(path, None)


def parse_rollout_record(
    record: dict[str, Any], session_id: str, path: Path | None = None
) -> list[Event]:
    """Turn one rollout line into zero or more normalized events."""

    record_type = record.get("type")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = record.get("item")
    if not isinstance(payload, dict):
        payload = record

    ts = _timestamp(record.get("timestamp")) or time.time()

    if record_type == "session_meta":
        return []

    if record_type == "response_item" or payload.get("type") in _RESPONSE_TYPES:
        return _response_item_events(payload, session_id, ts)

    if record_type == "event_msg":
        return _event_msg_events(payload, session_id, ts)

    return []


_RESPONSE_TYPES = {
    "reasoning",
    "message",
    "function_call",
    "custom_tool_call",
    "local_shell_call",
}


def _response_item_events(payload: dict[str, Any], session_id: str, ts: float) -> list[Event]:
    item_type = payload.get("type")

    if item_type == "reasoning":
        text = _reasoning_text(payload)
        if not text:
            return []
        return [
            Event(
                agent="codex",
                kind="reasoning",
                text=text,
                session_id=session_id,
                ts=ts,
            )
        ]

    if item_type == "message":
        if payload.get("role") != "assistant":
            return []
        if payload.get("phase") not in ("commentary",):
            return []
        text = _message_text(payload)
        if not text:
            return []
        return [
            Event(
                agent="codex",
                kind="reasoning",
                text=text,
                session_id=session_id,
                ts=ts,
            )
        ]

    if item_type in ("function_call", "custom_tool_call"):
        name = str(payload.get("name") or "tool")
        arguments = payload.get("arguments")
        if arguments is None:
            arguments = payload.get("input")
        tool_input = _json_object(arguments)
        return [
            Event(
                agent="codex",
                kind="tool",
                session_id=session_id,
                tool_name=name,
                tool_input=tool_input,
                ts=ts,
            )
        ]

    if item_type == "local_shell_call":
        action = payload.get("action")
        tool_input: dict[str, Any] = {}
        if isinstance(action, dict):
            command = action.get("command")
            if isinstance(command, list):
                tool_input["command"] = " ".join(str(part) for part in command)
            elif isinstance(command, str):
                tool_input["command"] = command
        return [
            Event(
                agent="codex",
                kind="tool",
                session_id=session_id,
                tool_name="shell",
                tool_input=tool_input,
                ts=ts,
            )
        ]

    return []


def _event_msg_events(payload: dict[str, Any], session_id: str, ts: float) -> list[Event]:
    event_type = payload.get("type")

    if event_type in ("agent_reasoning", "agent_reasoning_raw_content"):
        text = str(payload.get("text") or "").strip()
        if not text:
            return []
        return [
            Event(
                agent="codex",
                kind="reasoning",
                text=text,
                session_id=session_id,
                ts=ts,
            )
        ]

    if event_type == "exec_command_begin":
        command = payload.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        return [
            Event(
                agent="codex",
                kind="tool",
                session_id=session_id,
                tool_name="shell",
                tool_input={"command": str(command or "")},
                ts=ts,
            )
        ]

    if event_type == "patch_apply_begin":
        changes = payload.get("changes")
        path = ""
        if isinstance(changes, dict) and changes:
            path = str(next(iter(changes)))
        return [
            Event(
                agent="codex",
                kind="tool",
                session_id=session_id,
                tool_name="apply_patch",
                tool_input={"path": path},
                ts=ts,
            )
        ]

    if event_type == "mcp_tool_call_begin":
        invocation = payload.get("invocation")
        name = "mcp tool"
        if isinstance(invocation, dict):
            name = str(invocation.get("tool") or name)
        return [
            Event(
                agent="codex",
                kind="tool",
                session_id=session_id,
                tool_name=name,
                ts=ts,
            )
        ]

    if event_type == "web_search_begin":
        return [
            Event(
                agent="codex",
                kind="tool",
                session_id=session_id,
                tool_name="web_search",
                ts=ts,
            )
        ]

    return []


def _reasoning_text(payload: dict[str, Any]) -> str:
    summary = payload.get("summary")
    text = _content_list_text(summary)
    if text:
        return text
    content = payload.get("content")
    text = _content_list_text(content)
    if text:
        return text
    direct = payload.get("text")
    return direct.strip() if isinstance(direct, str) else ""


def _content_list_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text") or item.get("summary_text") or item.get("reasoning_text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return " ".join(parts).strip()


def _message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts).strip()
    text = payload.get("text")
    return text.strip() if isinstance(text, str) else ""


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"input": value[:500]}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _session_id(path: Path, record: dict[str, Any]) -> str:
    if record.get("type") == "session_meta":
        payload = record.get("payload")
        if isinstance(payload, dict):
            value = payload.get("id") or payload.get("session_id")
            if isinstance(value, str) and value:
                return value
    match = UUID.search(path.name)
    return match.group(1) if match else path.stem


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number / 1000.0 if number > 10_000_000_000 else number
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp()
        except ValueError:
            return None
    return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0
