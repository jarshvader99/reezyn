from __future__ import annotations

from reezyn.config import (
    AMBIENCE_LEVELS,
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
    assert config.ambience.enabled is False
    assert config.ambience.volume == 0.35

    config = with_voice(config, "am_michael")
    config = with_speed(config, 1.01)
    config.save(tmp_path)

    loaded = load_config(tmp_path)
    assert loaded.speech.voice == "am_michael"
    assert loaded.speech.speed == 1.01


def test_config_accepts_a_spanish_voice(tmp_path):
    (tmp_path / "config.toml").write_text('[speech]\nvoice = "ef_dora"\n')
    assert load_config(tmp_path).speech.voice == "ef_dora"

    config = with_voice(load_config(tmp_path), "em_alex")
    assert config.speech.voice == "em_alex"

    (tmp_path / "config.toml").write_text('[speech]\nvoice = "ff_siwis"\n')
    assert load_config(tmp_path).speech.voice == "ff_siwis"


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
    assert config.ambience.enabled is False
    assert config.ambience.volume == 0.35


def test_ambience_levels_are_few_and_quiet():
    assert list(AMBIENCE_LEVELS) == ["Whisper", "Soft", "Clear"]
    assert all(0.0 < level <= 0.6 for level in AMBIENCE_LEVELS.values())
    assert AMBIENCE_LEVELS["Soft"] == 0.35


def test_ambience_config_round_trip(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[ambience]\nenabled = true\nvolume = 2\n"
    )
    config = load_config(tmp_path)
    assert config.ambience.enabled is True
    assert config.ambience.volume == 1.0

    config.ambience.volume = 0.3
    config.save(tmp_path)
    loaded = load_config(tmp_path)
    assert loaded.ambience.enabled is True
    assert loaded.ambience.volume == 0.3


def test_banter_defaults_clamp_and_round_trip(tmp_path):
    config = load_config(tmp_path)
    assert config.banter.enabled is True
    assert (
        config.banter.after_s,
        config.banter.repeat_s,
        config.banter.max_lines,
    ) == (30.0, 75.0, 2)

    (tmp_path / "config.toml").write_text(
        "[banter]\n"
        "enabled = false\n"
        "after_s = 1\n"
        "repeat_s = 900\n"
        "max_lines = 99\n"
    )
    loaded = load_config(tmp_path)
    assert loaded.banter.enabled is False
    assert loaded.banter.after_s == 8
    assert loaded.banter.repeat_s == 600
    assert loaded.banter.max_lines == 5

    loaded.banter.enabled = True
    loaded.save(tmp_path)
    again = load_config(tmp_path)
    assert again.banter.enabled is True
    assert again.banter.after_s == 8


def test_invalid_toml_falls_back_to_defaults(tmp_path):
    (tmp_path / "config.toml").write_text("this is not toml [[[")
    assert isinstance(load_config(tmp_path), Config)
