"""The macOS menu-bar face of Reezyn."""

from __future__ import annotations

import atexit
import subprocess
import threading
from pathlib import Path
from typing import Any

import rumps

from . import __version__
from .config import CONFIG_ENV, SPEEDS, Config, app_home, load_config
from .languages import LANGUAGES
from .service import ReezynService

AGENT_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex CLI",
    "gemini": "Gemini CLI",
    "cursor": "Cursor",
    "opencode": "OpenCode",
    "freebuff": "Freebuff",
    "simulation": "Simulation",
    "generic": "Custom",
}


class ReezynMenuApp(rumps.App):
    def __init__(self, config: Config | None = None, home: Path | None = None) -> None:
        super().__init__("Reezyn", title="reezyn", quit_button=None)
        self.home = home or app_home()
        self.config = config or load_config(self.home)
        self.service = ReezynService(self.config, self.home, on_change=self._service_changed)
        atexit.register(self.service.stop)
        self._last_title = ""
        self._last_status = ""
        self._download_error = ""
        self._download_thread: threading.Thread | None = None

        self.status_item = rumps.MenuItem("Starting…")
        self.agent_item = rumps.MenuItem("Active agent: none yet")
        self.agent_item.set_callback(None)
        self.last_item = rumps.MenuItem("Last spoken: nothing yet")
        self.last_item.set_callback(None)
        self.pause_item = rumps.MenuItem("Pause narration", callback=self._toggle_pause)
        self.skip_item = rumps.MenuItem("Skip this thought", callback=self._skip)
        self.recent_menu = rumps.MenuItem("Recent thoughts")
        self.recent_items: list[rumps.MenuItem] = []
        for _ in range(6):
            item = rumps.MenuItem("")
            item.set_callback(None)
            self.recent_items.append(item)
            self.recent_menu.add(item)

        voice_menu = rumps.MenuItem("Voice")
        self.voice_items: dict[str, rumps.MenuItem] = {}
        for pack in LANGUAGES:
            language_menu = rumps.MenuItem(pack.name)
            for voice_id, label in pack.voices.items():
                item = rumps.MenuItem(label, callback=self._make_voice_callback(voice_id))
                self.voice_items[voice_id] = item
                language_menu.add(item)
            voice_menu.add(language_menu)

        pace_menu = rumps.MenuItem("Pace")
        self.pace_items: dict[str, rumps.MenuItem] = {}
        for label, speed in SPEEDS.items():
            item = rumps.MenuItem(label, callback=self._make_speed_callback(speed))
            self.pace_items[label] = item
            pace_menu.add(item)

        hooks_menu = rumps.MenuItem("Install agent hooks")
        hooks_menu.add(rumps.MenuItem("Claude Code…", callback=self._install_claude))
        hooks_menu.add(rumps.MenuItem("Gemini CLI…", callback=self._install_gemini))
        hooks_menu.add(rumps.separator)
        watched_menu = rumps.MenuItem("Watched automatically")
        for label in ("Codex CLI", "Cursor", "OpenCode", "Freebuff"):
            item = rumps.MenuItem(label)
            item.set_callback(None)
            watched_menu.add(item)
        hooks_menu.add(watched_menu)

        self.download_item = rumps.MenuItem(
            "Download voice model…", callback=self._download_model
        )
        self.simulate_item = rumps.MenuItem("Simulate a session", callback=self._simulate)
        self.open_item = rumps.MenuItem("Open Reezyn folder", callback=self._open_folder)
        self.about_item = rumps.MenuItem("About Reezyn", callback=self._about)
        self.quit_item = rumps.MenuItem("Quit Reezyn", callback=self._quit)

        self.menu = [
            self.status_item,
            self.agent_item,
            self.last_item,
            rumps.separator,
            self.pause_item,
            self.skip_item,
            self.recent_menu,
            rumps.separator,
            voice_menu,
            pace_menu,
            rumps.separator,
            hooks_menu,
            self.download_item,
            self.simulate_item,
            self.open_item,
            self.about_item,
            rumps.separator,
            self.quit_item,
        ]
        self._refresh_checks()
        self._refresh_recent()
        self._tick()

    def run(self) -> None:
        self.service.start()
        timer = rumps.Timer(self._tick, 0.35)
        timer.start()
        super().run()

    def _service_changed(self) -> None:
        # The timer is the only UI mutation point; this callback exists so the
        # service can be used headlessly in tests as well.
        return

    def _tick(self, _timer: Any = None) -> None:
        status = self.service.status()
        download = self.service.download_progress

        if download:
            label = str(download.get("label") or "model")
            percent = float(download.get("percent") or 0.0)
            self.status_item.title = f"Downloading {label} — {percent:.0f}%"
            title = "reezyn ↓"
        elif status.state == "setup":
            self.status_item.title = "Voice model not installed — download it below"
            title = "reezyn !"
        elif status.state == "error":
            detail = status.last_error or "Speech error"
            self.status_item.title = f"Speech error: {detail[:80]}"
            title = "reezyn !"
        elif status.paused:
            self.status_item.title = "Paused"
            title = "reezyn ⏸"
        elif status.speaking:
            self.status_item.title = _shorten(status.current or "Speaking…", 84)
            title = "reezyn ·"
        elif status.simulating:
            self.status_item.title = "Simulating an agent session…"
            title = "reezyn"
        else:
            self.status_item.title = "Listening for agent reasoning"
            title = "reezyn"

        if title != self._last_title:
            self.title = title
            self._last_title = title

        agent = _agent_label(status.active_agent)
        self.agent_item.title = f"Active agent: {agent}" if agent else "Active agent: none yet"
        last_spoken = status.recent[0] if status.recent else ""
        self.last_item.title = (
            f"Last spoken: {_shorten(last_spoken, 72)}"
            if last_spoken
            else "Last spoken: nothing yet"
        )

        self.pause_item.title = "Resume narration" if status.paused else "Pause narration"
        self.download_item.hidden = status.model_ready
        if status.state != self._last_status:
            self._refresh_recent()
            self._last_status = status.state
        else:
            # Captions should appear promptly even when the state is unchanged.
            self._refresh_recent()

        if self._download_error:
            message = self._download_error
            self._download_error = ""
            rumps.alert(title="Could not download the voice model", message=message, ok="OK")

    def _refresh_checks(self) -> None:
        for voice_id, item in self.voice_items.items():
            item.state = 1 if voice_id == self.config.speech.voice else 0
        current = min(
            SPEEDS.items(),
            key=lambda pair: abs(pair[1] - self.config.speech.speed),
        )[0]
        for label, item in self.pace_items.items():
            item.state = 1 if label == current else 0

    def _refresh_recent(self) -> None:
        recent = self.service.recent()
        if not recent:
            self.recent_items[0].title = "Nothing yet"
            for item in self.recent_items[1:]:
                item.title = ""
            return
        for index, item in enumerate(self.recent_items):
            item.title = _shorten(recent[index], 64) if index < len(recent) else ""

    def _toggle_pause(self, _sender: Any = None) -> None:
        self.service.toggle_pause()
        self._tick()

    def _skip(self, _sender: Any = None) -> None:
        self.service.skip()
        self._tick()

    def _make_voice_callback(self, voice_id: str) -> Any:
        def choose(_sender: Any) -> None:
            self.service.set_voice(voice_id)
            self.config = self.service.config
            self._refresh_checks()

        return choose

    def _make_speed_callback(self, speed: float) -> Any:
        def choose(_sender: Any) -> None:
            self.service.set_speed(speed)
            self.config = self.service.config
            self._refresh_checks()

        return choose

    def _install_claude(self, _sender: Any = None) -> None:
        self._install_agent(
            "claude",
            "Install Reezyn's Claude Code hook?\n\n"
            "This adds one async PreToolUse hook to ~/.claude/settings.json. "
            "Reezyn keeps a backup and never replaces your other hooks.",
        )

    def _install_gemini(self, _sender: Any = None) -> None:
        self._install_agent(
            "gemini",
            "Install Reezyn's Gemini CLI hook?\n\n"
            "This adds one BeforeTool hook to ~/.gemini/settings.json. "
            "Reezyn keeps a backup and never replaces your other hooks.",
        )

    def _install_agent(self, agent: str, message: str) -> None:
        if rumps.alert(title=f"Install {agent.title()} hook", message=message, ok="Install", cancel="Cancel") != 1:
            return
        try:
            result = self.service.install_agent(agent)
        except Exception as exc:
            rumps.alert(title="Hook install failed", message=str(exc), ok="OK")
            return
        if result.changed:
            text = f"Hook installed in {result.path}.\n\nStart a new {agent.title()} session to hear it."
            if result.backup:
                text += f"\n\nBackup: {result.backup}"
        else:
            text = f"The hook is already present in {result.path}."
        rumps.alert(title="Reezyn hook", message=text, ok="Done")

    def _download_model(self, _sender: Any = None) -> None:
        if self._download_thread and self._download_thread.is_alive():
            return
        if self.service.speaker.ready:
            return
        self._download_thread = threading.Thread(
            target=self._download_worker, name="reezyn-download", daemon=True
        )
        self._download_thread.start()

    def _download_worker(self) -> None:
        try:
            self.service.download_voice_model()
        except Exception as exc:
            self._download_error = str(exc)

    def _simulate(self, _sender: Any = None) -> None:
        self.service.simulate()

    def _open_folder(self, _sender: Any = None) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(self.home)])

    def _about(self, _sender: Any = None) -> None:
        rumps.alert(
            title="Reezyn",
            message=(
                f"Version {__version__}\n\n"
                "A calm, local narrator for coding-agent reasoning.\n\n"
                "Kokoro speech · Apache-2.0 model · no telemetry\n"
                f"State: {self.home}\n\n"
                f"Set {CONFIG_ENV} to move Reezyn's state directory."
            ),
            ok="OK",
        )

    def _quit(self, _sender: Any = None) -> None:
        self.service.stop()
        rumps.quit_application()


def _agent_label(agent: str) -> str:
    if not agent:
        return ""
    return AGENT_LABELS.get(agent, agent.replace("_", " ").replace("-", " ").strip().title())


def _shorten(text: str, width: int) -> str:
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "…"


def run_menu(config: Config | None = None, home: Path | None = None) -> None:
    ReezynMenuApp(config=config, home=home).run()
