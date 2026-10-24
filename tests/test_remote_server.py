from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar

from reezyn.config import Config
from reezyn.remote.server import RemoteServer


@dataclass
class FakeActions:
    sent: list[tuple[str, bool]] = field(default_factory=list)
    answers: list[bool] = field(default_factory=list)
    stopped: bool = False

    def start(self) -> None:
        return

    def stop(self) -> None:
        self.stopped = True

    def snapshot(self) -> dict:
        return {
            "locked": False,
            "queued": 0,
            "terminal": "Terminal",
            "frontmost": "Terminal",
            "can_type": True,
            "keep_awake": True,
            "error": "",
        }

    def say(self, text: str, submit: bool) -> dict:
        self.sent.append((text, submit))
        return {"ok": True, "queued": False, "message": "Sent."}

    def answer_permission(self, allow: bool) -> dict:
        self.answers.append(allow)
        return {"ok": True, "queued": False, "message": "Allowed." if allow else "Denied."}


class FakeService:
    def __init__(self) -> None:
        self.config = Config()
        self.activities: list[tuple[str, str]] = []
        self.permissions: list[bool] = []
        self.listening: list[bool] = []

    def remote_snapshot(self) -> dict:
        return {
            "state": "listening",
            "paused": False,
            "speaking": False,
            "current": "",
            "recent": ["I fixed the parser."],
            "active_agent": "claude",
            "project": "reezyn",
            "last_error": "",
            "model_ready": True,
            "simulating": False,
            "voice": None,
            "permission": {"text": "Run the tests?", "ts": 1.0, "agent": "claude"},
        }

    def remote_context(self) -> dict:
        return {"project": "reezyn", "agent": "claude"}

    def remote_activity(self, kind: str, text: str = "") -> None:
        self.activities.append((kind, text))

    def remote_permission_answered(self, allow: bool) -> None:
        self.permissions.append(allow)

    def recap(self) -> str:
        return "You fixed the parser and left the tests green."

    def set_remote_listening(self, active: bool) -> None:
        self.listening.append(active)


def _client() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def _request(
    opener,
    base: str,
    method: str,
    path: str,
    payload=None,
    headers=None,
    raw: bytes | None = None,
):
    data = raw
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(base + path, data=data, method=method, headers=request_headers)
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _start(tmp_path, monkeypatch):
    service = FakeService()
    config = Config()
    config.remote.enabled = True
    config.remote.port = 0
    server = RemoteServer(
        service,
        tmp_path,
        config.remote,
        publish=lambda port: {"ok": True, "url": "https://reezyn-test.ts.net", "reason": ""},
    )
    actions = FakeActions()
    server._actions = actions
    monkeypatch.setattr(
        "reezyn.remote.server.asr.transcribe_samples",
        lambda samples, rate, locale, context: "hello from the phone",
    )
    assert server.start()
    server.wait_publish(5.0)
    base = f"http://127.0.0.1:{server.port}"
    return server, service, actions, base


def test_the_page_is_public_but_state_needs_pairing(tmp_path, monkeypatch):
    server, _service, _actions, base = _start(tmp_path, monkeypatch)
    try:
        status, headers, body = _request(urllib.request.build_opener(), base, "GET", "/")
        assert status == 200
        assert b"Reezyn" in body
        assert "default-src 'self'" in headers.get("Content-Security-Policy", "")

        status, _headers, _body = _request(urllib.request.build_opener(), base, "GET", "/api/state")
        assert status == 401
    finally:
        server.stop()


