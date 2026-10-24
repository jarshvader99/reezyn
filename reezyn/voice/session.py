"""The hands-free voice loop.

Deterministic and clock-driven: the audio tap feeds frames, ``poll`` advances
the state machine, and every side effect (recognition, typing, sound) goes
through an injectable seam so the whole conversation can be tested without a
microphone, a permission prompt, or a terminal.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import VoiceConfig
from ..languages import DEFAULT_PACK, LanguagePack
from .asr import SpeechEngine, locale_for_language
from .audio import Microphone
from .typing import Typer
from .wake import (
    expand_dictation,
    find_wake,
    parse_intent,
    strip_wake,
    take_trailing_send,
)

log = logging.getLogger("reezyn")

OFF = "off"
IDLE = "idle"
LISTENING = "listening"
ERROR = "error"

POLL_S = 0.05
VOICE_FLOOR = 0.012


@dataclass(frozen=True, slots=True)
class VoiceSnapshot:
    state: str
    partial: str
    mode: str
    error: str
    inserted: int


class VoiceSession:
    def __init__(
        self,
        config: VoiceConfig,
        *,
        narrator: Any | None = None,
        pack: LanguagePack = DEFAULT_PACK,
        on_change: Callable[[VoiceSnapshot], None] | None = None,
        microphone: Any | None = None,
        engine: Any | None = None,
        typer: Typer | None = None,
        clock: Callable[[], float] = time.monotonic,
        auto_poll: bool = True,
        ack: Callable[[], None] | None = None,
        vocabulary: Callable[[], list[str]] | None = None,
    ) -> None:
        self._config = config
        self._narrator = narrator
        self._pack = pack
        self._on_change = on_change
        self._clock = clock
        self._auto_poll = auto_poll
        self._ack = ack
        self._vocabulary = vocabulary

        self._typer = typer or Typer(config.allow_apps, config.require_text_field)
        self._engine = engine
        self._microphone = microphone or Microphone(self.feed, self._on_audio_error)

        self._lock = threading.RLock()
        self._frames: deque[tuple[Any, float]] = deque(maxlen=128)
        self._running = False
        self._state = OFF
        self._mode = "wake"  # "wake" needs the phrase, "command" does not
        self._direct = False  # conversation turns submit on their own
        self._command_until = 0.0
        self._permission = False
        self._confirming = False
        self._wake_seen = False
        self._wake_acked = False
        self._partial = ""
        self._error = ""
        self._inserted = 0
        self._speaking = False
        self._noise = 0.01
        self._last_voice = 0.0
        self._rec_started = 0.0
        self._rec: Any | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return True
            self._running = True
            self._state = IDLE
            self._error = ""
        if self._microphone is not None:
            if not self._microphone.start():
                with self._lock:
                    self._running = False
                return False
        if self._auto_poll and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(
                target=self._run, name="reezyn-voice", daemon=True
            )
            self._thread.start()
        self._notify()
        return True

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._state = OFF
            rec = self._rec
            self._rec = None
        if rec is not None:
            rec.cancel()
        if self._microphone is not None:
            self._microphone.stop()
        self._notify()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
            try:
                self.poll()
            except Exception:
                log.exception("voice loop failed")
            time.sleep(POLL_S)

    def set_speaking(self, speaking: bool) -> None:
        with self._lock:
            self._speaking = speaking
            rec = self._rec if speaking else None
            if speaking:
                self._rec = None
                self._partial = ""
            elif self._confirming:
                # The question was spoken while the window was open; give the
                # answer the same amount of time once Reezyn is quiet.
                self._command_until = max(
                    self._command_until, self._clock() + self._config.confirm_s
                )
        if rec is not None:
            rec.cancel()

    def open_for_conversation(
        self, seconds: float | None = None, permission: bool = False
    ) -> None:
        with self._lock:
            if not self._running:
                return
            now = self._clock()
            self._mode = "command"
            self._direct = True
            self._permission = permission
            self._confirming = False
            self._command_until = now + (
                seconds if seconds is not None else self._config.conversation_s
            )
            self._state = LISTENING
            self._partial = ""
            self._error = ""
            self._wake_seen = False
            self._wake_acked = False
        self._play_ack()
        self._notify()

    def expect_permission(self, flag: bool = True) -> None:
        with self._lock:
            self._permission = flag

    def cancel(self) -> None:
        with self._lock:
            rec = self._rec
            self._rec = None
        if rec is not None:
            rec.cancel()
        self._reset_idle()

    def clear_error(self) -> None:
        with self._lock:
            self._error = ""
            self._state = IDLE if self._running else OFF
        self._notify()

    def status(self) -> VoiceSnapshot:
        with self._lock:
            return VoiceSnapshot(
                state=self._state,
                partial=self._partial,
                mode="conversation" if self._direct else self._mode,
                error=self._error,
                inserted=self._inserted,
            )

    # -- audio path --------------------------------------------------------

    def feed(self, buffer: Any, rms: float) -> None:
        with self._lock:
            self._frames.append((buffer, float(rms)))

    def _on_audio_error(self, message: str) -> None:
        with self._lock:
            self._error = message
            self._state = ERROR
        self._notify()

    def poll(self, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        with self._lock:
            frames = list(self._frames)
            self._frames.clear()
        for buffer, rms in frames:
            self._consume(buffer, rms, now)
        self._advance(now)

    def _consume(self, buffer: Any, rms: float, now: float) -> None:
        start = False
        with self._lock:
            if not self._running or self._error:
                return
            # While Reezyn speaks, only the wake phrase is listened for: that is
            # how the developer can cut in without reaching for the keyboard.
            if self._speaking and self._mode != "wake":
                return
            voiced = rms >= max(VOICE_FLOOR, self._noise * 3.0)
            if not voiced:
                if rms < 0.05:
                    self._noise = self._noise * 0.98 + rms * 0.02
                return
            self._last_voice = now
            if self._rec is None:
                if not self._can_listen(now):
                    return
                start = True
            rec = self._rec
        if start:
            # Creating a recognition session is not free; keep it out of the lock.
            self._start_recognition(now)
            with self._lock:
                rec = self._rec
        if rec is not None:
            rec.append(buffer)

    def _can_listen(self, now: float) -> bool:
        if self._mode == "wake":
            # Only arm the wake phrase when a terminal-like text field has the
            # caret. Anywhere else, nothing is captured at all.
            return self._typer.focus_ok()
        with self._lock:
            return now <= self._command_until

    def _start_recognition(self, now: float) -> None:
        engine = self._engine or self._make_engine()
        if engine is None:
            return
        try:
            recognition = engine.begin(self._context())
        except Exception as exc:
            self._error = str(exc)
            self._state = ERROR
            log.warning("voice recognition unavailable: %s", exc)
            return
        self._rec = recognition
        self._rec_started = now
        self._partial = ""
        self._state = LISTENING
        self._notify()

    def _make_engine(self) -> Any | None:
        locale = locale_for_language(self._pack.kokoro_lang, self._config.locale)
        try:
            self._engine = SpeechEngine(locale, on_device=True)
        except Exception as exc:
            self._error = f"Speech recognition is unavailable: {exc}"
            self._state = ERROR
            log.warning("could not create the speech engine: %s", exc)
            return None
        return self._engine

    def _context(self) -> list[str]:
        words = self._config.wake_word.split()
        context = [word.strip() for word in words if word.strip()]
        context.append("Reezyn")
        context += ["Claude Code", "Gemini", "Codex", "Cursor", "OpenCode", "Freebuff"]
        if self._vocabulary is not None:
            try:
                context += list(self._vocabulary())[:40]
            except Exception:
                log.debug("could not read project vocabulary", exc_info=True)
        return [item for item in dict.fromkeys(context) if item]

    # -- state machine -----------------------------------------------------

    def _advance(self, now: float) -> None:
        cancel = None
        finish = False
        expired = False
        with self._lock:
            if not self._running:
                return
            rec = self._rec
            if rec is not None:
                if self._speaking and self._mode != "wake":
                    self._rec = None
                    self._partial = ""
                    cancel = rec
                else:
                    partial = rec.partial
                    if self._mode == "wake" and not self._wake_seen and partial:
                        if partial != self._partial:
                            self._partial = partial
                            self._notify()
                        if find_wake(partial, self._config.wake_word):
                            self._enter_command(now)
                    silence_ms = (now - self._last_voice) * 1000.0
                    threshold = (
                        self._config.wake_silence_ms
                        if self._mode == "wake" and not self._wake_seen
                        else self._config.silence_ms
                    )
                    finish = (
                        silence_ms >= threshold and now - self._rec_started >= 0.25
                    ) or now - self._rec_started >= self._config.max_utterance_s
            elif self._mode == "command" and now >= self._command_until:
                self._mode = "wake"
                self._direct = False
                self._permission = False
                self._confirming = False
                self._state = IDLE
                self._partial = ""
                expired = True
        if cancel is not None:
            cancel.cancel()
        if finish:
            self._finish(now)
        if expired:
            self._notify()

    def _enter_command(self, now: float) -> None:
        self._wake_seen = True
        self._mode = "command"
        self._state = LISTENING
        self._command_until = now + self._config.follow_up_s
        if not self._wake_acked:
            self._wake_acked = True
            self._play_ack_later()
        narrator = self._narrator
        if narrator is not None:
            try:
                narrator.skip()
            except Exception:
                pass
        self._notify()

    def _finish(self, now: float) -> None:
        with self._lock:
            rec = self._rec
            self._rec = None
            if rec is None:
                return
            mode = self._mode
            wake_seen = self._wake_seen
            self._wake_seen = False
            self._partial = ""
        text = rec.end(timeout=1.2).strip()

        span = find_wake(text, self._config.wake_word) if text else None
        if mode == "wake" and not wake_seen and span is None:
            self._reset_idle()
            return
        if span is not None:
            text = strip_wake(text, span)
            if not wake_seen:
                self._wake_seen = True
                self._wake_acked = True
                self._play_ack_later()
        if not text:
            # Just the wake phrase: stay open for the actual request.
            with self._lock:
                self._mode = "command"
                self._state = LISTENING
                self._command_until = now + self._config.follow_up_s
            self._notify()
            return
        self._handle_text(text, now)

    def _handle_text(self, text: str, now: float) -> None:
        with self._lock:
            permission = self._permission
            confirming = self._confirming

        if not permission:
            # "…send it now" sends in one breath, with no question to answer.
            remaining, hot = take_trailing_send(text)
            if hot:
                self._send_now(remaining, now)
                return

        intent, value = parse_intent(
            text, permission=permission, confirmation=confirming and not permission
        )

        if intent == "empty":
            self._after_utterance(now)
            return
        if intent == "cancel":
            self._reset_idle()
            return
        if intent == "scratch":
            self._typer.backspace(self._inserted)
            self._inserted = 0
            with self._lock:
                self._confirming = False
            self._after_utterance(now)
            return
        if intent == "send":
            self._send_now("", now)
            return
        if intent == "keep":
            with self._lock:
                self._confirming = False
            self._after_utterance(now)
            return
        if intent in ("allow", "deny"):
            if intent == "allow":
                self._typer.press_return()
            else:
                self._typer.press_escape()
            with self._lock:
                self._permission = False
                self._confirming = False
                self._mode = "wake"
                self._direct = False
                self._state = IDLE
                self._partial = ""
            self._notify()
            return

        expanded = expand_dictation(value)
        if not expanded:
            self._after_utterance(now)
            return
        ok, reason = self._typer.guard()
        if not ok:
            self._fail(reason)
            return
        with self._lock:
            direct = self._direct
        self._inserted += self._typer.insert_text(expanded)
        if self._config.auto_send or (
            direct and self._config.auto_send_in_conversation
        ):
            self._typer.press_return()
            # The draft is gone, so "scratch that" must not reach back into the
            # message that was just submitted.
            self._inserted = 0
            with self._lock:
                self._confirming = False
            log.info("voice inserted and sent in conversation mode")
            self._after_utterance(now)
            return
        log.info("voice inserted %d characters", self._inserted)
        if self._ask_to_send(now):
            return
        self._after_utterance(now)

    def _send_now(self, remaining: str, now: float) -> None:
        """Submit the draft, optionally appending the words before the keyword."""

        expanded = expand_dictation(remaining) if remaining.strip() else ""
        if expanded:
            ok, reason = self._typer.guard()
            if not ok:
                self._fail(reason)
                return
            self._inserted += self._typer.insert_text(expanded)
        self._typer.press_return()
        self._inserted = 0
        with self._lock:
            self._confirming = False
        self._after_utterance(now)

    def _ask_to_send(self, now: float) -> bool:
        """After a dictated pause, ask before pressing Return.

        The conversational loop skips the question: there, a reply is already an
        answer, and the developer may be away from the screen.
        """

        if not self._config.confirm_send:
            return False
        with self._lock:
            if self._direct or self._config.auto_send:
                return False
            self._confirming = True
            self._mode = "command"
            self._command_until = (
                now + self._config.confirm_s + self._config.follow_up_s
            )
            self._state = LISTENING
            self._partial = ""
        self._speak_send_prompt()
        self._notify()
        return True

    def _speak_send_prompt(self) -> None:
        narrator = self._narrator
        if narrator is not None:
            try:
                narrator.announce(self._pack.phrase("send_prompt"))
                return
            except Exception:
                log.debug("could not speak the send prompt", exc_info=True)
        self._play_ack_later()

    def _fail(self, reason: str) -> None:
        with self._lock:
            self._error = reason
            self._state = ERROR
        log.warning("voice input blocked: %s", reason)
        self._notify()

    def _after_utterance(self, now: float) -> None:
        with self._lock:
            if self._mode == "command":
                self._command_until = now + self._config.follow_up_s
                self._state = LISTENING
            else:
                self._state = IDLE
            self._partial = ""
        self._notify()

    def _reset_idle(self) -> None:
        with self._lock:
            rec = self._rec
            self._rec = None
            self._mode = "wake"
            self._direct = False
            self._state = IDLE if self._running else OFF
            self._permission = False
            self._confirming = False
            self._wake_seen = False
            self._wake_acked = False
            self._partial = ""
        if rec is not None:
            rec.cancel()
        self._notify()

    # -- feedback ----------------------------------------------------------

    def _play_ack(self) -> None:
        if not self._config.ack_sound:
            return
        if self._ack is not None:
            try:
                self._ack()
            except Exception:
                pass
            return
        try:
            from AppKit import NSSound

            sound = NSSound.soundNamed_("Tink")
            if sound is not None:
                sound.play()
        except Exception:
            log.debug("could not play the listening sound", exc_info=True)

    def _play_ack_later(self) -> None:
        threading.Thread(target=self._play_ack, name="reezyn-ack", daemon=True).start()

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(self.status())
        except Exception:
            log.debug("voice change handler failed", exc_info=True)
