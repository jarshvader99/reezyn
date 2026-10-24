"""Turn agent events into short, speakable, first-person thoughts.

This module is deliberately deterministic. Reezyn's job is to reveal the
agent's own reasoning, not to invent a new summary with another model.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import NarrationConfig
from .events import Event

log = logging.getLogger("reezyn")

FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`([^`]*)`")
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
URL = re.compile(r"https?://\S+")
ABS_PATH = re.compile(r"(?:~|\.{1,2})?/[\w./@+-]+")
REL_PATH = re.compile(r"\b[\w.-]+(?:/[\w.-]+){1,6}\b")
SNAKE_CASE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9]*(?:_[a-zA-Z0-9]+)+\b")
CAMEL_CASE = re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]+){1,6}\b")
WHITESPACE = re.compile(r"\s+")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
WORD = re.compile(r"[A-Za-z0-9']+")
EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F1E6-\U0001F1FF]"
)

CONNECTORS = ("Okay,", "Right,", "So,", "Now,")

TEST_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:uv run )?(?:pytest|py\.test|npm (?:run )?test|pnpm test|"
    r"yarn test|bun test|go test|cargo test|vitest|jest|rspec|phpunit)\b"
)
LINT_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:ruff|eslint|mypy|pyright|tsc|biome|prettier|"
    r"cargo clippy|cargo fmt|gofmt)\b"
)
SEARCH_COMMAND = re.compile(r"(?:^|[\s;&|])(?:rg|grep|ag|ack|fd|fzf)\b")
INSTALL_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:npm|pnpm|yarn|bun) (?:install|add)\b|"
    r"(?:^|[\s;&|])(?:uv add|uv sync|pip install|poetry add|cargo add|go get)\b"
)
READ_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:cat|bat|head|tail|less|more|sed|awk|nl)\b"
)
LIST_COMMAND = re.compile(r"(?:^|[\s;&|])(?:ls|find|tree|du|stat)\b")
MUTATE_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:rm|mv|cp|mkdir|touch|chmod|chown|ln)\b"
)
NETWORK_COMMAND = re.compile(r"(?:^|[\s;&|])(?:curl|wget|http|ping)\b")
GIT_COMMAND = re.compile(r"(?:^|[\s;&|])git\b")
RUN_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:python|python3|node|deno|bun|uv run|npm run|pnpm run|"
    r"yarn run|go run|cargo run|make)\b"
)


@dataclass(slots=True)
class Narration:
    text: str
    fingerprint: str
    events: list[Event] = field(default_factory=list)


def clean_for_speech(text: str) -> str:
    """Make reasoning readable aloud without reading code or markup."""

    if not text:
        return ""
    value = FENCED_CODE.sub(" ", text)
    value = MARKDOWN_LINK.sub(r"\1", value)
    value = URL.sub("a link", value)
    value = INLINE_CODE.sub(lambda m: _code_to_words(m.group(1)), value)
    value = ABS_PATH.sub(lambda m: _basename(m.group(0)), value)
    value = REL_PATH.sub(lambda m: _basename(m.group(0)), value)
    value = re.sub(r"<[^>]{1,80}>", " ", value)
    value = value.replace("&", " and ")
    value = value.replace("->", " to ").replace("=>", " so ")
    value = value.replace("==", " equals ").replace("&&", " and ")
    value = value.replace("%", " percent ")
    value = SNAKE_CASE.sub(_expand_identifier, value)
    value = CAMEL_CASE.sub(_expand_identifier, value)
    value = value.replace("`", "").replace("*", "").replace("#", "")
    value = value.replace("_", " ")
    value = re.sub(r"\[(?:x|X| )?\]", " ", value)
    value = EMOJI.sub(" ", value)
    value = (
        value.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2014", ", ")
        .replace("\u2013", ", ")
        .replace("\u2026", ".")
    )
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*[-*+]\s+", ". ", value)
    value = re.sub(r"\n+", ". ", value)
    value = re.sub(r"([,;:])\s*([,;:])+", r"\1", value)
    value = re.sub(r"\.(?:\s*\.)+", ".", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = WHITESPACE.sub(" ", value).strip(" .;:-")
    value = _remove_reasoning_labels(value)
    value = re.sub(
        r"\bThe user (?:wants|is asking|asked) me to\b",
        "I'll",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bI should\b", "I'll", value)
    value = re.sub(r"\bI am going to\b", "I'll", value)
    value = re.sub(r"\bI'm going to\b", "I'll", value)
    value = re.sub(r"\bLet me\b", "Let me", value)
    return value.strip()


def select_excerpt(text: str, max_words: int = 42) -> str:
    """Pick one or two complete sentences, capped to a comfortable length."""

    cleaned = clean_for_speech(text)
    if not cleaned:
        return ""
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(cleaned) if s.strip()]
    if not sentences:
        sentences = [cleaned]

    chosen: list[str] = []
    total = 0
    for sentence in sentences:
        count = _word_count(sentence)
        if not chosen:
            if count > max_words:
                sentence = _truncate_sentence(sentence, max_words)
                count = _word_count(sentence)
            chosen.append(sentence)
            total = count
            continue
        if total + count <= max_words and len(chosen) < 2:
            chosen.append(sentence)
            total += count
        else:
            break

    excerpt = " ".join(chosen).strip()
    excerpt = _ensure_terminal_punctuation(excerpt)
    return excerpt


def describe_tool(name: str, tool_input: dict[str, Any] | None = None) -> str:
    """A short, non-technical description of an agent action."""

    tool_input = tool_input or {}
    raw = (name or "tool").strip()
    key = raw.lower()
    file_name = _tool_file(tool_input)

    if key in {"bash", "shell", "exec_command", "run_shell_command", "local_shell_call"}:
        command = str(tool_input.get("command") or tool_input.get("cmd") or "")
        return command_intent(command)
    if key in {"read", "read_file", "view", "open_file"}:
        return f"reading {file_name}" if file_name else "reading through the code"
    if key in {"edit", "write", "multiedit", "write_file", "replace", "apply_patch"}:
        return f"updating {file_name}" if file_name else "editing the files"
    if key in {"notebookedit", "edit_notebook"}:
        return "updating the notebook"
    if key in {"grep", "grep_search"}:
        pattern = str(tool_input.get("pattern") or "").strip()
        if pattern and len(pattern) <= 40 and re.search(r"[A-Za-z0-9]", pattern):
            return f'searching for "{_clean_quoted(pattern)}"'
        return "searching the codebase"
    if key in {"glob", "list_files", "file_search"}:
        return "finding the relevant files"
    if key in {"websearch", "google_web_search", "web_search"}:
        return "checking the docs"
    if key in {"webfetch", "web_fetch", "fetch"}:
        return "reading the docs"
    if key in {"task", "agent", "subagent"}:
        return "handing that to a subagent"
    if key in {"todowrite", "update_plan", "todo_write"}:
        return "updating the plan"
    if key in {"view_image", "read_image"}:
        return "looking at the screenshot"
    if key.startswith("mcp__"):
        parts = raw.split("__")
        server = parts[1] if len(parts) > 1 else "an MCP server"
        return f"using {server.replace('_', ' ')}"
    human = re.sub(r"[_\-]+", " ", raw).strip()
    return f"using {human}" if human else "working"


def command_intent(command: str) -> str:
    command = command.strip()
    if not command:
        return "running a command"
    if TEST_COMMAND.search(command):
        return "running the tests"
    if LINT_COMMAND.search(command):
        return "checking the code"
    if SEARCH_COMMAND.search(command):
        return "searching the codebase"
    if re.search(r"\bgit (?:status|diff|show)\b", command):
        return "checking what changed"
    if re.search(r"\bgit log\b", command):
        return "looking at the recent history"
    if INSTALL_COMMAND.search(command):
        return "installing dependencies"
    if READ_COMMAND.search(command):
        return "reading through the code"
    if LIST_COMMAND.search(command):
        return "looking around the project"
    if MUTATE_COMMAND.search(command):
        return "organizing the files"
    if NETWORK_COMMAND.search(command):
        return "checking an endpoint"
    if GIT_COMMAND.search(command):
        return "working with git"
    if RUN_COMMAND.search(command):
        return "running the project"
    return "running a command"


def compose_narration(
    events: Iterable[Event],
    config: NarrationConfig,
    now: float | None = None,
) -> Narration | None:
    """Coalesce a burst of events into one calm spoken thought."""

    now = time.time() if now is None else now
    fresh = [
        event
        for event in sorted(events, key=lambda item: item.ts)
        if now - event.ts <= config.stale_after_s
    ]
    if not fresh:
        return None

    reasoning = next(
        (event for event in reversed(fresh) if event.kind == "reasoning" and event.text.strip()),
        None,
    )
    tool_events = [event for event in fresh if event.kind == "tool" and event.tool_name]
    latest_tool = tool_events[-1] if tool_events else None

    if reasoning:
        excerpt = select_excerpt(reasoning.text, config.max_words)
        if not excerpt:
            reasoning = None
        else:
            if (
                latest_tool
                and now - latest_tool.ts <= 2.5
                and _word_count(excerpt) < 9
            ):
                action = describe_tool(latest_tool.tool_name, latest_tool.tool_input)
                if action and action not in excerpt.lower():
                    terminal = "" if excerpt.endswith((".", "!", "?")) else "."
                    excerpt = f"{excerpt}{terminal} I'm {action}."
            text = excerpt
            fingerprint = reasoning.fingerprint
            source_events = [reasoning]
            if latest_tool and latest_tool.ts >= reasoning.ts:
                source_events.append(latest_tool)
            return _finish(text, fingerprint, source_events, config)

    if not tool_events:
        return None

    actions: list[str] = []
    for event in tool_events:
        action = describe_tool(event.tool_name, event.tool_input)
        if action and action not in actions:
            actions.append(action)
    actions = actions[-2:]
    if not actions:
        return None

    if len(actions) == 1:
        text = f"I'm {actions[0]}."
    else:
        text = f"I'm {actions[0]}, then {actions[1]}."
    fingerprint = latest_tool.fingerprint if latest_tool else "tools"
    return _finish(text, fingerprint, tool_events[-2:], config)


def _finish(
    text: str, fingerprint: str, events: list[Event], config: NarrationConfig
) -> Narration:
    text = _ensure_terminal_punctuation(text)
    if config.connectors:
        text = _add_connector(text, fingerprint)
    return Narration(text=text, fingerprint=fingerprint, events=events)


def _add_connector(text: str, fingerprint: str) -> str:
    if not text:
        return text
    if text.lower().startswith(("okay", "right", "so ", "now ", "well", "hmm")):
        return text
    score = int(hashlib.sha1(fingerprint.encode()).hexdigest()[:8], 16)
    if score % 4 != 0:
        return text
    connector = CONNECTORS[score % len(CONNECTORS)]
    return f"{connector} {text}"


def _truncate_sentence(sentence: str, max_words: int) -> str:
    words = sentence.split()
    if len(words) <= max_words:
        return sentence
    cut = " ".join(words[:max_words]).rstrip(" ,;:-")
    # Prefer a clause boundary if one is close to the end.
    boundary = max(cut.rfind(","), cut.rfind(";"), cut.rfind(":"))
    if boundary >= len(cut) * 0.55:
        cut = cut[:boundary]
    return cut.rstrip(" ,;:-")


def _ensure_terminal_punctuation(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    if text[0].islower():
        text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def _word_count(text: str) -> int:
    return len(WORD.findall(text))


def _basename(path: str) -> str:
    name = Path(path.rstrip(".,;:)")).name
    return name or "the file"


def _code_to_words(code: str) -> str:
    value = code.strip()
    if not value:
        return " "
    if re.fullmatch(r"[\w./@+-]+", value):
        if "/" in value or "." in value:
            return _basename(value)
        return SNAKE_CASE.sub(_expand_identifier, CAMEL_CASE.sub(_expand_identifier, value))
    if re.search(r"\s", value):
        return "the command"
    return "that"


def _expand_identifier(match: re.Match[str]) -> str:
    value = match.group(0)
    if "_" in value:
        return " ".join(part for part in value.split("_") if part)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return value.lower()


def _remove_reasoning_labels(text: str) -> str:
    text = re.sub(
        r"^(?:thinking|reasoning|analysis|thought)\s*[:\-]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _clean_quoted(value: str) -> str:
    value = re.sub(r"[^\w\s.-]", " ", value)
    return WHITESPACE.sub(" ", value).strip()[:36]


def _tool_file(tool_input: dict[str, Any]) -> str:
    for key in ("file_path", "path", "notebook_path", "file"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _basename(value)
    return ""


class Narrator:
    """A debounced, deduplicated, interruptible speech queue."""

    def __init__(
        self,
        speak: Callable[[str], bool | None],
        config: NarrationConfig,
        stop_speech: Callable[[], None] | None = None,
        on_change: Callable[[], None] | None = None,
        on_spoken: Callable[[str], None] | None = None,
    ) -> None:
        self._speak = speak
        self._stop_speech = stop_speech or (lambda: None)
        self._config = config
        self._on_change = on_change
        self._on_spoken = on_spoken
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending: list[Event] = []
        self._last_submit = 0.0
        self._last_finish = 0.0
        self._seen: dict[str, float] = {}
        self._paused = False
        self._speaking = False
        self._current = ""
        self._recent: deque[str] = deque(maxlen=6)
        self._last_error = ""
        self._error_until = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reezyn-narrator", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._stop_speech()
        if self._thread:
            self._thread.join(timeout=3)

    def submit(self, event: Event) -> None:
        now = time.monotonic()
        with self._lock:
            for key, seen_at in list(self._seen.items()):
                if now - seen_at > self._config.dedupe_s:
                    del self._seen[key]
            if event.fingerprint in self._seen:
                return
            self._seen[event.fingerprint] = now
            self._pending.append(event)
            self._last_submit = now
        self._wake.set()
        self._notify()

    def pause(self) -> None:
        with self._lock:
            if self._paused:
                return
            self._paused = True
            self._pending.clear()
        self._stop_speech()
        self._notify()

    def resume(self) -> None:
        with self._lock:
            if not self._paused:
                return
            self._paused = False
        self._wake.set()
        self._notify()

    def toggle_pause(self) -> bool:
        if self.is_paused:
            self.resume()
            return False
        self.pause()
        return True

    def skip(self) -> None:
        with self._lock:
            self._pending.clear()
        self._stop_speech()
        self._notify()

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._speaking

    @property
    def current(self) -> str:
        with self._lock:
            return self._current

    @property
    def recent(self) -> list[str]:
        with self._lock:
            return list(self._recent)

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def state(self) -> str:
        with self._lock:
            if self._paused:
                return "paused"
            if self._speaking:
                return "speaking"
            if self._last_error and time.monotonic() < self._error_until:
                return "error"
            return "listening"

    def _run(self) -> None:
        while not self._stop.is_set():
            events: list[Event] | None = None
            with self._lock:
                if not self._paused and not self._speaking and self._pending:
                    now = time.monotonic()
                    if now - self._last_submit >= self._config.debounce_ms / 1000.0:
                        events = self._pending
                        self._pending = []
                        self._speaking = True
            if events is None:
                self._wake.wait(0.08)
                self._wake.clear()
                continue

            narration = compose_narration(events, self._config)
            if not narration:
                with self._lock:
                    self._speaking = False
                continue

            with self._lock:
                self._current = narration.text
                error_blocked = time.monotonic() < self._error_until
            self._notify()

            ok = True
            if not error_blocked and not self._stop.is_set():
                log.info("speaking: %s", narration.text)
                try:
                    result = self._speak(narration.text)
                    ok = result is not False
                except Exception as exc:  # keep the narrator alive for the session
                    ok = False
                    log.warning("speech failed: %s", exc)
                    with self._lock:
                        self._last_error = f"{type(exc).__name__}: {exc}"
                        self._error_until = time.monotonic() + 15
                        ok = False

            with self._lock:
                self._speaking = False
                self._current = ""
                self._last_finish = time.monotonic()
                if ok:
                    self._recent.appendleft(narration.text)
                    self._last_error = ""
                    self._error_until = 0
            if ok and self._on_spoken:
                try:
                    self._on_spoken(narration.text)
                except Exception:
                    pass
            self._notify()

            while (
                not self._stop.is_set()
                and time.monotonic() - self._last_finish < self._config.min_gap_s
            ):
                self._wake.wait(0.05)
                self._wake.clear()

    def _notify(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                pass
