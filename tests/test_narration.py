from __future__ import annotations

import threading
import time

from reezyn.config import NarrationConfig
from reezyn.events import Event
from reezyn.narration import (
    Narrator,
    clean_for_speech,
    compose_narration,
    describe_tool,
    select_excerpt,
)


def test_clean_for_speech_removes_code_and_paths():
    text = (
        "The user wants me to check `src/auth/session.ts` and see why "
        "getUserById fails. ```ts\nconst broken = true\n```"
    )
    cleaned = clean_for_speech(text)
    assert "I'll check session.ts" in cleaned
    assert "get user by id" in cleaned
    assert "```" not in cleaned
    assert "const broken" not in cleaned


def test_clean_for_speech_handles_markdown_and_commands():
    text = "I'll read the **middleware** first.\n\n```ts\nconst x = 1\n```\nThen I'll run `npm test -- auth`."
    cleaned = clean_for_speech(text)
    assert "middleware" in cleaned
    assert ".." not in cleaned
    assert "Then I'll run the command" in cleaned


def test_select_excerpt_prefers_complete_short_sentences():
    text = (
        "I'll start by reading the session middleware, since that is where the "
        "refresh token is validated. Then I'll check the auth tests before editing "
        "anything. Finally I'll rerun the full suite."
    )
    excerpt = select_excerpt(text, max_words=20)
    assert excerpt.endswith(".")
    assert len(excerpt.split()) <= 22
    assert "Finally" not in excerpt


def test_describe_tool_maps_common_actions():
    assert describe_tool("Bash", {"command": "npm test -- auth"}) == "running the tests"
    assert describe_tool("Read", {"file_path": "/a/b/session.ts"}) == "reading session.ts"
    assert describe_tool("Grep", {"pattern": "refreshToken"}) == 'searching for "refreshToken"'
    assert describe_tool("Edit", {"file_path": "src/auth.ts"}) == "updating auth.ts"
    assert describe_tool("WebSearch", {}) == "checking the docs"


def test_compose_prefers_reasoning_and_respects_staleness():
    now = time.time()
    events = [
        Event(
            agent="test",
            kind="reasoning",
            text=(
                "I'll inspect the middleware to see where the refresh token is "
                "validated, then compare the token versions."
            ),
            ts=now,
        ),
        Event(
            agent="test",
            kind="tool",
            tool_name="Read",
            tool_input={"path": "session.ts"},
            ts=now + 0.1,
        ),
    ]
    narration = compose_narration(events, NarrationConfig(connectors=False), now=now + 0.2)
    assert narration is not None
    assert narration.text.startswith("I'll inspect the middleware")
    assert "reading session.ts" not in narration.text

    stale = compose_narration(
        events, NarrationConfig(connectors=False, stale_after_s=1), now=now + 10
    )
    assert stale is None


def test_compose_appends_action_to_very_short_reasoning():
    now = time.time()
    events = [
        Event(agent="test", kind="reasoning", text="Let me check.", ts=now),
        Event(
            agent="test",
            kind="tool",
            tool_name="Read",
            tool_input={"path": "session.ts"},
            ts=now + 0.1,
        ),
    ]
    narration = compose_narration(events, NarrationConfig(connectors=False), now=now + 0.2)
    assert narration is not None
    assert narration.text == "Let me check. I'm reading session.ts."


def test_compose_falls_back_to_tool_actions():
    now = time.time()
    events = [
        Event(agent="test", kind="tool", tool_name="Read", tool_input={"path": "session.ts"}, ts=now),
        Event(agent="test", kind="tool", tool_name="Bash", tool_input={"command": "pytest"}, ts=now + 0.1),
    ]
    narration = compose_narration(events, NarrationConfig(connectors=False), now=now + 0.2)
    assert narration is not None
    assert narration.text == "I'm reading session.ts, then running the tests."


def test_narrator_deduplicates_and_speaks():
    spoken: list[str] = []

    def speak(text: str) -> bool:
        spoken.append(text)
        return True

    narrator = Narrator(
        speak,
        NarrationConfig(
            debounce_ms=20,
            min_gap_s=0,
            connectors=False,
            stale_after_s=5,
        ),
        on_change=lambda: None,
    )
    narrator.start()
    try:
        event = Event(agent="test", kind="reasoning", text="I'll check the auth tests.")
        narrator.submit(event)
        narrator.submit(event)
        deadline = time.time() + 2
        while not spoken and time.time() < deadline:
            time.sleep(0.02)
        assert spoken == ["I'll check the auth tests."]
    finally:
        narrator.stop()


def test_narrator_pause_stops_queue():
    spoken: list[str] = []
    stopped = threading.Event()

    def speak(text: str) -> bool:
        spoken.append(text)
        return True

    narrator = Narrator(
        speak,
        NarrationConfig(debounce_ms=10, min_gap_s=0, connectors=False),
        stop_speech=stopped.set,
    )
    narrator.start()
    try:
        narrator.pause()
        assert narrator.is_paused
        assert stopped.is_set()
        narrator.submit(Event(agent="test", kind="reasoning", text="I'll check."))
        time.sleep(0.1)
        assert spoken == []
        narrator.resume()
    finally:
        narrator.stop()
