"""Local Kokoro speech, model download, and interruptible playback."""

from __future__ import annotations

import asyncio
import hashlib
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import SpeechConfig
from .languages import LanguagePack, pack_for_voice

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
TARGET_PEAK = 0.95
# Kokoro's int8 model comes out around -25 dBFS RMS, which is quiet next to
# anything else playing on the Mac. Six decibels of fixed makeup puts a line
# near -20 dBFS without the level of one line drifting from the next: measured
# across sentence lengths, the model's own RMS barely moves.
MAKEUP_GAIN = 2.0
# Above this the signal bends into a soft knee instead of being clipped or
# turned down as a whole; speech peaks only reach it on emphatic words.
KNEE = 0.7
# Kokoro only splits a line at 510 phonemes, which is longer than anything
# Reezyn says, so a line used to arrive as one batch after the whole thought
# had been synthesized. Pieces are capped by words instead, so the first
# sentence can play while the rest of the line is still being made.
PIECE_WORDS = 18
SENTENCE_PAUSE = 0.22
CLAUSE_PAUSE = 0.08
CLAUSE_SPLIT = re.compile(r"(?<=[,;:])\s+")
_PCM_RNG = np.random.default_rng()
# name, url, and the SHA-256 of the published release asset. The download path
# refuses anything else: onnxruntime parses these bytes in-process, so an
# unchecked model is a code path, not just a broken voice.
MODEL_FILES = {
    "int8": (
        "kokoro-v1.0.int8.onnx",
        f"{RELEASE}/kokoro-v1.0.int8.onnx",
        "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c",
    ),
    "fp16": (
        "kokoro-v1.0.fp16.onnx",
        f"{RELEASE}/kokoro-v1.0.fp16.onnx",
        "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96",
    ),
}
VOICES_FILE = (
    "voices-v1.0.bin",
    f"{RELEASE}/voices-v1.0.bin",
    "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
)
USER_AGENT = "reezyn/0.1 (+local speech)"


class SpeechError(RuntimeError):
    pass


@dataclass(slots=True)
class ModelPaths:
    model: Path
    voices: Path

    @property
    def ready(self) -> bool:
        return self.model.is_file() and self.voices.is_file()


def model_paths(home: Path, model: str = "int8") -> ModelPaths:
    name = MODEL_FILES.get(model, MODEL_FILES["int8"])[0]
    models = home / "models"
    return ModelPaths(models / name, models / VOICES_FILE[0])


def download_models(
    home: Path,
    model: str = "int8",
    progress: Callable[[str, int, int], None] | None = None,
) -> ModelPaths:
    """Download the selected Kokoro model and voices into Reezyn's home."""

    paths = model_paths(home, model)
    paths.model.parent.mkdir(parents=True, exist_ok=True)
    model_name, model_url, model_sha = MODEL_FILES.get(model, MODEL_FILES["int8"])
    _download(model_url, paths.model, model_name, progress, model_sha)
    voices_name, voices_url, voices_sha = VOICES_FILE
    _download(voices_url, paths.voices, voices_name, progress, voices_sha)
    return paths


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(
    url: str,
    target: Path,
    label: str,
    progress: Callable[[str, int, int], None] | None,
    sha256: str,
) -> None:
    """Fetch one model file and accept it only if it is the published asset."""

    if target.is_file():
        if _digest(target) == sha256:
            if progress:
                size = target.stat().st_size
                progress(label, size, size)
            return
        # Wrong bytes on disk (truncated, tampered, or from an older release):
        # fetch a clean copy rather than loading them.
        target.unlink(missing_ok=True)

    part = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    downloaded = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(label, downloaded, total)
        if _digest(part) != sha256:
            raise SpeechError(
                f"{label} did not match its published checksum; nothing was installed"
            )
        os.replace(part, target)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    if progress:
        progress(label, downloaded, downloaded)


