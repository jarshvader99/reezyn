from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path

import pytest

from reezyn import updates
from reezyn.updates import Update, UpdateError

SHA = "a" * 64


def _manifest(**overrides):
    payload = {
        "version": "0.3.0",
        "url": "https://reezyn.elastikmind.com/updates/Reezyn-0.3.0.zip",
        "sha256": SHA,
        "size": 12,
        "channel": "stable",
        "min_macos": "13.0",
        "notes": ["Faster narration", "  ", "Update checks"],
    }
    payload.update(overrides)
    return payload


def _opener(payload: bytes, seen: list | None = None):
    def open_(_request, timeout=None):
        if seen is not None:
            seen.append(_request.full_url)
        return io.BytesIO(payload)

    return open_


def test_manifest_is_validated_before_download():
    update = updates.parse_manifest(_manifest())
    assert update.version == "0.3.0"
    assert update.notes == ("Faster narration", "Update checks")
    assert update.size == 12


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": "0.3"},
        {"url": "http://insecure.example/Reezyn.zip"},
        {"sha256": "not-a-checksum"},
        {"sha256": ""},
        {"size": 10 * 1024 * 1024 * 1024},
    ],
)
def test_bad_manifests_are_rejected(overrides):
    with pytest.raises(UpdateError):
        updates.parse_manifest(_manifest(**overrides))


def test_version_comparison():
    assert updates.is_newer("0.3.0", "0.2.9")
    assert updates.is_newer("1.0.0", "0.99.99")
    assert not updates.is_newer("0.2.0", "0.2.0")
    assert not updates.is_newer("0.1.9", "0.2.0")
    assert updates.version_tuple("2.10.1") == (2, 10, 1)


def test_min_macos_allows_what_this_mac_can_run():
    assert updates._at_least((26, 1), (13, 0))
    assert updates._at_least((13, 0), (13, 0))
    assert not updates._at_least((12, 7, 1), (13, 0))


def test_a_newer_stable_build_is_offered(tmp_path):
    now = time.time()
    found = updates.check_for_update(
        home=tmp_path,
        current="0.2.0",
        fetch=lambda _url: updates.parse_manifest(_manifest()),
        now=now,
    )
    assert found is not None and found.version == "0.3.0"
    assert updates.load_last_check(tmp_path) == now


def test_the_same_or_older_build_is_silent(tmp_path):
    for version in ("0.2.0", "0.1.9"):
        found = updates.check_for_update(
            home=tmp_path,
            current="0.2.0",
            force=True,
            fetch=lambda _url, v=version: updates.parse_manifest(_manifest(version=v)),
        )
        assert found is None


def test_beta_users_accept_stable_builds_but_not_the_reverse(tmp_path):
    stable = updates.parse_manifest(_manifest(channel="stable"))
    beta = updates.parse_manifest(_manifest(channel="beta"))
    assert updates._channel_ok(stable.channel, "beta")
    assert updates._channel_ok(stable.channel, "stable")
    assert not updates._channel_ok(beta.channel, "stable")


def test_a_build_for_a_newer_macos_is_skipped(tmp_path):
    found = updates.check_for_update(
        home=tmp_path,
        current="0.2.0",
        force=True,
        fetch=lambda _url: updates.parse_manifest(_manifest(min_macos="99.0")),
    )
    assert found is None


def test_checks_are_throttled_until_due(tmp_path):
    calls: list[str] = []

    def fetch(url: str) -> Update:
        calls.append(url)
        return updates.parse_manifest(_manifest())

    start = time.time()
    first = updates.check_for_update(home=tmp_path, current="0.2.0", fetch=fetch, now=start)
    assert first is not None
    soon = updates.check_for_update(home=tmp_path, current="0.2.0", fetch=fetch, now=start + 60)
    assert soon is None
    assert len(calls) == 1
    assert not updates.due(tmp_path, now=start + 60)
    assert updates.due(tmp_path, now=start + updates.CHECK_INTERVAL_S)
    forced = updates.check_for_update(
        home=tmp_path, current="0.2.0", force=True, fetch=fetch, now=start + 120
    )
    assert forced is not None and len(calls) == 2


def test_a_failed_check_is_recorded_and_stays_quiet(tmp_path):
    def fetch(_url: str) -> Update:
        raise OSError("offline")

    now = time.time()
    assert updates.check_for_update(home=tmp_path, current="0.2.0", fetch=fetch, now=now) is None
    assert updates.load_last_check(tmp_path) == now


def test_download_matches_the_manifest_checksum(tmp_path):
    body = b"x" * 32
    update = Update(
        version="0.3.0",
        url="https://example.com/Reezyn.zip",
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body),
    )
    seen: list[str] = []
    path = updates.download(update, tmp_path / "a.zip", opener=_opener(body, seen))
    assert path.read_bytes() == body
    assert seen == ["https://example.com/Reezyn.zip"]


