"""The small HTTP server behind the phone page.

It listens on loopback only. Tailscale Serve terminates HTTPS and forwards to
this port, so the page is reachable from the tailnet and nowhere else. Pairing
issues an HttpOnly cookie; every API call after that is a bearer of it.
"""

from __future__ import annotations

import json
import logging
import secrets
import socketserver
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .. import __version__
from ..voice import asr
from ..voice.asr import locale_for_language
from ..config import Config
from . import tailscale
from .actions import RemoteActions
from .store import COOKIE_NAME, RemoteStore

log = logging.getLogger("reezyn")

WEB = Path(__file__).parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
MAX_BODY = 6_000_000
CODE_TTL_S = 900.0
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_S = 60.0
MAX_AUDIO_S = 60.0
# The phone refreshes this while its read-aloud is on. If it sleeps, loses the
# network, or closes the page without saying goodbye, the Mac speaks again.
READ_ALOUD_TTL_S = 45.0


class _RemoteHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        # HTTPServer.server_bind calls socket.getfqdn(host). On a Mac that can
        # block for a long time in mDNS when reverse lookup is slow, which
        # would leave the page bound but never serving. The name is unused.
        socketserver.TCPServer.server_bind(self)
        _host, port = self.server_address[:2]
        self.server_name = "127.0.0.1"
        self.server_port = int(port)


