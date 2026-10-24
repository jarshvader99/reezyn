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
from .ambience import Ambience
from .config import (
    Config,
    with_speed,
    with_voice,
)
from .events import Event
from .installers import InstallResult, install_hooks, is_installed
from .languages import pack_for_voice
from .ledger import LedgerStore
from .narration import Narrator, select_excerpt
from .recall import Recaller
from .remote import RemoteServer
from .runtime import clear_running, write_running
from .spool import SpoolWatcher
from .system_audio import DeviceWatcher
from .tts import KokoroSpeaker, download_models
from .voice import VoiceSession, VoiceSnapshot

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
    ambience: bool
    ambience_error: str


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
        # Set while a paired phone is reading new lines aloud. The Mac then
        # stays silent: its narration is what makes macOS pull the AirPods off
        # the phone, and two voices reading the same line is noise anyway.
        self._remote_listening = False
        # The phone cannot own Bluetooth headphones while the Mac's microphone
        # or presence bed has an open stream either, so those are parked too.
        self._ambience_parked_for_phone = False

        self.claude = ClaudeHookAdapter()
        self.gemini = GeminiHookAdapter()
        self.ambience = Ambience(config.ambience)
        self.narrator = Narrator(
            speak=self._speak_narration,
            config=config.narration,
            stop_speech=self.speaker.stop,
            on_change=self._changed,
            on_speaking=self._on_speaking,
            pack=pack_for_voice(config.speech.voice),
            banter=config.banter,
        )
        self.recaller = Recaller(
            LedgerStore(home),
            config.recall,
            pack=pack_for_voice(config.speech.voice),
        )
        self._last_project = ""
        self.remote: RemoteServer | None = None
        # A permission event is the one thing the phone must act on; it stays
        # visible until an answer arrives or another agent event moves work on.
        self._pending_permission: dict[str, Any] | None = None
        self.voice: VoiceSession | None = None
        # Set while a closing answer is being spoken, so the microphone opens
        # when speech ends rather than while Reezyn is still talking.
        self._voice_pending = ""
        # Incremented whenever the system's default input or output changes, so
        # the menu can refresh state that belongs to the old device.
        self._device_epoch = 0
        self._devices = DeviceWatcher(
            self._input_device_changed, self._output_device_changed
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
        if self.config.voice.enabled:
            self._start_voice()
        if not self.ambience.start():
            log.warning("Presence bed did not start: %s", self.ambience.error)
        self._devices.start()
        self.spool.start()
        if self.config.remote.enabled:
            self._start_remote()
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
        self._devices.stop()
        self.spool.stop()
        self._stop_remote()
        self.narrator.stop()
        if self.voice is not None:
            self.voice.stop()
        self.ambience.stop()
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
            ambience=self.ambience.running,
            ambience_error=self.ambience.error,
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

    # -- voice input -------------------------------------------------------

    def _start_voice(self) -> bool:
        if self._remote_listening:
            # The phone owns the audio route; starting the microphone here
            # would pull the AirPods back to the Mac.
            return True
        if self.voice is None:
            self.voice = VoiceSession(
                self.config.voice,
                narrator=self.narrator,
                on_change=self._voice_changed,
                on_permission_answer=self._permission_answered,
            )
        return self.voice.start()

    def set_voice_input(self, enabled: bool) -> bool:
        self.config.voice.enabled = enabled
        if enabled:
            ok = self._start_voice()
        else:
            if self.voice is not None:
                self.voice.stop()
            ok = True
        with self._lock:
            self._voice_pending = ""
        self.config.save(self.home)
        self._changed()
        return ok

    def set_conversation(self, enabled: bool) -> None:
        self.config.voice.conversation = enabled
        self.config.save(self.home)
        self._changed()

    def set_confirm_send(self, enabled: bool) -> None:
        self.config.voice.confirm_send = enabled
        self.config.save(self.home)
        self._changed()

    def voice_snapshot(self) -> VoiceSnapshot | None:
        return self.voice.status() if self.voice is not None else None

    def _voice_changed(self, _snapshot: VoiceSnapshot | None = None) -> None:
        self._changed()

    def _speak_narration(self, text: str) -> bool:
        with self._lock:
            if self._remote_listening:
                return True
        return self.speaker.speak(text)

    def set_remote_listening(self, active: bool) -> None:
        """Mute the Mac while a phone is reading the feed aloud.

        AirPods automatically hop to whichever device starts playing. Reezyn
        narrates often, so leaving the Mac output running while the phone reads
        keeps taking the headphones away from the phone and leaves it silent.
        Releasing the output lets the phone keep the headphones, and the line
        still lands in the feed and `recent` for the phone to read.
        """

        with self._lock:
            if self._remote_listening == active:
                return
            self._remote_listening = active
        if active:
            release = getattr(self.speaker, "reset_output", None)
            try:
                if callable(release):
                    release()
                else:
                    self.speaker.stop()
            except Exception:
                log.debug("could not release the Mac output", exc_info=True)
            if self.ambience.running:
                self._ambience_parked_for_phone = True
                self.ambience.stop()
            voice = self.voice
            if voice is not None and self.config.voice.enabled:
                self.voice = None
                with self._lock:
                    self._voice_pending = ""
                try:
                    voice.stop()
                except Exception:
                    log.debug("could not release the Mac microphone", exc_info=True)
            log.info("phone read-aloud is on; Mac narration is muted")
        else:
            if self._ambience_parked_for_phone:
                self._ambience_parked_for_phone = False
                if self.config.ambience.enabled and not self.ambience.running:
                    self.ambience.start()
            if self.config.voice.enabled and self.voice is None:
                try:
                    self._start_voice()
                except Exception:
                    log.warning("could not reopen the Mac microphone", exc_info=True)
            log.info("phone read-aloud is off; Mac narration is resumed")
        self._changed()

    @property
    def device_epoch(self) -> int:
        return self._device_epoch

    def _input_device_changed(self, _device: int = 0) -> None:
        self._device_epoch += 1
        log.info("default input device changed")
        voice = self.voice
        if voice is not None and self.config.voice.enabled:
            restart = getattr(voice, "restart_microphone", None)
            if callable(restart):
                restart()
        self._changed()

    def _output_device_changed(self, _device: int = 0) -> None:
        self._device_epoch += 1
        log.info("default output device changed")
        # The presence bed owns its own PortAudio stream; close it before the
        # speaker rebuilds PortAudio's device list, then reopen it on the new
        # default device.
        ambience_was_running = self.ambience.running
        if ambience_was_running:
            self.ambience.stop()
        reset = getattr(self.speaker, "reset_output", None)
        if callable(reset):
            reset()
        if ambience_was_running:
            self.ambience.start()
        self._changed()

    def set_voice(self, voice: str) -> None:
        self.config = with_voice(self.config, voice)
        self.speaker.set_voice(voice)
        self.narrator.set_pack(pack_for_voice(voice))
        self.recaller.set_pack(pack_for_voice(voice))
        self.config.save(self.home)
        self._changed()

    def set_ambience(self, enabled: bool) -> bool:
        self.config.ambience.enabled = enabled
        if enabled:
            ok = self.ambience.start()
            if not ok:
                self.config.ambience.enabled = False
        else:
            self.ambience.stop()
            ok = True
        self.config.save(self.home)
        self._changed()
        return ok

    def set_ambience_level(self, volume: float) -> bool:
        self.config.ambience.volume = max(0.0, min(1.0, volume))
        return self.set_ambience(True)

    def toggle_ambience(self) -> bool:
        return self.set_ambience(not self.ambience.running)

    def set_banter(self, enabled: bool) -> None:
        self.config.banter.enabled = enabled
        if enabled:
            self.narrator.reset_banter()
        self.config.save(self.home)
        self._changed()

    def set_recall(self, enabled: bool) -> None:
        self.config.recall.enabled = enabled
        self.recaller.set_config(self.config.recall)
        self.config.save(self.home)
        self._changed()

    def recap(self, minutes: float | None = None) -> str:
        """Speak where the last stretch of work landed, and return the line."""

        with self._lock:
            project = self._last_project
        window = minutes * 60.0 if minutes else None
        try:
            text = self.recaller.recap(project, window_s=window)
        except Exception:
            log.debug("recap failed", exc_info=True)
            return ""
        if text:
            self.narrator.announce(text)
        return text

    # -- remote access -----------------------------------------------------

    def set_remote(self, enabled: bool) -> bool:
        self.config.remote.enabled = enabled
        if enabled:
            ok = self._start_remote()
            if not ok:
                self.config.remote.enabled = False
        else:
            self._stop_remote()
            ok = True
        self.config.save(self.home)
        self._changed()
        return ok

    def _start_remote(self) -> bool:
        if self.remote is not None and self.remote.running:
            return True
        server = RemoteServer(self, self.home, self.config.remote)
        started = server.start()
        self.remote = server
        if not started:
            log.warning("remote access could not start: %s", server.info().get("error"))
        self._changed()
        return started

    def _stop_remote(self) -> None:
        server = self.remote
        self.remote = None
        if server is not None:
            server.stop()
        with self._lock:
            self._pending_permission = None

    def remote_info(self) -> dict[str, Any]:
        if self.remote is not None:
            return self.remote.info()
        return {
            "running": False,
            "url": "",
            "local_url": "",
            "paired": 0,
            "code": "",
            "error": "",
            "locked": False,
            "queued": 0,
            "terminal": "",
        }

    def remote_wait(self, timeout: float = 10.0) -> dict[str, Any]:
        if self.remote is not None:
            return self.remote.wait_publish(timeout)
        return self.remote_info()

    def remote_new_code(self) -> str:
        return self.remote.new_code() if self.remote is not None else ""

    def remote_forget_phones(self) -> int:
        return self.remote.forget_phones() if self.remote is not None else 0

    def remote_context(self) -> dict[str, Any]:
        with self._lock:
            return {"project": self._last_project, "agent": self._active_agent}

    def remote_activity(self, kind: str, text: str = "") -> None:
        # Never log the reply itself: it is a prompt, not diagnostics.
        log.info("remote %s", kind)

    def remote_permission_answered(self, allow: bool) -> None:
        with self._lock:
            self._pending_permission = None
        self.remote_activity("permission", "allow" if allow else "deny")
        self._changed()

    def remote_snapshot(self) -> dict[str, Any]:
        status = self.status()
        voice = self.voice_snapshot()
        with self._lock:
            project = self._last_project
            phone_audio = self._remote_listening
            pending = dict(self._pending_permission) if self._pending_permission else None
        if pending is not None:
            age = time.time() - float(pending.get("ts") or 0)
            if age > 180.0:
                pending = None
                with self._lock:
                    self._pending_permission = None
        return {
            "state": status.state,
            "paused": status.paused,
            "speaking": status.speaking,
            "current": status.current,
            "recent": list(status.recent),
            "active_agent": status.active_agent,
            "agent": status.active_agent,
            "project": project,
            "last_error": status.last_error,
            "model_ready": status.model_ready,
            "simulating": status.simulating,
            "phone_audio": phone_audio,
            "voice": (
                {
                    "state": voice.state,
                    "partial": voice.partial,
                    "mode": voice.mode,
                    "error": voice.error,
                }
                if voice is not None
                else None
            ),
            "permission": pending,
        }

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

    def _on_speaking(self, speaking: bool) -> None:
        self.ambience.set_ducked(speaking)
        voice = self.voice
        if voice is None:
            return
        voice.set_speaking(speaking)
        if speaking:
            return
        with self._lock:
            pending = self._voice_pending
            self._voice_pending = ""
        if pending:
            # The agent's turn is over and its answer has been spoken: hand the
            # conversation back to the developer without another wake word.
            voice.open_for_conversation(permission=(pending == "permission"))

    def _permission_answered(self, _allow: bool) -> None:
        with self._lock:
            self._pending_permission = None
        self._changed()

    def _on_event(self, event: Event) -> None:
        self._submit_event(event)

    def _submit_event(self, event: Event) -> None:
        with self._lock:
            self._active_agent = event.agent
            if event.project:
                self._last_project = event.project
        try:
            self.recaller.record(event)
        except Exception:
            log.debug("could not record event", exc_info=True)
        if event.kind == "prompt":
            try:
                line = self.recaller.recall_for(event)
            except Exception:
                log.debug("recall failed", exc_info=True)
                line = ""
            if line:
                self.narrator.announce(line)
        if event.kind == "permission":
            with self._lock:
                self._pending_permission = {
                    "text": " ".join((event.text or "").split())[:1200],
                    "agent": event.agent,
                    "session": event.session_id,
                    "ts": time.time(),
                }
            self._offer_conversation(event, permission=True)
        elif event.kind in ("tool", "prompt", "closing"):
            with self._lock:
                pending = self._pending_permission
                if pending is not None and pending.get("agent") == event.agent:
                    if (
                        not pending.get("session")
                        or not event.session_id
                        or pending.get("session") == event.session_id
                    ):
                        self._pending_permission = None
            if event.kind == "closing":
                self._offer_conversation(event, permission=False)
        self.narrator.submit(event)

    def _offer_conversation(self, event: Event, permission: bool) -> None:
        """Speak the agent's last words, then open the microphone for an answer."""

        voice = self.voice
        if voice is None or not self.config.voice.enabled:
            return
        if not self.config.voice.conversation:
            return

        # If the developer is already mid-sentence, their words win: don't talk
        # over them or reset the draft they are dictating.
        snapshot = voice.status()
        if snapshot is not None and snapshot.state == "listening" and snapshot.partial:
            return

        pack = pack_for_voice(self.config.speech.voice)
        text = " ".join(event.text.split())
        if permission and not text:
            agent = event.agent.replace("-", " ").title() or "The agent"
            text = f"{agent} needs your permission to run a tool. Say allow or deny."
        line = ""
        if text:
            line = select_excerpt(
                text,
                self.config.voice.max_answer_words,
                0 if permission else 2,
                pack,
            )
        if not line:
            voice.open_for_conversation(permission=permission)
            return

        with self._lock:
            self._voice_pending = "permission" if permission else "closing"
        self.narrator.announce(line)
        # A paused narrator, a missing voice model, or a speech error would
        # never report the end of speech, so open the microphone directly.
        if self.narrator.is_paused or not self.speaker.ready:
            with self._lock:
                self._voice_pending = ""
            voice.open_for_conversation(permission=permission)
            return
        threading.Thread(
            target=self._watch_pending_voice,
            args=(3.0 + len(line.split()) / 4.0,),
            name="reezyn-voice-open",
            daemon=True,
        ).start()

    def _watch_pending_voice(self, delay: float) -> None:
        """Open the microphone if an answer was never actually spoken."""

        time.sleep(max(1.0, delay))
        with self._lock:
            pending = self._voice_pending
            if not pending or self.narrator.is_speaking:
                return
            self._voice_pending = ""
        voice = self.voice
        if voice is not None:
            voice.open_for_conversation(permission=(pending == "permission"))

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
    if payload.get("kind") in ("reasoning", "tool", "prompt") or payload.get("text") or payload.get("tool_name"):
        return [Event.from_dict(payload)]
    return []
