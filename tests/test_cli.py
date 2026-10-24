from __future__ import annotations

import io
import json
from types import SimpleNamespace

from reezyn.cli import main
from reezyn.config import load_config


def test_claude_hook_is_silent_and_spools(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REEZYN_HOME", str(tmp_path))
    payload = {
        "session_id": "s",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": "TOKEN=sk-abcdefghijklmnopqrstuvwxyz npm test",
            "description": "run tests",
        },
        "transcript_path": "/tmp/transcript.jsonl",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert main(["hook", "claude"]) == 0
    assert capsys.readouterr().out == ""

    entries = list((tmp_path / "events").glob("*.json"))
    assert len(entries) == 1
    data = json.loads(entries[0].read_text())
    assert data["source"] == "claude"
    assert data["payload"]["tool_name"] == "Bash"
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in data["payload"]["tool_input"]["command"]


def test_gemini_hook_prints_protocol_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REEZYN_HOME", str(tmp_path))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"hook_event_name": "BeforeTool", "tool_name": "read_file"})),
    )
    assert main(["hook", "gemini"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"suppressOutput": True}


def test_generic_emit_creates_event(tmp_path, monkeypatch):
    monkeypatch.setenv("REEZYN_HOME", str(tmp_path))
    assert main(["emit", "--agent", "custom", "--text", "I'll read the config."]) == 0
    entries = list((tmp_path / "events").glob("*.json"))
    assert len(entries) == 1
    data = json.loads(entries[0].read_text())
    assert data["payload"]["kind"] == "reasoning"
    assert data["payload"]["agent"] == "custom"


def test_setup_persists_selected_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REEZYN_HOME", str(tmp_path))

    def fake_download(home, model, progress):
        assert model == "fp16"
        return SimpleNamespace(ready=True)

    monkeypatch.setattr("reezyn.tts.download_models", fake_download)
    assert main(["setup", "--model", "fp16"]) == 0
    assert load_config(tmp_path).speech.model == "fp16"
    assert "Voice model ready" in capsys.readouterr().out


def test_ingest_reads_jsonl(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REEZYN_HOME", str(tmp_path))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"kind":"tool","tool_name":"Bash","tool_input":{"command":"pytest"}}\n'),
    )
    assert main(["ingest"]) == 0
    assert "Queued 1 event" in capsys.readouterr().out
    entries = list((tmp_path / "events").glob("*.json"))
    assert len(entries) == 1
