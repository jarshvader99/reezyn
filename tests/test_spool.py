from __future__ import annotations

import json
import time

from reezyn.spool import Spool, SpoolWatcher


def test_spool_round_trip(tmp_path):
    spool = Spool(tmp_path)
    spool.emit("claude", {"hook_event_name": "PreToolUse"})
    path = next((tmp_path / "events").glob("*.json"))
    data = json.loads(path.read_text())
    assert data["source"] == "claude"
    assert data["payload"]["hook_event_name"] == "PreToolUse"


def test_spool_watcher_filters_sources(tmp_path):
    spool = Spool(tmp_path)
    seen: list[tuple[str, dict]] = []
    watcher = SpoolWatcher(tmp_path, lambda source, payload: seen.append((source, payload)), sources={"claude"})
    spool.emit("gemini", {"text": "ignore"})
    spool.emit("claude", {"text": "keep"})
    watcher.poll_once()
    assert seen == [("claude", {"text": "keep"})]
    assert not list((tmp_path / "events").glob("*.json"))


def test_spool_watcher_drops_stale_events(tmp_path):
    spool = Spool(tmp_path)
    path = spool.emit("claude", {"text": "old"})
    data = json.loads(path.read_text())
    data["received_at"] = time.time() - 500
    path.write_text(json.dumps(data))

    seen: list[tuple[str, dict]] = []
    watcher = SpoolWatcher(tmp_path, lambda source, payload: seen.append((source, payload)))
    watcher.poll_once()
    assert seen == []