def _prepare_samples(audio: np.ndarray, sample_rate: int, volume: float) -> np.ndarray:
    """Remove subsonic energy, lift the level, and stay below full scale.

    Kokoro's output has a small DC offset and measurable energy below 20 Hz.
    On small speakers that rumble eats headroom and can sound like distortion,
    so the chunk is high-passed first. The model is consistently quiet, so a
    fixed makeup gain brings the line up to a usable level and the soft knee
    keeps peaks under the ceiling without turning a whole chunk down over one
    transient.
    """

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        return samples
    samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    samples = _dc_block(samples, sample_rate)

    gain = max(0.0, min(1.0, float(volume))) * MAKEUP_GAIN
    samples = _limit(samples * gain).astype(np.float32, copy=False)
    return _fade_edges(samples, sample_rate)


def _limit(samples: np.ndarray) -> np.ndarray:
    """A soft knee above ``KNEE`` that approaches ``TARGET_PEAK`` smoothly."""

    magnitude = np.abs(samples)
    if not bool(np.any(magnitude > KNEE)):
        return samples
    span = TARGET_PEAK - KNEE
    over = (magnitude - KNEE) / span
    bent = KNEE + span * np.tanh(over)
    return np.where(magnitude > KNEE, np.sign(samples) * bent, samples)


def _to_pcm16(samples: np.ndarray) -> np.ndarray:
    """Round to 16 bits with triangular dither instead of truncating.

    Truncation toward zero biases low-level tails and leaves quantisation
    noise correlated with the signal, which is audible as a gritty edge on
    quiet consonants. One least-significant bit of TPDF dither trades that for
    a flat, inaudible noise floor.
    """

    dither = (_PCM_RNG.random(samples.size) - _PCM_RNG.random(samples.size)).astype(
        np.float32
    )
    scaled = np.rint(samples * 32767.0 + dither)
    return np.clip(scaled, -32768, 32767).astype(np.int16)


def _speech_pieces(
    text: str, pack: LanguagePack, max_words: int = PIECE_WORDS
) -> list[str]:
    """Break a line into the pieces Kokoro should synthesize one at a time.

    Sentences are the natural seam: each keeps its own prosody and the pause
    between them is added back as silence. A single sentence longer than the
    budget falls back to its clauses, which is the same seam the engine uses
    when it splits a batch.
    """

    text = text.strip()
    if not text or _word_count(text, pack) <= max_words:
        return [text] if text else []

    sentences = [
        part.strip() for part in pack.sentence_split.split(text) if part.strip(" .!?")
    ]
    if len(sentences) == 1:
        clauses = [
            part.strip() for part in CLAUSE_SPLIT.split(text) if part.strip(" .!?")
        ]
        if len(clauses) > 1:
            sentences = clauses

    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and _word_count(candidate, pack) > max_words:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces or [text]


def _with_pause(audio: np.ndarray, sample_rate: int, seconds: float) -> np.ndarray:
    """Hold the seam between two pieces for as long as a sentence would."""

    silence = np.zeros(int(max(1, sample_rate) * seconds), dtype=np.float32)
    return np.concatenate([np.asarray(audio, dtype=np.float32), silence])


def _word_count(text: str, pack: LanguagePack) -> int:
    return len(pack.word.findall(text))


