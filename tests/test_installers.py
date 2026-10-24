from __future__ import annotations

import json

from reezyn.installers import (
    install_hooks,
    is_installed,
    settings_path,
    uninstall_hooks,
)


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
