from __future__ import annotations

from dataclasses import dataclass

from reezyn.remote.actions import RemoteActions


@dataclass
class FakeConfig:
    queue_when_locked: bool = True
    keep_awake: bool = False


class FakeTyper:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.inserted: list[str] = []
        self.returns = 0
        self.escapes = 0

    def guard(self) -> tuple[bool, str]:
        return (self.allowed, "" if self.allowed else "The focused control is not a text field.")

    def insert_text(self, text: str) -> int:
        self.inserted.append(text)
        return len(text)

    def press_return(self) -> None:
        self.returns += 1

    def press_escape(self) -> None:
        self.escapes += 1


def _actions(typer: FakeTyper | None = None, *, terminal: bool = True):
    state = {"front": "com.apple.Terminal" if terminal else "com.apple.Safari", "locked": False}

    def activate(bundle: str) -> bool:
        state["front"] = bundle
        return True

    actions = RemoteActions(
        FakeConfig(),
        ["com.apple.Terminal"],
        typer=typer or FakeTyper(),
        frontmost=lambda: state["front"],
        activate=activate,
        locked=lambda: state["locked"],
        trusted=lambda: True,
        start_caffeinate=False,
    )
    return actions, state


def test_a_reply_is_typed_and_submitted_into_the_terminal():
    typer = FakeTyper()
    actions, _ = _actions(typer)

    result = actions.say("run the tests", submit=True)

    assert result["ok"] is True
    assert typer.inserted == ["run the tests"]
    assert typer.returns == 1


def test_the_terminal_is_brought_forward_when_another_app_has_focus():
    typer = FakeTyper()
    actions, state = _actions(typer, terminal=False)
    actions._last_terminal = "com.apple.Terminal"

    result = actions.say("status?", submit=False)

    assert result["ok"] is True
    assert state["front"] == "com.apple.Terminal"
    assert typer.inserted == ["status?"]
    assert typer.returns == 0


def test_nothing_is_typed_when_no_terminal_is_known():
    typer = FakeTyper()
    actions, _ = _actions(typer, terminal=False)
    actions._activate = lambda bundle: False

    result = actions.say("hello")

    assert result["ok"] is False
    assert result["queued"] is False
    assert "terminal" in result["message"].lower()
    assert typer.inserted == []


def test_a_locked_mac_queues_and_flushes_after_unlock():
    typer = FakeTyper()
    actions, state = _actions(typer)
    state["locked"] = True

    result = actions.say("after you unlock", submit=True)

    assert result["ok"] is False
    assert result["queued"] is True
    assert typer.inserted == []

    state["locked"] = False
    actions._drain_once()

    assert typer.inserted == ["after you unlock"]
    assert typer.returns == 1
    assert actions.snapshot()["queued"] == 0


def test_a_failed_guard_reports_the_reason():
    typer = FakeTyper(allowed=False)
    actions, _ = _actions(typer)

    result = actions.say("hello")

    assert result["ok"] is False
    assert "text field" in result["message"]
    assert typer.inserted == []


def test_permission_answers_press_the_right_key():
    typer = FakeTyper()
    actions, _ = _actions(typer)

    assert actions.answer_permission(True)["ok"] is True
    assert typer.returns == 1
    assert typer.escapes == 0

    assert actions.answer_permission(False)["ok"] is True
    assert typer.escapes == 1


def test_permission_answers_need_accessibility():
    typer = FakeTyper()
    actions, _ = _actions(typer)
    actions._trusted = lambda: False

    result = actions.answer_permission(True)

    assert result["ok"] is False
    assert "Accessibility" in result["message"]
    assert typer.returns == 0


def test_a_locked_mac_refuses_permission_answers():
    typer = FakeTyper()
    actions, state = _actions(typer)
    state["locked"] = True

    result = actions.answer_permission(True)

    assert result["ok"] is False
    assert "locked" in result["message"].lower()
    assert typer.returns == 0