def test_a_tampered_download_is_deleted(tmp_path):
    body = b"x" * 32
    update = Update(
        version="0.3.0",
        url="https://example.com/Reezyn.zip",
        sha256=hashlib.sha256(b"other").hexdigest(),
        size=len(body),
    )
    with pytest.raises(UpdateError, match="checksum"):
        updates.download(update, tmp_path / "a.zip", opener=_opener(body))
    assert not (tmp_path / "a.zip").exists()


def test_a_short_download_is_rejected(tmp_path):
    body = b"x" * 4
    update = Update(
        version="0.3.0",
        url="https://example.com/Reezyn.zip",
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body) + 10,
    )
    with pytest.raises(UpdateError, match="expected"):
        updates.download(update, tmp_path / "a.zip", opener=_opener(body))


def _bundle(path: Path) -> Path:
    (path / "Contents" / "MacOS").mkdir(parents=True)
    return path


def test_verification_insists_on_the_developer_id(tmp_path):
    app = _bundle(tmp_path / "Reezyn.app")

    def wrong_team(args, **_kwargs):
        if args[0] == "codesign" and args[1] == "-dvvv":
            return _result(stdout="", stderr=f"Identifier={updates.BUNDLE_ID}\nTeamIdentifier=OTHER")
        return _result()

    with pytest.raises(UpdateError, match="team"):
        updates.verify_app(app, run=wrong_team)


def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    import subprocess

    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_verification_accepts_a_correctly_signed_bundle(tmp_path):
    app = _bundle(tmp_path / "Reezyn.app")

    def signed(args, **_kwargs):
        if args[1] == "-dvvv":
            return _result(
                stderr=f"Identifier={updates.BUNDLE_ID}\nTeamIdentifier={updates.TEAM_ID}"
            )
        return _result()

    updates.verify_app(app, run=signed)


def test_verification_rejects_a_bundle_codesign_flags(tmp_path):
    app = _bundle(tmp_path / "Reezyn.app")
    with pytest.raises(UpdateError, match="signature"):
        updates.verify_app(app, run=lambda *_a, **_k: _result(1, stderr="invalid signature"))


def test_verification_rejects_something_that_is_not_an_app(tmp_path):
    with pytest.raises(UpdateError, match="not an app"):
        updates.verify_app(tmp_path / "nothing.app")


def test_swap_script_waits_rolls_back_and_reopens(tmp_path):
    script = updates.swap_script(
        Path("/Applications/Reezyn.app"),
        tmp_path / "Reezyn.app",
        4321,
        tmp_path,
    )
    assert "while kill -0 \"$pid\"" in script
    assert "pid=4321" in script
    assert "/Applications/Reezyn.app.previous" in script
    # the rollback path puts the old bundle back before the final reopen
    assert script.index('mv "$backup" "$app"') < script.rindex('open "$app"')
    assert "xattr -dr com.apple.quarantine" in script
    assert f"rm -rf {tmp_path}" in script


def test_install_refuses_when_there_is_no_app_to_replace(tmp_path):
    update = Update(
        version="0.3.0", url="https://example.com/Reezyn.zip", sha256=SHA, size=1
    )
    with pytest.raises(UpdateError, match="not installed"):
        updates.install(update, app_path=tmp_path / "missing.app")


def test_install_verifies_then_schedules_the_swap(tmp_path, monkeypatch):
    target = _bundle(tmp_path / "Applications" / "Reezyn.app")
    calls: list[str] = []

    def fake_download(_update, dest, **_kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"zip")
        return dest

    def fake_unpack(_archive, into, **_kwargs):
        return _bundle(into / "Reezyn.app")

    monkeypatch.setattr(updates, "download", fake_download)
    monkeypatch.setattr(updates, "unpack", fake_unpack)
    monkeypatch.setattr(updates, "verify_app", lambda app, **_k: calls.append(str(app)))
    monkeypatch.setattr(
        updates.subprocess,
        "Popen",
        lambda argv, **kwargs: calls.append(" ".join(argv)),
    )

    update = Update(
        version="0.3.0", url="https://example.com/Reezyn.zip", sha256=SHA, size=1
    )
    work = tmp_path / "tmp"
    work.mkdir()
    helper = updates.install(update, app_path=target, temp_root=work)

    assert calls and calls[0].endswith("unpacked/Reezyn.app")
    assert helper.exists() and helper.name == "swap.sh"
    assert "Reezyn.app" in helper.read_text()
    assert calls[-1].startswith("/bin/sh ")


def test_the_feed_itself_must_be_https():
    """The manifest names the archive; it has to arrive over TLS too."""

    with pytest.raises(UpdateError):
        updates.fetch_manifest("http://reezyn.elastikmind.com/updates/latest.json")
