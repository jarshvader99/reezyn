from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from reezyn.adapters.cursor import CursorWatcher, parse_cursor_record
from reezyn.adapters.opencode import OpenCodeWatcher

FIXTURES = Path(__file__).parent / "fixtures"


def test_cursor_parser_extracts_thinking_and_tools():
    lines = (FIXTURES / "cursor_transcript.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    events = []
    for record in records:
        events.extend(parse_cursor_record(record, "cursor-session-1"))

    reasoning = [event for event in events if event.kind == "reasoning"]
    tools = [event for event in events if event.kind == "tool"]
    assert any("session middleware" in event.text for event in reasoning)
    assert any("refresh path" in event.text for event in reasoning)
    assert any(event.tool_name == "read_file" for event in tools)
    assert any(event.tool_name == "edit_file" for event in tools)
    assert all("Done. The login flow" not in event.text for event in reasoning)


def test_cursor_watcher_reads_a_new_transcript(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    seen = []
    watcher = CursorWatcher(seen.append, root=root, poll_s=0.05)
    watcher.start()
    try:
        day = root / "Users-me-app" / "agent-transcripts" / "cursor-session-2"
        day.mkdir(parents=True)
        transcript = day / "cursor-session-2.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "I'll check the failing test."},
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "npm test"},
                            },
                        ]
                    },
                }
            )
            + "\n"
        )
        deadline = time.time() + 2
        while not seen and time.time() < deadline:
            watcher.scan_now()
            time.sleep(0.05)
        assert seen
        assert any(event.kind == "reasoning" for event in seen)
        assert any(event.tool_name == "Bash" for event in seen)
    finally:
        watcher.stop()


def test_cursor_user_message_becomes_a_prompt(tmp_path):
    root = tmp_path / "projects"
    session = root / "Users-me-app" / "agent-transcripts" / "cursor-session-3"
    session.mkdir(parents=True)
    transcript = session / "cursor-session-3.jsonl"
    records = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": "why does login fail?"}]},
        },
        {
            "role": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "Checking session.ts."},
                    {"type": "tool_use", "name": "read_file", "input": {"path": "src/auth/session.ts"}},
                ]
            },
        },
    ]
    transcript.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    seen = []
    watcher = CursorWatcher(seen.append, root=root, poll_s=0.05)
    for _ in range(10):
        watcher.scan_now()
        if any(event.kind == "prompt" for event in seen):
            break
        time.sleep(0.02)

    prompts = [event for event in seen if event.kind == "prompt"]
    assert [event.text for event in prompts] == ["why does login fail?"]
    assert prompts[0].project == "Users-me-app"
    assert any(event.kind == "reasoning" for event in seen)


def test_opencode_prompts_carry_the_session_directory(tmp_path):
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE session_message ("
        "id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER, "
        "time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    con.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, directory TEXT)")
    now_ms = int(time.time() * 1000)
    con.execute("INSERT INTO session_v2 VALUES (?,?)", ("session-9", "/Users/me/app"))
    con.execute(
        "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
        (
            "msg-user",
            "session-9",
            "user",
            1,
            now_ms,
            now_ms,
            json.dumps({"text": "why does login fail?", "time": {"created": now_ms}}),
        ),
    )
    con.commit()
    con.close()

    seen = []
    watcher = OpenCodeWatcher(seen.append, db_path=db, poll_s=0.05)
    watcher.scan_now()

    prompts = [event for event in seen if event.kind == "prompt"]
    assert [event.text for event in prompts] == ["why does login fail?"]
    assert prompts[0].project == "/Users/me/app"


def test_opencode_legacy_user_parts_become_one_prompt(tmp_path):
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)"
    )
    con.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    con.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT)")
    now_ms = int(time.time() * 1000)
    con.execute("INSERT INTO session VALUES (?,?)", ("session-old", "/Users/me/app"))
    con.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        ("msg-user", "session-old", now_ms, json.dumps({"role": "user"})),
    )
    for index, text in enumerate(["why does login fail", "after a token refresh?"]):
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            (
                f"part-{index}",
                "msg-user",
                "session-old",
                now_ms + index,
                now_ms + index,
                json.dumps({"type": "text", "text": text}),
            ),
        )
    con.commit()
    con.close()

    seen = []
    watcher = OpenCodeWatcher(seen.append, db_path=db, poll_s=0.05)
    watcher.scan_now()

    prompts = [event for event in seen if event.kind == "prompt"]
    assert [event.text for event in prompts] == ["why does login fail after a token refresh?"]
    assert prompts[0].project == "/Users/me/app"