def _fade_edges(samples: np.ndarray, sample_rate: int, seconds: float = 0.008) -> np.ndarray:
    """A few milliseconds of fade so a stream never starts or stops mid-wave."""

    length = min(int(max(1, sample_rate) * seconds), samples.size // 2)
    if length <= 1:
        return samples
    ramp = np.linspace(0.0, 1.0, length, dtype=np.float32)
    samples[:length] *= ramp
    samples[-length:] *= ramp[::-1]
    return samples


def _dc_block(samples: np.ndarray, sample_rate: int, cutoff_hz: float = 35.0) -> np.ndarray:
    """A one-pole high-pass filter with its corner just below speech."""

    # R = exp(-2*pi*fc/fs), approximated well for the small ratios we use.
    ratio = min(0.5, max(0.001, cutoff_hz / max(1, sample_rate)))
    decay = max(0.9, min(0.9999, 1.0 - (2.0 * np.pi * ratio)))

    output = np.empty_like(samples)
    previous_input = float(samples[0])
    previous_output = 0.0
    for index in range(samples.size):
        value = float(samples[index])
        previous_output = value - previous_input + decay * previous_output
        previous_input = value
        output[index] = previous_output
    return output


class KokoroSpeaker:
    """Synthesize speech locally and play it with interruption support."""

    def __init__(self, home: Path, config: SpeechConfig) -> None:
        self.home = home
        self.config = config
        self._engine: Any | None = None
        self._engine_lock = threading.Lock()
        self._load_error = ""
        self._current_stop = threading.Event()
        self._play_lock = threading.RLock()
        self._generation = 0
        self._synth_lock = threading.Lock()
        self._afplay: subprocess.Popen[bytes] | None = None
        self._sounddevice_ok: bool | None = None

    @property
    def paths(self) -> ModelPaths:
        return model_paths(self.home, self.config.model)

    @property
    def ready(self) -> bool:
        return self.paths.ready

    @property
    def load_error(self) -> str:
        return self._load_error

    def set_voice(self, voice: str) -> None:
        self.config.voice = voice

    def set_speed(self, speed: float) -> None:
        self.config.speed = speed

    def preload(self) -> None:
        try:
            self._ensure_engine()
        except Exception:
            return

    def speak(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return True
        with self._play_lock:
            generation = self._generation
        engine = self._ensure_engine()
        stop = threading.Event()
        with self._play_lock:
            # A pause/skip during model loading should cancel this utterance too.
            if generation != self._generation:
                return False
            self._current_stop = stop

        chunk_queue: queue.Queue[tuple[np.ndarray, int] | BaseException | None] = queue.Queue(
            maxsize=3
        )
        producer = threading.Thread(
            target=self._produce,
            args=(engine, text, chunk_queue, stop),
            name="reezyn-kokoro",
            daemon=True,
        )
        producer.start()

        interrupted = False
        try:
            while True:
                item = chunk_queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    if isinstance(item, SpeechError):
                        raise item
                    raise SpeechError(str(item)) from item
                if stop.is_set():
                    interrupted = True
                    break
                audio, sample_rate = item
                if not self._play(audio, sample_rate, stop):
                    interrupted = True
                    break
            if stop.is_set():
                interrupted = True
        finally:
            stop.set()
            self._stop_playback()
            producer.join(timeout=2.0)
        return not interrupted

    def stop(self) -> None:
        with self._play_lock:
            self._generation += 1
            self._current_stop.set()
        self._stop_playback()

    def reset_output(self) -> None:
        """Drop any stream bound to the old default output device.

        The next line opens a fresh stream, so it lands on whatever the system
        now considers the default. PortAudio caches its device list when it
        starts, so switching from AirPods to the speakers can leave it pointing
        at an index that no longer exists until the library is rebuilt.
        """

        self._sounddevice_ok = None
        with self._play_lock:
            # Keep a new utterance from opening a stream while PortAudio is
            # being rebuilt underneath it.
            self._generation += 1
            self._current_stop.set()
            self._stop_playback()
            self._refresh_portaudio()

    @staticmethod
    def _refresh_portaudio() -> None:
        try:
            import sounddevice as sd

            sd._terminate()
            sd._initialize()
        except Exception:
            log.debug("could not refresh PortAudio's device list", exc_info=True)

    def _produce(
        self,
        engine: Any,
        text: str,
        output: queue.Queue[tuple[np.ndarray, int] | BaseException | None],
        stop: threading.Event,
    ) -> None:
        async def put(audio: np.ndarray, sample_rate: int) -> None:
            while not stop.is_set():
                try:
                    output.put((audio, sample_rate), timeout=0.1)
                    return
                except queue.Full:
                    continue

        async def run() -> None:
            try:
                pack = pack_for_voice(self.config.voice)
                pieces = _speech_pieces(text, pack)
                for index, piece in enumerate(pieces):
                    if stop.is_set():
                        break
                    stream = engine.create_stream(
                        piece,
                        self.config.voice,
                        speed=self.config.speed,
                        lang=pack.kokoro_lang,
                        sentence_pause=SENTENCE_PAUSE,
                        clause_pause=CLAUSE_PAUSE,
                    )
                    # One chunk of lookahead, so the pause after a piece lands
                    # once, on its last chunk, instead of after every one.
                    held: tuple[np.ndarray, int] | None = None
                    async for audio, sample_rate in stream:
                        if stop.is_set():
                            break
                        if held is not None:
                            await put(*held)
                        held = (audio, sample_rate)
                    if held is None or stop.is_set():
                        continue
                    audio, sample_rate = held
                    if index < len(pieces) - 1:
                        audio = _with_pause(audio, sample_rate, SENTENCE_PAUSE)
                    await put(audio, sample_rate)
            except BaseException as exc:
                _put(output, SpeechError(str(exc)), stop)
            finally:
                _put(output, None, stop)

        try:
            with self._synth_lock:
                asyncio.run(run())
        except BaseException as exc:
            _put(output, SpeechError(str(exc)), stop)
            _put(output, None, stop)

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is not None:
                return self._engine
            if not self.paths.ready:
                raise SpeechError("Voice model is not installed. Run `reezyn setup`.")
            try:
                from kokoro_onnx import Kokoro

                self._engine = Kokoro(str(self.paths.model), str(self.paths.voices))
                self._load_error = ""
                return self._engine
            except Exception as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"
                raise SpeechError(f"Could not load Kokoro: {exc}") from exc

    def _play(self, audio: np.ndarray, sample_rate: int, stop: threading.Event) -> bool:
        if audio is None or len(audio) == 0:
            return True
        samples = _prepare_samples(audio, sample_rate, self.config.volume)

        if self._can_use_sounddevice():
            try:
                return self._play_sounddevice(samples, sample_rate, stop)
            except Exception:
                # PortAudio can disappear when a device is unplugged or the
                # default output switches. Retry it next time instead of
                # falling back to afplay for the rest of the session.
                self._sounddevice_ok = None
        return self._play_afplay(samples, sample_rate, stop)

    def _can_use_sounddevice(self) -> bool:
        if self._sounddevice_ok is not None:
            return self._sounddevice_ok
        try:
            import sounddevice as sd

            sd.query_devices(kind="output")
            self._sounddevice_ok = True
        except Exception:
            self._sounddevice_ok = False
        return self._sounddevice_ok

    def _play_sounddevice(self, samples: np.ndarray, sample_rate: int, stop: threading.Event) -> bool:
        import sounddevice as sd

        with self._play_lock:
            if stop.is_set():
                return False
            # A larger PortAudio buffer avoids dropouts while Kokoro is
            # synthesizing the next chunk on another thread.
            sd.play(samples, sample_rate, blocking=False, latency="high")
        while True:
            if stop.is_set():
                sd.stop()
                return False
            stream = sd.get_stream()
            if stream is None or not stream.active:
                return True
            time.sleep(0.025)

    def _play_afplay(self, samples: np.ndarray, sample_rate: int, stop: threading.Event) -> bool:
        fd, name = tempfile.mkstemp(prefix="reezyn-", suffix=".wav")
        os.close(fd)
        path = Path(name)
        try:
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(_to_pcm16(samples).tobytes())
            with self._play_lock:
                if stop.is_set():
                    return False
                self._afplay = subprocess.Popen(
                    ["/usr/bin/afplay", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            while self._afplay.poll() is None:
                if stop.is_set():
                    self._afplay.terminate()
                    return False
                time.sleep(0.025)
            return self._afplay.returncode == 0
        finally:
            with self._play_lock:
                self._afplay = None
            path.unlink(missing_ok=True)

    def _stop_playback(self) -> None:
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass
        with self._play_lock:
            process = self._afplay
            if process and process.poll() is None:
                process.terminate()


def _put(
    output: queue.Queue[tuple[np.ndarray, int] | BaseException | None],
    item: tuple[np.ndarray, int] | BaseException | None,
    stop: threading.Event,
) -> None:
    while True:
        try:
            output.put(item, timeout=0.1)
            return
        except queue.Full:
            if stop.is_set():
                return
