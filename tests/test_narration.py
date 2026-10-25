from __future__ import annotations

import threading
import time

from reezyn.config import BanterConfig, NarrationConfig
from reezyn.events import Event
from reezyn.languages import DEFAULT_PACK, PACKS
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


def test_select_excerpt_sentence_limit_and_whole_thought():
    text = "First idea here. Second idea here. Third idea here."
    assert select_excerpt(text, max_words=50, max_sentences=1) == "First idea here."
    assert "Third" not in select_excerpt(text, max_words=50, max_sentences=2)
    assert (
        select_excerpt(text, max_words=50, max_sentences=0)
        == "First idea here. Second idea here. Third idea here."
    )


def test_describe_tool_maps_common_actions():
    assert describe_tool("Bash", {"command": "npm test -- auth"}) == "running the tests"
    assert describe_tool("Read", {"file_path": "/a/b/session.ts"}) == "reading session.ts"
    assert describe_tool("Grep", {"pattern": "refreshToken"}) == 'searching for "refreshToken"'
    assert describe_tool("Edit", {"file_path": "src/auth.ts"}) == "updating auth.ts"
    assert describe_tool("WebSearch", {}) == "checking the docs"
    assert describe_tool("run_terminal_cmd", {"command": "pytest"}) == "running the tests"
    assert describe_tool("search_replace", {"file_path": "src/auth.ts"}) == "updating auth.ts"
    assert describe_tool("write", {"path": "src/new.ts"}) == "writing new.ts"
    assert describe_tool("delete_file", {"path": "src/old.ts"}) == "deleting old.ts"
    assert describe_tool("list_dir", {}) == "finding the relevant files"
    assert describe_tool("fetch_rules", {}) == "checking the project rules"
    assert (
        describe_tool(
            "apply_patch",
            {"input": "*** Begin Patch\n*** Update File: src/auth/session.ts\n@@\n"},
        )
        == "updating session.ts"
    )
    assert (
        describe_tool(
            "edit",
            {"metadata": {"files": [{"file": "src/auth/session.ts"}]}},
        )
        == "updating session.ts"
    )


def test_a_resumed_thought_gets_a_return_cue():
    now = time.time()
    events = [Event(agent="t", kind="reasoning", text="I'll recheck the token.", ts=now)]
    plain = compose_narration(events, NarrationConfig(connectors=False), now=now + 0.1)
    resumed = compose_narration(
        events, NarrationConfig(connectors=False), now=now + 0.1, lapsed=True
    )
    assert plain is not None and resumed is not None
    assert plain.text == "I'll recheck the token."
    assert resumed.text == "Back to this. I'll recheck the token."


def test_the_narrator_opens_a_turn_with_a_cue():
    spoken: list[str] = []
    narrator = Narrator(
        speak=lambda text: spoken.append(text) or True,
        config=NarrationConfig(min_gap_s=0),
        banter=BanterConfig(enabled=False),
    )
    narrator.start()
    try:
        narrator.opener()
        deadline = time.time() + 2.0
        while not spoken and time.time() < deadline:
            time.sleep(0.02)
    finally:
        narrator.stop()
    assert spoken
    assert spoken[0] in DEFAULT_PACK.phrases["cue_open"]


def test_a_long_gap_marks_the_next_burst_as_resumed():
    narrator = Narrator(
        speak=lambda text: True,
        config=NarrationConfig(return_after_s=5),
        banter=BanterConfig(enabled=False),
    )
    fresh = Narrator(
        speak=lambda text: True,
        config=NarrationConfig(return_after_s=5),
        banter=BanterConfig(enabled=False),
    )
    fresh.submit(Event(agent="t", kind="reasoning", text="First."))
    assert fresh._lapsed is False

    narrator._last_finish = time.monotonic() - 30
    narrator.submit(Event(agent="t", kind="reasoning", text="Again."))
    assert narrator._lapsed is True


def test_lull_lines_follow_the_work_that_went_quiet():
    narrator = Narrator(
        speak=lambda text: True,
        config=NarrationConfig(),
        banter=BanterConfig(enabled=True, after_s=0, max_lines=1),
    )
    narrator._scenario = "tests"
    narrator._last_activity = time.monotonic() - 5
    line = narrator._due_banter()
    assert line in DEFAULT_PACK.scenario_banter["tests"]


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


