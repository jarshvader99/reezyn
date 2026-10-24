"""The long-running Reezyn service shared by the menu app and tests."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters.claude import ClaudeHookAdapter
from .adapters.codex import CodexWatcher
from .adapters.cursor import CursorWatcher
from .adapters.freebuff import FreebuffWatcher
from .adapters.gemini import GeminiHookAdapter
from .adapters.opencode import OpenCodeWatcher
from .config import (
    Config,
    with_speed,
    with_voice,
)
from .events import Event
from .installers import InstallResult, install_hooks, is_installed
from .narration import Narrator
from .runtime import clear_running, write_running
from .spool import SpoolWatcher
from .tts import KokoroSpeaker, download_models

log = logging.getLogger("reezyn")


@dataclass(slots=True)
class ServiceStatus:
    state: str
    paused: bool
    speaking: bool
    current: str
    recent: list[str]
    last_error: str
    model_ready: bool
    model_error: str
    version: int
    simulating: bool
    active_agent: str


class ReezynService:
    def __init__(
        self,
        config: Config,
        home: Path,
        speaker: Any | None = None,
        on_change: Any | None = None,
    ) -> None:
        self.config = config
        self.home = home
        self.speaker = speaker or KokoroSpeaker(home, config.speech)
        self._on_change = on_change
        self._lock = threading.RLock()
        self._version = 0
        self._started = False
        self._simulating = False
        self._active_agent = ""

        self.claude = ClaudeHookAdapter()
        self.gemini = GeminiHookAdapter()
        self.narrator = Narrator(
            speak=self.speaker.speak,
            config=config.narration,
            stop_speech=self.speaker.stop,
            on_change=self._changed,
        )

        sources = {"generic"}
        if config.agents.claude:
            sources.add("claude")
        if config.agents.gemini:
            sources.add("gemini")
        self.spool = SpoolWatcher(home, self._on_spool, sources=sources)
        self.codex = CodexWatcher(self._on_event) if config.agents.codex else None
        self.cursor = CursorWatcher(self._on_event) if config.agents.cursor else None
        self.opencode = OpenCodeWatcher(self._on_event) if config.agents.opencode else None
        self.freebuff = FreebuffWatcher(self._on_event) if config.agents.freebuff else None
        self._preload_thread: threading.Thread | None = None
        self._download: dict[str, Any] | None = None

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        write_running(self.home)
        self.narrator.start()
        self.spool.start()
        for name, watcher in (
            ("Codex", self.codex),
            ("Cursor", self.cursor),
            ("OpenCode", self.opencode),
            ("Freebuff", self.freebuff),
        ):
            if watcher:
                try:
                    watcher.start()
                except Exception:
                    log.exception("Could not start the %s watcher", name)
        if self.speaker.ready:
            self._preload_thread = threading.Thread(
                target=self.speaker.preload, name="reezyn-preload", daemon=True
            )
            self._preload_thread.start()
        self._changed()

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        for watcher in (self.codex, self.cursor, self.opencode, self.freebuff):
            if watcher:
                watcher.stop()
        self.spool.stop()
        self.narrator.stop()
        self.speaker.stop()
        clear_running(self.home)

    def status(self) -> ServiceStatus:
        state = self.narrator.state()
        if not self.speaker.ready:
            state = "setup"
        with self._lock:
            active_agent = self._active_agent
        return ServiceStatus(
            state=state,
            paused=self.narrator.is_paused,
            speaking=self.narrator.is_speaking,
            current=self.narrator.current,
            recent=self.narrator.recent,
            last_error=self.narrator.last_error or self.speaker.load_error,
            model_ready=self.speaker.ready,
            model_error=self.speaker.load_error,
            version=self._version,
            simulating=self._simulating,
            active_agent=active_agent,
        )

    def pause(self) -> None:
        self.narrator.pause()

    def resume(self) -> None:
        self.narrator.resume()

    def toggle_pause(self) -> bool:
        paused = self.narrator.toggle_pause()
        self._changed()
        return paused

    def skip(self) -> None:
        self.narrator.skip()

    def set_voice(self, voice: str) -> None:
        self.config = with_voice(self.config, voice)
        self.speaker.set_voice(voice)
        self.config.save(self.home)
        self._changed()

    def set_speed(self, speed: float) -> None:
        self.config = with_speed(self.config, speed)
        self.speaker.set_speed(speed)
        self.config.save(self.home)
        self._changed()

    def install_agent(self, agent: str, system_home: Path | None = None) -> InstallResult:
        from typing import cast

        from .installers import AgentName

        return install_hooks(cast(AgentName, agent), system_home=system_home)

    def is_agent_installed(self, agent: str, system_home: Path | None = None) -> bool:
        from typing import cast

        from .installers import AgentName

        return is_installed(cast(AgentName, agent), system_home=system_home)

    def download_voice_model(self, progress: Any | None = None) -> None:
        def update(label: str, done: int, total: int) -> None:
            with self._lock:
                self._download = {
                    "label": label,
                    "done": done,
                    "total": total,
                    "percent": (done / total * 100.0) if total else 0.0,
                }
            self._changed()
            if progress:
                progress(label, done, total)

        with self._lock:
            self._download = {"label": "starting", "done": 0, "total": 0, "percent": 0.0}
        try:
            download_models(self.home, self.config.speech.model, update)
            self.speaker.preload()
        finally:
            with self._lock:
                self._download = None
            self._changed()

    @property
    def download_progress(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._download) if self._download else None

    def recent(self) -> list[str]:
        return self.narrator.recent

    def simulate(self, pause_between: float = 0.45) -> None:
        """Send a short, realistic session through the same queue as real agents."""

        with self._lock:
            if self._simulating:
                return
            self._simulating = True
        thread = threading.Thread(
            target=self._simulate_worker,
            args=(pause_between,),
            name="reezyn-simulate",
            daemon=True,
        )
        thread.start()

    def _simulate_worker(self, pause_between: float) -> None:
        events = [
            Event(
                agent="simulation",
                kind="reasoning",
                text=(
                    "The user wants me to find why login fails after a token refresh. "
                    "I'll start by reading the session middleware, since that's where "
                    "the refresh token gets validated."
                ),
            ),
            Event(agent="simulation", kind="tool", tool_name="Read", tool_input={"path": "src/auth/session.ts"}),
            Event(agent="simulation", kind="tool", tool_name="Grep", tool_input={"pattern": "refreshToken"}),
            Event(
                agent="simulation",
                kind="reasoning",
                text=(
                    "The refresh path is rotating the cookie but not invalidating the "
                    "old token. I'll check the test expectations before editing, so I "
                    "don't break the rotation flow."
                ),
            ),
            Event(agent="simulation", kind="tool", tool_name="Bash", tool_input={"command": "npm test -- auth"}),
            Event(
                agent="simulation",
                kind="reasoning",
                text=(
                    "The tests confirm the old token is accepted. I'll update the "
                    "validation to compare token versions."
                ),
            ),
            Event(agent="simulation", kind="tool", tool_name="Edit", tool_input={"file_path": "src/auth/session.ts"}),
            Event(
                agent="simulation",
                kind="reasoning",
                text=(
                    "Now I'll rerun the auth suite to make sure refresh works and the "
                    "stale token is rejected."
                ),
            ),
            Event(agent="simulation", kind="tool", tool_name="Bash", tool_input={"command": "npm test -- auth"}),
        ]
        try:
            for index, event in enumerate(events):
                self._submit_event(event)
                # A real tool call is fast; a new reasoning block usually takes longer.
                time.sleep(pause_between if index % 3 else 1.0)
        finally:
            with self._lock:
                self._simulating = False
            self._changed()

    def _on_spool(self, source: str, payload: dict[str, Any]) -> None:
        if source == "claude":
            events = self.claude.handle(payload)
        elif source == "gemini":
            events = self.gemini.handle(payload)
        else:
            events = _generic_events(payload)
        for event in events:
            self._submit_event(event)

    def _on_event(self, event: Event) -> None:
        self._submit_event(event)

    def _submit_event(self, event: Event) -> None:
        with self._lock:
            self._active_agent = event.agent
        self.narrator.submit(event)

    def _changed(self) -> None:
        with self._lock:
            self._version += 1
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                pass


def _generic_events(payload: dict[str, Any]) -> list[Event]:
    if isinstance(payload.get("events"), list):
        return [Event.from_dict(item) for item in payload["events"] if isinstance(item, dict)]
    if payload.get("kind") in ("reasoning", "tool") or payload.get("text") or payload.get("tool_name"):
        return [Event.from_dict(payload)]
    return []
