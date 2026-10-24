from __future__ import annotations

from reezyn.config import AgentsConfig, Config, NarrationConfig, VoiceConfig
from reezyn.events import Event
from reezyn.service import ReezynService


class FakeSpeaker:
    ready = True
    load_error = ""

    def speak(self, text: str) -> bool:
        return True

    def stop(self) -> None:
        return

    def preload(self) -> None:
        return

    def set_voice(self, voice: str) -> None:
        return

    def set_speed(self, speed: float) -> None:
        return


def _service(tmp_path):
    config = Config(
        narration=NarrationConfig(debounce_ms=20, stale_after_s=5, min_gap_s=0),
        voice=VoiceConfig(enabled=False),
        agents=AgentsConfig(
            claude=True,
            codex=False,
            gemini=False,
            cursor=False,
            opencode=False,
            freebuff=False,
        ),
    )
    return ReezynService(config, tmp_path, speaker=FakeSpeaker())


def test_a_permission_event_stays_visible_until_an_answer_or_new_work(tmp_path):
    service = _service(tmp_path)
    service._submit_event(
        Event(agent="claude", kind="permission", text="Claude wants to run Bash.", project="reezyn")
    )

    snapshot = service.remote_snapshot()
    assert snapshot["permission"]["text"] == "Claude wants to run Bash."
    assert snapshot["project"] == "reezyn"
    assert snapshot["active_agent"] == "claude"

    service._submit_event(
        Event(agent="claude", kind="tool", tool_name="Bash", project="reezyn", session_id="s1")
    )
    assert service.remote_snapshot()["permission"] is None


def test_another_agents_tool_does_not_hide_a_pending_permission(tmp_path):
    service = _service(tmp_path)
    service._submit_event(
        Event(agent="claude", kind="permission", text="Allow Read?", session_id="s1")
    )

    service._submit_event(
        Event(agent="gemini", kind="tool", tool_name="Read", session_id="g1")
    )

    assert service.remote_snapshot()["permission"] is not None


def test_answering_from_the_phone_clears_the_permission(tmp_path):
    service = _service(tmp_path)
    service._submit_event(Event(agent="claude", kind="permission", text="Allow Read?"))
    assert service.remote_snapshot()["permission"] is not None

    service.remote_permission_answered(True)

    assert service.remote_snapshot()["permission"] is None
