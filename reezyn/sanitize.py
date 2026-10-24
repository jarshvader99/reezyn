"""Keep only the small part of a hook payload Reezyn actually needs.

The hook process runs inside the agent, so it should be fast and should not
leave a full tool input (which can contain credentials) on disk. The spool is
short-lived, but minimizing it is still the right default.
"""

from __future__ import annotations

import re
from typing import Any

SECRET_KEY = re.compile(
    r"(?:api[_-]?key|secret|token|password|passwd|authorization|credential|cookie)",
    re.IGNORECASE,
)
SECRET_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})"
)
KEEP_INPUT_KEYS = {
    "command",
    "cmd",
    "description",
    "file_path",
    "filePath",
    "filepath",
    "file",
    "filename",
    "file_name",
    "absolute_path",
    "glob",
    "globPattern",
    "notebook_path",
    "path",
    "pattern",
    "relativePath",
    "target_file",
    "targetFile",
    "prompt",
    "query",
    "url",
}


def sanitize_hook_payload(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal payload suitable for the local event spool."""

    out: dict[str, Any] = {"agent": agent}
    for key in ("session_id", "hook_event_name", "transcript_path", "cwd", "tool_use_id"):
        value = payload.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            out[key] = str(value)[:2000]

    tool_name = payload.get("tool_name")
    if isinstance(tool_name, str):
        out["tool_name"] = tool_name[:200]

    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        out["tool_input"] = sanitize_tool_input(tool_input)

    # Gemini's hook uses this spelling.
    if "tool_name" not in out and isinstance(payload.get("tool"), str):
        out["tool_name"] = payload["tool"][:200]

    return out


def sanitize_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in value.items():
        if SECRET_KEY.search(str(key)):
            out[str(key)] = "[redacted]"
            continue
        if key not in KEEP_INPUT_KEYS:
            continue
        out[str(key)] = _clean_value(item)
    return out


def _clean_value(value: Any, depth: int = 0) -> Any:
    if depth > 2:
        return "[…]"
    if isinstance(value, str):
        text = SECRET_VALUE.sub("[redacted]", value)
        return text[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_clean_value(v, depth + 1) for v in value[:8]]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in list(value.items())[:12]:
            if SECRET_KEY.search(str(key)):
                cleaned[str(key)] = "[redacted]"
            else:
                cleaned[str(key)] = _clean_value(item, depth + 1)
        return cleaned
    return str(value)[:200]
