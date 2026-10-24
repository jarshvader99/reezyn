from __future__ import annotations

from reezyn.menu import _agent_label


def test_agent_labels_are_human_readable():
    assert _agent_label("claude") == "Claude Code"
    assert _agent_label("codex") == "Codex CLI"
    assert _agent_label("gemini") == "Gemini CLI"
    assert _agent_label("cursor") == "Cursor"
    assert _agent_label("opencode") == "OpenCode"
    assert _agent_label("freebuff") == "Freebuff"
    assert _agent_label("simulation") == "Simulation"
    assert _agent_label("custom_agent") == "Custom Agent"
    assert _agent_label("") == ""
