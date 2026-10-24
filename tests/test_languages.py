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
FRENCH = PACKS["fr"]


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
    assert pack_for_voice("ff_siwis") is PACKS["fr"]
    assert pack_for_voice("not-a-voice") is DEFAULT_PACK
    assert {"ef_dora", "em_alex", "em_santa", "ff_siwis"} <= set(VOICES)
    assert [pack.name for pack in LANGUAGES] == ["English", "Spanish", "French"]
    assert SPANISH.kokoro_lang == "es"
    assert FRENCH.kokoro_lang == "fr-fr"


def test_spanish_cleaning_uses_spanish_words():
    cleaned = clean_for_speech(
        "Revisa https://example.com y el 50% de session.ts.", pack=SPANISH
    )
    assert "un enlace" in cleaned
    assert "por ciento" in cleaned
    assert " and " not in cleaned


def test_language_packs_cover_the_same_phrases():
    keys = set(PACKS["en"].phrases)
    for pack in PACKS.values():
        assert set(pack.phrases) == keys


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


def test_describe_tool_speaks_french():
    assert describe_tool("Read", {"path": "session.ts"}, FRENCH) == "lire session.ts"
    assert describe_tool("str_replace", {"path": "src/auth.ts"}, FRENCH) == (
        "mettre à jour auth.ts"
    )
    assert describe_tool("code_search", {"pattern": "refreshToken"}, FRENCH) == (
        "chercher « refreshToken »"
    )
    assert describe_tool("Bash", {"command": "pytest"}, FRENCH) == "lancer les tests"
    assert describe_tool("WebSearch", {}, FRENCH) == "consulter la documentation"
    assert describe_tool("write_todos", {}, FRENCH) == "mettre à jour le plan"


def test_french_excerpt_keeps_accents_and_sentence_limits():
    text = (
        "Je vais inspecter la session. Ensuite je lancerai les tests. "
        "Enfin je relirai le journal."
    )
    assert select_excerpt(text, max_words=50, max_sentences=2, pack=FRENCH) == (
        "Je vais inspecter la session. Ensuite je lancerai les tests."
    )
    assert select_excerpt(text, max_words=50, max_sentences=1, pack=FRENCH) == (
        "Je vais inspecter la session."
    )


def test_compose_speaks_tool_actions_in_french():
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
        events, NarrationConfig(connectors=False), now=now + 0.2, pack=FRENCH
    )
    assert narration is not None
    assert narration.text == "Je vais lire session.ts, puis lancer les tests."


def test_compose_joins_french_reasoning_and_action():
    now = time.time()
    events = [
        Event(agent="test", kind="reasoning", text="Je vérifie.", ts=now),
        Event(
            agent="test",
            kind="tool",
            tool_name="Read",
            tool_input={"path": "session.ts"},
            ts=now + 0.1,
        ),
    ]
    narration = compose_narration(
        events, NarrationConfig(connectors=False), now=now + 0.2, pack=FRENCH
    )
    assert narration is not None
    assert narration.text == "Je vérifie. Je vais lire session.ts."


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
    for voice, lang in (
        ("em_alex", "es"),
        ("ff_siwis", "fr-fr"),
        ("af_heart", "en-us"),
    ):
        speaker = KokoroSpeaker(tmp_path, SpeechConfig(voice=voice))
        engine = _FakeEngine()
        output: queue.Queue = queue.Queue()
        speaker._produce(engine, "Hola.", output, threading.Event())
        assert engine.seen["voice"] == voice
        assert engine.seen["lang"] == lang
        assert output.get(timeout=1) is None
