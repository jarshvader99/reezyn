from __future__ import annotations

from typing import Any

from reezyn.config import VoiceConfig
from reezyn.voice.session import VoiceSession
from reezyn.voice.typing import Typer, chunks
from reezyn.voice.wake import (
    expand_dictation,
    find_wake,
    parse_intent,
    strip_wake,
    take_trailing_send,
)

TERMINAL = "com.apple.Terminal"


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRecognition:
    def __init__(self) -> None:
        self.partial = ""
        self.final = ""
        self.done = False
        self.cancelled = False
        self.appended = 0

    def append(self, _buffer: Any) -> None:
        self.appended += 1

    def end(self, timeout: float = 1.5) -> str:
        self.done = True
        return self.final or self.partial

    def cancel(self) -> None:
        self.cancelled = True
        self.done = True


class FakeEngine:
    def __init__(self) -> None:
        self.sessions: list[FakeRecognition] = []
        self.context: list[str] = []

    def begin(self, context: list[str]) -> FakeRecognition:
        self.context = context
        recognition = FakeRecognition()
        self.sessions.append(recognition)
        return recognition


class FakeMicrophone:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.stopped = True


class FakeNarrator:
    def __init__(self) -> None:
        self.skipped = False
        self.announced: list[str] = []

    def skip(self) -> None:
        self.skipped = True

    def announce(self, text: str) -> None:
        self.announced.append(text)


class Harness:
    def __init__(
        self,
        *,
        focus: bool = True,
        guard: tuple[bool, str] = (True, ""),
        narrator: Any | None = None,
        confirm_send: bool = True,
    ) -> None:
        self.clock = FakeClock()
        self.engine = FakeEngine()
        self.microphone = FakeMicrophone()
        self.typed: list[str] = []
        self.keys: list[Any] = []
        self.focus = {"ok": focus}
        self.guard_result = guard
        config = VoiceConfig(
            enabled=True,
            conversation=True,
            silence_ms=1000,
            wake_silence_ms=700,
            follow_up_s=8.0,
            conversation_s=12.0,
            auto_send=False,
            auto_send_in_conversation=True,
            confirm_send=confirm_send,
            ack_sound=False,
        )
        self.session = VoiceSession(
            config,
            narrator=narrator,
            microphone=self.microphone,
            engine=self.engine,
            typer=self.make_typer(),
            clock=self.clock,
            auto_poll=False,
            ack=lambda: None,
        )
        self.session.start()

    def make_typer(self) -> Typer:
        def insert(text: str) -> int:
            self.typed.append(text)
            return len(text)

        def backspace(count: int) -> None:
            self.keys.append(("backspace", count))

        def check() -> tuple[bool, str]:
            return self.guard_result

        def focus() -> bool:
            return self.focus["ok"]

        return Typer(
            [TERMINAL],
            insert=insert,
            return_key=lambda: self.keys.append("return"),
            escape_key=lambda: self.keys.append("escape"),
            backspace=backspace,
            check=check,
            focus=focus,
        )

    def say(self, text: str, *, clock: FakeClock | None = None) -> None:
        """One frame of speech, then a pause long enough to commit it."""

        self.session.feed(object(), 0.2)
        self.session.poll()
        recognition = self.engine.sessions[-1]
        recognition.partial = text
        recognition.final = text
        self.session.poll()
        self.clock.advance(1.4)
        self.session.poll()

    def hear(self, partial: str) -> None:
        """Only a partial result: the transcript is drafting, not committed."""

        self.session.feed(object(), 0.2)
        self.session.poll()
        self.engine.sessions[-1].partial = partial
        self.session.poll()


# -- wake word and intent parsing -----------------------------------------


def test_bare_and_greeted_wake_phrases_are_recognized():
    assert find_wake("Reezyn, run the tests") == (0, 1)
    assert find_wake("Hey Reezyn, run the tests") == (0, 2)
    # The recognizer does not know the name; it guesses.
    assert find_wake("Hey Reason, run the tests") == (0, 2)
    assert find_wake("Hey there, can you look") is None
    assert find_wake("") is None


