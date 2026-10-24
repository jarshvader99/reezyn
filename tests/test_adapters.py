from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from reezyn.adapters.claude import ClaudeHookAdapter, assistant_text
from reezyn.adapters.codex import CodexWatcher, parse_rollout_record
from reezyn.adapters.freebuff import parse_freebuff_messages
from reezyn.adapters.gemini import GeminiHookAdapter, latest_assistant_reasoning
from reezyn.events import Event
from reezyn.sanitize import sanitize_hook_payload

FIXTURES = Path(__file__).parent / "fixtures"


def test_claude_extracts_reasoning_then_falls_back_to_tool(tmp_path):
    transcript = tmp_path / "session.jsonl"
    shutil.copy(FIXTURES / "claude_transcript.jsonl", transcript)
    adapter = ClaudeHookAdapter()
    payload = {
        "session_id": "claude-1",
        "hook_event_name": "PreToolUse",
        "transcript_path": str(transcript),
        "tool_name": "Read",
        "tool_input": {"file_path": "/Users/me/app/src/auth/session.ts"},
    }
    events = adapter.handle(payload)
    assert len(events) == 1
    assert events[0].kind == "reasoning"
    assert "session middleware" in events[0].text

    # No new transcript content: the adapter still reports the action.
    events = adapter.handle(payload)
    assert len(events) == 1
    assert events[0].kind == "tool"


def test_claude_prompt_hook_captures_the_question():
    adapter = ClaudeHookAdapter()
    payload = {
        "session_id": "claude-2",
        "hook_event_name": "UserPromptSubmit",
        "cwd": "/Users/me/reezyn",
        "prompt": "why is the refresh token still valid?",
    }
    events = adapter.handle(payload)
    assert len(events) == 1
    assert events[0].kind == "prompt"
    assert events[0].project == "/Users/me/reezyn"
    assert events[0].text == "why is the refresh token still valid?"

    # The spool keeps the prompt, so the app can remember what was asked.
    clean = sanitize_hook_payload("claude", payload)
    assert clean["prompt"] == "why is the refresh token still valid?"


def test_claude_tool_events_carry_the_project(tmp_path):
    transcript = tmp_path / "session.jsonl"
    shutil.copy(FIXTURES / "claude_transcript.jsonl", transcript)
    events = ClaudeHookAdapter().handle(
        {
            "session_id": "claude-1",
            "hook_event_name": "PreToolUse",
            "cwd": "/Users/me/reezyn",
            "transcript_path": str(transcript),
            "tool_name": "Read",
            "tool_input": {"file_path": "src/auth/session.ts"},
        }
    )
    assert events[0].project == "/Users/me/reezyn"


def test_claude_assistant_text_supports_thinking():
    record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "I should read the tests first."}],
        },
    }
    assert assistant_text(record) == "I should read the tests first."


def test_codex_prompts_join_the_session_project(tmp_path):
    root = tmp_path / "sessions"
    day = root / "2026" / "10" / "23"
    day.mkdir(parents=True)
    rollout = day / "rollout-2026-10-23T10-00-00-codex-session-9.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "codex-session-9", "cwd": "/Users/me/app"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "why does login fail after refresh?"}],
            },
        },
        {"type": "event_msg", "payload": {"type": "user_message", "message": "and after a reload?"}},
    ]
    rollout.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    seen: list[Event] = []
    watcher = CodexWatcher(seen.append, root=root, poll_s=0.05)
    watcher.scan_now()

    prompts = [event for event in seen if event.kind == "prompt"]
    assert [event.text for event in prompts] == [
        "why does login fail after refresh?",
        "and after a reload?",
    ]
    # The cwd from session_meta is what joins this session to the project ledger.
    assert all(event.project == "/Users/me/app" for event in prompts)


def test_codex_parser_handles_reasoning_and_tools():
    events: list[Event] = []
    for line in (FIXTURES / "codex_rollout.jsonl").read_text().splitlines():
        record = json.loads(line)
        events.extend(parse_rollout_record(record, "codex-session-1"))

    reasons = [event for event in events if event.kind == "reasoning"]
    tools = [event for event in events if event.kind == "tool"]
    assert any("refresh path" in event.text for event in reasons)
    assert any("token versions" in event.text for event in reasons)
    assert any(event.tool_name == "shell" for event in tools)
    assert any(event.tool_name == "apply_patch" for event in tools)
    assert len([event for event in tools if event.tool_name == "shell"]) == 1


