"""Gemini CLI adapter.

Gemini CLI exposes BeforeTool hooks with the tool input and a transcript path,
and a BeforeAgent hook carrying the prompt the developer just submitted. The
transcript schema is allowed to drift, so the extractor walks the common message
shapes and prefers explicit thought/reasoning fields.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..events import Event
from ..transcripts import parse_json_or_jsonl

ASSISTANT_ROLES = {"gemini", "model", "assistant", "agent"}
_MESSAGE_KEYS = ("messages", "history", "turns", "conversation", "chat", "entries")


class GeminiHookAdapter:
    def __init__(self) -> None:
        self._cache: dict[Path, tuple[float, int, str]] = {}

    def handle(self, payload: dict[str, Any]) -> list[Event]:
        event_name = str(payload.get("hook_event_name") or "")
        session_id = str(payload.get("session_id") or "")
        project = str(payload.get("cwd") or "")

        if event_name == "BeforeAgent":
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                return []
            return [
                Event(
                    agent="gemini",
                    kind="prompt",
                    text=prompt,
                    session_id=session_id,
                    project=project,
                )
            ]

        tool_name = str(payload.get("tool_name") or "").strip()
        if event_name and event_name != "BeforeTool":
            return []
        if not tool_name:
            return []

        transcript_path = str(payload.get("transcript_path") or "")
        reasoning = ""
        if transcript_path:
            reasoning = self._latest_reasoning(Path(transcript_path))

        if reasoning:
            return [
                Event(
                    agent="gemini",
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
                agent="gemini",
                kind="tool",
                session_id=session_id,
                project=project,
                tool_name=tool_name,
                tool_input=_dict(payload.get("tool_input")),
            )
        ]

    def _latest_reasoning(self, path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            return ""
        cached = self._cache.get(path)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]

        data = parse_json_or_jsonl(path)
        text = latest_assistant_reasoning(data)
        self._cache[path] = (stat.st_mtime, stat.st_size, text)
        return text


def latest_assistant_reasoning(data: Any) -> str:
    entries = _entries(data)
    latest_text = ""
    latest_thought = ""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not _is_assistant(entry):
            continue
        thought = _thought_text(entry)
        if thought:
            latest_thought = thought
            latest_text = ""
            continue
        text = _message_text(entry)
        if text:
            latest_text = text
            latest_thought = ""
    return latest_thought or latest_text


def _entries(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in _MESSAGE_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            return value
    # Some exports wrap the conversation in one more object.
    for value in data.values():
        if isinstance(value, dict):
            nested = _entries(value)
            if nested:
                return nested
    return [data]


def _is_assistant(entry: dict[str, Any]) -> bool:
    for key in ("role", "type", "author", "speaker", "from"):
        value = entry.get(key)
        if isinstance(value, str) and value.lower() in ASSISTANT_ROLES:
            return True
    content = entry.get("content")
    if isinstance(content, dict):
        role = content.get("role")
        return isinstance(role, str) and role.lower() in ASSISTANT_ROLES
    return False


def _thought_text(entry: dict[str, Any]) -> str:
    for key in ("thought", "thinking", "reasoning", "thoughts"):
        value = entry.get(key)
        text = _text_from_value(value, prefer_thought=True)
        if text:
            return text

    content = entry.get("content")
    if isinstance(content, dict):
        for key in ("thought", "thinking", "reasoning", "thoughts", "parts"):
            text = _text_from_value(content.get(key), prefer_thought=True)
            if text:
                return text
    text = _text_from_value(entry.get("parts"), prefer_thought=True)
    return text


def _message_text(entry: dict[str, Any]) -> str:
    for key in ("content", "text", "message", "parts"):
        text = _text_from_value(entry.get(key), prefer_thought=False)
        if text:
            return text
    return ""


def _text_from_value(value: Any, prefer_thought: bool) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        normal: list[str] = []
        thoughts: list[str] = []
        for item in value:
            if isinstance(item, str):
                normal.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    if item.get("thought") is True:
                        thoughts.append(text.strip())
                    else:
                        normal.append(text.strip())
        if prefer_thought and thoughts:
            return " ".join(thoughts).strip()
        return " ".join(normal or thoughts).strip()
    if isinstance(value, dict):
        if prefer_thought and value.get("thought") is True:
            text = value.get("text")
            return text.strip() if isinstance(text, str) else ""
        for key in ("parts", "content", "text"):
            text = _text_from_value(value.get(key), prefer_thought=prefer_thought)
            if text:
                return text
    return ""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
