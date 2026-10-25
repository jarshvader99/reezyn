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
from .captions import CaptionOverlay
from .config import (
    AMBIENCE_LEVELS,
    CAPTION_FONTS,
    CAPTION_OPACITIES,
    CONFIG_ENV,
    SPEEDS,
    Config,
    app_home,
    load_config,
)
from .languages import LANGUAGES
from .service import ReezynService
from .status_icon import icon_alpha, reduce_motion, seedling_image
from .system_audio import VOLUME_LEVELS, output_volume, set_output_volume

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
        self._system_volume: int | None = None
        self._volume_checked = 0.0
        self._volume_epoch = -1
        self._download_error = ""
        self._download_thread: threading.Thread | None = None
        self._remote_checked = 0.0
        self._remote_info: dict[str, Any] = {}
        self.caption_overlay: CaptionOverlay | None = None
        self._caption_error = ""

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

        self.captions_menu = rumps.MenuItem("CC viewer")
        self.captions_toggle = rumps.MenuItem(
            "Show captions", callback=self._toggle_captions
        )
        self.captions_menu.add(self.captions_toggle)
        opacity_menu = rumps.MenuItem("Opacity")
        self.captions_opacity_items: dict[float, rumps.MenuItem] = {}
        for opacity in CAPTION_OPACITIES:
            item = rumps.MenuItem(
                f"{opacity * 100:.0f}%", callback=self._make_captions_opacity(opacity)
            )
            self.captions_opacity_items[opacity] = item
            opacity_menu.add(item)
        self.captions_menu.add(opacity_menu)
        size_menu = rumps.MenuItem("Text size")
        self.captions_size_items: dict[str, rumps.MenuItem] = {}
        for label, size in CAPTION_FONTS.items():
            item = rumps.MenuItem(
                label, callback=self._make_captions_font(size)
            )
            self.captions_size_items[label] = item
            size_menu.add(item)
        self.captions_menu.add(size_menu)
        self.captions_collapse = rumps.MenuItem(
            "Collapse to a pill", callback=self._toggle_captions_collapsed
        )
        self.captions_click = rumps.MenuItem(
            "Click through unless ⌥", callback=self._toggle_captions_click
        )
        self.captions_hint = rumps.MenuItem("⌥⌘H shows and hides")
        self.captions_hint.set_callback(None)
        self.captions_menu.add(rumps.separator)
        self.captions_menu.add(self.captions_collapse)
        self.captions_menu.add(self.captions_click)
        self.captions_menu.add(self.captions_hint)
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

        self.volume_menu = rumps.MenuItem("System volume")
        self.volume_items: dict[int, rumps.MenuItem] = {}
        for level in VOLUME_LEVELS:
            item = rumps.MenuItem(
                f"{level}%", callback=self._make_volume_callback(level)
            )
            self.volume_items[level] = item
            self.volume_menu.add(item)

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

        self.remote_menu = rumps.MenuItem("Remote access")
        self.remote_toggle = rumps.MenuItem(
            "Turn on remote access", callback=self._toggle_remote
        )
        self.remote_show = rumps.MenuItem(
            "Show connection…", callback=self._show_remote
        )
        self.remote_code = rumps.MenuItem(
            "New pairing code", callback=self._new_pairing_code
        )
        self.remote_forget = rumps.MenuItem(
            "Forget paired phones", callback=self._forget_phones
        )
        self.remote_tailscale = rumps.MenuItem(
            "Tailscale setup…", callback=self._tailscale_help
        )
        self.remote_menu.add(self.remote_toggle)
        self.remote_menu.add(self.remote_show)
        self.remote_menu.add(self.remote_code)
        self.remote_menu.add(rumps.separator)
        self.remote_menu.add(self.remote_forget)
        self.remote_menu.add(self.remote_tailscale)

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
            self.captions_menu,
            rumps.separator,
            voice_menu,
            pace_menu,
            self.volume_menu,
            self.ambience_menu,
            self.banter_item,
            self.recall_item,
            rumps.separator,
            self.voice_input_item,
            self.conversation_item,
            self.confirm_item,
            self.permissions_menu,
            self.remote_menu,
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
        self._refresh_remote()
        self._refresh_system_volume()
        self.download_item.hidden = status.model_ready
        if status.state != self._last_status:
            self._refresh_recent()
            self._last_status = status.state
        else:
            # Captions should appear promptly even when the state is unchanged.
            self._refresh_recent()
        self._refresh_captions(status)

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

    def _refresh_captions(self, status: Any) -> None:
        config = self.service.config.captions
        overlay = self.caption_overlay
        if config.enabled and overlay is None:
            overlay = self._open_captions()
        if overlay is not None:
            if config.enabled and not overlay.visible:
                # If the window cannot be built, switch the feature off rather
                # than retrying on every tick.
                if not overlay.show():
                    config.enabled = False
                    self._caption_error = overlay.error
                    self.service.config.save(self.home)
            elif not config.enabled and overlay.visible:
                overlay.hide()
            overlay.update(
                status.current if status.speaking else "",
                status.recent,
                status.paused,
            )
        self.captions_toggle.state = 1 if config.enabled else 0
        self.captions_collapse.state = 1 if config.collapsed else 0
        self.captions_click.state = 1 if config.click_through else 0
        font = min(
            CAPTION_FONTS.items(), key=lambda pair: abs(pair[1] - config.font_size)
        )[0]
        for label, item in self.captions_size_items.items():
            item.state = 1 if label == font else 0
        opacity = min(
            CAPTION_OPACITIES, key=lambda value: abs(value - config.opacity)
        )
        for value, item in self.captions_opacity_items.items():
            item.state = 1 if value == opacity else 0
        self.captions_hint.title = (
            "⌥⌘H shows and hides"
            if self._permissions.get("accessibility") == "authorized"
            else "⌥⌘H needs Accessibility"
        )

    def _open_captions(self) -> CaptionOverlay | None:
        if self.caption_overlay is not None:
            return self.caption_overlay
        try:
            overlay = CaptionOverlay(
                self.service.config.captions,
                on_change=self._captions_changed,
                on_pause=self.service.toggle_pause,
                on_skip=self.service.skip,
            )
        except Exception as exc:
            log.warning("could not create the caption viewer: %s", exc)
            self._caption_error = str(exc)
            return None
        self.caption_overlay = overlay
        return overlay

    def _captions_changed(self) -> None:
        self.service.config.save(self.home)
        self._tick()

    def _toggle_captions(self, _sender: Any = None) -> None:
        config = self.service.config.captions
        if config.enabled:
            config.enabled = False
            if self.caption_overlay is not None:
                self.caption_overlay.hide()
        else:
            overlay = self._open_captions()
            if overlay is None:
                rumps.alert(
                    title="CC viewer unavailable",
                    message=self._caption_error
                    or "The caption window could not be created.",
                    ok="OK",
                )
                self._tick()
                return
            config.enabled = True
            if not overlay.show():
                config.enabled = False
                rumps.alert(
                    title="CC viewer unavailable",
                    message=overlay.error or "The caption window could not be shown.",
                    ok="OK",
                )
        self.service.config.save(self.home)
        self._tick()

    def _make_captions_opacity(self, value: float) -> Any:
        def choose(_sender: Any = None) -> None:
            if self.caption_overlay is not None:
                self.caption_overlay.set_opacity(value)
            else:
                self.service.config.captions.opacity = value
            self.service.config.save(self.home)
            self._tick()

        return choose

    def _make_captions_font(self, size: float) -> Any:
        def choose(_sender: Any = None) -> None:
            config = self.service.config.captions
            config.font_size = size
            if self.caption_overlay is not None:
                self.caption_overlay.refresh()
            self.service.config.save(self.home)
            self._tick()

        return choose

    def _toggle_captions_collapsed(self, _sender: Any = None) -> None:
        config = self.service.config.captions
        config.collapsed = not config.collapsed
        if self.caption_overlay is not None:
            self.caption_overlay.refresh()
        self.service.config.save(self.home)
        self._tick()

    def _toggle_captions_click(self, _sender: Any = None) -> None:
        if self.caption_overlay is not None:
            self.caption_overlay.toggle_click_through()
        else:
            config = self.service.config.captions
            config.click_through = not config.click_through
            self.service.config.save(self.home)
        self._tick()

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

    def _make_volume_callback(self, level: int) -> Any:
        def choose(_sender: Any = None) -> None:
            if set_output_volume(level):
                self._system_volume = level
                self._refresh_system_volume(force=True)

        return choose

    def _refresh_system_volume(self, force: bool = False) -> None:
        now = time.monotonic()
        epoch = self.service.device_epoch
        if (
            not force
            and epoch == self._volume_epoch
            and now - self._volume_checked < 10.0
        ):
            return
        self._volume_epoch = epoch
        self._volume_checked = now
        self._system_volume = output_volume()
        volume = self._system_volume
        if volume is None:
            for item in self.volume_items.values():
                item.state = 0
            return
        closest = min(self.volume_items, key=lambda level: abs(level - volume))
        for level, item in self.volume_items.items():
            item.state = 1 if level == closest else 0

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

    def _refresh_remote(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._remote_checked < 2.0:
            return
        self._remote_checked = now
        try:
            info = self.service.remote_info()
        except Exception:
            info = {}
        self._remote_info = info
        running = bool(info.get("running"))
        self.remote_menu.title = "Remote access · on" if running else "Remote access"
        self.remote_toggle.title = (
            "Turn off remote access" if running else "Turn on remote access"
        )
        self.remote_toggle.state = 1 if running else 0
        for item in (self.remote_show, self.remote_code, self.remote_forget):
            item.hidden = not running

    def _toggle_remote(self, _sender: Any = None) -> None:
        self._refresh_remote(force=True)
        enabling = not bool(self._remote_info.get("running"))
        if not enabling:
            self.service.set_remote(False)
            self._refresh_remote(force=True)
            self._tick()
            return

        ok = self.service.set_remote(True)
        if ok:
            # Give a working Serve mapping a few seconds to answer, so the
            # first alert can include the real URL instead of asking again.
            self.service.remote_wait(10.0)
        self._refresh_remote(force=True)
        info = self._remote_info
        self._tick()
        if not ok:
            rumps.alert(
                title="Remote access is not available",
                message=(
                    (info.get("error") or "The page could not start.")
                    + "\n\n"
                    + self._tailscale_text()
                ),
                ok="OK",
            )
            return

        code = str(info.get("code") or "")
        url = str(info.get("url") or info.get("local_url") or "")
        local = str(info.get("local_url") or "")
        setup = str(info.get("setup_url") or "")
        lines = []
        if info.get("https"):
            lines += [
                "Open this on your phone, with Tailscale connected:",
                url,
                "",
            ]
        else:
            lines += [
                "The page is running on this Mac, but it is not published to "
                "your tailnet yet.",
                "",
            ]
            detail = str(info.get("error") or "").strip()
            if detail:
                lines += [detail, ""]
            if setup:
                _copy(setup)
                lines += [
                    "The enable link is on your clipboard. Open it once, then "
                    "turn remote access off and on.",
                    "",
                ]
            if info.get("publishing"):
                lines += ["Still publishing to Tailscale; check Show connection in a few seconds.", ""]
            lines += ["Local test address: " + local, ""]
        if code:
            minutes = max(1, int(info.get("code_expires_s") or 0) // 60)
            lines += [f"Pairing code: {code}  (expires in about {minutes} min)", ""]
        lines += [
            "The Mac stays awake while remote access is on. Sending still "
            "needs the screen unlocked; replies queue until you unlock it.",
            "",
            "Find the address and code again under Remote access → "
            "Show connection.",
        ]
        if not info.get("https"):
            lines += ["", self._tailscale_text()]
        rumps.alert(
            title=(
                "Reezyn on your phone"
                if info.get("https")
                else "Remote access is local only"
            ),
            message="\n".join(lines),
            ok="Done",
        )
        if setup:
            if rumps.alert(
                title="Enable Tailscale Serve",
                message=(
                    "Tailscale needs a one-time approval before this Mac can be "
                    "reached from your phone.\n\nOpen the enable link now?"
                ),
                ok="Open link",
                cancel="Later",
            ) == 1:
                try:
                    subprocess.Popen(["open", setup])
                except OSError:
                    pass

    def _show_remote(self, _sender: Any = None) -> None:
        self._refresh_remote(force=True)
        info = self._remote_info
        if not info.get("running"):
            rumps.alert(
                title="Remote access",
                message="Remote access is off. Turn it on from the Reezyn menu.",
                ok="OK",
            )
            return
        lines = ["Open on your phone:", str(info.get("url") or "")]
        if not info.get("https"):
            lines += ["", str(info.get("error") or "Not published to the tailnet.")]
            setup = str(info.get("setup_url") or "")
            if setup:
                _copy(setup)
                lines += ["", "Enable link (copied):", setup]
            if info.get("publishing"):
                lines += ["", "Still publishing to Tailscale…"]
            lines += ["", self._tailscale_text()]
        code = str(info.get("code") or "")
        if code:
            minutes = max(1, int(info.get("code_expires_s") or 0) // 60)
            lines += ["", f"Pairing code: {code}  (about {minutes} min left)"]
        else:
            lines += ["", "Pairing code: none active. Use New pairing code."]
        lines += [
            "",
            f"Paired phones: {int(info.get('paired') or 0)}",
            f"Queued replies: {int(info.get('queued') or 0)}",
            "Mac: " + ("locked" if info.get("locked") else "unlocked"),
        ]
        terminal = str(info.get("terminal") or "")
        if terminal:
            lines.append("Last terminal: " + terminal)
        rumps.alert(title="Reezyn remote", message="\n".join(lines), ok="Done")

    def _new_pairing_code(self, _sender: Any = None) -> None:
        code = self.service.remote_new_code()
        if not code:
            rumps.alert(
                title="Pairing code",
                message="Turn remote access on first.",
                ok="OK",
            )
            return
        self._refresh_remote(force=True)
        url = str(self._remote_info.get("url") or "")
        message = f"Pairing code: {code}\n\nIt expires in 15 minutes."
        if url:
            message += f"\n\nOpen on your phone:\n{url}"
        rumps.alert(title="Reezyn pairing", message=message, ok="Done")

    def _forget_phones(self, _sender: Any = None) -> None:
        count = self.service.remote_forget_phones()
        self._refresh_remote(force=True)
        rumps.alert(
            title="Paired phones",
            message=(
                f"Forgot {count} paired phone{'s' if count != 1 else ''}.\n\n"
                "Pair again with a new code from the menu."
            ),
            ok="OK",
        )

    def _tailscale_help(self, _sender: Any = None) -> None:
        rumps.alert(
            title="Tailscale",
            message=self._tailscale_text(),
            ok="OK",
        )

    def _tailscale_text(self) -> str:
        try:
            from .remote import tailscale

            state = tailscale.status()
            if not state.get("available"):
                detail = "Tailscale's command line is not installed yet."
            elif state.get("running"):
                detail = "Connected as " + str(state.get("dns_name") or state.get("ip") or "this Mac") + "."
            else:
                detail = str(state.get("reason") or "Tailscale is not connected.")
            help_text = tailscale.INSTALL_HELP
        except Exception as exc:
            detail = str(exc)
            help_text = "Remote access needs the Tailscale app and its command line."
        return (
            detail
            + "\n\n"
            + help_text
            + "\n\nReezyn uses Tailscale Serve for HTTPS. It opens no router "
            "ports and is never exposed to the public internet. If Serve asks, "
            "enable HTTPS certificates for your tailnet in the Tailscale admin "
            "console."
        )

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
        if self.caption_overlay is not None:
            self.caption_overlay.close()
            self.caption_overlay = None
        self.service.stop()
        rumps.quit_application()


def _copy(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False, timeout=2)
    except Exception:
        pass


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