def test_opencode_current_schema_watcher(tmp_path):
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE session_message ("
        "id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER, "
        "time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    con.commit()
    con.close()

    seen = []
    watcher = OpenCodeWatcher(seen.append, db_path=db, poll_s=0.05)
    watcher.start()
    try:
        now_ms = int(time.time() * 1000)
        data = json.loads((FIXTURES / "opencode_v2_assistant.json").read_text())
        for key in ("created", "streamed", "completed"):
            data["time"][key] = now_ms
        for block in data["content"]:
            if "time" in block:
                for key in block["time"]:
                    block["time"][key] = now_ms
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
            ("msg-1", "session-1", "assistant", 1, now_ms, now_ms, json.dumps(data)),
        )
        con.commit()
        con.close()

        for _ in range(10):
            watcher.scan_now()
            if seen:
                break
            time.sleep(0.05)
        assert seen
        reasoning = [event for event in seen if event.kind == "reasoning"]
        tools = [event for event in seen if event.kind == "tool"]
        assert any("stale token" in event.text for event in reasoning)
        assert {event.tool_name for event in tools} == {"read", "shell"}

        count = len(seen)
        watcher.scan_now()
        assert len(seen) == count
    finally:
        watcher.stop()


def test_opencode_waits_for_edit_target_before_narrating(tmp_path):
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE session_message ("
        "id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER, "
        "time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    now_ms = int(time.time() * 1000)
    data = {
        "time": {"created": now_ms, "streamed": now_ms},
        "content": [
            {
                "type": "tool",
                "name": "edit",
                "state": {
                    "status": "running",
                    "input": {"newString": "I'll add the new setting."},
                },
            }
        ],
    }
    con.execute(
        "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
        ("msg-edit", "session-edit", "assistant", 1, now_ms, now_ms, json.dumps(data)),
    )
    con.commit()
    con.close()

    seen = []
    watcher = OpenCodeWatcher(seen.append, db_path=db, poll_s=0.05)
    watcher.scan_now()
    assert seen == []

    con = sqlite3.connect(db)
    data["content"][0]["state"] = {
        "status": "completed",
        "input": {
            "path": "/Users/me/app/src/auth/session.ts",
            "newString": "I'll add the new setting.",
        },
    }
    con.execute(
        "UPDATE session_message SET data = ? WHERE id = ?",
        (json.dumps(data), "msg-edit"),
    )
    con.commit()
    con.close()

    watcher.scan_now()
    assert len(seen) == 1
    assert seen[0].tool_name == "edit"
    assert seen[0].tool_input["path"] == "/Users/me/app/src/auth/session.ts"


def test_opencode_legacy_message_part_schema(tmp_path):
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)"
    )
    con.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    now_ms = int(time.time() * 1000)
    con.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        ("msg-old", "session-old", now_ms, json.dumps({"role": "assistant"})),
    )
    con.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part-reasoning",
            "msg-old",
            "session-old",
            now_ms,
            now_ms,
            json.dumps({"type": "reasoning", "text": "I'll inspect the old schema."}),
        ),
    )
    con.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (
            "part-tool",
            "msg-old",
            "session-old",
            now_ms + 1,
            now_ms + 1,
            json.dumps(
                {
                    "type": "tool",
                    "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "pytest"}},
                }
            ),
        ),
    )
    con.commit()
    con.close()

    seen = []
    watcher = OpenCodeWatcher(seen.append, db_path=db, poll_s=0.05)
    watcher.scan_now()
    assert any(event.kind == "reasoning" for event in seen)
    assert any(event.tool_name == "bash" for event in seen)
