from __future__ import annotations

import shutil
import time
from pathlib import Path

from reezyn.config import AgentsConfig, Config, NarrationConfig, SpeechConfig
from reezyn.events import Event
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
