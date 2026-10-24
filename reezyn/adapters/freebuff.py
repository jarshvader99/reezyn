"""Freebuff adapter.

Freebuff keeps each chat as a JSON snapshot at
``~/.config/manicode/projects/<project>/chats/<chat>/chat-messages.json``.
Assistant messages carry ordered blocks: reasoning text, user-facing
commentary, and tool calls. The snapshot is rewritten as a turn advances rather
than appended to, so the watcher re-reads a chat only when its size or mtime
changes and emits only blocks it has not seen before.

A trailing reasoning block is held for one poll. While a turn is streaming the
last block can still be growing; holding it means a sentence is spoken once,
complete, and in the same order as the tool calls that follow it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..events import Event
from ..sanitize import sanitize_tool_input

REASONING_TEXT_TYPES = {"reasoning", "thinking", "thought", "analysis"}
TOOL_BLOCK_TYPES = {"tool", "tool_call", "tool-call", "tool_use", "tool-use"}
MAX_CHAT_BYTES = 64 * 1024 * 1024
MAX_TRACKED_CHATS = 40
MAX_REMEMBERED_THOUGHTS = 400


def freebuff_projects() -> Path:
    override = os.environ.get("FREEBUFF_PROJECTS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "manicode" / "projects"


@dataclass(slots=True)
class _Block:
    key: str
    event: Event
    text: bool = False
    commentary: bool = False


@dataclass(slots=True)
class _ChatState:
    signature: tuple[int, int] | None = None
    seen: set[str] = field(default_factory=set)
    pending: dict[str, _Block] = field(default_factory=dict)
    thoughts: deque[str] = field(default_factory=deque)
    thought_set: set[str] = field(default_factory=set)


class FreebuffWatcher:
    def __init__(
        self,
        on_event: Callable[[Event], None],
        root: Path | None = None,
        poll_s: float = 0.35,
    ) -> None:
        self._on_event = on_event
        self._root = root or freebuff_projects()
        self._poll_s = poll_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._chats: dict[Path, _ChatState] = {}
        self._last_scan = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._scan(prime=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reezyn-freebuff", daemon=True)
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

        for path, state in list(self._chats.items()):
            signature = _signature(path)
            if signature is None:
                continue
            if signature != state.signature:
                self._read(path, state, signature)
            elif state.pending:
                # The snapshot stopped changing, so a held reasoning block is
                # as complete as it is going to get.
                self._flush(state)

    def _discover(self, prime: bool) -> None:
        # Keep the newest chats; old projects on this machine can hold a lot of
        # history that no running agent will touch again.
        newest = sorted(_chat_files(self._root), key=_mtime, reverse=True)[:MAX_TRACKED_CHATS]
        current = set(newest)
        for path in current:
            if path in self._chats:
                continue
            state = _ChatState()
            self._chats[path] = state
            if prime:
                signature = _signature(path)
                if signature is not None:
                    self._prime(path, state, signature)
        for path in list(self._chats):
            if path not in current:
                self._chats.pop(path, None)

    def _prime(self, path: Path, state: _ChatState, signature: tuple[int, int]) -> None:
        """Remember existing blocks so a restart does not replay old thoughts."""

        if signature[0] > MAX_CHAT_BYTES:
            state.signature = signature
            return
        blocks = _load_blocks(path)
        if blocks is None:
            return
        for block in blocks:
            state.seen.add(block.key)
        state.signature = signature

    def _read(self, path: Path, state: _ChatState, signature: tuple[int, int]) -> None:
        if signature[0] > MAX_CHAT_BYTES:
            state.signature = signature
            return
        blocks = _load_blocks(path)
        if blocks is None:
            # Most likely a snapshot caught mid-write; try again next poll.
            return
        state.signature = signature
        if not blocks:
            return

        held = {block.key: block for block in blocks}
        last_key = blocks[-1].key
        for key, block in list(state.pending.items()):
            current = held.get(key)
            if current is None:
                # The block vanished (compaction); speak what we last saw.
                self._emit_block(state, block)
            elif current.event.text == block.event.text:
                if key != last_key:
                    # Another block follows, so this thought is complete.
                    self._emit_block(state, block)
            else:
                # Still growing; wait for it to settle.
                state.pending[key] = current

        for position, block in enumerate(blocks):
            if block.key in state.seen:
                continue
            if block.text:
                if block.event.fingerprint in state.thought_set:
                    state.seen.add(block.key)
                    continue
                if not block.commentary and position == len(blocks) - 1:
                    # Still at the end: it may be streaming. Hold it.
                    state.pending[block.key] = block
                    continue
            self._emit_block(state, block)

    def _flush(self, state: _ChatState) -> None:
        for block in list(state.pending.values()):
            self._emit_block(state, block)

    def _emit_block(self, state: _ChatState, block: _Block) -> None:
        state.pending.pop(block.key, None)
        state.seen.add(block.key)
        if block.text:
            fingerprint = block.event.fingerprint
            if fingerprint in state.thought_set:
                return
            state.thoughts.append(fingerprint)
            state.thought_set.add(fingerprint)
            while len(state.thoughts) > MAX_REMEMBERED_THOUGHTS:
                state.thought_set.discard(state.thoughts.popleft())
        try:
            self._on_event(block.event)
        except Exception:
            pass


def _chat_files(root: Path) -> Iterator[Path]:
    try:
        projects = list(root.iterdir())
    except OSError:
        return
    for project in projects:
        chats = project / "chats"
        try:
            chat_dirs = list(chats.iterdir())
        except OSError:
            continue
        for chat in chat_dirs:
            path = chat / "chat-messages.json"
            try:
                if path.is_file():
                    yield path
            except OSError:
                continue


def _load_blocks(path: Path) -> list[_Block] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        messages = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(messages, list):
        return None
    return extract_freebuff_blocks(messages, _session_id(path))


def parse_freebuff_messages(
    messages: list[Any], session_id: str, now: float | None = None
) -> list[Event]:
    return [block.event for block in extract_freebuff_blocks(messages, session_id, now)]


def extract_freebuff_blocks(
    messages: list[Any], session_id: str, now: float | None = None
) -> list[_Block]:
    observed = time.time() if now is None else now
    out: list[_Block] = []

    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        blocks = message.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            continue
        variant = str(message.get("variant") or "").lower()
        if variant and variant not in ("ai", "assistant", "model"):
            continue

        message_id = str(message.get("id") or f"message-{message_index}")
        ts = _record_ts(message) or observed
        local: list[_Block] = []
        reasoning = False
        tool = False
        commentary: list[int] = []

        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "").lower()
            if kind == "text":
                content = block.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                text_type = str(block.get("textType") or "text").lower()
                thinking_id = block.get("thinkingId")
                key = (
                    str(thinking_id)
                    if thinking_id
                    else f"{message_id}:text:{index}"
                )
                event = Event(
                    agent="freebuff",
                    kind="reasoning",
                    text=content.strip(),
                    session_id=session_id,
                    ts=ts,
                )
                if text_type in REASONING_TEXT_TYPES:
                    reasoning = True
                    local.append(_Block(key=key, event=event, text=True))
                else:
                    commentary.append(len(local))
                    local.append(_Block(key=key, event=event, text=True, commentary=True))
            elif kind in TOOL_BLOCK_TYPES:
                name = (
                    block.get("toolName")
                    or block.get("tool")
                    or block.get("name")
                    or "tool"
                )
                tool_call_id = block.get("toolCallId")
                key = (
                    str(tool_call_id)
                    if tool_call_id
                    else f"{message_id}:tool:{index}"
                )
                tool = True
                local.append(
                    _Block(
                        key=key,
                        event=Event(
                            agent="freebuff",
                            kind="tool",
                            session_id=session_id,
                            tool_name=str(name),
                            tool_input=_tool_input(block.get("input")),
                            ts=ts,
                        ),
                    )
                )

        # User-facing text is normally the final answer, which the user is
        # already reading. Keep only commentary that introduced a tool call,
        # and only when there was no explicit reasoning block.
        if commentary and (reasoning or not tool):
            local = [block for block in local if not block.commentary]
        elif commentary:
            keep = commentary[-1]
            local = [
                block
                for index, block in enumerate(local)
                if not block.commentary or index == keep
            ]
        out.extend(local)

    return out


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


def _session_id(path: Path) -> str:
    chat = path.parent.name
    try:
        project = path.parents[2].name
    except IndexError:
        project = ""
    return f"{project}/{chat}" if project else chat


def _record_ts(message: dict[str, Any]) -> float | None:
    for key in ("timestamp_ms", "timestampMs", "completionTime", "createdAt"):
        value = message.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            return number / 1000.0 if number > 10_000_000_000 else number
    return None


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
