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
class AmbienceConfig:
    enabled: bool = False
    volume: float = 0.35


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
    ambience: AmbienceConfig = field(default_factory=AmbienceConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)

    def save(self, home: Path | None = None) -> Path:
        home = home or app_home()
        home.mkdir(parents=True, exist_ok=True)
        path = home / "config.toml"
        path.write_text(self.to_toml(), encoding="utf-8")
        return path

    def to_toml(self) -> str:
        s = self.speech
        n = self.narration
        a = self.agents
        m = self.ambience
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
            "[agents]\n"
            f"claude = {str(a.claude).lower()}\n"
            f"codex = {str(a.codex).lower()}\n"
            f"gemini = {str(a.gemini).lower()}\n"
            f"cursor = {str(a.cursor).lower()}\n"
            f"opencode = {str(a.opencode).lower()}\n"
            f"freebuff = {str(a.freebuff).lower()}\n\n"
            "[ambience]\n"
            f"enabled = {str(m.enabled).lower()}\n"
            f"volume = {m.volume:.2f}\n"
        )


def quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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
    ambience = _section(raw, "ambience")
    agents = _section(raw, "agents")

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
        ambience=AmbienceConfig(
            enabled=_bool(ambience.get("enabled"), False),
            volume=_clamp_float(ambience.get("volume"), 0.0, 1.0, 0.35),
        ),
        agents=AgentsConfig(
            claude=_bool(agents.get("claude"), True),
            codex=_bool(agents.get("codex"), True),
            gemini=_bool(agents.get("gemini"), True),
            cursor=_bool(agents.get("cursor"), True),
            opencode=_bool(agents.get("opencode"), True),
            freebuff=_bool(agents.get("freebuff"), True),
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


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, float(value)))
