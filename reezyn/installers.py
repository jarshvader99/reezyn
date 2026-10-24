"""Install Reezyn's hooks into agent settings without replacing user config."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AgentName = Literal["claude", "gemini"]

# Claude gets a prompt hook as well as a tool hook: the prompt is how Reezyn
# learns what the developer asked, so it can recall earlier work when a
# question comes back around.
CLAUDE_HOOK_EVENTS = ("PreToolUse", "UserPromptSubmit", "Stop", "Notification")
# Gemini's BeforeAgent fires after the prompt is submitted and before planning,
# which is the equivalent of Claude's UserPromptSubmit. AfterAgent fires when a
# turn is done, and Notification reports tool-permission prompts.
GEMINI_HOOK_EVENTS = ("BeforeTool", "BeforeAgent", "AfterAgent", "Notification")


@dataclass(slots=True)
class InstallResult:
    agent: str
    path: Path
    changed: bool
    backup: Path | None = None
    settings: dict[str, Any] | None = None


def hook_command(agent: AgentName) -> str:
    # A bundled Reezyn.app is its own interpreter, so it dispatches the hook
    # subcommand directly; a source checkout runs the module with its Python.
    if getattr(sys, "frozen", False):
        return f"{shlex.quote(sys.executable)} hook {agent}"
    return f"{shlex.quote(sys.executable)} -m reezyn hook {agent}"


def settings_path(agent: AgentName, system_home: Path | None = None) -> Path:
    system_home = system_home or Path.home()
    if agent == "claude":
        override = os.environ.get("CLAUDE_CONFIG_DIR")
        if override:
            return Path(override).expanduser() / "settings.json"
        return system_home / ".claude" / "settings.json"
    override = os.environ.get("GEMINI_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / "settings.json"
    return system_home / ".gemini" / "settings.json"


def install_hooks(
    agent: AgentName,
    system_home: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    path = settings_path(agent, system_home)
    settings = _load_settings(path)

    command = hook_command(agent)
    if agent == "claude":
        for event in CLAUDE_HOOK_EVENTS:
            _replace_ours(settings, event, agent)
            _add_group(
                settings,
                event,
                {
                    **({"matcher": "*"} if event == "PreToolUse" else {}),
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "timeout": 5,
                            "async": True,
                        }
                    ],
                },
            )
    else:
        for event in GEMINI_HOOK_EVENTS:
            _replace_ours(settings, event, agent)
            _add_group(
                settings,
                event,
                {
                    **({"matcher": ".*"} if event == "BeforeTool" else {}),
                    "hooks": [
                        {
                            "type": "command",
                            "name": "reezyn",
                            "command": command,
                            "timeout": 5000,
                        }
                    ],
                },
            )

    # A dry run still reports what would be written without touching the file.
    if dry_run:
        return InstallResult(agent=agent, path=path, changed=True, settings=settings)

    original = _read_text(path)
    new_text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    changed = original != new_text
    backup = None
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = path.with_name(path.name + ".reezyn.bak")
            if not backup.exists():
                shutil.copy2(path, backup)
        _atomic_write(path, new_text)
    return InstallResult(
        agent=agent,
        path=path,
        changed=changed,
        backup=backup,
        settings=settings,
    )


def uninstall_hooks(
    agent: AgentName,
    system_home: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    path = settings_path(agent, system_home)
    settings = _load_settings(path)
    events = CLAUDE_HOOK_EVENTS if agent == "claude" else GEMINI_HOOK_EVENTS
    changed = False
    for event in events:
        if _replace_ours(settings, event, agent):
            changed = True
    if dry_run:
        return InstallResult(agent=agent, path=path, changed=changed, settings=settings)
    if changed:
        backup = None
        if path.exists():
            backup = path.with_name(path.name + ".reezyn.bak")
            if not backup.exists():
                shutil.copy2(path, backup)
        _atomic_write(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    return InstallResult(
        agent=agent,
        path=path,
        changed=changed,
        backup=backup,
        settings=settings,
    )


def is_installed(agent: AgentName, system_home: Path | None = None) -> bool:
    path = settings_path(agent, system_home)
    try:
        settings = _load_settings(path)
    except ValueError:
        return False
    events = CLAUDE_HOOK_EVENTS if agent == "claude" else GEMINI_HOOK_EVENTS
    return all(bool(_remove_ours(settings, event, agent, mutate=False)) for event in events)


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return value


def _add_group(settings: dict[str, Any], event: str, group: dict[str, Any]) -> None:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("The existing 'hooks' setting is not an object")
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"The existing hooks.{event} setting is not a list")
    groups.append(group)


def _remove_ours(
    settings: dict[str, Any],
    event: str,
    agent: AgentName,
    mutate: bool = True,
) -> list[Any]:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return []
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return []
    removed: list[Any] = []
    kept: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            kept.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            kept.append(group)
            continue
        ours = [handler for handler in handlers if _is_our_handler(handler, agent)]
        others = [handler for handler in handlers if not _is_our_handler(handler, agent)]
        if not ours:
            kept.append(group)
            continue
        removed.extend(ours)
        if others:
            group = dict(group)
            group["hooks"] = others
            kept.append(group)
    if mutate:
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
            if not hooks:
                settings.pop("hooks", None)
    return removed


def _replace_ours(settings: dict[str, Any], event: str, agent: AgentName) -> bool:
    return bool(_remove_ours(settings, event, agent, mutate=True))


def _is_our_handler(handler: Any, agent: AgentName) -> bool:
    if not isinstance(handler, dict):
        return False
    command = handler.get("command")
    if not isinstance(command, str):
        return False
    return "reezyn" in command.lower() and f"hook {agent}" in command


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _atomic_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp = Path(name)
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