def test_wake_phrase_is_stripped_from_the_command():
    assert strip_wake("Hey Reezyn, run the tests") == "run the tests"
    assert strip_wake("Reezyn fix the build") == "fix the build"
    assert strip_wake("nothing to strip") == "nothing to strip"


def test_intents_cover_commit_cancel_and_permissions():
    assert parse_intent("send it") == ("send", "")
    assert parse_intent("Send it.") == ("send", "")
    assert parse_intent("scratch that") == ("scratch", "")
    assert parse_intent("never mind") == ("cancel", "")
    assert parse_intent("add a login test") == ("text", "add a login test")
    assert parse_intent("yes", permission=True) == ("allow", "")
    assert parse_intent("nope", permission=True) == ("deny", "")
    assert parse_intent("use the other file", permission=True) == (
        "text",
        "use the other file",
    )


def test_send_keyword_can_end_a_sentence():
    assert take_trailing_send("add a login test send it now") == (
        "add a login test",
        True,
    )
    assert take_trailing_send("please send it") == ("", True)
    assert take_trailing_send("send it") == ("", True)
    # The plain word "send" is too common to act as a hidden keyword.
    assert take_trailing_send("could you send me the file") == (
        "could you send me the file",
        False,
    )


def test_confirmation_intents_do_not_mistake_a_sentence_for_yes():
    assert parse_intent("yes", confirmation=True) == ("send", "")
    assert parse_intent("send it", confirmation=True) == ("send", "")
    assert parse_intent("not yet", confirmation=True) == ("keep", "")
    assert parse_intent("scratch that", confirmation=True) == ("scratch", "")
    assert parse_intent("yes but use staging", confirmation=True) == (
        "text",
        "yes but use staging",
    )


def test_spoken_punctuation_becomes_typed_characters():
    assert expand_dictation("run the tests, then commit") == "run the tests, then commit"
    assert expand_dictation("new line") == "\n"
    assert expand_dictation("snake underscore case") == "snake_case"
    assert expand_dictation("say hello exclamation point") == "say hello!"


# -- typing guard ----------------------------------------------------------


def test_chunks_never_split_a_surrogate_pair():
    pieces = list(chunks("a😀b", limit=2))
    assert "".join(pieces) == "a😀b"
    for piece in pieces:
        piece.encode("utf-8")  # raises on a lone surrogate


def test_typer_reports_a_blocked_guard():
    typer = Typer(
        [TERMINAL],
        check=lambda: (False, "Accessibility is not granted."),
        focus=lambda: True,
    )
    ok, reason = typer.guard()
    assert not ok
    assert "Accessibility" in reason


# -- the voice loop --------------------------------------------------------


def test_wake_word_types_at_the_caret_without_submitting():
    harness = Harness()
    harness.say("Hey Reezyn, add a login test")
    assert harness.typed == ["add a login test"]
    assert "return" not in harness.keys
    assert harness.session.status().state == "listening"


def test_nothing_is_captured_without_a_focused_terminal_input():
    harness = Harness(focus=False)
    harness.session.feed(object(), 0.2)
    harness.session.poll()
    assert harness.engine.sessions == []
    assert harness.typed == []


def test_partial_transcript_is_shown_but_not_typed():
    harness = Harness()
    harness.hear("Hey Reezyn, add a")
    assert harness.typed == []
    snapshot = harness.session.status()
    assert snapshot.state == "listening"
    assert snapshot.partial == "Hey Reezyn, add a"


def test_a_blocked_guard_reports_instead_of_typing():
    harness = Harness(guard=(False, "The focused control is not a text field."))
    harness.say("Hey Reezyn, add a test")
    assert harness.typed == []
    snapshot = harness.session.status()
    assert snapshot.state == "error"
    assert "not a text field" in snapshot.error


