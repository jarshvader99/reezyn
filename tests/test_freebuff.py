from __future__ import annotations

import json
from pathlib import Path

from reezyn.adapters.freebuff import FreebuffWatcher, parse_freebuff_messages

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> list[dict]:
    return json.loads((FIXTURES / "freebuff_chat.json").read_text())


def _chat_file(root: Path, chat: str = "2026-10-24T02-49-18.222Z") -> Path:
    chat_dir = root / "reezyn" / "chats" / chat
    chat_dir.mkdir(parents=True, exist_ok=True)
    return chat_dir / "chat-messages.json"


def test_freebuff_parser_extracts_reasoning_and_tools():
    events = parse_freebuff_messages(_load_fixture(), "reezyn/chat-1")
    reasoning = [event for event in events if event.kind == "reasoning"]
    tools = [event for event in events if event.kind == "tool"]

    assert any("chat snapshots" in event.text for event in reasoning)
    assert any("chat-messages.json" in event.text for event in reasoning)
    assert all("Freebuff support is wired in" not in event.text for event in reasoning)
    assert [event.tool_name for event in tools] == ["list_directory", "str_replace"]
    assert tools[1].tool_input["path"] == "reezyn/config.py"
    assert all(event.agent == "freebuff" for event in events)


def test_freebuff_user_message_becomes_a_prompt():
    events = parse_freebuff_messages(
        [
            {
                # Freebuff stores a prompt as a plain content string.
                "id": "user-2",
                "variant": "user",
                "content": "why does the login session survive a refresh?",
            },
            {
                "id": "user-3",
                "variant": "user",
                "blocks": [
                    {"type": "text", "content": "and what about the cookie?"}
                ],
            },
            {
                "id": "ai-5",
                "variant": "ai",
                "blocks": [
                    {
                        "type": "text",
                        "textType": "reasoning",
                        "thinkingId": "t-5",
                        "content": "The token is never invalidated.",
                    }
                ],
            },
        ],
        "reezyn/chat-3",
        project="reezyn",
    )
    prompts = [event for event in events if event.kind == "prompt"]
    assert [event.text for event in prompts] == [
        "why does the login session survive a refresh?",
        "and what about the cookie?",
    ]
    assert all(event.project == "reezyn" for event in prompts)
    assert prompts[0].session_id == "reezyn/chat-3"
    assert any(event.kind == "reasoning" for event in events)


def test_freebuff_uses_commentary_only_when_there_is_no_reasoning():
    events = parse_freebuff_messages(
        [
            {
                "id": "ai-2",
                "variant": "ai",
                "blocks": [
                    {"type": "text", "textType": "text", "content": "I'll run the suite first."},
                    {"type": "tool", "toolCallId": "call-3", "toolName": "run_terminal_command", "input": {"command": "pytest"}},
                ],
            },
            {
                "id": "ai-3",
                "variant": "ai",
                "blocks": [{"type": "text", "textType": "text", "content": "All done. The tests pass."}],
            },
        ],
        "reezyn/chat-2",
    )
    reasoning = [event for event in events if event.kind == "reasoning"]
    assert [event.text for event in reasoning] == ["I'll run the suite first."]
    assert any(event.tool_name == "run_terminal_command" for event in events)


def test_freebuff_watcher_skips_history_and_emits_new_blocks(tmp_path):
    root = tmp_path / "projects"
    chat = _chat_file(root)
    fixture = _load_fixture()
    chat.write_text(json.dumps(fixture[:2]))

    seen = []
    watcher = FreebuffWatcher(seen.append, root=root, poll_s=0.05)
    watcher.start()
    try:
        watcher.scan_now()
        assert seen == []

        next_message = {
            "id": "ai-2",
            "variant": "ai",
            "blocks": [
                {
                    "type": "text",
                    "textType": "reasoning",
                    "thinkingId": "thought-3",
                    "content": "The snapshot changed, so I'll read only the new block.",
                },
                {"type": "tool", "toolCallId": "call-9", "toolName": "read_files", "input": {"paths": ["reezyn/service.py"]}},
            ],
        }
        chat.write_text(json.dumps(fixture[:2] + [next_message]))
        watcher.scan_now()

        assert [event.kind for event in seen] == ["reasoning", "tool"]
        assert seen[0].text == "The snapshot changed, so I'll read only the new block."
        assert seen[1].tool_name == "read_files"
        # The project folder name is how a watched chat joins the right ledger.
        assert all(event.project == "reezyn" for event in seen)

        count = len(seen)
        chat.write_text(json.dumps(fixture[:2] + [next_message]))
        watcher.scan_now()
        assert len(seen) == count
    finally:
        watcher.stop()


def test_freebuff_watcher_waits_for_a_streaming_thought(tmp_path):
    root = tmp_path / "projects"
    chat = _chat_file(root)
    chat.write_text(json.dumps([]))

    seen = []
    watcher = FreebuffWatcher(seen.append, root=root, poll_s=0.05)
    watcher.start()
    try:
        message = {
            "id": "ai-4",
            "variant": "ai",
            "blocks": [{"type": "text", "textType": "reasoning", "thinkingId": "thought-4", "content": "I need to"}],
        }
        chat.write_text(json.dumps([message]))
        watcher.scan_now()
        assert seen == []

        message["blocks"][0]["content"] = "I need to check the voice model first."
        chat.write_text(json.dumps([message]))
        watcher.scan_now()
        assert seen == []

        watcher.scan_now()
        assert len(seen) == 1
        assert seen[0].text == "I need to check the voice model first."
    finally:
        watcher.stop()
