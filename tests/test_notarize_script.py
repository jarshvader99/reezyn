from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "notarize.sh"

# A stand-in for xcrun: it records every call, can stall on the first upload,
# and reports a scripted sequence of statuses for `notarytool info`. hdiutil is
# stubbed too, so no disk image is ever touched. Everything else (shasum,
# plutil, mktemp, ps) is the real thing, so the script's parsing is exercised.
STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
case "$1" in
  notarytool)
    case "$2" in
      submit)
        n=$(( $(cat "$STUB_DIR/submits" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$STUB_DIR/submits"
        if (( n <= ${STUB_HANG_SUBMITS:-0} )); then
          sleep 30
          exit 1
        fi
        printf '{"id":"stub-%s","status":"In Progress","message":"Successfully uploaded file"}\\n' "$n"
        ;;
      info)
        id="$3"
        n=$(( $(cat "$STUB_DIR/info-$id" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$STUB_DIR/info-$id"
        total=$(printf '%s' "$STUB_STATUSES" | awk -F, '{print NF}')
        (( n > total )) && n=$total
        status=$(printf '%s' "$STUB_STATUSES" | cut -d, -f"$n")
        printf '{"id":"%s","status":"%s"}\\n' "$id" "$status"
        ;;
      log)
        printf '{"issues":[{"severity":"error","message":"stub rejection"}]}\\n'
        ;;
    esac
    ;;
  stapler)
    case "$2" in
      staple)
        echo "stub stapled"
        touch "$STUB_DIR/stapled"
        ;;
      validate)
        if [[ "${STUB_STAPLED:-0}" == "1" || -f "$STUB_DIR/stapled" ]]; then
          echo "stub ticket present"
        else
          echo "stub: no ticket attached" >&2
          exit 1
        fi
        ;;
    esac
    ;;
esac
exit 0
"""

FAKE_HDIUTIL = """#!/usr/bin/env bash
echo "stub hdiutil $*"
exit 0
"""


def _stub_path(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "xcrun").write_text(STUB, encoding="utf-8")
    (bin_dir / "hdiutil").write_text(FAKE_HDIUTIL, encoding="utf-8")
    for name in ("xcrun", "hdiutil"):
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "stub"
    state.mkdir()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(tmp_path / "calls.log"),
        "STUB_DIR": str(state),
        "NOTARY_STATE_DIR": str(tmp_path / "notary-state"),
        "NOTARY_POLL_INTERVAL": "0",
        "NOTARY_RETRY_DELAY": "0",
        "NOTARY_TIMEOUT": "5",
        "NOTARY_ATTEMPTS": "1",
        "NOTARY_UPLOAD_TIMEOUT": "10",
        "STUB_STATUSES": "In Progress,Accepted",
        # Keep the developer's real notary credentials out of the test.
        "NOTARY_PROFILE": "stub-profile",
    }
    return state, env


def _dmg(tmp_path: Path) -> Path:
    path = tmp_path / "Reezyn-9.9.9.dmg"
    path.write_bytes(b"not a real disk image\n" * 64)
    return path


def _run(tmp_path: Path, env: dict[str, str], dmg: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(dmg)],
        cwd=str(tmp_path),
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
    )


def _calls(tmp_path: Path) -> str:
    log = tmp_path / "calls.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def _state(tmp_path: Path, dmg: Path) -> dict[str, str]:
    path = tmp_path / "notary-state" / f"{dmg.stem}.state"
    if not path.is_file():
        return {}
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )


def test_uploads_polls_and_staples(tmp_path: Path):
    _, env = _stub_path(tmp_path)
    dmg = _dmg(tmp_path)

    result = _run(tmp_path, env, dmg)

    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert calls.count("notarytool submit") == 1
    assert "--no-wait" in calls
    assert "stapler staple" in calls
    assert "stapler validate" in calls
    state = _state(tmp_path, dmg)
    assert state["id"] == "stub-1"
    assert state["status"] == "Accepted"
    assert state["sha256"] == hashlib.sha256(dmg.read_bytes()).hexdigest()


def test_accepted_submission_is_stapled_without_uploading_again(tmp_path: Path):
    _, env = _stub_path(tmp_path)
    dmg = _dmg(tmp_path)
    state_dir = tmp_path / "notary-state"
    state_dir.mkdir()
    (state_dir / f"{dmg.stem}.state").write_text(
        f"sha256={hashlib.sha256(dmg.read_bytes()).hexdigest()}\nid=prior-1\nstatus=In Progress\n",
        encoding="utf-8",
    )
    env["STUB_STATUSES"] = "Accepted"

    result = _run(tmp_path, env, dmg)

    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert "notarytool submit" not in calls
    assert "notarytool info prior-1" in calls
    assert "stapler staple" in calls


def test_an_already_stapled_image_is_not_rewritten(tmp_path: Path):
    _, env = _stub_path(tmp_path)
    dmg = _dmg(tmp_path)
    state_dir = tmp_path / "notary-state"
    state_dir.mkdir()
    (state_dir / f"{dmg.stem}.state").write_text(
        f"sha256={hashlib.sha256(dmg.read_bytes()).hexdigest()}\nid=prior-1\nstatus=Accepted\n",
        encoding="utf-8",
    )
    before = hashlib.sha256(dmg.read_bytes()).hexdigest()
    env["STUB_STATUSES"] = "Accepted"
    env["STUB_STAPLED"] = "1"

    result = _run(tmp_path, env, dmg)

    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert "stapler staple" not in calls
    assert "stapler validate" in calls
    assert "notarytool submit" not in calls
    assert hashlib.sha256(dmg.read_bytes()).hexdigest() == before


def test_a_changed_image_is_uploaded_again(tmp_path: Path):
    _, env = _stub_path(tmp_path)
    dmg = _dmg(tmp_path)
    state_dir = tmp_path / "notary-state"
    state_dir.mkdir()
    (state_dir / f"{dmg.stem}.state").write_text(
        "sha256=" + "0" * 64 + "\nid=stale-1\nstatus=Accepted\n", encoding="utf-8"
    )

    result = _run(tmp_path, env, dmg)

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path).count("notarytool submit") == 1
    assert _state(tmp_path, dmg)["id"] == "stub-1"


def test_a_rejected_image_reports_apple_log_and_staples_nothing(tmp_path: Path):
    _, env = _stub_path(tmp_path)
    dmg = _dmg(tmp_path)
    env["STUB_STATUSES"] = "Invalid"
    env["NOTARY_ATTEMPTS"] = "1"

    result = _run(tmp_path, env, dmg)

    assert result.returncode != 0
    calls = _calls(tmp_path)
    assert "notarytool log" in calls
    assert "stapler staple" not in calls
    assert "stub rejection" in result.stderr


def test_a_stalled_upload_is_retried(tmp_path: Path):
    _, env = _stub_path(tmp_path)
    dmg = _dmg(tmp_path)
    env["NOTARY_ATTEMPTS"] = "2"
    env["NOTARY_UPLOAD_TIMEOUT"] = "1"
    env["STUB_HANG_SUBMITS"] = "1"

    result = _run(tmp_path, env, dmg)

    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert calls.count("notarytool submit") == 2
    assert _state(tmp_path, dmg)["status"] == "Accepted"


def test_a_timeout_keeps_the_submission_and_the_next_run_resumes_it(tmp_path: Path):
    _, env = _stub_path(tmp_path)
    dmg = _dmg(tmp_path)
    env["NOTARY_TIMEOUT"] = "1"
    env["STUB_STATUSES"] = "In Progress"

    first = _run(tmp_path, env, dmg)

    assert first.returncode != 0
    assert "re-run" in first.stderr.lower()
    state = _state(tmp_path, dmg)
    assert state["id"] == "stub-1"
    assert "stapler staple" not in _calls(tmp_path)

    env["STUB_STATUSES"] = "Accepted"
    second = _run(tmp_path, env, dmg)

    assert second.returncode == 0, second.stderr
    assert _calls(tmp_path).count("notarytool submit") == 1
    assert "stapler staple" in _calls(tmp_path)
    assert _state(tmp_path, dmg)["status"] == "Accepted"
