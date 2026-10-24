from __future__ import annotations

from reezyn.events import Event


def test_prompt_events_have_distinct_fingerprints():
    first = Event(agent="codex", kind="prompt", text="why does login fail?", session_id="s")
    second = Event(agent="codex", kind="prompt", text="and after a reload?", session_id="s")

    # A shared fingerprint would make every watcher's dedupe drop the second
    # prompt of a session, and recall would never see it.
    assert first.fingerprint != second.fingerprint


def test_prompt_events_survive_a_dict_round_trip():
    event = Event(
        agent="cursor",
        kind="prompt",
        text="why does login fail?",
        session_id="s",
        project="/Users/me/app",
    )
    restored = Event.from_dict(event.to_dict())

    assert restored.kind == "prompt"
    assert restored.project == "/Users/me/app"
    assert restored.fingerprint == event.fingerprint
