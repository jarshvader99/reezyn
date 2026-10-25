"""Publish a GitHub release for a tagged Reezyn build.

    github_release.py publish 0.2.4 --dmg=dist/Reezyn-0.2.4.dmg --zip=dist/Reezyn-0.2.4.zip

The tag has to exist on origin first, so this runs after the push — from
``packaging/release.sh --publish-release`` or on its own. Re-running is safe:
an existing release is updated in place and assets already attached are left
alone.

Authentication comes from ``GITHUB_TOKEN``/``GH_TOKEN`` when set, otherwise
from the credential git already uses for github.com.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
SITE_URL = "https://reezyn.elastikmind.com"
SLUG_RE = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

Call = Callable[..., tuple[int, Any]]


class ReleaseError(RuntimeError):
    """The release could not be published."""


def parse_slug(remote: str) -> str:
    """``owner/repo`` from an https, ssh, or scp-style git remote."""

    text = remote.strip()
    if text.startswith("git@"):
        text = text.split(":", 1)[-1]
    elif "://" in text:
        text = urllib.parse.urlparse(text).path.lstrip("/")
    match = SLUG_RE.match(text)
    if not match:
        raise ReleaseError(f"cannot read a GitHub repository from {remote!r}")
    return f"{match.group('owner')}/{match.group('repo')}"


def origin_slug() -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise ReleaseError("this checkout has no origin remote")
    return parse_slug(result.stdout)


def token() -> str:
    value = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if value:
        return value.strip()
    result = subprocess.run(
        ["git", "credential", "fill"],
        cwd=ROOT,
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("password=") and line[9:].strip():
            return line[9:].strip()
    raise ReleaseError("no GitHub token in GITHUB_TOKEN, GH_TOKEN, or git's credential store")


def call(
    method: str,
    url: str,
    *,
    auth: str,
    data: bytes | None = None,
    content_type: str = "application/json",
    timeout: float = 60.0,
) -> tuple[int, Any]:
    """One API request; the status plus decoded JSON, or raw text on failure."""

    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {auth}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    except OSError as exc:
        raise ReleaseError(f"{method} {url} failed: {exc}") from exc
    if not body:
        return status, None
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body.decode("utf-8", "replace")


def _message(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("message") or payload)
    return str(payload)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def release_body(slug: str, version: str, notes: list[str], dmg: Path | None) -> str:
    lines = [f"• {note}" for note in notes] or [f"Reezyn {version}."]
    lines.append("")
    if dmg is not None and dmg.is_file():
        size_mb = dmg.stat().st_size / (1024 * 1024)
        link = f"https://github.com/{slug}/releases/download/v{version}/{dmg.name}"
        lines.append(
            f"**Download:** [{dmg.name}]({link}) · {size_mb:.1f} MB · "
            f"SHA-256 `{sha256_of(dmg)}`"
        )
    lines.append(
        f"Requires macOS 13 or later. The in-app updater reads {SITE_URL}/updates/latest.json."
    )
    return "\n".join(lines)


def feed_notes(version: str) -> list[str]:
    """The published notes for version, when the feed is about that version."""

    try:
        manifest = json.loads((ROOT / "site" / "updates" / "latest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if str(manifest.get("version")) != version:
        return []
    return [str(note) for note in manifest.get("notes") or [] if str(note).strip()]


def git_notes(version: str) -> list[str]:
    """Commit subjects since the previous tag, for a version with no feed entry."""

    tag = f"v{version}"
    previous = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", f"{tag}^"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()
    span = f"{previous}..{tag}" if previous else tag
    result = subprocess.run(
        ["git", "log", "--pretty=%s", span], cwd=ROOT, capture_output=True, text=True
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def find_release(
    slug: str, tag: str, *, auth: str, call: Call = call
) -> dict[str, Any] | None:
    status, payload = call("GET", f"{API}/repos/{slug}/releases/tags/{tag}", auth=auth)
    if status == 404:
        return None
    if status != 200 or not isinstance(payload, dict):
        raise ReleaseError(f"could not look up {tag}: HTTP {status} {_message(payload)}")
    return payload


def publish_asset(
    slug: str, release_id: int, path: Path, *, auth: str, call: Call = call
) -> str:
    """Attach one file unless a release asset of that name is already there."""

    status, assets = call("GET", f"{API}/repos/{slug}/releases/{release_id}/assets", auth=auth)
    if status != 200 or not isinstance(assets, list):
        raise ReleaseError(f"could not list assets: HTTP {status} {_message(assets)}")
    if any(asset.get("name") == path.name for asset in assets if isinstance(asset, dict)):
        return f"{path.name} already attached"

    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    query = urllib.parse.urlencode({"name": path.name})
    status, payload = call(
        "POST",
        f"{UPLOADS}/repos/{slug}/releases/{release_id}/assets?{query}",
        auth=auth,
        data=path.read_bytes(),
        content_type=content_type,
        timeout=600.0,
    )
    if status not in (200, 201):
        raise ReleaseError(f"uploading {path.name} failed: HTTP {status} {_message(payload)}")
    return f"{path.name} uploaded"


def publish(
    version: str,
    *,
    dmg: Path | None = None,
    zip_path: Path | None = None,
    latest: bool = True,
    notes: list[str] | None = None,
    slug: str | None = None,
    auth: str | None = None,
    call: Call = call,
) -> dict[str, Any]:
    """Create or update the release for version, then attach the build."""

    slug = slug or origin_slug()
    auth = auth or token()
    tag = f"v{version}"
    notes = feed_notes(version) if notes is None else notes
    if not notes:
        notes = git_notes(version)

    payload: dict[str, Any] = {
        "name": f"Reezyn {version}",
        "body": release_body(slug, version, notes, dmg),
        "draft": False,
        "prerelease": False,
    }
    if latest:
        payload["make_latest"] = "true"

    existing = find_release(slug, tag, auth=auth, call=call)
    if existing is None:
        status, created = call(
            "POST",
            f"{API}/repos/{slug}/releases",
            auth=auth,
            data=json.dumps({"tag_name": tag, **payload}).encode(),
        )
        if status not in (200, 201) or not isinstance(created, dict):
            raise ReleaseError(f"could not create {tag}: HTTP {status} {_message(created)}")
        release, action = created, "created"
    else:
        status, updated = call(
            "PATCH",
            f"{API}/repos/{slug}/releases/{existing['id']}",
            auth=auth,
            data=json.dumps(payload).encode(),
        )
        if status != 200 or not isinstance(updated, dict):
            raise ReleaseError(f"could not update {tag}: HTTP {status} {_message(updated)}")
        release, action = updated, "updated"

    steps = [action, str(release.get("html_url") or "")]
    for asset in (dmg, zip_path):
        if asset is not None and asset.is_file():
            steps.append(publish_asset(slug, int(release["id"]), asset, auth=auth, call=call))
    return {"action": action, "url": steps[1], "steps": steps}


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "publish":
        raise SystemExit(__doc__)
    version = argv[2]
    if not VERSION_RE.match(version):
        raise SystemExit(f"not a version: {version}")
    dmg = zip_path = None
    latest = True
    for flag in argv[3:]:
        if flag.startswith("--dmg="):
            dmg = Path(flag.split("=", 1)[1]).expanduser().resolve()
        elif flag.startswith("--zip="):
            zip_path = Path(flag.split("=", 1)[1]).expanduser().resolve()
        elif flag == "--no-latest":
            latest = False
        else:
            raise SystemExit(f"unknown argument: {flag}")
    result = publish(version, dmg=dmg, zip_path=zip_path, latest=latest)
    print(f"Release {result['action']}: {result['url']}")
    for line in result["steps"][2:]:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
