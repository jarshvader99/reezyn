from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "download_counts.py"


def _load():
    spec = importlib.util.spec_from_file_location("download_counts_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def line(
    path: str = "/downloads/Reezyn-0.2.4.dmg",
    *,
    ip: str = "203.0.113.7",
    day: str = "25/Oct/2026:09:31:02 +0000",
    method: str = "GET",
    status: int = 200,
    agent: str = "curl/8.7.1",
    vhost: str = "",
) -> str:
    prefix = f"{vhost} " if vhost else ""
    return (
        f'{prefix}{ip} - - [{day}] "{method} {path} HTTP/1.1" {status} 60754867 '
        f'"-" "{agent}"'
    )


def test_a_get_for_the_disk_image_is_a_download():
    module = _load()

    summary = module.count([line()])

    assert summary.downloads == 1
    assert summary.by_file == {"Reezyn-0.2.4.dmg": 1}
    assert summary.visitors == 1


def test_the_vhost_prefix_some_hosts_write_is_tolerated():
    module = _load()

    summary = module.count([line(vhost="reezyn.elastikmind.com:443")])

    assert summary.downloads == 1


def test_head_is_not_a_download():
    module = _load()

    summary = module.count([line(method="HEAD"), line(method="HEAD")])

    assert summary.downloads == 0
    assert summary.heads == 2


def test_a_failed_request_is_not_a_download():
    module = _load()

    summary = module.count([line(status=404), line(status=500)])

    assert summary.downloads == 0
    assert summary.failed == 2


def test_a_crawler_is_skipped():
    module = _load()

    summary = module.count([line(agent="Mozilla/5.0 (compatible; AhrefsBot/7.0)")])

    assert summary.downloads == 0
    assert summary.bot_requests == 1


def test_a_page_view_is_not_a_download():
    module = _load()

    summary = module.count([line(path="/"), line(path="/index.html")])

    assert summary.downloads == 0


def test_the_same_visitor_twice_is_two_downloads_and_one_visit():
    module = _load()

    summary = module.count([line(), line(day="25/Oct/2026:11:02:41 +0000")])

    assert summary.downloads == 2
    assert summary.visitors == 1


def test_two_addresses_are_two_visits():
    module = _load()

    summary = module.count([line(), line(ip="198.51.100.4")])

    assert summary.visitors == 2


def test_visits_do_not_carry_across_days():
    module = _load()

    summary = module.count([line(), line(day="26/Oct/2026:08:00:00 +0000")])

    assert summary.downloads == 2
    assert summary.visitors == 2
    assert [day.isoformat() for day in sorted(summary.by_day)] == ["2026-10-25", "2026-10-26"]


def test_since_drops_earlier_days():
    module = _load()

    summary = module.count(
        [line(day="20/Oct/2026:08:00:00 +0000"), line(day="25/Oct/2026:08:00:00 +0000")],
        since=date(2026, 10, 24),
    )

    assert summary.downloads == 1
    assert list(summary.by_day) == [date(2026, 10, 25)]


def test_a_malformed_line_is_ignored():
    module = _load()

    summary = module.count(["this is not an access log", ""])

    assert summary.downloads == 0


def test_the_json_report_is_stable():
    module = _load()

    summary = module.count([line(), line(ip="198.51.100.4", agent="Safari/17")])
    payload = json.loads(json.dumps(summary.as_dict()))

    assert payload["downloads"] == 2
    assert payload["visitors"] == 2
    assert payload["by_file"] == {"Reezyn-0.2.4.dmg": 2}
    assert payload["by_day"] == [{"day": "2026-10-25", "downloads": 2, "visitors": 2}]
    assert set(payload["skipped"]) == {"head", "bots", "failed"}


def test_release_totals_add_the_assets_that_are_downloads():
    module = _load()

    payload = [
        {
            "tag_name": "v0.2.4",
            "published_at": "2026-10-25T08:00:00Z",
            "assets": [
                {"name": "Reezyn-0.2.4.dmg", "download_count": 41},
                {"name": "Reezyn-0.2.4.zip", "download_count": 4},
                {"name": "checksums.txt", "download_count": 90},
            ],
        },
        {
            "tag_name": "v0.2.0",
            "published_at": "2026-10-24T08:00:00Z",
            "assets": [{"name": "Reezyn-0.2.0.dmg", "download_count": 3}],
        },
    ]

    totals = module.github_totals(payload)

    assert totals["downloads"] == 48
    assert [release["tag"] for release in totals["releases"]] == ["v0.2.0", "v0.2.4"]
