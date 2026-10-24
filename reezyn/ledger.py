"""A per-project memory of what was asked, concluded, and done.

Narration is ephemeral: once a line is spoken it is gone. This module is the
other half of the voice — a small append-only ledger for each project, so
Reezyn can say "we have been here before" when a new prompt arrives and "here
is where we landed" when asked.

No model is involved. Entries are the agent's own words or a rendered action
phrase, and recall is lexical: query terms are weighted by how rare they are in
this project's own history. That keeps recall instant, offline, and
explainable, and it keeps Reezyn quoting the agent rather than paraphrasing it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROLE_PROMPT = "prompt"
ROLE_THOUGHT = "thought"
ROLE_ACTION = "action"
ROLES = frozenset({ROLE_PROMPT, ROLE_THOUGHT, ROLE_ACTION})

MAX_TEXT = 1200
MAX_ENTRIES = 600  # what a pruning pass keeps
MAX_AGE_S = 45 * 86400.0
DEDUPE_WINDOW = 25
MIN_ENTRY_WORDS = 4
# A prompt is compared against everything that happened before it, but not
# against the turn that is starting right now.
SELF_WINDOW_S = 20.0

WORD = re.compile(r"[^\W_]+", re.UNICODE)
IDENT_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|[_\-]+|(?<=[A-Z])(?=[A-Z][a-z])"
)

STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those there here
    is are was were be been being do does did doing have has had having
    i you he she it we they me him her us them my your his its our their
    to of in on at by for with from into over under about as not no yes
    can could should would will shall may might must
    what which who whom whose when where why how
    just also only very really still again back out up down
    get got make made making use used using want wants wanted need needs
    now new like let lets okay ok well so
    work works working worked after before during while since until once
    el la los las un una unos unas de del al y o pero es son fue ser
    que con por para en lo le se su sus mi tu
    le les des une du et ou mais est sont ce cette ces
    qui quoi dont avec pour dans sur pas plus
    """.split()
)


@dataclass(frozen=True, slots=True)
class Entry:
    ts: float
    session: str
    role: str
    text: str
    agent: str = ""
    fingerprint: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "ts": round(self.ts, 3),
            "session": self.session,
            "role": self.role,
            "text": self.text,
            "agent": self.agent,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_json(cls, data: Any) -> Entry | None:
        if not isinstance(data, dict):
            return None
        role = data.get("role")
        text = data.get("text")
        ts = data.get("ts")
        if role not in ROLES or not isinstance(text, str) or not text.strip():
            return None
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            return None
        return cls(
            ts=float(ts),
            session=str(data.get("session") or ""),
            role=role,
            text=text[:MAX_TEXT],
            agent=str(data.get("agent") or ""),
            fingerprint=str(data.get("fingerprint") or ""),
        )


@dataclass(frozen=True, slots=True)
class Match:
    entry: Entry
    score: float


def project_root(path: Path) -> Path:
    """The repository a working directory belongs to, when there is one."""

    try:
        path = path.expanduser().resolve()
    except OSError:
        return path
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        try:
            if (candidate / ".git").exists():
                return candidate
        except OSError:
            continue
    return path


def tokens(text: str) -> list[str]:
    """Lowercased, stopword-free, lightly stemmed terms for lexical recall.

    Identifiers are split on case and underscores first, so ``refreshToken`` in
    a thought still meets "refresh token" in a prompt.
    """

    out: list[str] = []
    for chunk in IDENT_BOUNDARY.split(text):
        for raw in WORD.findall(chunk.lower()):
            if len(raw) < 2 or raw in STOPWORDS:
                continue
            out.append(_stem(raw))
    return out


