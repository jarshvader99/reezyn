from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from reezyn.cli import _configure_logging, _hook_log
from reezyn.config import Config, ensure_home


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture(autouse=True)
def clean_reezyn_logger():
    logger = logging.getLogger("reezyn")
    before = list(logger.handlers)
    try:
        yield
    finally:
        for handler in list(logger.handlers):
            if handler not in before:
                handler.close()
                logger.removeHandler(handler)


def test_ensure_home_repairs_a_world_readable_directory(tmp_path):
    home = tmp_path / "state"
    home.mkdir()
    os.chmod(home, 0o755)

    assert ensure_home(home) == home
    assert _mode(home) == 0o700


def test_config_save_leaves_an_owner_only_directory(tmp_path):
    old = os.umask(0o022)
    try:
        home = tmp_path / "state"
        Config().save(home)
        assert _mode(home) == 0o700
        assert (home / "config.toml").is_file()
    finally:
        os.umask(old)


def test_the_log_and_its_rotations_are_owner_only(tmp_path):
    home = tmp_path / "state"
    home.mkdir()
    older = home / "reezyn.log.1"
    older.write_text("previous session\n", encoding="utf-8")
    os.chmod(older, 0o644)

    old = os.umask(0o022)
    try:
        _configure_logging(home)
    finally:
        os.umask(old)

    assert _mode(home) == 0o700
    assert _mode(home / "reezyn.log") == 0o600
    # A rotation that was already on disk is repaired too.
    assert _mode(older) == 0o600


def test_hook_errors_are_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("REEZYN_HOME", str(tmp_path / "state"))
    old = os.umask(0o022)
    try:
        _hook_log("claude", "the hook could not speak")
    finally:
        os.umask(old)

    path = tmp_path / "state" / "hook-errors.log"
    assert path.is_file()
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700
    assert "the hook could not speak" in path.read_text(encoding="utf-8")
