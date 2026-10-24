"""Reezyn configuration and on-disk locations."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .languages import DEFAULT_VOICE, VOICES

APP_NAME = "reezyn"
CONFIG_ENV = "REEZYN_HOME"

DEFAULT_VOICE_APPS = (
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "com.microsoft.VSCode",
    "com.todesktop.230313mzl4w4u92",  # Cursor
    "com.mitchellh.ghostty",
    "dev.warp.Warp-Stable",
    "com.github.wez.wezterm",
    "net.kovidgoyal.kitty",
    "org.alacritty",
    "co.zeit.hyper",
)

SPEEDS: dict[str, float] = {
    "Calm": 0.84,
    "Natural": 0.94,
    "Brisk": 1.04,
}

AMBIENCE_LEVELS: dict[str, float] = {
    "Whisper": 0.15,
    "Soft": 0.35,
    "Clear": 0.55,
}


def app_home() -> Path:
    """Return Reezyn's private state directory.

    ``REEZYN_HOME`` makes the app testable and lets people keep their state
    somewhere else. Everything Reezyn writes lives below this directory.
    """

    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".reezyn"


@dataclass(slots=True)
class SpeechConfig:
    voice: str = DEFAULT_VOICE
    speed: float = 0.92
    volume: float = 0.9
    model: str = "int8"


@dataclass(slots=True)
class NarrationConfig:
    max_words: int = 42
    max_sentences: int = 2  # 0 reads every sentence in the thought
    debounce_ms: int = 700
    stale_after_s: float = 9.0
    min_gap_s: float = 0.35
    connectors: bool = True
    dedupe_s: float = 30.0


@dataclass(slots=True)
class BanterConfig:
    enabled: bool = True
    after_s: float = 30.0  # silence before the first line
    repeat_s: float = 75.0  # silence between later lines
    max_lines: int = 2  # per stretch of agent activity; 0 is silent


@dataclass(slots=True)
class AmbienceConfig:
    enabled: bool = False
    volume: float = 0.35


@dataclass(slots=True)
class RecallConfig:
    enabled: bool = True
    # Conservative by default: one stray shared word is not memory. Recall
    # also requires two matching terms once a prompt has three.
    threshold: float = 0.30
    max_age_days: float = 30.0
    cooldown_s: float = 90.0
    recap_minutes: float = 45.0


@dataclass(slots=True)
class VoiceConfig:
    enabled: bool = False
    conversation: bool = True
    wake_word: str = "hey reezyn"
    locale: str = ""  # empty follows the selected voice's language
    silence_ms: int = 1000
    wake_silence_ms: int = 700
    max_utterance_s: float = 15.0
    follow_up_s: float = 8.0
    conversation_s: float = 12.0
    auto_send: bool = False
    auto_send_in_conversation: bool = True
    # After a dictated pause, ask whether to submit instead of guessing.
    confirm_send: bool = True
    confirm_s: float = 6.0
    max_answer_words: int = 90
    ack_sound: bool = True
    require_text_field: bool = True
    allow_apps: list[str] = field(default_factory=lambda: list(DEFAULT_VOICE_APPS))


@dataclass(slots=True)
class RemoteConfig:
    enabled: bool = False
    port: int = 8787
    # Keep the Mac awake while the phone can reach it. Keystrokes still need an
    # unlocked screen; this only prevents idle sleep from cutting the link.
    keep_awake: bool = True
    queue_when_locked: bool = True
    # Default for the page's read-aloud toggle; the phone remembers its choice.
    read_aloud: bool = False
    session_days: int = 30


@dataclass(slots=True)
class AgentsConfig:
    claude: bool = True
    codex: bool = True
    gemini: bool = True
    cursor: bool = True
    opencode: bool = True
    freebuff: bool = True


@dataclass(slots=True)
class Config:
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    narration: NarrationConfig = field(default_factory=NarrationConfig)
    banter: BanterConfig = field(default_factory=BanterConfig)
    ambience: AmbienceConfig = field(default_factory=AmbienceConfig)
    recall: RecallConfig = field(default_factory=RecallConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)

    def save(self, home: Path | None = None) -> Path:
        home = home or app_home()
        home.mkdir(parents=True, exist_ok=True)
        path = home / "config.toml"
        path.write_text(self.to_toml(), encoding="utf-8")
        return path

    def to_toml(self) -> str:
        s = self.speech
        n = self.narration
        b = self.banter
        a = self.agents
        m = self.ambience
        r = self.recall
        v = self.voice
        remote = self.remote
        return (
            "# Reezyn configuration\n"
            "# Changes here are read when the menu-bar app starts.\n\n"
            "[speech]\n"
            f"voice = {quote(s.voice)}\n"
            f"speed = {s.speed:.2f}\n"
            f"volume = {s.volume:.2f}\n"
            f"model = {quote(s.model)}\n\n"
            "[narration]\n"
            f"max_words = {n.max_words}\n"
            f"max_sentences = {n.max_sentences}\n"
            f"debounce_ms = {n.debounce_ms}\n"
            f"stale_after_s = {n.stale_after_s:.1f}\n"
            f"min_gap_s = {n.min_gap_s:.2f}\n"
            f"connectors = {str(n.connectors).lower()}\n"
            f"dedupe_s = {n.dedupe_s:.1f}\n\n"
            "[banter]\n"
            f"enabled = {str(b.enabled).lower()}\n"
            f"after_s = {b.after_s:.1f}\n"
            f"repeat_s = {b.repeat_s:.1f}\n"
            f"max_lines = {b.max_lines}\n\n"
            "[agents]\n"
            f"claude = {str(a.claude).lower()}\n"
            f"codex = {str(a.codex).lower()}\n"
            f"gemini = {str(a.gemini).lower()}\n"
            f"cursor = {str(a.cursor).lower()}\n"
            f"opencode = {str(a.opencode).lower()}\n"
            f"freebuff = {str(a.freebuff).lower()}\n\n"
            "[ambience]\n"
            f"enabled = {str(m.enabled).lower()}\n"
            f"volume = {m.volume:.2f}\n\n"
            "[recall]\n"
            f"enabled = {str(r.enabled).lower()}\n"
            f"threshold = {r.threshold:.2f}\n"
            f"max_age_days = {r.max_age_days:.1f}\n"
            f"cooldown_s = {r.cooldown_s:.1f}\n"
            f"recap_minutes = {r.recap_minutes:.1f}\n\n"
            "[voice]\n"
            f"enabled = {str(v.enabled).lower()}\n"
            f"conversation = {str(v.conversation).lower()}\n"
            f"wake_word = {quote(v.wake_word)}\n"
            f"locale = {quote(v.locale)}\n"
            f"silence_ms = {v.silence_ms}\n"
            f"wake_silence_ms = {v.wake_silence_ms}\n"
            f"max_utterance_s = {v.max_utterance_s:.1f}\n"
            f"follow_up_s = {v.follow_up_s:.1f}\n"
            f"conversation_s = {v.conversation_s:.1f}\n"
            f"auto_send = {str(v.auto_send).lower()}\n"
            f"auto_send_in_conversation = {str(v.auto_send_in_conversation).lower()}\n"
            f"confirm_send = {str(v.confirm_send).lower()}\n"
            f"confirm_s = {v.confirm_s:.1f}\n"
            f"max_answer_words = {v.max_answer_words}\n"
            f"ack_sound = {str(v.ack_sound).lower()}\n"
            f"require_text_field = {str(v.require_text_field).lower()}\n"
            f"allow_apps = {_quote_list(v.allow_apps)}\n\n"
            "[remote]\n"
            f"enabled = {str(remote.enabled).lower()}\n"
            f"port = {remote.port}\n"
            f"keep_awake = {str(remote.keep_awake).lower()}\n"
            f"queue_when_locked = {str(remote.queue_when_locked).lower()}\n"
            f"read_aloud = {str(remote.read_aloud).lower()}\n"
            f"session_days = {remote.session_days}\n"
        )


def quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _quote_list(values: list[str]) -> str:
    return "[" + ", ".join(quote(value) for value in values) + "]"


def load_config(home: Path | None = None) -> Config:
    home = home or app_home()
    path = home / "config.toml"
    if not path.exists():
        config = Config()
        config.save(home)
        return config

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return Config()

    speech = _section(raw, "speech")
    narration = _section(raw, "narration")
    banter = _section(raw, "banter")
    ambience = _section(raw, "ambience")
    recall = _section(raw, "recall")
    voice = _section(raw, "voice")
    agents = _section(raw, "agents")
    remote = _section(raw, "remote")

    config = Config(
        speech=SpeechConfig(
            voice=_choice(speech.get("voice"), VOICES, DEFAULT_VOICE),
            speed=_clamp_float(speech.get("speed"), 0.72, 1.18, 0.92),
            volume=_clamp_float(speech.get("volume"), 0.0, 1.0, 0.9),
            model="fp16" if speech.get("model") == "fp16" else "int8",
        ),
        narration=NarrationConfig(
            max_words=int(_clamp_float(narration.get("max_words"), 12, 400, 42)),
            max_sentences=int(
                _clamp_float(narration.get("max_sentences"), 0, 12, 2)
            ),
            debounce_ms=int(_clamp_float(narration.get("debounce_ms"), 150, 3000, 700)),
            stale_after_s=_clamp_float(narration.get("stale_after_s"), 2, 60, 9),
            min_gap_s=_clamp_float(narration.get("min_gap_s"), 0, 4, 0.35),
            connectors=_bool(narration.get("connectors"), True),
            dedupe_s=_clamp_float(narration.get("dedupe_s"), 1, 300, 30),
        ),
        banter=BanterConfig(
            enabled=_bool(banter.get("enabled"), True),
            after_s=_clamp_float(banter.get("after_s"), 8, 300, 30.0),
            repeat_s=_clamp_float(banter.get("repeat_s"), 20, 600, 75.0),
            max_lines=int(_clamp_float(banter.get("max_lines"), 0, 5, 2)),
        ),
        ambience=AmbienceConfig(
            enabled=_bool(ambience.get("enabled"), False),
            volume=_clamp_float(ambience.get("volume"), 0.0, 1.0, 0.35),
        ),
        recall=RecallConfig(
            enabled=_bool(recall.get("enabled"), True),
            threshold=_clamp_float(recall.get("threshold"), 0.15, 0.9, 0.30),
            max_age_days=_clamp_float(recall.get("max_age_days"), 1, 365, 30.0),
            cooldown_s=_clamp_float(recall.get("cooldown_s"), 5, 3600, 90.0),
            recap_minutes=_clamp_float(recall.get("recap_minutes"), 2, 720, 45.0),
        ),
        voice=VoiceConfig(
            enabled=_bool(voice.get("enabled"), False),
            conversation=_bool(voice.get("conversation"), True),
            wake_word=_text(voice.get("wake_word"), "hey reezyn"),
            locale=_text(voice.get("locale"), ""),
            silence_ms=int(_clamp_float(voice.get("silence_ms"), 400, 3000, 1000)),
            wake_silence_ms=int(
                _clamp_float(voice.get("wake_silence_ms"), 300, 2000, 700)
            ),
            max_utterance_s=_clamp_float(
                voice.get("max_utterance_s"), 4, 60, 15.0
            ),
            follow_up_s=_clamp_float(voice.get("follow_up_s"), 2, 60, 8.0),
            conversation_s=_clamp_float(
                voice.get("conversation_s"), 3, 90, 12.0
            ),
            auto_send=_bool(voice.get("auto_send"), False),
            auto_send_in_conversation=_bool(
                voice.get("auto_send_in_conversation"), True
            ),
            confirm_send=_bool(voice.get("confirm_send"), True),
            confirm_s=_clamp_float(voice.get("confirm_s"), 2, 20, 6.0),
            max_answer_words=int(
                _clamp_float(voice.get("max_answer_words"), 20, 400, 90)
            ),
            ack_sound=_bool(voice.get("ack_sound"), True),
            require_text_field=_bool(voice.get("require_text_field"), True),
            allow_apps=_strings(voice.get("allow_apps"), list(DEFAULT_VOICE_APPS)),
        ),
        agents=AgentsConfig(
            claude=_bool(agents.get("claude"), True),
            codex=_bool(agents.get("codex"), True),
            gemini=_bool(agents.get("gemini"), True),
            cursor=_bool(agents.get("cursor"), True),
            opencode=_bool(agents.get("opencode"), True),
            freebuff=_bool(agents.get("freebuff"), True),
        ),
        remote=RemoteConfig(
            enabled=_bool(remote.get("enabled"), False),
            port=int(_clamp_float(remote.get("port"), 1024, 65535, 8787)),
            keep_awake=_bool(remote.get("keep_awake"), True),
            queue_when_locked=_bool(remote.get("queue_when_locked"), True),
            read_aloud=_bool(remote.get("read_aloud"), False),
            session_days=int(
                _clamp_float(remote.get("session_days"), 1, 365, 30)
            ),
        ),
    )
    return config


def with_voice(config: Config, voice: str) -> Config:
    if voice not in VOICES:
        return config
    return replace(config, speech=replace(config.speech, voice=voice))


def with_speed(config: Config, speed: float) -> Config:
    return replace(config, speech=replace(config.speech, speed=speed))


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    return value if isinstance(value, dict) else {}


def _choice(value: Any, choices: dict[str, str], default: str) -> str:
    return value if isinstance(value, str) and value in choices else default


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _text(value: Any, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _strings(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return default
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return items or default


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, float(value)))