def test_compose_can_include_the_whole_thought():
    now = time.time()
    events = [
        Event(
            agent="test",
            kind="reasoning",
            text="First idea here. Second idea here. Third idea here.",
            ts=now,
        )
    ]
    narration = compose_narration(
        events,
        NarrationConfig(connectors=False, max_words=50, max_sentences=0),
        now=now + 0.2,
    )
    assert narration is not None
    assert narration.text == "First idea here. Second idea here. Third idea here."


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


def test_compose_stays_with_the_latest_session():
    now = time.time()
    events = [
        Event(agent="a", session_id="one", kind="reasoning", text="I'll inspect the old task.", ts=now),
        Event(agent="a", session_id="two", kind="reasoning", text="I'll inspect the new task.", ts=now + 0.2),
    ]
    narration = compose_narration(events, NarrationConfig(connectors=False), now=now + 1)
    assert narration is not None
    assert narration.text == "I'll inspect the new task."


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


def test_narrator_reports_when_speech_starts_and_ends():
    spoken: list[str] = []
    states: list[bool] = []

    def speak(text: str) -> bool:
        spoken.append(text)
        return True

    narrator = Narrator(
        speak,
        NarrationConfig(
            debounce_ms=10,
            min_gap_s=0,
            connectors=False,
            stale_after_s=5,
        ),
        on_speaking=states.append,
    )
    narrator.start()
    try:
        narrator.submit(
            Event(agent="test", kind="reasoning", text="I'll check the auth tests.")
        )
        deadline = time.time() + 2
        while (not spoken or len(states) < 2) and time.time() < deadline:
            time.sleep(0.02)
        assert spoken
        assert states == [True, False]
    finally:
        narrator.stop()


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.02)
    return predicate()


def test_narrator_fills_a_lull_then_stops_at_the_limit():
    spoken: list[str] = []

    narrator = Narrator(
        lambda text: spoken.append(text) or True,
        NarrationConfig(debounce_ms=10, min_gap_s=0, connectors=False),
        banter=BanterConfig(after_s=0.08, repeat_s=0.08, max_lines=2),
    )
    narrator.start()
    try:
        narrator.submit(
            Event(agent="test", kind="reasoning", text="I'll check the auth tests.")
        )
        assert _wait_until(lambda: len(spoken) == 3)
        assert spoken[0] == "I'll check the auth tests."
        assert all(line in DEFAULT_PACK.banter for line in spoken[1:])

        time.sleep(0.35)
        assert len(spoken) == 3
    finally:
        narrator.stop()


def test_narrator_banter_waits_for_activity_and_uses_the_pack():
    spoken: list[str] = []
    spanish = PACKS["es"]

    narrator = Narrator(
        lambda text: spoken.append(text) or True,
        NarrationConfig(debounce_ms=10, min_gap_s=0, connectors=False),
        banter=BanterConfig(after_s=0.05, repeat_s=0.05, max_lines=2),
        pack=spanish,
    )
    narrator.start()
    try:
        time.sleep(0.25)
        assert spoken == []

        narrator.submit(
            Event(agent="test", kind="reasoning", text="Voy a revisar la sesión.")
        )
        assert _wait_until(lambda: len(spoken) >= 2)
        assert spoken[1] in spanish.banter
    finally:
        narrator.stop()


def test_narrator_banter_resets_when_a_new_event_arrives():
    spoken: list[str] = []

    narrator = Narrator(
        lambda text: spoken.append(text) or True,
        NarrationConfig(debounce_ms=10, min_gap_s=0, connectors=False),
        banter=BanterConfig(after_s=0.06, repeat_s=0.06, max_lines=1),
    )
    narrator.start()
    try:
        narrator.submit(
            Event(agent="test", kind="reasoning", text="I'll check the first thing.")
        )
        assert _wait_until(lambda: len(spoken) == 2)

        narrator.submit(
            Event(agent="test", kind="reasoning", text="I'll check the second thing.")
        )
        assert _wait_until(lambda: len(spoken) == 4)
        assert spoken[2] == "I'll check the second thing."
        assert spoken[3] in DEFAULT_PACK.banter
    finally:
        narrator.stop()


def test_narrator_banter_can_be_switched_off():
    spoken: list[str] = []

    narrator = Narrator(
        lambda text: spoken.append(text) or True,
        NarrationConfig(debounce_ms=10, min_gap_s=0, connectors=False),
        banter=BanterConfig(enabled=False, after_s=0.05, repeat_s=0.05),
    )
    narrator.start()
    try:
        narrator.submit(
            Event(agent="test", kind="reasoning", text="I'll check the auth tests.")
        )
        assert _wait_until(lambda: len(spoken) == 1)
        time.sleep(0.3)
        assert len(spoken) == 1
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
