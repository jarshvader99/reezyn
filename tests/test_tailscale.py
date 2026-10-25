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
