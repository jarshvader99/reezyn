"""Tailscale detection and HTTPS publishing.

The page listens on loopback. ``tailscale serve`` puts it on the tailnet behind
Tailscale's own HTTPS certificate, so no port is opened to the internet and the
phone needs nothing but the Tailscale app and the same account.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import Any

log = logging.getLogger("reezyn")

# Only the notarized app bundle is trusted by path. Command-line installs are
# reached through PATH as a fallback, so a package-manager directory is never
# preferred over the app.
CLI_CANDIDATES = (
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/tailscale",
)

INSTALL_HELP = (
    "Install the Tailscale command line first:\n\n"
    "1. Open Tailscale from the menu bar.\n"
    "2. Choose Settings → CLI integration.\n"
    "3. Click Install Now.\n\n"
    "Then turn remote access on again."
)


def cli() -> str | None:
    # The installed app bundle first: it is the binary that was notarized with
    # the same identity as this app. PATH is the fallback for machines that
    # only have a command-line install, and it is chosen last on purpose, since
    # a PATH entry is whatever the launching environment says it is.
    for path in CLI_CANDIDATES:
        if _is_executable(path):
            return path
    return shutil.which("tailscale")


def _is_executable(path: str) -> bool:
    import os

    return os.path.isfile(path) and os.access(path, os.X_OK)


def _run(args: list[str], timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
    binary = cli()
    if binary is None:
        raise FileNotFoundError("tailscale")
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def status() -> dict[str, Any]:
    """The parts of ``tailscale status`` the app cares about."""

    try:
        result = _run(["status", "--json"])
    except (OSError, subprocess.SubprocessError, FileNotFoundError):
        return {"available": False, "running": False, "reason": INSTALL_HELP}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return {
            "available": True,
            "running": False,
            "reason": detail or "Tailscale is not connected.",
        }
    try:
        raw = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"available": True, "running": False, "reason": "Could not read tailscale status."}
    self_info = raw.get("Self") if isinstance(raw.get("Self"), dict) else {}
    dns = str(self_info.get("DNSName") or "").rstrip(".")
    ips = self_info.get("TailscaleIPs") or []
    ip = str(ips[0]) if isinstance(ips, list) and ips else ""
    state = str(raw.get("BackendState") or "")
    return {
        "available": True,
        "running": state == "Running",
        "state": state,
        "dns_name": dns,
        "ip": ip,
        "reason": "" if state == "Running" else f"Tailscale is {state or 'not connected'}.",
    }


def _serve_url() -> str:
    try:
        result = _run(["serve", "status", "--json"])
    except (OSError, subprocess.SubprocessError, FileNotFoundError):
        return ""
    if result.returncode != 0:
        return ""
    try:
        raw = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    web = raw.get("Web")
    if isinstance(web, dict):
        for key in web:
            host, _, port = str(key).rpartition(":")
            if host and port in ("443", ""):
                return f"https://{host}"
    return ""


def publish(port: int) -> dict[str, Any]:
    """Expose ``http://127.0.0.1:<port>`` on the tailnet, in the background."""

    info = status()
    if not info.get("available"):
        return {"ok": False, "reason": info.get("reason") or INSTALL_HELP, "url": ""}
    if not info.get("running"):
        return {"ok": False, "reason": info.get("reason") or "Tailscale is not connected.", "url": ""}

    try:
        result = _run(["serve", "--bg", "--yes", str(port)], timeout=8.0)
    except subprocess.TimeoutExpired as exc:
        output = _text(exc.stdout) + _text(exc.stderr)
        setup_url = _setup_url(output)
        if setup_url:
            return {
                "ok": False,
                "reason": (
                    "Serve is not enabled on your tailnet. Open this link once, "
                    "then turn remote access off and on:\n\n" + setup_url
                ),
                "url": "",
                "setup_url": setup_url,
            }
        return {
            "ok": False,
            "reason": "Tailscale did not answer in time.\n\n" + output.strip()[:600],
            "url": "",
        }
    except (OSError, subprocess.SubprocessError, FileNotFoundError):
        return {"ok": False, "reason": INSTALL_HELP, "url": ""}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        setup_url = _setup_url(detail)
        if setup_url:
            return {
                "ok": False,
                "reason": (
                    "Serve is not enabled on your tailnet. Open this link once, "
                    "then turn remote access off and on:\n\n" + setup_url
                ),
                "url": "",
                "setup_url": setup_url,
            }
        if "https" in detail.lower():
            detail += (
                "\n\nIf Tailscale asks, enable HTTPS certificates for your tailnet "
                "in the admin console, then try again."
            )
        return {
            "ok": False,
            "reason": detail or "tailscale serve failed.",
            "url": "",
            "setup_url": "",
        }

    url = _serve_url()
    if not url:
        dns = str(info.get("dns_name") or "")
        url = f"https://{dns}" if dns else ""
    return {
        "ok": bool(url),
        "reason": "" if url else "Serve started but no HTTPS name was found.",
        "url": url,
    }


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def _setup_url(output: str) -> str:
    match = re.search(r"https://login\.tailscale\.com/\S+", output or "")
    return match.group(0).rstrip(".,)") if match else ""


def local_ip() -> str:
    """A tailnet address for a plain-HTTP fallback, or empty."""

    return str(status().get("ip") or "")
