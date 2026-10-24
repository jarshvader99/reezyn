from __future__ import annotations

import json
import shlex
import sys

from reezyn.installers import (
    hook_command,
    install_hooks,
    is_installed,
    settings_path,
    uninstall_hooks,
)


def test_source_checkout_hook_uses_the_module(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert hook_command("claude") == \
        f"{shlex.quote(sys.executable)} -m reezyn hook claude"


def test_bundled_app_dispatches_the_hook_directly(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert hook_command("gemini") == f"{shlex.quote(sys.executable)} hook gemini"


def test_existing_bundled_hook_is_recognized(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = settings_path("claude", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/Applications/Reezyn.app/Contents/MacOS/Reezyn hook claude",
                                }
                            ],
                        }
                    ],
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/Applications/Reezyn.app/Contents/MacOS/Reezyn hook claude",
                                }
                            ],
                        }
                    ],
                }
            }
        )
    )
    assert is_installed("claude", system_home=tmp_path)

    # Installing from a different build replaces it instead of adding another.
    install_hooks("claude", system_home=tmp_path)
    settings = json.loads(path.read_text())
    assert len(settings["hooks"]["PreToolUse"]) == 1
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1


def test_claude_install_adds_a_prompt_hook(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    install_hooks("claude", system_home=tmp_path)
    settings = json.loads(settings_path("claude", tmp_path).read_text())
    groups = settings["hooks"]["UserPromptSubmit"]
    assert len(groups) == 1
    hook = groups[0]["hooks"][0]
    assert hook["command"].endswith("hook claude")
    assert hook["async"] is True

    uninstall_hooks("claude", system_home=tmp_path)
    assert "hooks" not in json.loads(settings_path("claude", tmp_path).read_text())
    assert not is_installed("claude", system_home=tmp_path)


def test_tool_only_install_is_reported_for_upgrade(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = settings_path("claude", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{shlex.quote(sys.executable)} -m reezyn hook claude",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    # Without the prompt hook Reezyn cannot recall what was asked, so an
    # older install is reported as incomplete and can be upgraded in place.
    assert not is_installed("claude", system_home=tmp_path)
    install_hooks("claude", system_home=tmp_path)
    assert is_installed("claude", system_home=tmp_path)


def test_claude_install_preserves_existing_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = settings_path("claude", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "echo existing"}],
                        }
                    ]
                },
            }
        )
    )

    result = install_hooks("claude", system_home=tmp_path)
    assert result.changed
    assert result.backup and result.backup.exists()
    assert is_installed("claude", system_home=tmp_path)

    settings = json.loads(path.read_text())
    assert settings["theme"] == "dark"
    groups = settings["hooks"]["PreToolUse"]
    assert len(groups) == 2
    assert groups[0]["hooks"][0]["command"] == "echo existing"
    our_hook = groups[1]["hooks"][0]
    assert "hook claude" in our_hook["command"]
    assert our_hook["async"] is True

    again = install_hooks("claude", system_home=tmp_path)
    assert not again.changed
    assert len(json.loads(path.read_text())["hooks"]["PreToolUse"]) == 2


def test_gemini_install_and_uninstall(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_CONFIG_DIR", raising=False)
    result = install_hooks("gemini", system_home=tmp_path)
    assert result.changed
    settings = json.loads(settings_path("gemini", tmp_path).read_text())
    group = settings["hooks"]["BeforeTool"][0]
    assert group["matcher"] == ".*"
    assert "hook gemini" in group["hooks"][0]["command"]

    removed = uninstall_hooks("gemini", system_home=tmp_path)
    assert removed.changed
    assert "hooks" not in json.loads(settings_path("gemini", tmp_path).read_text())
    assert not is_installed("gemini", system_home=tmp_path)


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    result = install_hooks("claude", system_home=tmp_path, dry_run=True)
    assert result.changed
    assert not settings_path("claude", tmp_path).exists()


def test_invalid_settings_are_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = settings_path("claude", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    try:
        install_hooks("claude", system_home=tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid JSON should be rejected")
    assert path.read_text() == "{not json"
