from __future__ import annotations

import hashlib
import re

import pytest

from reezyn import tts
from reezyn.tts import MODEL_FILES, VOICES_FILE, SpeechError, _download

GOOD = b"the published kokoro weights" * 4096
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.position = 0
        self.headers = {"Content-Length": str(len(body))}

    def read(self, size: int = -1) -> bytes:
        if self.position >= len(self.body):
            return b""
        end = len(self.body) if size < 0 else self.position + size
        chunk = self.body[self.position : end]
        self.position += len(chunk)
        return chunk

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_the_pinned_digests_are_real_sha256_values():
    pinned = [entry[2] for entry in MODEL_FILES.values()] + [VOICES_FILE[2]]

    assert len(pinned) == 3
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in pinned)


def test_a_matching_body_is_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(GOOD))
    target = tmp_path / "kokoro-v1.0.int8.onnx"

    _download("https://example.com/model", target, "model", None, GOOD_SHA)

    assert target.read_bytes() == GOOD
    assert not (tmp_path / "kokoro-v1.0.int8.onnx.part").exists()


def test_a_body_that_does_not_match_the_pin_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(GOOD))
    target = tmp_path / "kokoro-v1.0.int8.onnx"

    with pytest.raises(SpeechError, match="published checksum"):
        _download("https://example.com/model", target, "model", None, "0" * 64)

    assert not target.exists()
    assert not (tmp_path / "kokoro-v1.0.int8.onnx.part").exists()


def test_a_tampered_file_on_disk_is_replaced(tmp_path, monkeypatch):
    target = tmp_path / "kokoro-v1.0.int8.onnx"
    target.write_bytes(b"not the model" * 1000)

    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(GOOD))
    _download("https://example.com/model", target, "model", None, GOOD_SHA)

    assert target.read_bytes() == GOOD


def test_an_already_correct_file_is_not_downloaded_again(tmp_path, monkeypatch):
    target = tmp_path / "kokoro-v1.0.int8.onnx"
    target.write_bytes(GOOD)

    def explode(*_args, **_kwargs):
        raise AssertionError("a correct model should not be fetched again")

    monkeypatch.setattr(tts.urllib.request, "urlopen", explode)

    _download("https://example.com/model", target, "model", None, GOOD_SHA)

    assert target.read_bytes() == GOOD
