"""Local Kokoro speech, model download, and interruptible playback."""

from __future__ import annotations

import asyncio
import os
import queue
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

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
TARGET_PEAK = 0.95
MODEL_FILES = {
    "int8": ("kokoro-v1.0.int8.onnx", f"{RELEASE}/kokoro-v1.0.int8.onnx"),
    "fp16": ("kokoro-v1.0.fp16.onnx", f"{RELEASE}/kokoro-v1.0.fp16.onnx"),
}
VOICES_FILE = ("voices-v1.0.bin", f"{RELEASE}/voices-v1.0.bin")
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
    model_name, model_url = MODEL_FILES.get(model, MODEL_FILES["int8"])
    _download(model_url, paths.model, model_name, progress)
    _download(VOICES_FILE[1], paths.voices, VOICES_FILE[0], progress)
    return paths


def _download(
    url: str,
    target: Path,
    label: str,
    progress: Callable[[str, int, int], None] | None,
) -> None:
    if target.is_file() and target.stat().st_size > 1_000_000:
        if progress:
            size = target.stat().st_size
            progress(label, size, size)
        return

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
        os.replace(part, target)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    if progress:
        progress(label, downloaded, downloaded)


def _prepare_samples(audio: np.ndarray, sample_rate: int, volume: float) -> np.ndarray:
    """Remove subsonic energy and keep the signal below full scale.

    Kokoro's output has a small DC offset and measurable energy below 20 Hz.
    On small speakers that rumble eats headroom and can sound like distortion,
    so the chunk is high-passed before the volume gain is applied. The peak
    guard scales a chunk down instead of hard-clipping it.
    """

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        return samples
    samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    samples = _dc_block(samples, sample_rate)

    gain = max(0.0, min(1.0, float(volume)))
    peak = float(np.max(np.abs(samples)))
    if peak > 0:
        gain = min(gain, TARGET_PEAK / peak)
    return (samples * gain).astype(np.float32, copy=False)


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

    def _produce(
        self,
        engine: Any,
        text: str,
        output: queue.Queue[tuple[np.ndarray, int] | BaseException | None],
        stop: threading.Event,
    ) -> None:
        async def run() -> None:
            try:
                stream = engine.create_stream(
                    text,
                    self.config.voice,
                    speed=self.config.speed,
                    lang="en-us",
                    sentence_pause=0.22,
                    clause_pause=0.08,
                )
                async for audio, sample_rate in stream:
                    if stop.is_set():
                        break
                    while not stop.is_set():
                        try:
                            output.put((audio, sample_rate), timeout=0.1)
                            break
                        except queue.Full:
                            continue
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
                # PortAudio can disappear (device unplugged, remote session).
                self._sounddevice_ok = False
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
                pcm = (samples * 32767.0).astype(np.int16)
                handle.writeframes(pcm.tobytes())
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
