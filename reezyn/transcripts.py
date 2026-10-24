"""Small, tolerant incremental readers for agent session files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonlTail:
    """Read complete JSONL records appended to a file, one call at a time."""

    def __init__(self, initial_tail_bytes: int = 0, start_at_end: bool = False) -> None:
        self.initial_tail_bytes = max(0, initial_tail_bytes)
        self.start_at_end = start_at_end
        self._offsets: dict[Path, int] = {}
        self._first_read: set[Path] = set()

    def prime(self, path: Path) -> None:
        """Ignore everything currently in a file and only read future appends."""

        try:
            self._offsets[path] = path.stat().st_size
        except OSError:
            self._offsets[path] = 0
        self._first_read.add(path)

    def read(self, path: Path) -> list[dict[str, Any]]:
        try:
            size = path.stat().st_size
        except OSError:
            return []

        offset = self._offsets.get(path)
        if offset is None:
            if self.start_at_end:
                offset = size
            elif self.initial_tail_bytes:
                offset = max(0, size - self.initial_tail_bytes)
            else:
                offset = 0
            self._first_read.add(path)

        if size < offset:
            offset = 0

        if size == offset:
            return []

        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read()
        except OSError:
            return []

        if not data:
            return []

        new_offset = offset + len(data)
        if not data.endswith(b"\n"):
            last_newline = data.rfind(b"\n")
            if last_newline < 0:
                # Wait for the writer to finish the line.
                return []
            data = data[: last_newline + 1]
            new_offset = offset + len(data)

        first = True
        records: list[dict[str, Any]] = []
        for raw_line in data.splitlines():
            line = raw_line.decode("utf-8", "replace").strip()
            if first and path in self._first_read and offset > 0:
                first = False
                if line and not self._starts_at_record_boundary(path, offset):
                    continue
            first = False
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)

        self._offsets[path] = new_offset
        self._first_read.discard(path)
        return records

    @staticmethod
    def _starts_at_record_boundary(path: Path, offset: int) -> bool:
        try:
            with path.open("rb") as handle:
                handle.seek(offset - 1)
                return handle.read(1) == b"\n"
        except OSError:
            return False


def read_jsonl_tail(path: Path, max_bytes: int = 262_144) -> list[dict[str, Any]]:
    """The last stretch of a JSONL file, for reading a final answer.

    This is a one-shot read for turn-end hooks, not an incremental tail, so it
    skips a partial first line at the window boundary.
    """

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            data = handle.read().decode("utf-8", "replace")
    except OSError:
        return []

    lines = data.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    records: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def parse_json_or_jsonl(path: Path, max_bytes: int = 8_000_000) -> Any:
    """Read a Gemini-style session file, accepting JSON or JSONL."""

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            data = handle.read().decode("utf-8", "replace")
    except OSError:
        return None

    text = data.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    records: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records or None
