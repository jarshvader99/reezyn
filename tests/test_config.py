from __future__ import annotations

from reezyn.config import (
    DEFAULT_VOICE,
    Config,
    app_home,
    load_config,
    with_speed,
    with_voice,
)


def test_config_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("REEZYN_HOME", str(tmp_path))
    assert app_home() == tmp_path
    config = load_config(tmp_path)
    assert (tmp_path / "config.toml").exists()
    assert config.speech.voice == DEFAULT_VOICE
    assert config.narration.max_sentences == 2
    assert config.agents.cursor is True
    assert config.agents.opencode is True
    assert config.agents.freebuff is True

    config = with_voice(config, "am_michael")
    config = with_speed(config, 1.01)
    config.save(tmp_path)

    loaded = load_config(tmp_path)
    assert loaded.speech.voice == "am_michael"
    assert loaded.speech.speed == 1.01


def test_config_clamps_bad_values(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[speech]\n"
        "voice = \"does_not_exist\"\n"
        "speed = 9\n"
        "volume = -2\n"
        "[narration]\n"
        "max_words = 1000\n"
        "max_sentences = 99\n"
        "debounce_ms = 1\n"
        "stale_after_s = -4\n"
        "[agents]\n"
        "codex = false\n"
    )
    config = load_config(tmp_path)
    assert config.speech.voice == DEFAULT_VOICE
    assert config.speech.speed == 1.18
    assert config.speech.volume == 0.0
    assert config.narration.max_words == 400
    assert config.narration.max_sentences == 12
    assert config.narration.debounce_ms == 150
    assert config.narration.stale_after_s == 2
    assert config.agents.codex is False
    assert config.agents.cursor is True
    assert config.agents.opencode is True
    assert config.agents.freebuff is True
    assert config.agents.freebuff is True


def test_invalid_toml_falls_back_to_defaults(tmp_path):
    (tmp_path / "config.toml").write_text("this is not toml [[[")
    assert isinstance(load_config(tmp_path), Config)
