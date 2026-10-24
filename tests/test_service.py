from __future__ import annotations

import shutil
import time
from pathlib import Path

from reezyn.config import (
    AgentsConfig,
    AmbienceConfig,
    BanterConfig,
    Config,
    NarrationConfig,
    RecallConfig,
    SpeechConfig,
    load_config,
)
from reezyn.events import Event
from reezyn.languages import DEFAULT_PACK
from reezyn.runtime import is_running
from reezyn.service import ReezynService
from reezyn.spool import Spool

FIXTURES = Path(__file__).parent / "fixtures"


class FakeSpeaker:
    ready = True
    load_error = ""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.stopped = False

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return True

    def stop(self) -> None:
        self.stopped = True

    def preload(self) -> None:
        return

    def set_voice(self, voice: str) -> None:
        return

    def set_speed(self, speed: float) -> None:
        return


def test_service_spools_a_claude_hook_into_speech(tmp_path, monkeypatch):
    transcript = tmp_path / "session.jsonl"
    shutil.copy(FIXTURES / "claude_transcript.jsonl", transcript)
    home = tmp_path / "state"
    config = Config(
        speech=SpeechConfig(),
        narration=NarrationConfig(
            debounce_ms=20,
            stale_after_s=5,
            min_gap_s=0,
            connectors=False,
        ),
        agents=AgentsConfig(
            claude=True,
            codex=False,
            gemini=False,
            cursor=False,
            opencode=False,
            freebuff=False,
        ),
    )
    speaker = FakeSpeaker()
    service = ReezynService(config, home, speaker=speaker)
    service.start()
    try:
        assert is_running(home)
        Spool(home).emit(
            "claude",
            {
                "session_id": "claude-1",
                "hook_event_name": "PreToolUse",
                "transcript_path": str(transcript),
                "tool_name": "Read",
                "tool_input": {"file_path": "/Users/me/app/src/auth/session.ts"},
            },
        )
        deadline = time.time() + 3
        while not speaker.spoken and time.time() < deadline:
            time.sleep(0.05)
        assert speaker.spoken
        assert "session middleware" in speaker.spoken[0]

        while not service.status().recent and time.time() < deadline:
            time.sleep(0.05)
        status = service.status()
        assert status.active_agent == "claude"
        assert status.recent[0] == speaker.spoken[0]
    finally:
        service.stop()
    assert not is_running(home)


def test_a_prompt_spools_into_a_recall_line(tmp_path):
    home = tmp_path / "state"
    project = str(tmp_path / "project")
    config = Config(
        narration=NarrationConfig(
            debounce_ms=20, stale_after_s=5, min_gap_s=0, connectors=False
        ),
        recall=RecallConfig(cooldown_s=0.0),
        agents=AgentsConfig(
            claude=False,
            codex=False,
            gemini=False,
            cursor=False,
            opencode=False,
            freebuff=False,
        ),
    )
    speaker = FakeSpeaker()
    service = ReezynService(config, home, speaker=speaker)
    service.recaller.record(
        Event(
            agent="claude",
            kind="reasoning",
            text=(
                "The refresh path rotates the cookie but never invalidates "
                "the old token."
            ),
            session_id="earlier",
            project=project,
            ts=time.time() - 3600,
        )
    )
    service.start()
    try:
        Spool(home).emit(
            "generic",
            Event(
                agent="claude",
                kind="prompt",
                text="why does login still work after refresh token rotation",
                session_id="now",
                project=project,
            ).to_dict(),
        )
        deadline = time.time() + 3
        while not speaker.spoken and time.time() < deadline:
            time.sleep(0.05)
        assert speaker.spoken
        assert speaker.spoken[0].startswith("Earlier today, the agent concluded:")
        assert service.recap().startswith("You asked: Why does login still work")
    finally:
        service.stop()


class FakeAmbience:
    def __init__(self, config: AmbienceConfig) -> None:
        self.config = config
        self.running = False
        self.error = ""
        self.fail = False
        self.ducked: list[bool] = []

    def start(self) -> bool:
        if self.fail:
            return False
        self.running = self.config.enabled
        return True

    def stop(self) -> None:
        self.running = False

    def set_ducked(self, ducked: bool) -> None:
        self.ducked.append(ducked)


