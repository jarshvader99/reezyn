"""Generate the film's voiceover with the product's own Kokoro voice.

Reads build/script.json, synthesizes one clip per sentence with the same voice,
speed and audio treatment Reezyn itself uses, concatenates them into
assets/audio/vo.wav, and writes build/timings.json with exact sentence and
scene windows so captions and scene boundaries land on the words.
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent.parent
sys.path.insert(0, str(REPO))

from reezyn.tts import _prepare_samples  # noqa: E402

SAMPLE_RATE = 24000


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(round(seconds * SAMPLE_RATE)), dtype=np.float32)


def write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def main() -> int:
    from kokoro_onnx import Kokoro

    spec = json.loads((ROOT / "build" / "script.json").read_text(encoding="utf-8"))
    models = REPO / ".." / ".reezyn" / "models"
    home_models = Path.home() / ".reezyn" / "models"
    model = home_models / "kokoro-v1.0.int8.onnx"
    voices = home_models / "voices-v1.0.bin"
    del models
    if not model.is_file() or not voices.is_file():
        raise SystemExit("the Kokoro model is not downloaded; run `reezyn setup` first")

    engine = Kokoro(str(model), str(voices))
    voice = spec.get("voice", "af_heart")
    speed = float(spec.get("speed", 0.96))
    gap = float(spec.get("gap_s", 0.34))
    scene_gap = float(spec.get("scene_gap_s", 0.95))
    lead_in = float(spec.get("lead_in_s", 0.7))
    tail = float(spec.get("tail_s", 2.2))

    # A richer language pack would pronounce differently; the film is English.
    lang = "en-us"

    pieces: list[np.ndarray] = [silence(lead_in)]
    cursor = lead_in
    sentences: list[dict] = []
    scenes: list[dict] = []
    scene_wavs: dict[str, list[np.ndarray]] = {}

    for index, scene in enumerate(spec["scenes"]):
        wavs: list[np.ndarray] = []
        for sentence_index, text in enumerate(scene["sentences"]):
            samples, rate = engine.create(text, voice=voice, speed=speed, lang=lang)
            if rate != SAMPLE_RATE:
                raise SystemExit(f"unexpected sample rate {rate}")
            audio = _prepare_samples(np.asarray(samples, dtype=np.float32), SAMPLE_RATE, 0.95)
            # Trim the model's own leading/trailing silence so gaps are ours.
            loud = np.flatnonzero(np.abs(audio) > 1e-4)
            if loud.size:
                audio = audio[loud[0] : loud[-1] + 1]
            start = cursor
            duration = len(audio) / SAMPLE_RATE
            sentences.append(
                {
                    "scene": scene["id"],
                    "text": text,
                    "start": round(start, 3),
                    "end": round(start + duration, 3),
                    "duration": round(duration, 3),
                }
            )
            wavs.append(audio)
            pieces.append(audio)
            cursor += duration
            last_of_scene = sentence_index == len(scene["sentences"]) - 1
            pad = silence(scene_gap if last_of_scene else gap)
            pieces.append(pad)
            cursor += (scene_gap if last_of_scene else gap)
        scene_wavs[scene["id"]] = wavs

    total = cursor + tail
    pieces.append(silence(tail))
    master = np.concatenate(pieces)
    out = ROOT / "assets" / "audio" / "vo.wav"
    write_wav(out, master)

    for index, scene in enumerate(spec["scenes"]):
        first = next(s for s in sentences if s["scene"] == scene["id"])
        last = [s for s in sentences if s["scene"] == scene["id"]][-1]
        start = 0.0 if index == 0 else max(0.0, first["start"] - 0.45)
        if index + 1 < len(spec["scenes"]):
            nxt = next(s for s in sentences if s["scene"] == spec["scenes"][index + 1]["id"])
            end = max(last["end"] + 0.3, nxt["start"] - 0.45)
        else:
            end = total
        scenes.append(
            {
                "id": scene["id"],
                "kicker": scene["kicker"],
                "headline": scene["headline"],
                "support": scene["support"],
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
            }
        )

    timings = {
        "total": round(total, 3),
        "audio": "assets/audio/vo.wav",
        "scenes": scenes,
        "sentences": sentences,
    }
    (ROOT / "build" / "timings.json").write_text(
        json.dumps(timings, indent=2) + "\n", encoding="utf-8"
    )

    words = sum(len(s["text"].split()) for s in sentences)
    print(f"vo: {out} · {total:.1f}s · {words} words · {len(sentences)} sentences")
    for scene in scenes:
        print(f"  {scene['id']:<10} {scene['start']:>6.2f} → {scene['end']:>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
