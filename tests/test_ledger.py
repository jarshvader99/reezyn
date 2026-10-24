from __future__ import annotations

from reezyn.ledger import (
    ROLE_ACTION,
    ROLE_PROMPT,
    ROLE_THOUGHT,
    Entry,
    LedgerStore,
    ProjectLedger,
    project_root,
    tokens,
)

NOW = 1_800_000_000.0


def _entry(
    role: str = ROLE_THOUGHT,
    text: str = "The refresh path rotates the cookie but never invalidates the old token.",
    ts: float | None = None,
    session: str = "s1",
    fingerprint: str = "",
) -> Entry:
    return Entry(
        ts=NOW - 600 if ts is None else ts,
        session=session,
        role=role,
        text=text,
        agent="claude",
        fingerprint=fingerprint,
    )


def test_tokens_fold_case_plurals_and_identifiers():
    assert tokens("refreshToken") == ["refresh", "token"]
    assert tokens("session_id") == ["session", "id"]
    assert tokens("tests") == tokens("test")
    assert tokens("running") == tokens("run")
    assert "the" not in tokens("the tests")


def test_entries_round_trip_between_instances(tmp_path):
    directory = tmp_path / "project"
    first = ProjectLedger(directory, "/repo")
    assert first.append(_entry(fingerprint="a"))

    reopened = ProjectLedger(directory, "/repo")
    entries = reopened.entries()
    assert len(entries) == 1
    assert entries[0].text.startswith("The refresh path")
    assert entries[0].role == ROLE_THOUGHT


def test_repeated_fingerprint_is_stored_once(tmp_path):
    ledger = ProjectLedger(tmp_path / "project", "/repo")
    assert ledger.append(_entry(fingerprint="same"))
    assert not ledger.append(_entry(fingerprint="same"))
    assert len(ledger.entries()) == 1


def test_search_finds_an_earlier_conclusion(tmp_path):
    ledger = ProjectLedger(tmp_path / "project", "/repo")
    ledger.append(_entry(fingerprint="old"))
    ledger.append(
        _entry(
            role=ROLE_ACTION,
            text="running the auth test suite",
            ts=NOW - 120,
            fingerprint="action",
        )
    )
    matches = ledger.search(
        "why does login still work after refresh token rotation", before=NOW, now=NOW
    )
    assert matches
    assert matches[0].entry.role == ROLE_THOUGHT
    assert "invalidates" in matches[0].entry.text


def test_search_is_silent_about_unrelated_work(tmp_path):
    ledger = ProjectLedger(tmp_path / "project", "/repo")
    ledger.append(
        _entry(text="The deploy script uploads the static bundle to a bucket.")
    )
    assert not ledger.search("why does login fail after token refresh", before=NOW, now=NOW)


def test_search_skips_prompts_and_the_turn_in_progress(tmp_path):
    ledger = ProjectLedger(tmp_path / "project", "/repo")
    ledger.append(_entry(role=ROLE_PROMPT, text="refresh token login problem"))
    ledger.append(_entry(text="The refresh token login path is one line.", ts=NOW - 5))
    assert not ledger.search("refresh token login problem", before=NOW, now=NOW)


def test_old_entries_are_pruned(tmp_path):
    ledger = ProjectLedger(tmp_path / "project", "/repo")
    total = 950
    for index in range(total):
        ledger.append(
            _entry(
                text=f"The refresh token case number {index} was handled here.",
                ts=NOW - total + index,
                fingerprint=f"f{index}",
            )
        )
    entries = ledger.entries()
    assert len(entries) <= 650
    assert entries[-1].text == f"The refresh token case number {total - 1} was handled here."


def test_store_resolves_a_bare_project_name_to_a_known_path(tmp_path):
    store = LedgerStore(tmp_path)
    absolute = store.for_project(str(tmp_path / "reezyn"))
    absolute.append(_entry(fingerprint="a"))

    reopened = LedgerStore(tmp_path)
    by_name = reopened.for_project("reezyn")
    assert by_name.key == absolute.key
    assert len(by_name.entries()) == 1


def test_project_root_prefers_the_repository(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "src" / "auth"
    nested.mkdir(parents=True)
    assert project_root(nested) == repo.resolve()
    assert project_root(nested / "session.ts") == repo.resolve()