def test_a_pause_asks_before_sending_and_yes_submits():
    narrator = FakeNarrator()
    harness = Harness(narrator=narrator)
    harness.say("Hey Reezyn, add a login test")
    assert harness.typed == ["add a login test"]
    assert "return" not in harness.keys
    assert any("Ready to send" in line for line in narrator.announced)
    assert harness.session.status().state == "listening"

    harness.say("yes")
    assert harness.keys == ["return"]


def test_declining_keeps_the_draft_and_dictation_continues():
    harness = Harness()
    harness.say("Hey Reezyn, add a login test")
    harness.say("not yet")
    assert harness.keys == []
    harness.say("and check the redirect")
    assert harness.typed == ["add a login test", "and check the redirect"]
    harness.say("send it")
    assert harness.keys == ["return"]


def test_hot_keyword_sends_without_waiting():
    narrator = FakeNarrator()
    harness = Harness(narrator=narrator)
    harness.say("Hey Reezyn, add a login test, send it now")
    assert harness.typed == ["add a login test"]
    assert harness.keys == ["return"]
    assert narrator.announced == []


def test_confirmation_can_be_switched_off():
    harness = Harness(confirm_send=False)
    harness.say("Hey Reezyn, add a login test")
    assert harness.typed == ["add a login test"]
    assert harness.keys == []


def test_send_and_scratch_act_on_the_draft():
    harness = Harness()
    harness.say("Hey Reezyn, add a login test")
    assert harness.typed == ["add a login test"]

    harness.say("scratch that")
    assert ("backspace", len("add a login test")) in harness.keys

    harness.say("actually, add a logout test")
    harness.say("send it")
    assert "return" in harness.keys


def test_follow_up_needs_no_second_wake_word_but_then_expires():
    harness = Harness()
    harness.say("Hey Reezyn, add a login test")
    harness.say("and check the redirect")
    assert harness.typed == ["add a login test", "and check the redirect"]

    harness.clock.advance(20.0)
    harness.session.poll()
    assert harness.session.status().state == "idle"
    harness.say("just thinking out loud")
    assert harness.typed == ["add a login test", "and check the redirect"]


def test_conversation_window_submits_without_a_wake_word():
    harness = Harness()
    harness.session.open_for_conversation()
    assert harness.session.status().mode == "conversation"
    harness.say("add a regression test for the refresh path")
    assert harness.typed == ["add a regression test for the refresh path"]
    assert harness.keys == ["return"]


def test_permission_answers_press_return_or_escape():
    harness = Harness()
    harness.session.open_for_conversation(permission=True)
    harness.say("allow it")
    assert harness.keys == ["return"]

    harness.session.open_for_conversation(permission=True)
    harness.say("no")
    assert harness.keys == ["return", "escape"]


def test_permission_window_treats_a_qualified_answer_as_words():
    harness = Harness()
    harness.session.open_for_conversation(permission=True)
    harness.say("yes but use the staging database")
    # A sentence is not a silent allow; it is typed for the agent to read.
    assert harness.typed == ["yes but use the staging database"]
    assert harness.keys == ["return"]


def test_wake_word_interrupts_narration():
    narrator = FakeNarrator()
    harness = Harness(narrator=narrator)
    harness.session.set_speaking(True)
    harness.hear("Hey Reezyn")
    assert narrator.skipped is True
    # Speaking no longer swallows the wake word, but it does swallow dictation.
    assert harness.session.status().state == "listening"
    harness.session.set_speaking(False)
    harness.say("run the auth tests")
    assert harness.typed == ["run the auth tests"]


def test_an_expired_permission_window_forgets_the_question():
    harness = Harness()
    harness.session.open_for_conversation(permission=True)
    harness.clock.advance(20.0)
    harness.session.poll()
    assert harness.session.status().state == "idle"

    harness.session.open_for_conversation()
    harness.say("yes")
    assert harness.typed == ["yes"]
    assert harness.keys == ["return"]  # the conversation's own submit, not an allow


def test_cancel_leaves_the_draft_alone():
    harness = Harness()
    harness.say("Hey Reezyn, add a test")
    harness.say("never mind")
    assert harness.typed == ["add a test"]
    assert harness.session.status().state == "idle"
