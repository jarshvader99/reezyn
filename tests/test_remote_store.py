from __future__ import annotations

import json
import time

from reezyn.remote.store import RemoteStore


def test_tokens_are_random_hashed_and_expire(tmp_path):
    store = RemoteStore(tmp_path / "remote.json")
    token = store.issue("Joshua's iPhone", days=1, now=1_000.0)

    assert token
    assert store.validate(token, now=1_001.0)
    assert not store.validate("not-the-token", now=1_001.0)

    raw = (tmp_path / "remote.json").read_text()
    assert token not in raw
    data = json.loads(raw)
    assert len(data["sessions"]) == 1
    assert "Joshua" in next(iter(data["sessions"].values()))["name"]

    assert not store.validate(token, now=1_000.0 + 86_401)
    assert store.count(now=90_000.0) == 0


def test_revoke_all_forgets_every_phone(tmp_path):
    store = RemoteStore(tmp_path / "remote.json")
    first = store.issue("Phone", now=1_000.0)
    second = store.issue("Tablet", now=1_000.0)
    assert store.count(now=1_001.0) == 2

    assert store.revoke_all() == 2
    assert not store.validate(first, now=1_001.0)
    assert not store.validate(second, now=1_001.0)
    assert store.count(now=1_001.0) == 0


def test_a_reloaded_store_keeps_live_sessions(tmp_path):
    path = tmp_path / "remote.json"
    now = time.time()
    store = RemoteStore(path)
    token = store.issue("Phone", days=30, now=now)

    reloaded = RemoteStore(path)
    assert reloaded.validate(token, now=now + 10)
    assert reloaded.count(now=now + 10) == 1
