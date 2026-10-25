"""Updating an installed Reezyn.

The site publishes ``updates/latest.json``: the newest build's version, a
download URL, its SHA-256, and the release notes. An update is only installed
when the archive matches that checksum and the app inside carries the same
Developer ID as the copy already installed; nothing else is trusted.

The swap runs in a detached shell helper after the app quits, so a failed copy
puts the previous bundle back and reopens it instead of leaving nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .config import UPDATE_FEED, app_home

log = logging.getLogger("reezyn")

BUNDLE_ID = "io.reezyn.reezyn"
TEAM_ID = "H4G8F4Q8D2"
DEFAULT_APP_PATH = Path("/Applications/Reezyn.app")
CHECK_INTERVAL_S = 24 * 60 * 60
STATE_FILE = "update-check.json"
CHUNK = 1 << 20
MAX_BYTES = 400 * 1024 * 1024
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

Runner = Callable[..., subprocess.CompletedProcess[str]]
Opener = Callable[..., Any]


class UpdateError(RuntimeError):
    """Something about the update could not be trusted or installed."""


@dataclass(frozen=True, slots=True)
class Update:
    version: str
    url: str
    sha256: str
    size: int = 0
    min_macos: str = ""
    channel: str = "stable"
    published_at: str = ""
    notes: tuple[str, ...] = ()


def is_version(text: str) -> bool:
    return bool(VERSION_RE.match(text))


def version_tuple(text: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def is_newer(candidate: str, current: str) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def release_parts(text: str) -> tuple[int, ...]:
    parts = [int(part) for part in text.split(".") if part.isdigit()]
    return tuple(parts) or (0,)


def _at_least(version: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    width = max(len(version), len(minimum))
    return version + (0,) * (width - len(version)) >= minimum + (0,) * (width - len(minimum))


def macos_version() -> tuple[int, ...]:
    return release_parts(platform.mac_ver()[0] or "0")


def parse_manifest(payload: Any) -> Update:
    """Validate a manifest before anything is downloaded."""

    if not isinstance(payload, dict):
        raise UpdateError("the update manifest is not an object")
    version = str(payload.get("version") or "").strip()
    url = str(payload.get("url") or "").strip()
    sha256 = str(payload.get("sha256") or "").strip().lower()
    if not is_version(version):
        raise UpdateError(f"manifest version is not X.Y.Z: {version or 'missing'!r}")
    if not url.startswith("https://"):
        raise UpdateError("manifest download URL is not https")
    if not SHA_RE.match(sha256):
        raise UpdateError("manifest checksum is missing or malformed")

    size = payload.get("size")
    if isinstance(size, bool) or not isinstance(size, (int, float)):
        size = 0
    if size < 0 or size > MAX_BYTES:
        raise UpdateError(f"manifest size is implausible: {size}")

    raw_notes = payload.get("notes") or []
    if isinstance(raw_notes, str):
        raw_notes = [raw_notes]
    notes = tuple(
        str(note).strip() for note in raw_notes if isinstance(note, str) and note.strip()
    )

    return Update(
        version=version,
        url=url,
        sha256=sha256,
        size=int(size),
        min_macos=str(payload.get("min_macos") or "").strip(),
        channel=str(payload.get("channel") or "stable").strip() or "stable",
        published_at=str(payload.get("published_at") or "").strip(),
        notes=notes,
    )


def fetch_manifest(url: str = UPDATE_FEED, timeout: float = 8.0) -> Update:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(1 << 20)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpdateError(f"manifest is not JSON: {exc}") from exc
    return parse_manifest(payload)


def load_last_check(home: Path) -> float:
    try:
        value = json.loads((home / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    stamp = value.get("last_check") if isinstance(value, dict) else None
    return float(stamp) if isinstance(stamp, (int, float)) else 0.0


def record_check(home: Path, when: float, version: str = "") -> None:
    try:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        (home / STATE_FILE).write_text(
            json.dumps({"last_check": when, "version": version}), encoding="utf-8"
        )
    except OSError:
        log.debug("could not record the update check", exc_info=True)


def due(home: Path, interval: float = CHECK_INTERVAL_S, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return now - load_last_check(home) >= interval


def _channel_ok(update_channel: str, channel: str) -> bool:
    # A stable build is also the newest build for beta users.
    return update_channel == channel or (channel == "beta" and update_channel == "stable")


def check_for_update(
    *,
    home: Path | None = None,
    feed_url: str = UPDATE_FEED,
    channel: str = "stable",
    current: str = __version__,
    force: bool = False,
    fetch: Callable[[str], Update] = fetch_manifest,
    now: float | None = None,
) -> Update | None:
    """The newer build, or None. Never raises: a check is allowed to fail."""

    home = home or app_home()
    now = time.time() if now is None else now
    if not force and not due(home, now=now):
        return None
    try:
        update = fetch(feed_url)
    except (OSError, UpdateError, ValueError) as exc:
        log.info("update check failed: %s", exc)
        record_check(home, now)
        return None

    record_check(home, now, update.version)
    if not _channel_ok(update.channel, channel):
        return None
    if update.min_macos and not _at_least(macos_version(), release_parts(update.min_macos)):
        log.info("update %s needs macOS %s", update.version, update.min_macos)
        return None
    if not is_newer(update.version, current):
        return None
    return update


def notes_text(update: Update) -> str:
    if not update.notes:
        return f"Reezyn {update.version} is available."
    return "\n".join(f"• {note}" for note in update.notes)


def download(
    update: Update,
    dest: Path,
    opener: Opener = urllib.request.urlopen,
    chunk: int = CHUNK,
) -> Path:
    """Stream the archive to dest, refusing anything that is not the manifest's."""

    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    written = 0
    request = urllib.request.Request(
        update.url, headers={"User-Agent": f"reezyn/{__version__}"}
    )
    with opener(request, timeout=60) as response, dest.open("wb") as handle:
        while True:
            block = response.read(chunk)
            if not block:
                break
            written += len(block)
            if written > MAX_BYTES:
                raise UpdateError("the download is larger than expected")
            digest.update(block)
            handle.write(block)
    if update.size and written != update.size:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"downloaded {written} bytes, expected {update.size}")
    if digest.hexdigest() != update.sha256:
        dest.unlink(missing_ok=True)
        raise UpdateError("checksum does not match the manifest")
    return dest


