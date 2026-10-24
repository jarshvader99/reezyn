"""Speak up when the project has already been here.

Two doors into the same ledger. ``recall_for`` runs when a prompt arrives and
quotes the closest earlier conclusion, so a developer who is about to re-ask
something already settled hears it first. ``recap`` answers "where did we land"
from the last stretch of work.

Both are extractive. The line is the agent's own sentence, trimmed; the pack
only supplies the frame around it.
"""

from __future__ import annotations

import threading
import time

from .config import RecallConfig
from .events import Event
from .languages import DEFAULT_PACK, LanguagePack
from .ledger import (
    MAX_TEXT,
    ROLE_ACTION,
    ROLE_PROMPT,
    ROLE_THOUGHT,
    Entry,
    LedgerStore,
    Match,
)
from .narration import describe_tool, select_excerpt

THOUGHT_LEAD = 1.1  # how much better an action must score to win over a thought


def entry_for_event(
    event: Event, pack: LanguagePack = DEFAULT_PACK
) -> Entry | None:
    """The ledger shape of one live event, or None when it says nothing."""

    text = " ".join((event.text or "").split())
    if event.kind == "prompt":
        role: str = ROLE_PROMPT
        value = text
    elif event.kind == "reasoning":
        role = ROLE_THOUGHT
        value = text
    else:
        role = ROLE_ACTION
        value = describe_tool(event.tool_name, event.tool_input, pack).rstrip(".")
    if not value:
        return None
    return Entry(
        ts=event.ts,
        session=event.session_id,
        role=role,
        text=value[:MAX_TEXT],
        agent=event.agent,
        fingerprint=event.fingerprint,
    )


class Recaller:
    """Turns a fresh prompt into an optional line about earlier work."""

    def __init__(
        self,
        store: LedgerStore,
        config: RecallConfig,
        pack: LanguagePack = DEFAULT_PACK,
    ) -> None:
        self._store = store
        self._config = config
        self._pack = pack
        self._lock = threading.RLock()
        self._announced: dict[str, float] = {}
        self._last_at = 0.0

    @property
    def config(self) -> RecallConfig:
        return self._config

    def set_pack(self, pack: LanguagePack) -> None:
        with self._lock:
            self._pack = pack

    def set_config(self, config: RecallConfig) -> None:
        with self._lock:
            self._config = config

    def record(self, event: Event) -> bool:
        """Remember one event. False when the project is unknown."""

        if not event.project.strip():
            return False
        entry = entry_for_event(event, self._pack)
        if entry is None:
            return False
        return self._store.for_project(event.project).append(entry)

    def recall_for(self, event: Event, now: float | None = None) -> str:
        """A line about earlier work, or an empty string when silence is right."""

        prompt = event.text.strip()
        if not prompt or not event.project.strip():
            return ""
        now = time.time() if now is None else now
        with self._lock:
            if not self._config.enabled:
                return ""
            if now - self._last_at < self._config.cooldown_s:
                return ""
            pack = self._pack

        matches = self._store.for_project(event.project).search(
            prompt,
            threshold=self._config.threshold,
            before=event.ts or now,
            max_age_s=self._config.max_age_days * 86400.0,
            limit=6,
            now=now,
        )
        with self._lock:
            fresh = [
                match
                for match in matches
                if match.entry.fingerprint not in self._announced
            ]
        chosen = _prefer(fresh)
        if chosen is None:
            return ""

        when = self._when(chosen.entry.ts, chosen.entry.session, event.session_id, now)
        if chosen.entry.role == ROLE_THOUGHT:
            gist = select_excerpt(chosen.entry.text, 30, 2, pack).rstrip(" .!?")
            if not gist:
                return ""
            text = pack.phrase("recall_thought", when=when, gist=gist)
        else:
            text = pack.phrase(
                "recall_action", when=when, action=chosen.entry.text.rstrip(".")
            )
        with self._lock:
            self._announced[chosen.entry.fingerprint] = now
            for key, seen in list(self._announced.items()):
                if now - seen > 86400:
                    del self._announced[key]
            self._last_at = now
        return text

    def recap(
        self,
        project: str,
        window_s: float | None = None,
        now: float | None = None,
    ) -> str:
        """What was asked, what it concluded, and what it last touched."""

        now = time.time() if now is None else now
        window = self._config.recap_minutes * 60.0 if window_s is None else window_s
        pack = self._pack
        if not project.strip():
            return pack.phrase("recap_empty")
        entries = self._store.for_project(project).recent(window, now)
        if not entries:
            return pack.phrase("recap_empty")

        # Overlapping agent sessions would otherwise produce a recap that cuts
        # between two unrelated tasks.
        session = entries[-1].session
        own = [
            entry
            for entry in entries
            if not session or not entry.session or entry.session == session
        ]
        asked = _last(own, ROLE_PROMPT)
        thought = next(
            (
                entry
                for entry in reversed(own)
                if entry.role == ROLE_THOUGHT and len(entry.text.split()) >= 5
            ),
            None,
        )
        action = _last(own, ROLE_ACTION)

        parts: list[str] = []
        if asked is not None:
            text = select_excerpt(asked.text, 26, 1, pack).rstrip(" .!?")
            if text:
                parts.append(pack.phrase("recap_asked", text=text))
        if thought is not None:
            text = select_excerpt(thought.text, 30, 2, pack).rstrip(" .!?")
            if text:
                parts.append(pack.phrase("recap_decided", text=text))
        if action is not None:
            parts.append(pack.phrase("recap_last", action=action.text.rstrip(".")))
        return " ".join(parts) if parts else pack.phrase("recap_empty")

    def _when(self, ts: float, session: str, current_session: str, now: float) -> str:
        if session and session == current_session and now - ts < 12 * 3600:
            return self._pack.phrase("ago_session")
        if time.localtime(ts)[:3] == time.localtime(now)[:3]:
            return self._pack.phrase("ago_today")
        return self._pack.phrase("ago_past")


def _prefer(matches: list[Match]) -> Match | None:
    """Prefer a conclusion to a description unless the action is much better."""

    thought = next(
        (match for match in matches if match.entry.role == ROLE_THOUGHT), None
    )
    action = next((match for match in matches if match.entry.role == ROLE_ACTION), None)
    if thought and action:
        return action if action.score > thought.score * THOUGHT_LEAD else thought
    return thought or action


def _last(entries: list[Entry], role: str) -> Entry | None:
    for entry in reversed(entries):
        if entry.role == role:
            return entry
    return None