def test_codex_watcher_reads_new_rollout_files(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    seen: list[Event] = []
    watcher = CodexWatcher(seen.append, root=root, poll_s=0.05)
    watcher.start()
    try:
        day = root / "2026" / "10" / "23"
        day.mkdir(parents=True)
        rollout = day / "rollout-2026-10-23T10-00-00-codex-session-2.jsonl"
        rollout.write_text(
            json.dumps(
                {
                    "timestamp": "2026-10-23T10:00:01.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "I'll inspect the failing test."}],
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
        assert "failing test" in seen[0].text
    finally:
        watcher.stop()


def test_gemini_extracts_thought_parts(tmp_path):
    session = tmp_path / "session.json"
    shutil.copy(FIXTURES / "gemini_session.json", session)
    adapter = GeminiHookAdapter()
    events = adapter.handle(
        {
            "session_id": "gemini-1",
            "hook_event_name": "BeforeTool",
            "transcript_path": str(session),
            "tool_name": "read_file",
            "tool_input": {"path": "src/auth/session.ts"},
        }
    )
    assert len(events) == 1
    assert events[0].kind == "reasoning"
    assert "rotates the cookie" in events[0].text


def test_gemini_prompt_hook_captures_the_question():
    adapter = GeminiHookAdapter()
    events = adapter.handle(
        {
            "session_id": "gemini-2",
            "hook_event_name": "BeforeAgent",
            "cwd": "/Users/me/reezyn",
            "transcript_path": "/Users/me/.gemini/tmp/reezyn/chats/session-1.json",
            "prompt": "why is the refresh token still valid?",
        }
    )
    assert len(events) == 1
    assert events[0].kind == "prompt"
    assert events[0].project == "/Users/me/reezyn"
    assert events[0].text == "why is the refresh token still valid?"

    clean = sanitize_hook_payload("gemini", {"prompt": events[0].text, "cwd": "/x"})
    assert clean["prompt"] == events[0].text


def test_gemini_transcript_shape_variants():
    assert (
        latest_assistant_reasoning(
            {
                "history": [
                    {"role": "user", "content": "hello"},
                    {"role": "model", "parts": [{"text": "I'll check the docs."}]},
                ]
            }
        )
        == "I'll check the docs."
    )


def test_hook_sanitizer_keeps_only_useful_fields():
    clean = sanitize_hook_payload(
        "claude",
        {
            "session_id": "s",
            "hook_event_name": "PreToolUse",
            "transcript_path": "/tmp/session.jsonl",
            "tool_name": "Bash",
            "tool_input": {
                "command": "API_KEY=sk-abcdefghijklmnopqrstuvwxyz npm test",
                "env": {"SECRET": "nope"},
                "content": "very large file body",
                "file": "src/auth/session.ts",
            },
        },
    )
    assert clean["tool_name"] == "Bash"
    assert "command" in clean["tool_input"]
    assert clean["tool_input"]["file"] == "src/auth/session.ts"
    assert "env" not in clean["tool_input"]
    assert "content" not in clean["tool_input"]
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in clean["tool_input"]["command"]


def test_claude_stop_speaks_only_the_final_answer(tmp_path):
    transcript = tmp_path / "final.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "I should clear the cache."},
                        {"type": "text", "text": "I cleared the cached token in session.ts."},
                    ],
                },
            }
        )
        + "\n"
    )
    events = ClaudeHookAdapter().handle(
        {
            "session_id": "claude-3",
            "hook_event_name": "Stop",
            "cwd": "/Users/me/reezyn",
            "transcript_path": str(transcript),
        }
    )
    assert [event.kind for event in events] == ["closing"]
    assert events[0].text.startswith("I cleared the cached token")
    assert events[0].project == "/Users/me/reezyn"


def test_claude_permission_notification_is_carried_through_the_spool():
    payload = {
        "session_id": "claude-4",
        "hook_event_name": "Notification",
        "cwd": "/Users/me/reezyn",
        "message": "Claude needs your permission to use Bash",
    }
    clean = sanitize_hook_payload("claude", payload)
    assert clean["message"] == payload["message"]

    events = ClaudeHookAdapter().handle(clean)
    assert [event.kind for event in events] == ["permission"]

    # An ordinary notification is not a decision the developer can answer.
    quiet = ClaudeHookAdapter().handle(
        {**clean, "message": "Claude is compacting the conversation"}
    )
    assert quiet == []


def test_gemini_after_agent_closes_the_turn():
    events = GeminiHookAdapter().handle(
        {
            "session_id": "gemini-3",
            "hook_event_name": "AfterAgent",
            "cwd": "/Users/me/reezyn",
            "prompt_response": "The refresh path now invalidates the old token.",
        }
    )
    assert [event.kind for event in events] == ["closing"]
    assert "invalidates" in events[0].text


def test_gemini_tool_permission_notification():
    payload = {
        "session_id": "gemini-4",
        "hook_event_name": "Notification",
        "cwd": "/Users/me/reezyn",
        "notification_type": "ToolPermission",
        "message": "Gemini wants to run npm test",
    }
    clean = sanitize_hook_payload("gemini", payload)
    assert clean["notification_type"] == "ToolPermission"
    events = GeminiHookAdapter().handle(clean)
    assert [event.kind for event in events] == ["permission"]


def test_codex_final_answer_and_task_complete_close_the_turn():
    events = parse_rollout_record(
        {
            "type": "response_item",
            "timestamp": "2026-10-24T10:00:00Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "The failing test now passes."}],
            },
        },
        "codex-1",
    )
    assert [event.kind for event in events] == ["closing"]
    assert events[0].text == "The failing test now passes."

    events = parse_rollout_record(
        {
            "type": "event_msg",
            "timestamp": "2026-10-24T10:00:01Z",
            "payload": {"type": "task_complete", "last_agent_message": "Done."},
        },
        "codex-1",
    )
    assert [event.kind for event in events] == ["closing"]
    assert events[0].text == "Done."


def test_freebuff_completed_message_closes_the_turn_once():
    messages = json.loads((FIXTURES / "freebuff_chat.json").read_text())
    events = parse_freebuff_messages(messages, "freebuff/chat-1")
    closings = [event for event in events if event.kind == "closing"]
    assert [event.text for event in closings] == ["Done. Freebuff support is wired in."]
    # The same sentence must not also be narrated as reasoning.
    assert not [
        event
        for event in events
        if event.kind == "reasoning" and event.text == closings[0].text
    ]