def test_pairing_issues_a_cookie_and_unlocks_the_api(tmp_path, monkeypatch):
    server, _service, actions, base = _start(tmp_path, monkeypatch)
    client = _client()
    try:
        status, _headers, _body = _request(
            client, base, "POST", "/api/pair", {"code": "not-a-code"}
        )
        assert status == 403

        status, headers, body = _request(
            client, base, "POST", "/api/pair", {"code": server.code(), "name": "iPhone"}
        )
        assert status == 200
        assert json.loads(body)["ok"] is True
        assert "reezyn_session=" in headers.get("Set-Cookie", "")
        assert "HttpOnly" in headers.get("Set-Cookie", "")

        status, _headers, body = _request(client, base, "GET", "/api/state")
        assert status == 200
        state = json.loads(body)
        assert state["recent"] == ["I fixed the parser."]
        assert state["remote"]["paired"] == 1
        assert state["remote"]["url"] == "https://reezyn-test.ts.net"
        assert state["permission"]["text"] == "Run the tests?"
        assert state["read_aloud_default"] is False

        status, _headers, body = _request(
            client, base, "POST", "/api/say", {"text": "looks good", "submit": True}
        )
        assert status == 200
        assert actions.sent == [("looks good", True)]

        status, _headers, body = _request(
            client, base, "POST", "/api/permission", {"allow": True}
        )
        assert status == 200
        assert actions.answers == [True]

        status, _headers, body = _request(client, base, "POST", "/api/recap", {})
        assert status == 200
        assert "parser" in json.loads(body)["text"]

        audio = b"\x00\x00" * 16000
        status, _headers, body = _request(
            client,
            base,
            "POST",
            "/api/transcribe",
            raw=audio,
            headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": "16000"},
        )
        assert status == 200
        assert json.loads(body)["text"] == "hello from the phone"
    finally:
        server.stop()


def test_cross_origin_posts_are_refused(tmp_path, monkeypatch):
    server, _service, _actions, base = _start(tmp_path, monkeypatch)
    client = _client()
    try:
        _request(client, base, "POST", "/api/pair", {"code": server.code()})
        status, _headers, _body = _request(
            client,
            base,
            "POST",
            "/api/say",
            {"text": "hello"},
            headers={"Origin": "https://evil.example"},
        )
        assert status == 403

        # Serve can preserve the tailnet name as the browser's Origin while
        # connecting over loopback; that is the same page, not a CSRF.
        status, _headers, _body = _request(
            client,
            base,
            "POST",
            "/api/say",
            {"text": "hello"},
            headers={"Origin": "https://reezyn-test.ts.net"},
        )
        assert status == 200
    finally:
        server.stop()


def test_read_aloud_mutes_the_mac_and_expires(tmp_path, monkeypatch):
    server, service, _actions, base = _start(tmp_path, monkeypatch)
    client = _client()
    try:
        status, _headers, _body = _request(
            urllib.request.build_opener(),
            base,
            "POST",
            "/api/read-aloud",
            {"enabled": True},
        )
        assert status == 401

        _request(client, base, "POST", "/api/pair", {"code": server.code()})
        now = [1000.0]
        server._clock = lambda: now[0]

        status, _headers, body = _request(
            client, base, "POST", "/api/read-aloud", {"enabled": True}
        )
        assert status == 200
        assert json.loads(body)["mac_audio"] is False
        assert service.listening == [True]
        assert server.info()["phone_audio"] is True

        # A heartbeat inside the window extends the claim without toggling it.
        now[0] += 40
        status, _headers, _body = _request(
            client, base, "POST", "/api/read-aloud", {"enabled": True}
        )
        assert status == 200
        assert service.listening == [True]

        # A phone that sleeps or loses the tailnet must not mute the Mac forever.
        now[0] += 46
        server.snapshot()
        assert service.listening[-1] is False
        assert server.info()["phone_audio"] is False

        status, _headers, _body = _request(
            client, base, "POST", "/api/read-aloud", {"enabled": "yes"}
        )
        assert status == 400

        _request(client, base, "POST", "/api/read-aloud", {"enabled": True})
        assert service.listening[-1] is True
        _request(client, base, "POST", "/api/read-aloud", {"enabled": False})
        assert service.listening[-1] is False
    finally:
        server.stop()


def test_pairing_is_rate_limited(tmp_path, monkeypatch):
    server, _service, _actions, base = _start(tmp_path, monkeypatch)
    client = _client()
    try:
        for _ in range(5):
            status, _headers, _body = _request(
                client, base, "POST", "/api/pair", {"code": "wrong"}
            )
            assert status == 403
        status, _headers, _body = _request(
            client, base, "POST", "/api/pair", {"code": server.code()}
        )
        assert status == 403
    finally:
        server.stop()