def test_service_ducks_the_presence_bed_under_speech(tmp_path, monkeypatch):
    monkeypatch.setattr("reezyn.service.Ambience", FakeAmbience)
    home = tmp_path / "state"
    config = Config(
        speech=SpeechConfig(),
        narration=NarrationConfig(
            debounce_ms=20,
            stale_after_s=5,
            min_gap_s=0,
            connectors=False,
        ),
        ambience=AmbienceConfig(enabled=True),
        agents=AgentsConfig(
            claude=False,
            codex=False,
            gemini=False,
            cursor=False,
            opencode=False,
            freebuff=False,
        ),
    )
    speaker = FakeSpeaker()
    service = ReezynService(config, home, speaker=speaker)
    service.start()
    try:
        assert service.status().ambience is True
        Spool(home).emit(
            "generic",
            Event(
                agent="test",
                kind="tool",
                tool_name="Read",
                tool_input={"path": "session.ts"},
            ).to_dict(),
        )
        deadline = time.time() + 3
        while (not speaker.spoken or len(service.ambience.ducked) < 2) and time.time() < deadline:
            time.sleep(0.05)

        assert speaker.spoken
        assert service.ambience.ducked[:2] == [True, False]

        assert service.set_ambience(False) is True
        assert service.status().ambience is False
        assert load_config(home).ambience.enabled is False
    finally:
        service.stop()


def test_service_selects_a_presence_level(tmp_path, monkeypatch):
    monkeypatch.setattr("reezyn.service.Ambience", FakeAmbience)
    home = tmp_path / "state"
    config = Config(
        speech=SpeechConfig(),
        narration=NarrationConfig(connectors=False),
        ambience=AmbienceConfig(enabled=False),
        agents=AgentsConfig(
            claude=False,
            codex=False,
            gemini=False,
            cursor=False,
            opencode=False,
            freebuff=False,
        ),
    )
    service = ReezynService(config, home, speaker=FakeSpeaker())
    service.start()
    try:
        assert service.set_ambience_level(0.15) is True
        assert service.status().ambience is True
        assert service.config.ambience.volume == 0.15
        assert load_config(home).ambience.volume == 0.15

        assert service.set_ambience_level(0.55) is True
        assert service.config.ambience.volume == 0.55

        assert service.set_ambience(False) is True
        assert service.config.ambience.volume == 0.55

        service.ambience.fail = True
        assert service.set_ambience_level(0.35) is False
        assert service.config.ambience.enabled is False
        assert service.status().ambience is False
    finally:
        service.stop()


def test_service_fills_a_lull_with_banter(tmp_path):
    home = tmp_path / "state"
    config = Config(
        speech=SpeechConfig(),
        narration=NarrationConfig(
            debounce_ms=20,
            stale_after_s=5,
            min_gap_s=0,
            connectors=False,
        ),
        banter=BanterConfig(after_s=0.08, repeat_s=0.08, max_lines=1),
        agents=AgentsConfig(
            claude=False,
            codex=False,
            gemini=False,
            cursor=False,
            opencode=False,
            freebuff=False,
        ),
    )
    speaker = FakeSpeaker()
    service = ReezynService(config, home, speaker=speaker)
    service.start()
    try:
        Spool(home).emit(
            "generic",
            Event(
                agent="test",
                kind="reasoning",
                text="I'll check the auth tests.",
            ).to_dict(),
        )
        deadline = time.time() + 3
        while len(speaker.spoken) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert speaker.spoken[0] == "I'll check the auth tests."
        assert speaker.spoken[1] in DEFAULT_PACK.banter
        assert len(speaker.spoken) == 2
        assert service.status().recent == ["I'll check the auth tests."]
    finally:
        service.stop()


def test_service_switches_lull_banter_off_and_on(tmp_path):
    home = tmp_path / "state"
    config = Config(
        narration=NarrationConfig(connectors=False),
        agents=AgentsConfig(
            claude=False,
            codex=False,
            gemini=False,
            cursor=False,
            opencode=False,
            freebuff=False,
        ),
    )
    service = ReezynService(config, home, speaker=FakeSpeaker())
    service.start()
    try:
        service.set_banter(False)
        assert service.config.banter.enabled is False
        assert load_config(home).banter.enabled is False

        service.set_banter(True)
        assert service.config.banter.enabled is True
        assert load_config(home).banter.enabled is True
    finally:
        service.stop()


def test_service_switches_narration_language_with_the_voice(tmp_path):
    home = tmp_path / "state"
    config = Config(
        speech=SpeechConfig(),
        narration=NarrationConfig(
            debounce_ms=20,
            stale_after_s=5,
            min_gap_s=0,
            connectors=False,
        ),
        agents=AgentsConfig(
            claude=False,
            codex=False,
            gemini=False,
            cursor=False,
            opencode=False,
            freebuff=False,
        ),
    )
    speaker = FakeSpeaker()
    service = ReezynService(config, home, speaker=speaker)
    service.start()
    try:
        service.set_voice("em_alex")
        Spool(home).emit(
            "generic",
            Event(
                agent="test",
                kind="tool",
                tool_name="Read",
                tool_input={"path": "session.ts"},
            ).to_dict(),
        )
        deadline = time.time() + 3
        while not speaker.spoken and time.time() < deadline:
            time.sleep(0.05)
        assert speaker.spoken == ["Estoy leyendo session.ts."]
    finally:
        service.stop()
