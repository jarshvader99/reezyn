"""Count downloads from the hosting access log, without a tracker in the page.

The site carries no analytics tag, no cookie, and no JavaScript of its own. The
one measurement is the access log the hosting account already keeps: this reads
it, counts the requests that asked for a disk image, and prints totals. Nothing
is written back, no visitor identifier is stored, and no request leaves the Mac.
Visitor counts are a per-run hash of address and user agent held in memory, so
the report can say "nine people, twelve downloads" without keeping a list of who.

    python packaging/download_counts.py --log access.log
    python packaging/download_counts.py --log access.log.1 --log access.log \
        --since 2026-10-20 --json
    python packaging/download_counts.py --github     # release asset totals

A download is a GET, or a ranged resume, for a file under ``/downloads/`` that
ended 2xx and was not sent by a crawler. ``HEAD`` is not counted: browsers and
link checkers send it before the real request.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

DOWNLOAD_PREFIX = "/downloads/"
DOWNLOAD_SUFFIXES = (".dmg", ".zip")

# Crawlers, link unfurlers and uptime monitors. curl and wget stay in: for a
# developer tool they are people, and the cask installs that way.
BOT_MARKERS = (
    "bot",
    "crawler",
    "spider",
    "slurp",
    "facebookexternalhit",
    "python-requests",
    "go-http-client",
    "headlesschrome",
    "uptime",
    "pingdom",
    "statuscake",
    "semrush",
    "ahrefs",
    "bytespider",
)

# "1.2.3.4 - - [25/Oct/2026:09:31:02 +0000] "GET /downloads/x.dmg HTTP/1.1" 200 60754867 "-" "curl/8.7""
# Hostinger prefixes the vhost on some tiers, so the match starts anywhere.
LOG_RE = re.compile(
    r"(?P<ip>[0-9a-fA-F:.]+) \S+ \S+ "
    r"\[(?P<stamp>[^\]]+)\] "
    r'"(?P<method>[A-Z]+) (?P<path>\S+)(?: [^"]*)?" '
    r"(?P<status>\d{3}) (?P<size>\S+)"
    r'(?: "[^"]*" "(?P<agent>[^"]*)")?'
)


class Request(NamedTuple):
    day: date
    method: str
    path: str
    status: int
    agent: str
    visitor: str


class Summary:
    """Running totals. Visitor sets live here and nowhere else."""

    def __init__(self) -> None:
        self.downloads = 0
        self.by_file: dict[str, int] = {}
        self.by_day: dict[date, int] = {}
        self.visitors_by_day: dict[date, set[str]] = {}
        self.bot_requests = 0
        self.heads = 0
        self.failed = 0

    def add(self, request: Request) -> None:
        name = request.path.rsplit("/", 1)[-1] or request.path
        self.downloads += 1
        self.by_file[name] = self.by_file.get(name, 0) + 1
        self.by_day[request.day] = self.by_day.get(request.day, 0) + 1
        self.visitors_by_day.setdefault(request.day, set()).add(request.visitor)

    @property
    def visitors(self) -> int:
        return sum(len(seen) for seen in self.visitors_by_day.values())

    def as_dict(self) -> dict:
        return {
            "downloads": self.downloads,
            "visitors": self.visitors,
            "by_file": dict(sorted(self.by_file.items())),
            "by_day": [
                {
                    "day": day.isoformat(),
                    "downloads": self.by_day[day],
                    "visitors": len(self.visitors_by_day.get(day, set())),
                }
                for day in sorted(self.by_day)
            ],
            "skipped": {
                "head": self.heads,
                "bots": self.bot_requests,
                "failed": self.failed,
            },
        }


def salt() -> str:
    """A per-run salt: the visitor hash cannot be matched with a later report."""

    return os.urandom(16).hex()


def visitor_hash(address: str, agent: str, run_salt: str) -> str:
    digest = hashlib.blake2b(f"{run_salt}\x00{address}\x00{agent}".encode(), digest_size=8)
    return digest.hexdigest()


def is_bot(agent: str) -> bool:
    lowered = agent.lower()
    return any(marker in lowered for marker in BOT_MARKERS)


def is_download(path: str, method: str, status: int) -> bool:
    if method not in ("GET",):
        return False
    if not 200 <= status < 300:
        return False
    clean = path.split("?", 1)[0]
    return clean.startswith(DOWNLOAD_PREFIX) and clean.lower().endswith(DOWNLOAD_SUFFIXES)


def parse_line(line: str, run_salt: str) -> Request | None:
    match = LOG_RE.search(line)
    if not match:
        return None
    try:
        stamp = datetime.strptime(match["stamp"], "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
    agent = match["agent"] or ""
    return Request(
        day=stamp.date(),
        method=match["method"],
        path=match["path"],
        status=int(match["status"]),
        agent=agent,
        visitor=visitor_hash(match["ip"], agent, run_salt),
    )


def open_log(path: Path) -> Iterator[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            yield from handle
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        yield from handle


def count(
    lines: Iterable[str], *, since: date | None = None, run_salt: str | None = None
) -> Summary:
    # One salt for every file in a run, or a visit that spans two rotated logs
    # would be counted twice.
    run_salt = run_salt or salt()
    summary = Summary()
    for line in lines:
        if not line.strip():
            continue
        request = parse_line(line, run_salt)
        if request is None:
            continue
        if not request.path.split("?", 1)[0].startswith(DOWNLOAD_PREFIX):
            continue
        if since and request.day < since:
            continue
        if request.method == "HEAD" and request.path.split("?", 1)[0].lower().endswith(
            DOWNLOAD_SUFFIXES
        ):
            summary.heads += 1
            continue
        if is_bot(request.agent):
            summary.bot_requests += 1
            continue
        if not is_download(request.path, request.method, request.status):
            summary.failed += 1
            continue
        summary.add(request)
    return summary


def report(summary: Summary, *, since: date | None = None) -> str:
    lines = []
    window = f" since {since.isoformat()}" if since else ""
    lines.append(f"{summary.downloads} downloads, {summary.visitors} visits{window}")
    for name, quantity in sorted(summary.by_file.items()):
        lines.append(f"  {name:<26} {quantity:>6}")
    for day in sorted(summary.by_day):
        seen = len(summary.visitors_by_day.get(day, set()))
        lines.append(f"  {day.isoformat()}  {summary.by_day[day]:>4} downloads  {seen:>4} visits")
    lines.append(
        f"  skipped: {summary.heads} HEAD, {summary.bot_requests} crawler, "
        f"{summary.failed} failed"
    )
    return "\n".join(lines)


def github_totals(payload: list[dict]) -> dict:
    """Sum release asset downloads, keeping the shape the API returns."""

    releases = []
    for release in payload:
        assets = [
            {"name": asset.get("name", ""), "downloads": int(asset.get("download_count", 0))}
            for asset in release.get("assets", [])
            if str(asset.get("name", "")).lower().endswith(DOWNLOAD_SUFFIXES)
        ]
        releases.append(
            {
                "tag": release.get("tag_name", ""),
                "published_at": release.get("published_at", ""),
                "assets": sorted(assets, key=lambda item: item["name"]),
                "downloads": sum(asset["downloads"] for asset in assets),
            }
        )
    releases.sort(key=lambda item: item["published_at"])
    return {
        "downloads": sum(release["downloads"] for release in releases),
        "releases": releases,
    }


def fetch_releases(repository: str, token: str | None) -> list[dict]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/releases?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "reezyn-download-counts",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count Reezyn downloads from an access log.")
    parser.add_argument("--log", action="append", default=[], help="access log, or .gz")
    parser.add_argument("--since", help="only count days from this date, YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--github", action="store_true", help="sum release asset counts instead")
    parser.add_argument("--repo", default="jarshvader99/reezyn")
    args = parser.parse_args(argv)

    since = date.fromisoformat(args.since) if args.since else None

    if args.github:
        token = os.environ.get("GITHUB_TOKEN")
        try:
            payload = fetch_releases(args.repo, token)
        except (urllib.error.URLError, urllib.error.HTTPError) as error:
            print(f"could not read releases: {error}", file=sys.stderr)
            return 1
        totals = github_totals(payload)
        if args.json:
            print(json.dumps(totals, indent=2))
            return 0
        print(f"{totals['downloads']} release downloads for {args.repo}")
        for release in totals["releases"]:
            print(f"  {release['tag']:<10} {release['downloads']:>6}")
        return 0

    if not args.log:
        parser.error("pass --log PATH (or --github)")

    summary = Summary()
    run_salt = salt()
    for name in args.log:
        path = Path(name).expanduser()
        if not path.is_file():
            print(f"no such log: {path}", file=sys.stderr)
            return 1
        partial = count(open_log(path), since=since, run_salt=run_salt)
        summary.downloads += partial.downloads
        summary.heads += partial.heads
        summary.bot_requests += partial.bot_requests
        summary.failed += partial.failed
        for key, value in partial.by_file.items():
            summary.by_file[key] = summary.by_file.get(key, 0) + value
        for day, value in partial.by_day.items():
            summary.by_day[day] = summary.by_day.get(day, 0) + value
        for day, seen in partial.visitors_by_day.items():
            summary.visitors_by_day.setdefault(day, set()).update(seen)

    if args.json:
        print(json.dumps(summary.as_dict(), indent=2))
    else:
        print(report(summary, since=since))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