def _stem(token: str) -> str:
    """A deliberately crude suffix fold: recall only needs both sides to meet.

    "tests" and "test" have to land on the same term, and so do "running" and
    "run". Over-stemming ("process" and "processing" both become "proce") is
    harmless here because the fold is applied consistently to queries and
    entries.
    """

    for _ in range(4):
        for suffix in ("ies", "ing", "ed", "es", "s"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                token = token[: -len(suffix)] + ("y" if suffix == "ies" else "")
                if suffix in ("ing", "ed") and len(token) >= 4 and token[-1] == token[-2]:
                    token = token[:-1]
                break
        else:
            return token
    return token


class ProjectLedger:
    """One append-only JSONL file, cached in memory until the file changes."""

    def __init__(self, directory: Path, key: str = "") -> None:
        self.directory = directory
        self.key = key
        self.path = directory / "ledger.jsonl"
        self.meta_path = directory / "meta.json"
        self._lock = threading.RLock()
        self._entries: list[Entry] | None = None
        self._signature: tuple[int, int] | None = None
        self._writes = 0

    def ensure(self) -> None:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.meta_path.exists():
                _write_json(self.meta_path, {"key": self.key, "name": Path(self.key).name})

    def append(self, entry: Entry) -> bool:
        """Add one entry, skipping exact repeats of the recent turn."""

        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.meta_path.exists():
                _write_json(self.meta_path, {"key": self.key, "name": Path(self.key).name})
            entries = self.entries()
            if entry.fingerprint:
                for recent in entries[-DEDUPE_WINDOW:]:
                    if recent.fingerprint == entry.fingerprint:
                        return False
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry.to_json(), ensure_ascii=False) + "\n")
            except OSError:
                return False
            entries.append(entry)
            self._entries = entries
            self._signature = self._stat()
            self._writes += 1
            if self._writes % 50 == 0:
                self._prune(entries)
            return True

    def entries(self) -> list[Entry]:
        with self._lock:
            signature = self._stat()
            if self._entries is not None and signature == self._signature:
                return list(self._entries)
            self._entries = self._load()
            self._signature = signature
            return list(self._entries)

    def search(
        self,
        query: str,
        *,
        threshold: float = 0.30,
        before: float | None = None,
        max_age_s: float = 30 * 86400.0,
        limit: int = 3,
        now: float | None = None,
    ) -> list[Match]:
        """Rank earlier thoughts and actions against a new prompt."""

        terms = set(tokens(query))
        if not terms:
            return []
        now = time.time() if now is None else now
        cutoff = (before if before is not None else now) - SELF_WINDOW_S
        floor = now - max_age_s
        candidates = [
            entry
            for entry in self.entries()
            if entry.role != ROLE_PROMPT
            and floor <= entry.ts <= cutoff
            and len(entry.text.split()) >= MIN_ENTRY_WORDS
        ]
        if not candidates:
            return []

        documents = [set(tokens(entry.text)) for entry in candidates]
        document_frequency: dict[str, int] = {}
        for document in documents:
            for term in terms & document:
                document_frequency[term] = document_frequency.get(term, 0) + 1

        total = len(candidates)
        weights = {
            term: math.log((total + 1) / (document_frequency.get(term, 0) + 1)) + 1.0
            for term in terms
        }
        weight_sum = sum(weights.values())
        if weight_sum <= 0:
            return []

        matches: list[Match] = []
        for entry, document in zip(candidates, documents):
            overlap = terms & document
            if not overlap:
                continue
            # One stray word out of three is usually a coincidence, not memory.
            if len(terms) >= 3 and len(overlap) < 2:
                continue
            score = sum(weights[term] for term in overlap) / weight_sum
            age = max(0.0, now - entry.ts)
            score *= 0.7 + 0.3 * math.exp(-age / (7 * 86400.0))
            if score >= threshold:
                matches.append(Match(entry=entry, score=score))
        matches.sort(key=lambda match: (match.score, match.entry.ts), reverse=True)
        return matches[:limit]

    def recent(self, window_s: float, now: float | None = None) -> list[Entry]:
        now = time.time() if now is None else now
        return [entry for entry in self.entries() if now - window_s <= entry.ts <= now + 60]

    def _load(self) -> list[Entry]:
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        entries: list[Entry] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry = Entry.from_json(value)
            if entry is not None:
                entries.append(entry)
        entries.sort(key=lambda entry: entry.ts)
        return entries

    def _prune(self, entries: list[Entry]) -> None:
        now = time.time()
        keep = [entry for entry in entries if now - entry.ts <= MAX_AGE_S][-MAX_ENTRIES:]
        if len(keep) == len(entries):
            return
        temp = self.path.with_suffix(".tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                for entry in keep:
                    handle.write(json.dumps(entry.to_json(), ensure_ascii=False) + "\n")
            os.replace(temp, self.path)
        except OSError:
            temp.unlink(missing_ok=True)
            return
        self._entries = keep
        self._signature = self._stat()

    def _stat(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns)


class LedgerStore:
    """Resolves a project to its ledger, wherever the project name came from."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.root = home / "projects"
        self._lock = threading.RLock()
        self._cache: dict[str, ProjectLedger] = {}

    def for_project(self, project: str) -> ProjectLedger:
        value = project.strip()
        if not value:
            raise ValueError("a project is required")
        with self._lock:
            key = self._resolve(value)
            ledger = self._cache.get(key)
            if ledger is not None:
                return ledger
            ledger = ProjectLedger(self.root / _directory_name(key), key)
            ledger.ensure()
            self._cache[key] = ledger
            return ledger

    def known(self) -> list[ProjectLedger]:
        ledgers: list[ProjectLedger] = []
        try:
            metas = sorted(self.root.glob("*/meta.json"))
        except OSError:
            return ledgers
        for meta in metas:
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            key = data.get("key") if isinstance(data, dict) else None
            if isinstance(key, str) and key:
                ledgers.append(ProjectLedger(meta.parent, key))
        return ledgers

    def _resolve(self, project: str) -> str:
        path = Path(project).expanduser()
        if path.is_absolute():
            return str(project_root(path))
        # A bare name is all some watchers know: Freebuff names its project
        # folders after the working directory. Reuse the absolute project with
        # the same name when there is exactly one.
        for ledger in self.known():
            if Path(ledger.key).name == project:
                return ledger.key
        return f"name:{project}"


def _directory_name(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:12]


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
