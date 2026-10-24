"""The three permissions voice input needs, and where to grant them."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from . import asr, audio, typing

SETTINGS_URLS = {
    "microphone": (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
    ),
    "speech": (
        "x-apple.systempreferences:com.apple.preference.security?"
        "Privacy_SpeechRecognition"
    ),
    "accessibility": (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    ),
}

LABELS = {
    "microphone": "Microphone",
    "speech": "Speech Recognition",
    "accessibility": "Accessibility",
}


def summary() -> dict[str, str]:
    return {
        "microphone": audio.microphone_status(),
        "speech": asr.authorization_status(),
        "accessibility": "authorized" if typing.accessibility_trusted() else "denied",
    }


def ready() -> bool:
    return all(status == "authorized" for status in summary().values())


def missing() -> list[str]:
    return [name for name, status in summary().items() if status != "authorized"]


def describe() -> str:
    status = summary()
    lines = ["Voice input needs three permissions:", ""]
    for name in ("microphone", "speech", "accessibility"):
        state = status[name].replace("_", " ")
        lines.append(f"• {LABELS[name]}: {state}")
    missing_now = [name for name in missing()]
    if missing_now:
        lines += [
            "",
            "Grant the missing ones in System Settings → Privacy & Security.",
            "Reezyn keeps the menu bar item and asks only once per session.",
        ]
    else:
        lines += ["", "Everything is granted."]
    return "\n".join(lines)


def open_settings(kind: str) -> None:
    url = SETTINGS_URLS.get(kind)
    if url is None:
        return
    try:
        subprocess.Popen(["open", url])
    except OSError:
        pass


def request_missing(callback: Callable[[dict[str, str]], None] | None = None) -> None:
    """Prompt for whatever can still be prompted, then report the status."""

    def finish(_granted: bool = False) -> None:
        # Accessibility is not a request prompt: macOS adds Reezyn to the list
        # and waits for the developer to switch it on.
        typing.request_accessibility()
        status = summary()
        if callback is not None:
            callback(status)

    def request_speech() -> None:
        state = asr.authorization_status()
        if state == "not_determined":
            asr.request_authorization(lambda _status: finish())
        else:
            finish()

    state = audio.microphone_status()
    if state == "not_determined":
        audio.request_microphone_access(lambda _granted: request_speech())
    else:
        request_speech()


def request_accessibility() -> None:
    typing.request_accessibility()
