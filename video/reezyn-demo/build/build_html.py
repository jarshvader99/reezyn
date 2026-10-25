"""Build index.html from the template, the voiceover timings, and real pauses.

Caption cues break each spoken sentence into readable chunks and snap their
boundaries onto the silences ffmpeg finds in the voiceover, so a cue changes on
the pause rather than mid-word.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
MAX_CUE_WORDS = 9
SNAP_S = 0.75


def silences(audio: Path) -> list[float]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(audio),
            "-af",
            "silencedetect=noise=-45dB:d=0.10",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    log = result.stderr
    starts = [float(m) for m in re.findall(r"silence_start: ([0-9.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", log)]
    return [round((a + b) / 2, 3) for a, b in zip(starts, ends) if b > a]


def chunks(text: str) -> list[str]:
    """Split a sentence into at most MAX_CUE_WORDS-word cues at natural breaks."""

    if len(text.split()) <= MAX_CUE_WORDS:
        return [text]
    words = text.split()
    pieces: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + MAX_CUE_WORDS, len(words))
        if end < len(words):
            for i in range(end, start + 3, -1):
                if words[i - 1].endswith((",", ";", ":", "—", "and", "but", "or")):
                    end = i
                    break
        pieces.append(" ".join(words[start:end]))
        start = end
    return pieces


def snap(target: float, pauses: list[float]) -> float:
    best = min(pauses, key=lambda p: abs(p - target), default=None)
    if best is not None and abs(best - target) <= SNAP_S:
        return best
    return target


def cues(sentences: list[dict], pauses: list[float]) -> list[dict]:
    out: list[dict] = []
    for sentence in sentences:
        parts = chunks(sentence["text"])
        if len(parts) == 1:
            out.append(
                {
                    "text": parts[0],
                    "start": sentence["start"],
                    "end": sentence["end"],
                    "scene": sentence["scene"],
                }
            )
            continue
        weights = [len(part) for part in parts]
        total = sum(weights)
        bounds = [sentence["start"]]
        cursor = sentence["start"]
        for weight in weights[:-1]:
            cursor += (sentence["end"] - sentence["start"]) * weight / total
            bounds.append(round(snap(cursor, pauses), 3))
        bounds.append(sentence["end"])
        for index, part in enumerate(parts):
            out.append(
                {
                    "text": part,
                    "start": bounds[index],
                    "end": bounds[index + 1],
                    "scene": sentence["scene"],
                }
            )
    # A cue that cannot be read in the time it holds is merged with the next
    # one (or the previous, at the end), never left too short to read.
    def floor(cue: dict) -> float:
        return 0.5 + 0.3 * len(cue["text"].split())

    merged: list[dict] = []
    for cue in out:
        if merged:
            last = merged[-1]
            too_short = (cue["end"] - cue["start"]) < floor(cue)
            fits = len(last["text"].split()) + len(cue["text"].split()) <= 14
            same_scene = last["scene"] == cue["scene"]
            if same_scene and (too_short or (last["end"] - last["start"]) < floor(last)) and fits:
                last["text"] = f"{last['text']} {cue['text']}"
                last["end"] = cue["end"]
                continue
        merged.append(dict(cue))
    out = merged

    # Cues must not overlap or run backwards after snapping.
    for index, cue in enumerate(out):
        if index + 1 < len(out) and cue["end"] > out[index + 1]["start"]:
            cue["end"] = out[index + 1]["start"]
        if cue["end"] <= cue["start"]:
            cue["end"] = round(cue["start"] + 0.6, 3)
    return out


def vtt_time(seconds: float) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}"


def write_vtt(path: Path, cue_list: list[dict]) -> None:
    blocks = ["WEBVTT", ""]
    for index, cue in enumerate(cue_list, start=1):
        blocks.append(str(index))
        blocks.append(f"{vtt_time(cue['start'])} --> {vtt_time(cue['end'])}")
        blocks.append(cue["text"])
        blocks.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")


def main() -> int:
    timings = json.loads((BUILD / "timings.json").read_text(encoding="utf-8"))
    audio = ROOT / timings["audio"]
    pauses = silences(audio)
    cue_list = cues(timings["sentences"], pauses)

    tokens: dict[str, str] = {"total": f"{timings['total']:.3f}"}
    schedule: dict[str, dict[str, float]] = {}
    for scene in timings["scenes"]:
        tokens[f"{scene['id']}.start"] = f"{scene['start']:.3f}"
        tokens[f"{scene['id']}.dur"] = f"{scene['duration']:.3f}"
        tokens[f"{scene['id']}.end"] = f"{scene['end']:.3f}"
        lines = [
            round(sentence["start"] - scene["start"], 3)
            for sentence in timings["sentences"]
            if sentence["scene"] == scene["id"]
        ]
        schedule[scene["id"]] = {
            "start": scene["start"],
            "duration": scene["duration"],
            "lines": lines,
        }

    caption_html = "\n".join(
        '      <div class="clip cap" id="cap-{n}" data-start="{start:.3f}" data-duration="{dur:.3f}">'
        '<span>{text}</span></div>'.format(
            n=n,
            start=cue["start"],
            dur=max(0.6, cue["end"] - cue["start"]),
            text=cue["text"],
        )
        for n, cue in enumerate(cue_list, start=1)
    )

    template = (BUILD / "index.template.html").read_text(encoding="utf-8")
    html = template.replace("@CAPTIONS@", caption_html).replace(
        "@SCHEDULE@", json.dumps(schedule, indent=2)
    )
    html = re.sub(
        r"@([a-z]+)\.(start|dur|end|total)@",
        lambda m: tokens.get(f"{m.group(1)}.{m.group(2)}", m.group(0)),
        html,
    )
    html = html.replace("@total@", tokens["total"])
    leftovers = re.findall(r"@[a-z]+\.[a-z]+@", html)
    if leftovers:
        raise SystemExit(f"unfilled tokens: {sorted(set(leftovers))}")
    (ROOT / "index.html").write_text(html, encoding="utf-8")
    print(f"index.html written · {len(cue_list)} caption cues · {len(pauses)} pauses found")
    if "--vtt" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--vtt") + 1]).resolve()
        write_vtt(target, cue_list)
        print(f"{target} written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
