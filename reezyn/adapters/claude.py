"""Claude Code adapter.

Claude Code hooks give us the tool call the moment it happens and point at the
session's JSONL transcript. Reading the last assistant block from that
transcript recovers the reasoning that led to the call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..events import Event
from ..transcripts import JsonlTail, read_jsonl_tail

# Enough to include the last few turns without re-reading a long session.
INITIAL_TAIL_BYTES = 512 * 1024


class ClaudeHookAdapter:
    def __init__(self) -> None:
        self._transcripts = JsonlTail(initial_tail_bytes=INITIAL_TAIL_BYTES)

    def handle(self, payload: dict[str, Any]) -> list[Event]:
        session_id = str(payload.get("session_id") or "")
        project = str(payload.get("cwd") or "")
        event_name = payload.get("hook_event_name")

        if event_name == "UserPromptSubmit":
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                return []
            return [
                Event(
                    agent="claude",
                    kind="prompt",
                    text=prompt,
                    session_id=session_id,
                    project=project,
                )
            ]

        if event_name == "Stop":
            # The turn is over: this is what opens the mic for an answer.
            transcript_path = str(payload.get("transcript_path") or "")
            text = ""
            if transcript_path:
                text = self._final_text(Path(transcript_path))
            return [
                Event(
                    agent="claude",
                    kind="closing",
                    text=text,
                    session_id=session_id,
                    project=project,
                )
            ]

        if event_name == "Notification":
            message = str(payload.get("message") or payload.get("title") or "").strip()
            if not message or not _mentions_permission(message):
                return []
            return [
                Event(
                    agent="claude",
                    kind="permission",
                    text=message,
                    session_id=session_id,
                    project=project,
                )
            ]

        if event_name != "PreToolUse":
            return []

        tool_name = str(payload.get("tool_name") or "").strip()
        if not tool_name:
            return []

        transcript_path = str(payload.get("transcript_path") or "")
        reasoning = ""
        if transcript_path:
            reasoning = self._latest_reasoning(Path(transcript_path))

        if reasoning:
            return [
                Event(
                    agent="claude",
                    kind="reasoning",
                    text=reasoning,
                    session_id=session_id,
                    project=project,
                    tool_name=tool_name,
                    tool_input=_dict(payload.get("tool_input")),
                )
            ]

        return [
            Event(
                agent="claude",
                kind="tool",
                session_id=session_id,
                project=project,
                tool_name=tool_name,
                tool_input=_dict(payload.get("tool_input")),
            )
        ]

    def _latest_reasoning(self, path: Path) -> str:
        records = self._transcripts.read(path)
        for record in reversed(records):
            text = assistant_text(record)
            if text:
                return text
        return ""

    def _final_text(self, path: Path) -> str:
        """The last thing the assistant actually said, not what it was thinking."""

        for record in reversed(read_jsonl_tail(path)):
            text = assistant_final_text(record)
            if text:
                return text
        return ""


def assistant_text(record: dict[str, Any]) -> str:
    """Extract the last useful assistant-authored block from a transcript line."""

    if record.get("isMeta") or record.get("type") != "assistant":
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        message = record
    content = message.get("content")

    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    text_blocks: list[str] = []
    thinking_blocks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            value = block.get("text")
            if isinstance(value, str) and value.strip():
                text_blocks.append(value.strip())
        elif block_type in ("thinking", "reasoning"):
            value = block.get("thinking") or block.get("text") or block.get("reasoning")
            if isinstance(value, str) and value.strip():
                thinking_blocks.append(value.strip())
        elif block_type == "redacted_thinking":
            continue

    if text_blocks:
        return text_blocks[-1]
    if thinking_blocks:
        return thinking_blocks[-1]
    return ""


def assistant_final_text(record: dict[str, Any]) -> str:
    """Only user-facing text; thinking blocks are never spoken as a closing."""

    if record.get("isMeta") or record.get("type") != "assistant":
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        message = record
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        value = block.get("text")
        if isinstance(value, str) and value.strip():
            texts.append(value.strip())
    return texts[-1] if texts else ""


def _mentions_permission(message: str) -> bool:
    lowered = message.lower()
    return any(
        word in lowered for word in ("permission", "approve", "approval", "allow")
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