def verify_app(
    app: Path, run: Runner = subprocess.run, team_id: str = TEAM_ID
) -> None:
    """The bundle must be signed, intact, and from the same Developer ID."""

    if not (app / "Contents" / "MacOS").is_dir():
        raise UpdateError(f"not an app bundle: {app}")
    check = run(
        ["codesign", "--verify", "--deep", "--strict", str(app)],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        detail = (check.stderr or check.stdout or "").strip()
        raise UpdateError(f"signature check failed: {detail or 'codesign said no'}")
    info = run(["codesign", "-dvvv", str(app)], capture_output=True, text=True)
    details = f"{info.stdout or ''}\n{info.stderr or ''}"
    if f"Identifier={BUNDLE_ID}" not in details:
        raise UpdateError("the bundle identifier is not Reezyn")
    if f"TeamIdentifier={team_id}" not in details:
        raise UpdateError(f"the bundle is not signed by team {team_id}")


def unpack(archive: Path, into: Path, run: Runner = subprocess.run) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    result = run(["ditto", "-x", "-k", str(archive), str(into)], capture_output=True, text=True)
    if result.returncode != 0:
        raise UpdateError((result.stderr or "could not expand the archive").strip())
    apps = sorted(into.glob("*.app"))
    if len(apps) != 1:
        raise UpdateError(f"expected one app in the archive, found {len(apps)}")
    return apps[0]


def installed_app_path() -> Path:
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.suffix == ".app":
            return parent
    return DEFAULT_APP_PATH


def swap_script(app_path: Path, new_app: Path, pid: int, workdir: Path | None = None) -> str:
    """Wait for this process to exit, then swap with rollback and reopen."""

    backup = app_path.with_name(app_path.name + ".previous")
    cleanup = f'\nrm -rf {shlex.quote(str(workdir))}' if workdir else ""
    return f"""#!/bin/sh
set -u
app={shlex.quote(str(app_path))}
new={shlex.quote(str(new_app))}
backup={shlex.quote(str(backup))}
pid={int(pid)}
waited=0
while kill -0 "$pid" 2>/dev/null; do
  # A process its parent has not reaped still answers kill -0; a zombie is dead.
  if [ "$(ps -p "$pid" -o stat= 2>/dev/null | cut -c1)" = "Z" ]; then break; fi
  sleep 0.2
  waited=$((waited + 1))
  if [ "$waited" -gt 900 ]; then
    echo "Reezyn did not quit; the update was not installed." >&2
    exit 1
  fi
done
rm -rf "$backup"
if ! mv "$app" "$backup"; then
  echo "could not move the installed app aside" >&2
  open "$app" 2>/dev/null || true
  exit 1
fi
if ditto "$new" "$app"; then
  xattr -dr com.apple.quarantine "$app" 2>/dev/null || true
  rm -rf "$backup"
else
  rm -rf "$app"
  mv "$backup" "$app"
fi
open "$app"{cleanup}
"""


def install(
    update: Update,
    *,
    app_path: Path | None = None,
    opener: Opener = urllib.request.urlopen,
    run: Runner = subprocess.run,
    temp_root: Path | None = None,
    pid: int | None = None,
) -> Path:
    """Download, verify and schedule the swap. Returns the helper script path.

    The caller is expected to quit the app right after this returns; the helper
    waits for the process to disappear before touching the bundle.
    """

    target = Path(app_path or installed_app_path())
    if not target.exists():
        raise UpdateError(f"Reezyn is not installed at {target}")

    workdir = Path(tempfile.mkdtemp(prefix="reezyn-update-", dir=temp_root))
    archive = download(update, workdir / f"Reezyn-{update.version}.zip", opener=opener)
    bundle = unpack(archive, workdir / "unpacked", run=run)
    verify_app(bundle, run=run)

    helper = workdir / "swap.sh"
    helper.write_text(
        swap_script(target, bundle, os.getpid() if pid is None else pid, workdir),
        encoding="utf-8",
    )
    helper.chmod(0o755)
    subprocess.Popen(
        ["/bin/sh", str(helper)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return helper
