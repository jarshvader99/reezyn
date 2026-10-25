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
    VoiceConfig,
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

    def reset_output(self) -> None:
        self.output_resets = getattr(self, "output_resets", 0) + 1
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
            ts=_an_hour_ago_today(),
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


class FakeVoice:
    def __init__(self) -> None:
        self.speaking: list[bool] = []
        self.opened: list[bool] = []
        self.restarts = 0

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        return

    def restart_microphone(self) -> None:
        self.restarts += 1

    def set_speaking(self, speaking: bool) -> None:
        self.speaking.append(speaking)

    def open_for_conversation(
        self, seconds: float | None = None, permission: bool = False
    ) -> None:
        self.opened.append(permission)

    def status(self) -> None:
        return None


def _voice_service(tmp_path, *, conversation: bool = True):
    home = tmp_path / "state"
    config = Config(
        speech=SpeechConfig(),
        narration=NarrationConfig(
            debounce_ms=20,
            stale_after_s=5,
            min_gap_s=0,
            connectors=False,
        ),
        voice=VoiceConfig(
            enabled=False,
            conversation=conversation,
            max_answer_words=40,
            ack_sound=False,
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
    service.config.voice.enabled = True
    voice = FakeVoice()
    service.voice = voice
    return service, voice, speaker


def _an_hour_ago_today() -> float:
    """An hour before now, clamped to today.

    Inside the first hour after local midnight a plain hour ago is yesterday,
    and the narrator rightly says "On a previous day". This test is about the
    recall line, not the calendar, so the moment stays inside today.
    """

    now = time.time()
    midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    return max(now - 3600, midnight)


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_closing_event_speaks_the_answer_then_opens_the_microphone(tmp_path):
    service, voice, speaker = _voice_service(tmp_path)
    try:
        service._submit_event(
            Event(
                agent="claude",
                kind="closing",
                text="The refresh test passes now. Anything else you want changed?",
            )
        )
        assert _wait_for(lambda: bool(voice.opened))
        assert voice.opened == [False]
        assert voice.speaking == [True, False]
        assert "refresh test passes" in speaker.spoken[-1]
    finally:
        service.stop()


def test_the_closing_answer_ends_with_a_cue(tmp_path):
    service, _voice, speaker = _voice_service(tmp_path)
    try:
        service._submit_event(
            Event(agent="claude", kind="closing", text="The tests pass.")
        )
        assert _wait_for(lambda: bool(speaker.spoken))
        assert speaker.spoken[-1] == "The tests pass. That's done."
    finally:
        service.stop()


def test_a_new_prompt_gets_an_opener(tmp_path):
    service, _voice, speaker = _voice_service(tmp_path, conversation=False)
    try:
        service._submit_event(
            Event(
                agent="claude",
                kind="prompt",
                text="why does login still work after refresh rotation",
                session_id="s1",
                project=str(tmp_path),
            )
        )
        assert _wait_for(lambda: bool(speaker.spoken))
        assert speaker.spoken[-1] in DEFAULT_PACK.phrases["cue_open"]
    finally:
        service.stop()


def test_permission_event_opens_the_microphone_for_an_answer(tmp_path):
    service, voice, speaker = _voice_service(tmp_path)
    try:
        service._submit_event(
            Event(
                agent="claude",
                kind="permission",
                text="Claude needs your permission to run npm test.",
            )
        )
        assert _wait_for(lambda: bool(voice.opened))
        assert voice.opened == [True]
        assert "permission" in speaker.spoken[-1]
    finally:
        service.stop()


def test_device_changes_reopen_the_microphone_and_the_output(tmp_path):
    service, voice, speaker = _voice_service(tmp_path)
    try:
        service._input_device_changed(1)
        assert voice.restarts == 1
        assert service.device_epoch == 1

        service._output_device_changed(2)
        assert speaker.output_resets == 1
        assert service.device_epoch == 2
    finally:
        service.stop()


def test_closing_event_is_silent_when_conversation_is_off(tmp_path):
    service, voice, speaker = _voice_service(tmp_path, conversation=False)
    try:
        service._submit_event(
            Event(agent="claude", kind="closing", text="The tests pass.")
        )
        time.sleep(0.4)
        assert voice.opened == []
        assert speaker.spoken == []
    finally:
        service.stop()


def test_phone_read_aloud_parks_the_microphone_too(tmp_path, monkeypatch):
    sessions: list[FakeVoice] = []

    class Session(FakeVoice):
        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__()
            self.stopped = 0
            sessions.append(self)

        def stop(self) -> None:
            self.stopped += 1

    monkeypatch.setattr("reezyn.service.VoiceSession", Session)
    home = tmp_path / "state"
    config = Config(
        voice=VoiceConfig(enabled=True, conversation=True, ack_sound=False),
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
        first = service.voice
        assert first is not None

        service.set_remote_listening(True)
        assert service.voice is None
        assert first.stopped == 1

        service.set_remote_listening(False)
        assert service.voice is not None
        assert service.voice is not first
    finally:
        service.stop()


def test_phone_read_aloud_keeps_the_mac_silent_until_it_stops(tmp_path):
    home = tmp_path / "state"
    config = Config(
        narration=NarrationConfig(
            debounce_ms=20, stale_after_s=5, min_gap_s=0, connectors=False
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
        service.set_remote_listening(True)
        # The current output must be released, not just skipped, or the Mac
        # keeps the AirPods on the wrong device.
        assert getattr(speaker, "output_resets", 0) == 1
        assert service.remote_snapshot()["phone_audio"] is True

        service._submit_event(
            Event(
                agent="claude",
                kind="tool",
                tool_name="Read",
                tool_input={"path": "a.ts"},
            )
        )
        assert _wait_for(lambda: bool(service.status().recent))
        assert speaker.spoken == []
        muted_line = service.status().recent[0]

        service.set_remote_listening(False)
        assert service.remote_snapshot()["phone_audio"] is False
        service._submit_event(
            Event(
                agent="claude",
                kind="tool",
                tool_name="Edit",
                tool_input={"path": "b.ts"},
            )
        )
        assert _wait_for(lambda: bool(speaker.spoken))
        assert speaker.spoken
        assert muted_line not in speaker.spoken
    finally:
        service.stop()
