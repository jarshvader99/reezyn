"""The macOS menu-bar face of Reezyn."""

from __future__ import annotations

import atexit
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import rumps

from . import __version__
from .config import AMBIENCE_LEVELS, CONFIG_ENV, SPEEDS, Config, app_home, load_config
from .languages import LANGUAGES
from .service import ReezynService
from .status_icon import icon_alpha, reduce_motion, seedling_image

log = logging.getLogger("reezyn")

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
        self._permissions: dict[str, str] = {}
        self._permissions_checked = 0.0
        self._download_error = ""
        self._download_thread: threading.Thread | None = None

        self._icon_ready = False
        self._icon_failed = False
        self._icon_alpha: float | None = None
        self._breathing = False
        self._breath_started = 0.0
        self._reduce_motion = False
        self._breath_timer = rumps.Timer(self._breathe, 0.1)

        self.status_item = rumps.MenuItem("Starting…")
        self.agent_item = rumps.MenuItem("Active agent: none yet")
        self.agent_item.set_callback(None)
        self.last_item = rumps.MenuItem("Last spoken: nothing yet")
        self.last_item.set_callback(None)
        self.pause_item = rumps.MenuItem("Pause narration", callback=self._toggle_pause)
        self.skip_item = rumps.MenuItem("Skip this thought", callback=self._skip)
        self.recent_menu = rumps.MenuItem("Recent thoughts")
        self.recap_item = rumps.MenuItem("Recap the last session", callback=self._recap)
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

        self.ambience_menu = rumps.MenuItem("Presence bed")
        self.ambience_off_item = rumps.MenuItem("Off", callback=self._disable_ambience)
        self.ambience_menu.add(self.ambience_off_item)
        self.ambience_menu.add(rumps.separator)
        self.ambience_items: dict[str, rumps.MenuItem] = {}
        for level_label, level in AMBIENCE_LEVELS.items():
            item = rumps.MenuItem(level_label, callback=self._make_ambience_callback(level))
            self.ambience_items[level_label] = item
            self.ambience_menu.add(item)

        self.banter_item = rumps.MenuItem("Lull banter", callback=self._toggle_banter)
        self.recall_item = rumps.MenuItem(
            "Recall when I re-ask", callback=self._toggle_recall
        )

        self.voice_input_item = rumps.MenuItem(
            "Voice input", callback=self._toggle_voice_input
        )
        self.conversation_item = rumps.MenuItem(
            "Talk back after a turn", callback=self._toggle_conversation
        )
        self.confirm_item = rumps.MenuItem(
            "Ask before sending", callback=self._toggle_confirm_send
        )
        self.permissions_menu = rumps.MenuItem("Voice permissions")
        self.permission_items: dict[str, rumps.MenuItem] = {}
        for key, label in (
            ("microphone", "Microphone"),
            ("speech", "Speech Recognition"),
            ("accessibility", "Accessibility"),
        ):
            item = rumps.MenuItem(f"{label}: checking…")
            item.set_callback(None)
            self.permission_items[key] = item
            self.permissions_menu.add(item)
        self.permissions_menu.add(rumps.separator)
        self.permissions_menu.add(
            rumps.MenuItem("Request permissions…", callback=self._request_permissions)
        )
        self.permissions_menu.add(
            rumps.MenuItem(
                "Open Microphone settings…",
                callback=self._make_settings_callback("microphone"),
            )
        )
        self.permissions_menu.add(
            rumps.MenuItem(
                "Open Speech Recognition settings…",
                callback=self._make_settings_callback("speech"),
            )
        )
        self.permissions_menu.add(
            rumps.MenuItem(
                "Open Accessibility settings…",
                callback=self._make_settings_callback("accessibility"),
            )
        )

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
            self.recap_item,
            rumps.separator,
            voice_menu,
            pace_menu,
            self.ambience_menu,
            self.banter_item,
            self.recall_item,
            rumps.separator,
            self.voice_input_item,
            self.conversation_item,
            self.confirm_item,
            self.permissions_menu,
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
        self._update_icon(status)
        icon_ok = self._icon_ready and not self._icon_failed

        voice = self.service.voice_snapshot()
        if download:
            label = str(download.get("label") or "model")
            percent = float(download.get("percent") or 0.0)
            self.status_item.title = f"Downloading {label} — {percent:.0f}%"
            title = "↓" if icon_ok else "reezyn ↓"
        elif voice is not None and voice.state == "error" and voice.error:
            self.status_item.title = f"Voice input: {_shorten(voice.error, 84)}"
            title = "!" if icon_ok else "reezyn !"
        elif voice is not None and voice.state == "listening":
            heard = voice.partial or "Listening…"
            prefix = "Reply" if voice.mode == "conversation" else "Listening"
            self.status_item.title = f'{prefix}: "{_shorten(heard, 76)}"'
            title = "●" if icon_ok else "reezyn ●"
        elif status.state == "setup":
            self.status_item.title = "Voice model not installed — download it below"
            title = "!" if icon_ok else "reezyn !"
        elif status.state == "error":
            detail = status.last_error or "Speech error"
            self.status_item.title = f"Speech error: {detail[:80]}"
            title = "!" if icon_ok else "reezyn !"
        elif status.paused:
            self.status_item.title = "Paused"
            title = "⏸" if icon_ok else "reezyn ⏸"
        elif status.speaking:
            self.status_item.title = _shorten(status.current or "Speaking…", 84)
            if not icon_ok:
                title = "reezyn ·"
            else:
                title = "·" if self._reduce_motion else ""
        elif status.simulating:
            self.status_item.title = "Simulating an agent session…"
            title = "" if icon_ok else "reezyn"
        else:
            self.status_item.title = "Listening for agent reasoning"
            title = "" if icon_ok else "reezyn"

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
        self.ambience_menu.state = 1 if status.ambience else 0
        self.ambience_off_item.state = 0 if status.ambience else 1
        volume = self.service.config.ambience.volume
        for level_label, item in self.ambience_items.items():
            item.state = (
                1
                if status.ambience and abs(volume - AMBIENCE_LEVELS[level_label]) < 0.01
                else 0
            )
        self.banter_item.state = 1 if self.service.config.banter.enabled else 0
        self.recall_item.state = 1 if self.service.config.recall.enabled else 0
        self.voice_input_item.state = 1 if self.service.config.voice.enabled else 0
        self.conversation_item.state = 1 if self.service.config.voice.conversation else 0
        self.confirm_item.state = 1 if self.service.config.voice.confirm_send else 0
        self._refresh_permissions()
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

    def _update_icon(self, status: Any) -> None:
        nsapp = getattr(self, "_nsapp", None)
        status_item = getattr(nsapp, "nsstatusitem", None) if nsapp is not None else None
        if status_item is None:
            return
        if not self._icon_ready and not self._icon_failed:
            self._install_icon(status_item)
        if not self._icon_ready or self._icon_failed:
            return

        self._reduce_motion = reduce_motion()
        if status.speaking and not self._reduce_motion:
            if not self._breathing:
                self._breathing = True
                self._breath_started = time.monotonic()
                self._breath_timer.start()
            self._set_icon_alpha(
                icon_alpha("speaking", time.monotonic() - self._breath_started),
                status_item,
            )
            return

        if self._breathing:
            self._breathing = False
            self._breath_timer.stop()
        self._set_icon_alpha(
            icon_alpha("paused" if status.paused else "listening"), status_item
        )

    def _install_icon(self, status_item: Any) -> None:
        try:
            button = status_item.button()
        except Exception:
            button = None
        if button is not None:
            try:
                button.setAccessibilityLabel_("Reezyn")
                button.setToolTip_("Reezyn — calm narration for agent reasoning")
            except Exception as exc:
                log.debug("could not label the status item: %s", exc)

        self._set_icon_alpha(1.0, status_item)
        if not self._icon_failed:
            self._icon_ready = True
            log.info("menu bar seedling icon ready")

    def _set_icon_alpha(self, alpha: float, status_item: Any) -> None:
        quantized = round(alpha * 48) / 48
        if quantized == self._icon_alpha:
            return
        try:
            status_item.setImage_(seedling_image(quantized))
        except Exception as exc:
            self._icon_failed = True
            log.warning("could not install the menu bar icon: %s", exc)
            return
        self._icon_alpha = quantized

    def _breathe(self, _timer: Any = None) -> None:
        if not self._icon_ready or self._icon_failed:
            self._breathing = False
            self._breath_timer.stop()
            return
        nsapp = getattr(self, "_nsapp", None)
        status_item = getattr(nsapp, "nsstatusitem", None) if nsapp is not None else None
        if status_item is None:
            return
        self._set_icon_alpha(
            icon_alpha("speaking", time.monotonic() - self._breath_started),
            status_item,
        )

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
            "Install Reezyn's Claude Code hooks?\n\n"
            "This adds four hooks to ~/.claude/settings.json: PreToolUse and "
            "UserPromptSubmit so Reezyn follows your work, Stop so it can speak "
            "the final answer and open the microphone, and Notification for "
            "permission prompts. Reezyn keeps a backup and never replaces your "
            "other hooks.",
        )

    def _install_gemini(self, _sender: Any = None) -> None:
        self._install_agent(
            "gemini",
            "Install Reezyn's Gemini CLI hooks?\n\n"
            "This adds four hooks to ~/.gemini/settings.json: BeforeTool and "
            "BeforeAgent so Reezyn follows your work, AfterAgent so it can speak "
            "the final answer and open the microphone, and Notification for "
            "permission prompts. Reezyn keeps a backup and never replaces your "
            "other hooks.",
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

    def _make_ambience_callback(self, level: float) -> Any:
        def choose(_sender: Any) -> None:
            ok = self.service.set_ambience_level(level)
            self.config = self.service.config
            self._tick()
            if not ok:
                error = (
                    self.service.status().ambience_error
                    or "No audio output device is available."
                )
                rumps.alert(title="Presence bed unavailable", message=error, ok="OK")

        return choose

    def _disable_ambience(self, _sender: Any = None) -> None:
        self.service.set_ambience(False)
        self.config = self.service.config
        self._tick()

    def _toggle_banter(self, _sender: Any = None) -> None:
        self.service.set_banter(not self.service.config.banter.enabled)
        self.config = self.service.config
        self._tick()

    def _toggle_recall(self, _sender: Any = None) -> None:
        self.service.set_recall(not self.service.config.recall.enabled)
        self.config = self.service.config
        self._tick()

    def _toggle_voice_input(self, _sender: Any = None) -> None:
        enabling = not self.service.config.voice.enabled
        ok = self.service.set_voice_input(enabling)
        self.config = self.service.config
        self._tick()
        if not enabling:
            return
        if not ok:
            snapshot = self.service.voice_snapshot()
            message = (
                snapshot.error
                if snapshot is not None and snapshot.error
                else "Reezyn could not open the microphone."
            )
            rumps.alert(title="Voice input is not ready", message=message, ok="OK")
        # The TCC prompts only appear for the bundled app, so ask once here.
        self._request_permissions(quiet=True)

    def _toggle_conversation(self, _sender: Any = None) -> None:
        self.service.set_conversation(not self.service.config.voice.conversation)
        self.config = self.service.config
        self._tick()

    def _toggle_confirm_send(self, _sender: Any = None) -> None:
        self.service.set_confirm_send(not self.service.config.voice.confirm_send)
        self.config = self.service.config
        self._tick()

    def _refresh_permissions(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._permissions_checked < 2.5:
            return
        self._permissions_checked = now
        try:
            from .voice import permissions

            self._permissions = permissions.summary()
        except Exception:
            self._permissions = {}
        for key, item in self.permission_items.items():
            state = self._permissions.get(key, "unknown").replace("_", " ")
            label = {
                "microphone": "Microphone",
                "speech": "Speech Recognition",
                "accessibility": "Accessibility",
            }[key]
            item.title = f"{label}: {state}"

    def _request_permissions(self, _sender: Any = None, quiet: bool = False) -> None:
        try:
            from .voice import permissions
        except Exception as exc:
            if not quiet:
                rumps.alert(title="Voice permissions", message=str(exc), ok="OK")
            return

        def report(state: dict[str, str]) -> None:
            self._refresh_permissions(force=True)
            missing = [name for name, value in state.items() if value != "authorized"]
            if not quiet or missing:
                self._show_permission_status(state)

        try:
            permissions.request_missing(report)
        except Exception as exc:
            if not quiet:
                rumps.alert(title="Voice permissions", message=str(exc), ok="OK")

    def _show_permission_status(self, state: dict[str, str]) -> None:
        from .voice import permissions

        lines = [permissions.describe()]
        if any(value != "authorized" for value in state.values()):
            lines.append("")
            lines.append(
                "After granting Microphone or Speech Recognition, macOS may need "
                "Reezyn restarted. Accessibility takes effect immediately."
            )
        rumps.alert(title="Voice permissions", message="\n".join(lines), ok="OK")

    def _make_settings_callback(self, kind: str) -> Any:
        def open_settings_item(_sender: Any = None) -> None:
            from .voice import permissions

            permissions.open_settings(kind)

        return open_settings_item

    def _recap(self, _sender: Any = None) -> None:
        self.service.recap()
        self._tick()

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
