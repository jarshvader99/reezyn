from __future__ import annotations

import queue
import threading
import time

from reezyn.config import NarrationConfig, SpeechConfig
from reezyn.events import Event
from reezyn.languages import DEFAULT_PACK, LANGUAGES, PACKS, VOICES, pack_for_voice
from reezyn.narration import (
    clean_for_speech,
    compose_narration,
    describe_tool,
    select_excerpt,
)
from reezyn.tts import KokoroSpeaker

SPANISH = PACKS["es"]


class _FakeEngine:
    def __init__(self) -> None:
        self.seen: dict[str, object] = {}

    def create_stream(self, text: str, voice: str, **kwargs: object):
        self.seen = {"text": text, "voice": voice, **kwargs}
        return _empty_stream()


async def _empty_stream():
    if False:
        yield None


def test_voices_resolve_to_their_language_pack():
    assert pack_for_voice("af_heart") is PACKS["en"]
    assert pack_for_voice("ef_dora") is PACKS["es"]
    assert pack_for_voice("em_alex") is PACKS["es"]
    assert pack_for_voice("not-a-voice") is DEFAULT_PACK
    assert {"ef_dora", "em_alex", "em_santa"} <= set(VOICES)
    assert [pack.name for pack in LANGUAGES] == ["English", "Spanish"]
    assert SPANISH.kokoro_lang == "es"


def test_spanish_cleaning_uses_spanish_words():
    cleaned = clean_for_speech(
        "Revisa https://example.com y el 50% de session.ts.", pack=SPANISH
    )
    assert "un enlace" in cleaned
    assert "por ciento" in cleaned
    assert " and " not in cleaned


def test_describe_tool_speaks_spanish():
    assert describe_tool("Read", {"file_path": "/a/b/session.ts"}, SPANISH) == "leyendo session.ts"
    assert describe_tool("str_replace", {"path": "src/auth.ts"}, SPANISH) == "actualizando auth.ts"
    assert describe_tool("code_search", {"pattern": "refreshToken"}, SPANISH) == (
        'buscando "refreshToken"'
    )
    assert describe_tool("Bash", {"command": "pytest"}, SPANISH) == "ejecutando las pruebas"
    assert describe_tool("WebSearch", {}, SPANISH) == "revisando la documentación"
    assert describe_tool("write_todos", {}, SPANISH) == "actualizando el plan"


def test_spanish_excerpt_keeps_accents_and_sentence_limits():
    text = (
        "Voy a revisar la sesión. Después ejecutaré las pruebas. "
        "Al final revisaré el registro."
    )
    assert select_excerpt(text, max_words=50, max_sentences=2, pack=SPANISH) == (
        "Voy a revisar la sesión. Después ejecutaré las pruebas."
    )
    assert select_excerpt(text, max_words=50, max_sentences=1, pack=SPANISH) == (
        "Voy a revisar la sesión."
    )


def test_compose_speaks_tool_actions_in_spanish():
    now = time.time()
    events = [
        Event(
            agent="test",
            kind="tool",
            tool_name="Read",
            tool_input={"path": "session.ts"},
            ts=now,
        ),
        Event(
            agent="test",
            kind="tool",
            tool_name="Bash",
            tool_input={"command": "pytest"},
            ts=now + 0.1,
        ),
    ]
    narration = compose_narration(
        events, NarrationConfig(connectors=False), now=now + 0.2, pack=SPANISH
    )
    assert narration is not None
    assert narration.text == "Estoy leyendo session.ts, y luego ejecutando las pruebas."


def test_compose_joins_spanish_reasoning_and_action():
    now = time.time()
    events = [
        Event(agent="test", kind="reasoning", text="Voy a comprobar.", ts=now),
        Event(
            agent="test",
            kind="tool",
            tool_name="Read",
            tool_input={"path": "session.ts"},
            ts=now + 0.1,
        ),
    ]
    narration = compose_narration(
        events, NarrationConfig(connectors=False), now=now + 0.2, pack=SPANISH
    )
    assert narration is not None
    assert narration.text == "Voy a comprobar. Estoy leyendo session.ts."


def test_speaker_uses_the_language_of_the_selected_voice(tmp_path):
    for voice, lang in (("em_alex", "es"), ("af_heart", "en-us")):
        speaker = KokoroSpeaker(tmp_path, SpeechConfig(voice=voice))
        engine = _FakeEngine()
        output: queue.Queue = queue.Queue()
        speaker._produce(engine, "Hola.", output, threading.Event())
        assert engine.seen["voice"] == voice
        assert engine.seen["lang"] == lang
        assert output.get(timeout=1) is None
