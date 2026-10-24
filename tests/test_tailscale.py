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
