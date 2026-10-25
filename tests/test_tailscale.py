from __future__ import annotations

import subprocess

from reezyn.remote import tailscale


def test_a_serve_setup_link_is_extracted_from_cli_output():
    output = (
        "\nServe is not enabled on your tailnet.\n"
        "To enable, visit:\n\n"
        "         https://login.tailscale.com/f/serve?node=nnbgNhMMEB21CNTRL\n\n"
    )
    assert tailscale._setup_url(output) == (
        "https://login.tailscale.com/f/serve?node=nnbgNhMMEB21CNTRL"
    )
    assert tailscale._setup_url("no link here") == ""


def test_publishing_reports_the_one_time_enable_link(monkeypatch):
    monkeypatch.setattr(
        tailscale,
        "status",
        lambda: {
            "available": True,
            "running": True,
            "state": "Running",
            "dns_name": "mac.tailnet.ts.net",
            "ip": "100.1.2.3",
            "reason": "",
        },
    )

    def fake_run(args, timeout=12.0):
        raise subprocess.TimeoutExpired(
            cmd=args,
            timeout=timeout,
            output=(
                b"Serve is not enabled on your tailnet.\n"
                b"https://login.tailscale.com/f/serve?node=abc123\n"
            ),
        )

    monkeypatch.setattr(tailscale, "_run", fake_run)

    result = tailscale.publish(8787)

    assert result["ok"] is False
    assert result["url"] == ""
    assert result["setup_url"] == "https://login.tailscale.com/f/serve?node=abc123"
    assert "Serve is not enabled" in result["reason"]


def test_the_cli_always_sees_a_terminal(monkeypatch):
    # A GUI app has no TERM, and the macOS Tailscale client turns that into
    # "The Tailscale GUI failed to start" on stdout with exit status 0.
    monkeypatch.delenv("TERM", raising=False)
    assert tailscale._child_env()["TERM"] == "xterm-256color"

    monkeypatch.setenv("TERM", "screen-256color")
    assert tailscale._child_env()["TERM"] == "screen-256color"


def test_the_running_app_is_asked_with_that_environment(monkeypatch, tmp_path):
    binary = tmp_path / "Tailscale"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(tailscale, "cli", lambda: str(binary))
    monkeypatch.delenv("TERM", raising=False)

    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout='{"BackendState": "Running"}', stderr="")

    monkeypatch.setattr(tailscale.subprocess, "run", fake_run)

    tailscale.status()

    assert seen["argv"] == [str(binary), "status", "--json"]
    assert seen["env"]["TERM"] == "xterm-256color"


def test_a_gui_failure_on_stdout_is_reported_instead_of_a_shrug(monkeypatch):
    def fake_run(args, timeout=12.0):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                "The Tailscale GUI failed to start: The operation couldn\u2019t be "
                "completed. (Tailscale.CLIError error 3.)\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(tailscale, "_run", fake_run)

    info = tailscale.status()

    assert info["available"] is True
    assert info["running"] is False
    assert "GUI failed to start" in info["reason"]
    assert "turn remote access off and on" in info["reason"]


def test_a_broken_status_read_still_starts_the_page_locally(monkeypatch):
    monkeypatch.setattr(tailscale, "status", lambda: {"available": True, "running": False, "reason": "nope"})

    published = tailscale.publish(8787)

    assert published == {"ok": False, "reason": "nope", "url": ""}


def test_the_installed_app_binary_wins_over_path(monkeypatch, tmp_path):
    pinned = tmp_path / "Tailscale.app" / "Tailscale"
    pinned.parent.mkdir()
    pinned.write_text("#!/bin/sh\n", encoding="utf-8")
    pinned.chmod(0o755)

    planted = tmp_path / "path-bin" / "tailscale"
    planted.parent.mkdir()
    planted.write_text("#!/bin/sh\necho planted\n", encoding="utf-8")
    planted.chmod(0o755)

    monkeypatch.setattr(tailscale, "CLI_CANDIDATES", (str(pinned),))
    monkeypatch.setenv("PATH", str(planted.parent))

    assert tailscale.cli() == str(pinned)


def test_path_is_only_the_fallback(monkeypatch, tmp_path):
    missing = tmp_path / "not-installed" / "Tailscale"
    planted = tmp_path / "path-bin" / "tailscale"
    planted.parent.mkdir()
    planted.write_text("#!/bin/sh\n", encoding="utf-8")
    planted.chmod(0o755)

    monkeypatch.setattr(tailscale, "CLI_CANDIDATES", (str(missing),))
    monkeypatch.setenv("PATH", str(planted.parent))

    assert tailscale.cli() == str(planted)
