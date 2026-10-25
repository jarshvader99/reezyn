from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from reezyn import cli, updates
from reezyn.config import Config


def _update() -> updates.Update:
    return updates.Update(
        version="9.9.9",
        url="https://example.com/Reezyn-9.9.9.zip",
        sha256="a" * 64,
        size=1,
    )


def _prepare(monkeypatch, tmp_path: Path, running: int) -> dict:
    seen: dict = {}
    monkeypatch.setattr(updates, "check_for_update", lambda **_kwargs: _update())
    monkeypatch.setattr(updates, "installed_app_path", lambda: tmp_path / "Reezyn.app")
    monkeypatch.setattr(updates, "running_app_pid", lambda: running)
    monkeypatch.setattr(
        updates,
        "install",
        lambda update, **kwargs: seen.update(kwargs, version=update.version) or tmp_path / "swap.sh",
    )
    monkeypatch.setattr(updates, "quit_app", lambda pid, **_kwargs: seen.update(quit=pid) or True)
    monkeypatch.setattr(cli, "load_config", lambda _home=None: Config())
    return seen


def test_the_cli_waits_on_the_running_app_and_quits_it(monkeypatch, tmp_path):
    seen = _prepare(monkeypatch, tmp_path, running=4242)

    code = cli.cmd_update(Namespace(check=False, yes=True, channel=None))

    assert code == 0
    assert seen["version"] == "9.9.9"
    # The helper must outlive the app, so the app's pid is what it waits on.
    assert seen["pid"] == 4242
    assert seen["quit"] == 4242


def test_without_a_running_app_the_helper_waits_on_this_command(monkeypatch, tmp_path):
    seen = _prepare(monkeypatch, tmp_path, running=0)

    code = cli.cmd_update(Namespace(check=False, yes=True, channel=None))

    assert code == 0
    assert seen["pid"] is None
    assert "quit" not in seen


def test_check_only_reports_and_installs_nothing(monkeypatch, tmp_path):
    seen = _prepare(monkeypatch, tmp_path, running=4242)

    code = cli.cmd_update(Namespace(check=True, yes=True, channel=None))

    assert code == 0
    assert seen == {}
