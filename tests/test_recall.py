from __future__ import annotations

import time

from reezyn.config import RecallConfig
from reezyn.events import Event
from reezyn.ledger import (
    ROLE_ACTION,
    ROLE_PROMPT,
    ROLE_THOUGHT,
    Entry,
    LedgerStore,
)
from reezyn.recall import Recaller, entry_for_event

NOW = time.time()
PROJECT = "/Users/me/reezyn"


def _ledger(tmp_path):
    return LedgerStore(tmp_path).for_project(PROJECT)


def _add(
    ledger,
    role: str,
    text: str,
    *,
    ts: float | None = None,
    session: str = "s1",
    fingerprint: str = "",
) -> None:
    ledger.append(
        Entry(
            ts=NOW - 600 if ts is None else ts,
            session=session,
            role=role,
            text=text,
            agent="claude",
            fingerprint=fingerprint,
        )
    )


def _prompt(text: str, session: str = "now") -> Event:
    return Event(
        agent="claude",
        kind="prompt",
        text=text,
        session_id=session,
        project=PROJECT,
        ts=NOW,
    )


def test_recall_quotes_an_earlier_conclusion(tmp_path):
    ledger = _ledger(tmp_path)
    _add(
        ledger,
        ROLE_THOUGHT,
        "The refresh path rotates the cookie but never invalidates the old token."
        " That is why the stale session still works.",
        fingerprint="old",
    )
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig())
    line = recaller.recall_for(
        _prompt("why does login still work after refresh token rotation"), now=NOW
    )
    assert line == (
        "Earlier today, the agent concluded: The refresh path rotates the cookie "
        "but never invalidates the old token. That is why the stale session "
        "still works."
    )


def test_recall_from_the_same_session_reads_differently(tmp_path):
    ledger = _ledger(tmp_path)
    _add(ledger, ROLE_THOUGHT, "The refresh token never gets invalidated.", session="s", ts=NOW - 120)
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig())
    line = recaller.recall_for(
        _prompt("is the refresh token invalidated?", session="s"), now=NOW
    )
    assert line.startswith("Earlier in this session, the agent concluded:")


def test_recall_prefers_an_action_when_there_is_no_conclusion(tmp_path):
    ledger = _ledger(tmp_path)
    _add(ledger, ROLE_ACTION, "running the auth tests", fingerprint="a")
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig())
    line = recaller.recall_for(_prompt("why are the auth tests failing"), now=NOW)
    assert line == "Earlier today, the agent was running the auth tests."


def test_recall_stays_quiet_about_unrelated_work(tmp_path):
    ledger = _ledger(tmp_path)
    _add(ledger, ROLE_THOUGHT, "The deploy script uploads the static bundle to a bucket.")
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig())
    assert recaller.recall_for(_prompt("why does the login fail"), now=NOW) == ""


def test_recall_respects_its_cooldown(tmp_path):
    ledger = _ledger(tmp_path)
    _add(ledger, ROLE_THOUGHT, "The refresh token is never invalidated.", fingerprint="a")
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig(cooldown_s=300))
    assert recaller.recall_for(_prompt("is the refresh token invalidated?"), now=NOW)
    assert recaller.recall_for(_prompt("is the refresh token invalidated?"), now=NOW + 5) == ""


def test_recall_does_not_repeat_the_same_memory(tmp_path):
    ledger = _ledger(tmp_path)
    _add(ledger, ROLE_THOUGHT, "The refresh token is never invalidated.", fingerprint="a")
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig(cooldown_s=0.0))
    assert recaller.recall_for(_prompt("is the refresh token invalidated?"), now=NOW)
    assert (
        recaller.recall_for(
            _prompt("is the refresh token invalidated?", session="later"), now=NOW + 600
        )
        == ""
    )


def test_recall_is_off_when_disabled(tmp_path):
    ledger = _ledger(tmp_path)
    _add(ledger, ROLE_THOUGHT, "The refresh token is never invalidated.", fingerprint="a")
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig(enabled=False))
    assert recaller.recall_for(_prompt("is the refresh token invalidated?"), now=NOW) == ""


def test_recap_reads_the_last_session(tmp_path):
    ledger = _ledger(tmp_path)
    _add(ledger, ROLE_PROMPT, "fix the login refresh flow", session="s1", ts=NOW - 600)
    _add(
        ledger,
        ROLE_THOUGHT,
        "The refresh path rotates the cookie but never invalidates the old token.",
        session="s1",
        ts=NOW - 540,
    )
    _add(ledger, ROLE_ACTION, "running the auth tests", session="s1", ts=NOW - 120)
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig())
    assert recaller.recap(PROJECT, now=NOW) == (
        "You asked: Fix the login refresh flow."
        " It concluded: The refresh path rotates the cookie but never invalidates the old token."
        " Last action: running the auth tests."
    )


def test_recap_ignores_work_outside_the_window(tmp_path):
    ledger = _ledger(tmp_path)
    _add(ledger, ROLE_PROMPT, "fix the login refresh flow", ts=NOW - 7200)
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig())
    assert recaller.recap(PROJECT, now=NOW) == "Nothing to recap in this project yet."


def test_recap_without_a_project_says_so(tmp_path):
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig())
    assert recaller.recap("", now=NOW) == "Nothing to recap in this project yet."


def test_events_become_ledger_entries(tmp_path):
    recaller = Recaller(LedgerStore(tmp_path), RecallConfig())

    prompt = Event(
        agent="claude",
        kind="prompt",
        text="  fix   the login flow ",
        session_id="s",
        project=PROJECT,
        ts=NOW,
    )
    entry = entry_for_event(prompt)
    assert entry is not None
    assert entry.role == ROLE_PROMPT
    assert entry.text == "fix the login flow"

    thought = Event(agent="claude", kind="reasoning", text="I'll read session.ts first.")
    assert entry_for_event(thought).role == ROLE_THOUGHT  # type: ignore[union-attr]

    tool = Event(
        agent="claude",
        kind="tool",
        tool_name="Edit",
        tool_input={"file_path": "src/auth/session.ts"},
    )
    action = entry_for_event(tool)
    assert action is not None
    assert action.role == ROLE_ACTION
    assert "session.ts" in action.text

    assert recaller.record(prompt)
    assert not recaller.record(Event(agent="claude", kind="prompt", text="no project"))
