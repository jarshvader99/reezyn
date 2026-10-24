"""The small normalized event format every adapter produces."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal["reasoning", "tool", "prompt"]


@dataclass(slots=True)
class Event:
    agent: str
    kind: EventKind
    text: str = ""
    session_id: str = ""
    # The working directory (or project name) this event belongs to. It is what
    # makes per-project memory possible.
    project: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = make_fingerprint(
                self.agent,
                self.session_id,
                self.kind,
                self.text if self.kind == "reasoning" else self.tool_name,
                "" if self.kind == "reasoning" else stable_tool_key(self.tool_input),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "kind": self.kind,
            "text": self.text,
            "session_id": self.session_id,
            "project": self.project,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "ts": self.ts,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        kind = data.get("kind")
        if kind not in ("reasoning", "tool", "prompt"):
            kind = "reasoning" if data.get("text") else "tool"
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        return cls(
            agent=str(data.get("agent") or "generic"),
            kind=kind,
            text=str(data.get("text") or ""),
            session_id=str(data.get("session_id") or ""),
            project=str(data.get("project") or ""),
            tool_name=str(data.get("tool_name") or ""),
            tool_input=tool_input,
            ts=_optional_ts(data.get("ts")),
            fingerprint=str(data.get("fingerprint") or ""),
        )


def make_fingerprint(*parts: str) -> str:
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8", "replace"))
    return digest.hexdigest()[:20]


def stable_tool_key(value: Any) -> str:
    """A compact, deterministic representation used only for dedupe."""

    if isinstance(value, dict):
        items = sorted((str(k), stable_tool_key(v)) for k, v in value.items())
        return "{" + ",".join(f"{k}:{v}" for k, v in items) + "}"
    if isinstance(value, list):
        return "[" + ",".join(stable_tool_key(v) for v in value) + "]"
    return str(value)[:300]


def _optional_ts(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Log timestamps are sometimes milliseconds.
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return number
    return time.time()
