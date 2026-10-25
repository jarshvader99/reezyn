"""Version arithmetic and file rewrites for packaging/release.sh.

Three small commands, so the shell script stays readable:

    release_version.py next  <current> <patch|minor|major|X.Y.Z>
    release_version.py apply <version>
    release_version.py stamp <version> <dmg>
    release_version.py feed  <version> <zip> [notes.txt]
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_URL = "https://reezyn.elastikmind.com"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
DMG_LINK = re.compile(r"downloads/Reezyn-\d+\.\d+\.\d+\.dmg")
CHECKSUM = re.compile(r"(<code>)[0-9a-f]{64}(</code>)")
SIZE = re.compile(r"(macOS 13 or later\. )\d+ MB\.")


def next_version(current: str, spec: str) -> str:
    if SEMVER.match(spec):
        return spec
    if spec not in ("patch", "minor", "major"):
        raise SystemExit(f"not a version or bump: {spec}")
    major, minor, patch = (int(part) for part in current.split("."))
    if spec == "major":
        return f"{major + 1}.0.0"
    if spec == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _replace(path: Path, pattern: re.Pattern[str], value: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = pattern.subn(value, text)
    if count == 0:
        raise SystemExit(f"nothing to rewrite in {path.relative_to(ROOT)}")
    path.write_text(updated, encoding="utf-8")


def apply_version(version: str) -> None:
    """Point the package and the download page at version."""

    _replace(ROOT / "pyproject.toml", re.compile(r'^version = "[^"]+"', re.M), f'version = "{version}"')
    _replace(ROOT / "reezyn" / "__init__.py", re.compile(r'^__version__ = "[^"]+"', re.M), f'__version__ = "{version}"')

    page = ROOT / "site" / "index.html"
    _replace(page, DMG_LINK, f"downloads/Reezyn-{version}.dmg")
    _replace(
        page,
        re.compile(r"(<p class=\"download-fine\">Version )\d+\.\d+\.\d+\."),
        rf"\g<1>{version}.",
    )


def stamp_download(version: str, dmg: Path) -> str:
    """Put the built DMG in site/downloads and stamp its checksum and size."""

    downloads = ROOT / "site" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    target = downloads / f"Reezyn-{version}.dmg"
    for old in downloads.glob("Reezyn-*.dmg"):
        if old != target:
            old.unlink()
    shutil.copy2(dmg, target)

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    size_mb = target.stat().st_size // (1024 * 1024)

    page = ROOT / "site" / "index.html"
    _replace(page, CHECKSUM, rf"\g<1>{digest}\g<2>")
    _replace(page, SIZE, rf"\g<1>{size_mb} MB.")
    return digest


def write_feed(version: str, archive: Path, notes: list[str]) -> Path:
    """Publish the update manifest the installed app reads."""

    updates = ROOT / "site" / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    target = updates / f"Reezyn-{version}.zip"
    for old in updates.glob("Reezyn-*.zip"):
        if old != target:
            old.unlink()
    shutil.copy2(archive, target)

    manifest = {
        "version": version,
        "channel": "stable",
        "min_macos": "13.0",
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "url": f"{SITE_URL}/updates/{target.name}",
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "size": target.stat().st_size,
        "notes": [note.strip() for note in notes if note.strip()],
    }
    path = updates / "latest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    command = argv[1]
    if command == "next" and len(argv) == 4:
        print(next_version(argv[2], argv[3]))
        return 0
    if command == "apply" and len(argv) == 3:
        if not SEMVER.match(argv[2]):
            raise SystemExit(f"not a version: {argv[2]}")
        apply_version(argv[2])
        return 0
    if command == "stamp" and len(argv) == 4:
        dmg = Path(argv[3]).resolve()
        if not dmg.is_file():
            raise SystemExit(f"no DMG at {dmg}")
        print(stamp_download(argv[2], dmg))
        return 0
    if command == "feed" and len(argv) in (4, 5):
        archive = Path(argv[3]).resolve()
        if not archive.is_file():
            raise SystemExit(f"no archive at {archive}")
        notes: list[str] = []
        if len(argv) == 5:
            notes = Path(argv[4]).read_text(encoding="utf-8").splitlines()
        print(write_feed(argv[2], archive, notes))
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