class RemoteServer:
    def __init__(
        self,
        service: Any,
        home: Path,
        config: Any,
        *,
        clock: Any = time.monotonic,
        publish: Any = tailscale.publish,
    ) -> None:
        self.service = service
        self.home = home
        self.config = config
        self.port = int(getattr(config, "port", 8787))
        self._clock = clock
        self._publish = publish
        self._store = RemoteStore(home / "remote.json")
        self._actions = RemoteActions(
            config,
            service.config.voice.allow_apps,
            service.config.voice.require_text_field,
            start_caffeinate=True,
        )
        self._lock = threading.RLock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._code = ""
        self._code_expires = 0.0
        self._failures: deque[float] = deque(maxlen=32)
        self._transcribes: deque[float] = deque(maxlen=32)
        # One entry per paired phone that is currently reading aloud, with the
        # moment its claim expires unless it checks in again.
        self._read_aloud: dict[str, float] = {}
        self._phone_audio = False
        self._error = ""
        self._publish_result: dict[str, Any] = {"ok": False, "reason": "", "url": ""}
        self._publishing = False
        self._publish_thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return True
        try:
            httpd = _RemoteHTTPServer(("127.0.0.1", self.port), _handler_for(self))
        except OSError as exc:
            self._error = f"Could not listen on 127.0.0.1:{self.port}: {exc}"
            log.warning(self._error)
            return False
        self._httpd = httpd
        if self.port == 0:
            self.port = int(httpd.server_address[1])
        self._running = True
        self._new_code_locked()
        threading.Thread(
            target=self._expire_audio_loop,
            name="reezyn-remote-audio",
            daemon=True,
        ).start()
        self._actions.start()
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.3},
            name="reezyn-remote",
            daemon=True,
        )
        self._thread.start()
        if getattr(self.config, "enabled", True):
            self._publishing = True
            self._publish_thread = threading.Thread(
                target=self._publish_now, name="reezyn-remote-publish", daemon=True
            )
            self._publish_thread.start()
        log.info("remote page listening on http://127.0.0.1:%d", self.port)
        return True

    def _publish_now(self) -> None:
        try:
            result = self._publish(self.port)
        except Exception as exc:
            result = {"ok": False, "reason": str(exc), "url": ""}
        with self._lock:
            self._publish_result = result
            self._publishing = False
        if result.get("ok"):
            log.info("remote published at %s", result.get("url"))
        else:
            log.info("remote is local only: %s", result.get("reason"))

    def wait_publish(self, timeout: float = 10.0) -> dict[str, Any]:
        thread = self._publish_thread
        if thread is not None:
            thread.join(timeout)
        return self.info()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            httpd = self._httpd
            self._httpd = None
        self._actions.stop()
        self._set_read_aloud_all_off()
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                log.debug("remote shutdown failed", exc_info=True)

    @property
    def running(self) -> bool:
        return self._running

    # -- pairing -----------------------------------------------------------

    def _new_code_locked(self) -> str:
        with self._lock:
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._code_expires = self._clock() + CODE_TTL_S
            self._failures.clear()
            return self._code

    def new_code(self) -> str:
        return self._new_code_locked()

    def code(self) -> str:
        with self._lock:
            if self._code and self._clock() <= self._code_expires:
                return self._code
            return self._new_code_locked()

    def _pair(self, code: str, name: str) -> str | None:
        now = self._clock()
        with self._lock:
            while self._failures and now - self._failures[0] > ATTEMPT_WINDOW_S:
                self._failures.popleft()
            if len(self._failures) >= MAX_ATTEMPTS:
                return None
            if not self._code or now > self._code_expires:
                return None
            if not code.isdigit() or not secrets.compare_digest(code, self._code):
                self._failures.append(now)
                return None
            self._failures.clear()
            token = self._store.issue(name or "Phone", getattr(self.config, "session_days", 30))
            self._code = ""
            self._code_expires = 0.0
        return token

    def forget_phones(self) -> int:
        self._set_read_aloud_all_off()
        return self._store.revoke_all()

    def _read_aloud_active_locked(self) -> bool:
        now = self._clock()
        for token in [key for key, until in self._read_aloud.items() if until <= now]:
            self._read_aloud.pop(token, None)
        return bool(self._read_aloud)

    def _apply_phone_audio(self, active: bool) -> None:
        setter = getattr(self.service, "set_remote_listening", None)
        if not callable(setter):
            return
        try:
            setter(active)
        except Exception:
            log.debug("could not update phone read-aloud state", exc_info=True)

    def _set_read_aloud(self, token: str, enabled: bool) -> bool:
        with self._lock:
            if enabled:
                self._read_aloud[token] = self._clock() + READ_ALOUD_TTL_S
            else:
                self._read_aloud.pop(token, None)
            active = self._read_aloud_active_locked()
            changed = active != self._phone_audio
            self._phone_audio = active
        if changed:
            self._apply_phone_audio(active)
        return active

    def _expire_read_aloud(self) -> None:
        with self._lock:
            active = self._read_aloud_active_locked()
            changed = active != self._phone_audio
            self._phone_audio = active
        if changed:
            self._apply_phone_audio(active)

    def _set_read_aloud_all_off(self) -> None:
        with self._lock:
            self._read_aloud.clear()
            changed = self._phone_audio
            self._phone_audio = False
        if changed:
            self._apply_phone_audio(False)

    def _expire_audio_loop(self) -> None:
        # A page can vanish without a clean unload. Without this, its claim
        # would only be noticed on the next request and the Mac could stay
        # muted after the phone is gone.
        while True:
            with self._lock:
                if not self._running:
                    return
            self._expire_read_aloud()
            time.sleep(5.0)


    def info(self) -> dict[str, Any]:
        with self._lock:
            code = self._code if self._code and self._clock() <= self._code_expires else ""
            expires = max(0, int(self._code_expires - self._clock()))
            publishing = self._publishing
            phone_audio = self._phone_audio
        local = f"http://127.0.0.1:{self.port}/"
        url = self._publish_result.get("url") or local
        snapshot = self._actions.snapshot()
        return {
            "running": self._running,
            "url": url,
            "local_url": local,
            "https": url.startswith("https://"),
            "paired": self._store.count(),
            "code": code,
            "code_expires_s": expires,
            "error": self._error or str(self._publish_result.get("reason") or ""),
            "tailscale": bool(self._publish_result.get("ok")),
            "publishing": publishing,
            "phone_audio": phone_audio,
            "setup_url": str(self._publish_result.get("setup_url") or ""),
            "locked": snapshot["locked"],
            "queued": snapshot["queued"],
            "terminal": snapshot["terminal"],
            "can_type": snapshot["can_type"],
        }

    # -- state -------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        self._expire_read_aloud()
        base = self.service.remote_snapshot()
        base["remote"] = {
            "paired": self._store.count(),
            "keep_awake": bool(getattr(self.config, "keep_awake", True)),
            "url": self._publish_result.get("url") or "",
        }
        base["read_aloud_default"] = bool(getattr(self.config, "read_aloud", False))
        base.update(self._actions.snapshot())
        base["version"] = __version__
        return base

    # -- requests ----------------------------------------------------------

    def _pair_request(self, code: str, name: str) -> str | None:
        return self._pair(code, name)

    def _transcribe_ok(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._transcribes and now - self._transcribes[0] > 60.0:
                self._transcribes.popleft()
            if len(self._transcribes) >= 10:
                return False
            self._transcribes.append(now)
            return True

    def _transcribe(self, body: bytes, rate: int) -> str:
        import numpy as np

        limit = int(rate * MAX_AUDIO_S) * 2
        if len(body) > limit:
            body = body[:limit]
        samples = np.frombuffer(body, dtype="<i2").astype(np.float32) / 32768.0
        if samples.size < int(rate * 0.2):
            raise ValueError("That was too short to transcribe.")
        config: Config = self.service.config
        from ..languages import pack_for_voice

        pack = pack_for_voice(config.speech.voice)
        locale = locale_for_language(pack.kokoro_lang, config.voice.locale)
        context = list(dict.fromkeys([
            *config.voice.wake_word.split(),
            "Reezyn",
            "Claude Code",
            "Codex",
            "Gemini",
            "Cursor",
            "OpenCode",
            "Freebuff",
            str(self.service.remote_context().get("project") or ""),
        ]))
        return asr.transcribe_samples(samples, rate, locale, context)

    def say(self, text: str, submit: bool) -> dict[str, Any]:
        result = self._actions.say(text, submit)
        self.service.remote_activity("send", text if result.get("ok") or result.get("queued") else "")
        return result

    def permission(self, allow: bool) -> dict[str, Any]:
        result = self._actions.answer_permission(allow)
        if result.get("ok"):
            self.service.remote_permission_answered(allow)
        return result


def _handler_for(remote: RemoteServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Reezyn"
        sys_version = ""

        # -- plumbing ------------------------------------------------------

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("remote: " + fmt, *args)

        def _host_only(self) -> str:
            host = self.headers.get("Host", "")
            parsed = urlparse(f"http://{host}")
            return (parsed.hostname or "").lower()

        def _secure(self) -> bool:
            proto = (self.headers.get("X-Forwarded-Proto") or "").lower()
            if proto:
                return proto == "https"
            host = self._host_only()
            return host not in ("127.0.0.1", "localhost", "::1", "")

        def _cookie_token(self) -> str:
            header = self.headers.get("Cookie", "")
            for part in header.split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE_NAME:
                    return value
            return ""

        def _token(self) -> str:
            token = self._cookie_token()
            if token:
                return token
            header = self.headers.get("Authorization", "")
            if header.lower().startswith("bearer "):
                return header[7:].strip()
            return ""

        def _authorized(self) -> bool:
            return remote._store.validate(self._token())

        def _headers(
            self,
            status: int,
            content_type: str,
            length: int,
            extra: list[tuple[str, str]] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "media-src 'self' blob:; style-src 'self'; script-src 'self'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            self.send_header("Permissions-Policy", "microphone=(self)")
            for name, value in extra or []:
                self.send_header(name, value)
            self.end_headers()

        def _json(self, status: int, payload: Any, extra: list[tuple[str, str]] | None = None) -> None:
            body = json.dumps(payload).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body), extra)
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"ok": False, "error": message})

        def _content_length(self) -> int:
            try:
                return int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                return -1

        def _read_json(self) -> dict[str, Any] | None:
            length = self._content_length()
            if length <= 0 or length > MAX_BODY:
                # Nothing was consumed; do not let it be parsed as the next
                # request on a keep-alive connection.
                self.close_connection = True
                return None
            try:
                value = json.loads(self.rfile.read(length) or b"{}")
            except (json.JSONDecodeError, OSError):
                return None
            return value if isinstance(value, dict) else None

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            host = (urlparse(origin).hostname or "").lower()
            if not host:
                return False
            forwarded = self.headers.get("X-Forwarded-Host") or ""
            expected = (urlparse(f"http://{forwarded}").hostname or "").lower()
            if not expected:
                expected = self._host_only()
            # Tailscale Serve may preserve either the tailnet name or the
            # loopback target as Host. Accept both, plus the local test name;
            # the session cookie is SameSite=Strict and still required.
            return (
                host == expected
                or host in ("127.0.0.1", "localhost", "::1")
                or host.endswith(".ts.net")
            )

        # -- routes --------------------------------------------------------

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/health":
                self._json(HTTPStatus.OK, {"ok": True})
                return
            if path in STATIC:
                self._static(path)
                return
            if path == "/api/state":
                if not self._authorized():
                    self._error(HTTPStatus.UNAUTHORIZED, "Pair this phone first.")
                    return
                self._json(HTTPStatus.OK, remote.snapshot())
                return
            if path == "/api/events":
                if not self._authorized():
                    self._error(HTTPStatus.UNAUTHORIZED, "Pair this phone first.")
                    return
                self._stream()
                return
            self._error(HTTPStatus.NOT_FOUND, "Not found.")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if not self._origin_ok():
                self.close_connection = True
                self._error(HTTPStatus.FORBIDDEN, "Cross-origin request refused.")
                return
            if path == "/api/pair":
                body = self._read_json()
                if body is None:
                    self._error(HTTPStatus.BAD_REQUEST, "Send a pairing code.")
                    return
                code = str(body.get("code") or "").strip()
                name = str(body.get("name") or self.headers.get("User-Agent") or "Phone")
                token = remote._pair_request(code, name[:80])
                if not token:
                    # 403, not 401: an unpaired browser is not an expired session.
                    self._error(HTTPStatus.FORBIDDEN, "That pairing code is wrong or expired.")
                    return
                secure = "; Secure" if self._secure() else ""
                cookie = (
                    f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/; "
                    f"Max-Age={int(getattr(remote.config, 'session_days', 30)) * 86400}"
                    f"{secure}"
                )
                self._json(HTTPStatus.OK, {"ok": True}, [("Set-Cookie", cookie)])
                return

            if not self._authorized():
                # The body has not been read; a reused connection would parse
                # it as a new request.
                self.close_connection = True
                self._error(HTTPStatus.UNAUTHORIZED, "Pair this phone first.")
                return

            if path == "/api/say":
                body = self._read_json()
                if body is None:
                    self._error(HTTPStatus.BAD_REQUEST, "Send a reply.")
                    return
                text = str(body.get("text") or "")
                submit = bool(body.get("submit", True))
                result = remote.say(text, submit)
                status = HTTPStatus.OK if result.get("ok") else (
                    HTTPStatus.ACCEPTED if result.get("queued") else HTTPStatus.CONFLICT
                )
                self._json(status, result)
                return
            if path == "/api/permission":
                body = self._read_json()
                if body is None or "allow" not in body:
                    self._error(HTTPStatus.BAD_REQUEST, "Say allow or deny.")
                    return
                allow = body.get("allow")
                if not isinstance(allow, bool):
                    self._error(HTTPStatus.BAD_REQUEST, "Say allow or deny.")
                    return
                result = remote.permission(allow)
                self._json(HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT, result)
                return
            if path == "/api/read-aloud":
                body = self._read_json()
                value = body.get("enabled") if body is not None else None
                if not isinstance(value, bool):
                    self._error(HTTPStatus.BAD_REQUEST, "Say whether read-aloud is on.")
                    return
                active = remote._set_read_aloud(self._token(), value)
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "enabled": value, "mac_audio": not active},
                )
                return
            if path == "/api/recap":
                self._read_json()  # consume the page's empty JSON object
                text = remote.service.recap()
                if not text:
                    self._error(HTTPStatus.NOT_FOUND, "Nothing recent to recap.")
                    return
                self._json(HTTPStatus.OK, {"ok": True, "text": text})
                return
            if path == "/api/transcribe":
                if not remote._transcribe_ok():
                    self.close_connection = True
                    self._error(HTTPStatus.TOO_MANY_REQUESTS, "Too many recordings. Wait a moment.")
                    return
                length = self._content_length()
                if length <= 0 or length > MAX_BODY:
                    self.close_connection = True
                    self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "That recording is too large.")
                    return
                try:
                    rate = int(self.headers.get("X-Sample-Rate") or 16000)
                except ValueError:
                    rate = 16000
                if rate < 8000 or rate > 96000:
                    rate = 16000
                body = self.rfile.read(length)
                try:
                    text = remote._transcribe(body, rate)
                except asr.SpeechUnavailable as exc:
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
                    return
                except Exception as exc:
                    log.debug("phone transcription failed", exc_info=True)
                    self._error(HTTPStatus.BAD_REQUEST, str(exc) or "Could not transcribe that.")
                    return
                self._json(HTTPStatus.OK, {"ok": True, "text": text})
                return
            self.close_connection = True
            self._error(HTTPStatus.NOT_FOUND, "Not found.")

        def _static(self, path: str) -> None:
            name, content_type = STATIC[path]
            try:
                body = (WEB / name).read_bytes()
            except OSError:
                self._error(HTTPStatus.NOT_FOUND, "Not found.")
                return
            self._headers(HTTPStatus.OK, content_type, len(body))
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _stream(self) -> None:
            token = self._token()
            # The response has no length; close when it ends instead of
            # waiting for another request on the same socket.
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            last = ""
            last_sent = 0.0
            try:
                self.wfile.write(b"retry: 1500\n\n")
                self.wfile.flush()
                while remote.running and remote._store.validate(token):
                    try:
                        payload = json.dumps(remote.snapshot())
                    except Exception:
                        log.debug("could not build remote state", exc_info=True)
                        payload = ""
                    now = time.monotonic()
                    if payload and (payload != last or now - last_sent > 10.0):
                        self.wfile.write(f"event: state\ndata: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        last = payload
                        last_sent = now
                    else:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                    time.sleep(0.8)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
    return Handler
