from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "github_release.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_release_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeAPI:
    """Records the requests publish() makes and answers them in order."""

    def __init__(self, release: dict | None, assets: list[dict] | None = None):
        self.release = release
        self.assets = assets or []
        self.calls: list[dict] = []

    def __call__(self, method, url, *, auth, data=None, content_type="application/json", **kwargs):
        self.calls.append(
            {"method": method, "url": url, "data": data, "content_type": content_type}
        )
        if method == "GET" and url.endswith("/releases/tags/v0.2.4"):
            if self.release is None:
                return 404, {"message": "Not Found"}
            return 200, self.release
        if method == "POST" and url.endswith("/releases"):
            body = json.loads(data)
            assert body["tag_name"] == "v0.2.4"
            return 201, {"id": 7, "html_url": "https://github.com/o/r/releases/tag/v0.2.4"}
        if method == "PATCH":
            return 200, {"id": 7, "html_url": "https://github.com/o/r/releases/tag/v0.2.4"}
        if method == "GET" and url.endswith("/assets"):
            return 200, self.assets
        if method == "POST" and "/assets?" in url:
            return 201, {"name": url.split("name=", 1)[1]}
        raise AssertionError(f"unexpected request {method} {url}")

    def body(self) -> dict:
        for entry in self.calls:
            if entry["method"] == "POST" and entry["url"].endswith("/releases"):
                return json.loads(entry["data"])
        for entry in self.calls:
            if entry["method"] == "PATCH":
                return json.loads(entry["data"])
        raise AssertionError("no release body was sent")


def _dmg(tmp_path: Path) -> Path:
    path = tmp_path / "Reezyn-0.2.4.dmg"
    path.write_bytes(b"disk image bytes")
    return path


def test_a_new_release_is_created_as_the_latest_with_the_build_attached(tmp_path):
    module = _load()
    dmg = _dmg(tmp_path)
    zip_path = tmp_path / "Reezyn-0.2.4.zip"
    zip_path.write_bytes(b"zip bytes")
    api = FakeAPI(release=None)

    result = module.publish(
        "0.2.4",
        dmg=dmg,
        zip_path=zip_path,
        notes=["fix: reopen the app after a CLI-initiated update"],
        slug="o/r",
        auth="t",
        call=api,
    )

    body = api.body()
    assert result["action"] == "created"
    assert body["name"] == "Reezyn 0.2.4"
    assert body["draft"] is False and body["prerelease"] is False
    assert body["make_latest"] == "true"
    assert "• fix: reopen the app after a CLI-initiated update" in body["body"]
    assert "https://github.com/o/r/releases/download/v0.2.4/Reezyn-0.2.4.dmg" in body["body"]
    assert module.sha256_of(dmg) in body["body"]
    uploads = [c for c in api.calls if "/assets?" in c["url"]]
    assert len(uploads) == 2
    assert result["url"].endswith("/v0.2.4")


def test_a_rerun_updates_the_release_and_skips_an_attached_asset(tmp_path):
    module = _load()
    dmg = _dmg(tmp_path)
    api = FakeAPI(
        release={"id": 7, "html_url": "https://github.com/o/r/releases/tag/v0.2.4"},
        assets=[{"name": "Reezyn-0.2.4.dmg"}],
    )

    result = module.publish(
        "0.2.4", dmg=dmg, notes=["note"], slug="o/r", auth="t", call=api
    )

    assert result["action"] == "updated"
    assert not [c for c in api.calls if "/assets?" in c["url"]]
    assert "already attached" in result["steps"][2]


def test_an_older_release_is_not_marked_latest(tmp_path):
    module = _load()
    api = FakeAPI(release=None)

    module.publish("0.2.4", notes=["n"], slug="o/r", auth="t", latest=False, call=api)

    assert "make_latest" not in api.body()


def test_the_origin_remote_is_read_in_every_form_git_allows():
    module = _load()

    assert module.parse_slug("https://github.com/jarshvader99/reezyn.git") == (
        "jarshvader99/reezyn"
    )
    assert module.parse_slug("git@github.com:jarshvader99/reezyn.git") == (
        "jarshvader99/reezyn"
    )
    assert module.parse_slug("https://github.com/jarshvader99/reezyn") == (
        "jarshvader99/reezyn"
    )


def test_the_release_body_carries_the_published_notes():
    module = _load()

    body = module.release_body("o/r", "0.2.4", ["one", "two"], None)

    assert body.startswith("• one\n• two\n")
    assert "https://reezyn.elastikmind.com/updates/latest.json" in body
