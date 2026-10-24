# Reezyn

Reezyn is a calm, local narrator for the reasoning behind coding-agent tool
calls. It lives in the macOS menu bar, listens to Claude Code, Codex CLI, Gemini
CLI, Cursor, OpenCode, and generic event streams, and speaks short excerpts of
the agent's own thoughts in an open-source Kokoro voice.

It does not summarize with an LLM, call a cloud API, or send transcripts
anywhere. Reezyn reads the agent's local session log, picks a short reasoning
excerpt, turns it into a speakable first-person line, and queues it at a human
pace.

```
agent event → adapter → burst coalescer → speech cleanup → Kokoro → audio
```

The queue is deliberately lazy. Coding agents emit tool calls in bursts; Reezyn
waits for a short pause, keeps the most recent useful thought, deduplicates the
rest, and drops stale material instead of reading a backlog.

## Requirements

- macOS (the UI is a menu-bar app)
- Python 3.11–3.13
- [`uv`](https://docs.astral.sh/uv/)
- An audio output device

## Install

```bash
cd reezyn
uv sync
uv run reezyn setup
uv run reezyn
```

`reezyn setup` downloads the Kokoro int8 model and voices (about 142 MB) into
`~/.reezyn/models`. The `reezyn` command starts the menu-bar app. A `reezyn`
title appears in the menu bar; there is no Dock icon.

Useful first commands:

```bash
uv run reezyn doctor          # environment, model, hooks, audio, Codex logs
uv run reezyn install claude  # add the Claude Code hook
uv run reezyn install gemini  # add the Gemini CLI hook
uv run reezyn simulate        # nine demo events through the real queue
```

If you move the checkout after installing hooks, reinstall them so the absolute
interpreter path is updated.

## Agents

### Claude Code

```bash
uv run reezyn install claude
```

This adds one async `PreToolUse` hook to `~/.claude/settings.json`. The hook
writes a minimized event to Reezyn's local spool and exits immediately. Reezyn
then reads the session's `transcript_path` for the latest assistant reasoning
that led to the tool call.

- Existing settings and hooks are preserved.
- The previous file is copied to `settings.json.reezyn.bak`.
- The installer is idempotent.
- Remove it with `uv run reezyn install claude --uninstall`.
- If `CLAUDE_CONFIG_DIR` is set, that settings directory is used.

Start a new Claude Code session after installing the hook.

### Gemini CLI

```bash
uv run reezyn install gemini
```

This adds a `BeforeTool` hook to `~/.gemini/settings.json`, with the same
backup, idempotency, and uninstall behavior. Reezyn reads the hook's
`transcript_path` and prefers explicit thought/reasoning parts.

### Codex CLI

No install step. Reezyn watches new rollout files under
`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (or `$CODEX_HOME/sessions`).
Start Reezyn before starting Codex so it can follow the new rollout as it is
appended. It reads reasoning summaries, agent-reasoning events, function calls,
shell commands, patches, MCP calls, and web searches.

Codex sometimes stores reasoning as opaque `encrypted_content`. Reezyn skips
that and uses the tool action instead.

### Cursor

No install step. Reezyn watches Cursor Agent transcripts under
`~/.cursor/projects/<project>/agent-transcripts/<session>/*.jsonl` (or
`$CURSOR_PROJECTS_DIR`). It reads assistant `thinking`/`reasoning` blocks,
commentary, and `tool_use` / `tool_call` blocks. Text-only final answers are not
read back to you.

Cursor's separate CLI chat `store.db` is not read yet; the JSONL transcript is
watched when Cursor writes it.

### OpenCode

No install step. Reezyn reads OpenCode's local SQLite database read-only at
`${OPENCODE_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/opencode}/opencode.db`.
It supports the current v2 `session_message` schema (assistant `content` blocks
of `reasoning`, `text`, and `tool`) and the older `message` + `part` schema.
Recent assistant rows are re-scanned briefly so streamed text and tool state
updates are picked up; WAL data is read without copying or modifying the
database.

Start Reezyn before or during the OpenCode session. Existing history is not
replayed beyond the short recent window.

### Anything else

Two generic paths are available:

```bash
# One event
uv run reezyn emit --agent custom --text "I'll read the session middleware."
uv run reezyn emit --agent custom --tool Bash --input '{"command":"pytest"}'

# JSONL from stdin
printf '%s\n' '{"kind":"tool","tool_name":"Bash","tool_input":{"command":"npm test"}}' \
  | uv run reezyn ingest --agent custom
```

Generic event fields:

```json
{
  "agent": "custom",
  "kind": "reasoning",
  "text": "I'll inspect the refresh path.",
  "session_id": "optional",
  "tool_name": "optional",
  "tool_input": {},
  "ts": 0
}
```

`kind` is `reasoning` or `tool`. A tool event needs `tool_name`; a reasoning
event needs `text`. If no reasoning is available, Reezyn describes the tool
call instead.

## The menu bar

- **Listening** — idle, waiting for a worthwhile event.
- **Speaking** — the menu title shows a dot; the status line displays the
  current line.
- **Pause narration** — stops the current audio and clears the queue.
- **Skip this thought** — interrupts the current utterance.
- **Recent thoughts** — the last six spoken lines.
- **Voice** — Heart, Michael, Bella, George, or Emma.
- **Pace** — Calm (0.84×), Natural (0.94×), Brisk (1.04×).
- **Install agent hooks** — writes the Claude or Gemini integration after a
  confirmation dialog. Codex, Cursor, and OpenCode are watched automatically.
- **Download voice model** — appears while the model is missing.
- **Simulate a session** — a local demo through the real pipeline.
- **Open Reezyn folder** — `~/.reezyn`.

`reezyn doctor` is the text version of the same state.

## Configuration

Reezyn creates `~/.reezyn/config.toml`:

```toml
[speech]
voice = "af_heart"
speed = 0.92
volume = 0.90
model = "int8"          # int8 or fp16

[narration]
max_words = 42
max_sentences = 2       # 0 = no sentence limit; raise max_words to fit longer thoughts
debounce_ms = 700
stale_after_s = 9.0
min_gap_s = 0.35
connectors = true       # occasional "Okay," / "Right," / "So,"
dedupe_s = 30.0

[agents]
claude = true
codex = true
gemini = true
cursor = true
opencode = true
```

The menu writes voice and pace changes back here. Other edits take effect when
the app restarts.

`REEZYN_HOME` moves all Reezyn state (config, models, spool, logs). Set it in
the environment of both the menu app and the agent that runs the hook.

## What gets spoken

Reasoning is cleaned before it reaches Kokoro:

- fenced and inline code are removed or reduced to a filename
- markdown, URLs, and full paths are stripped
- snake_case and camelCase become words
- at most `max_sentences` short sentences are selected (two by default), capped
  by `max_words`
- set `max_sentences = 0` and raise `max_words` (up to 400) to hear a whole
  summary; restart the app to apply it
- a little connective language is added occasionally

If the reasoning is shorter than nine words and a tool call follows, Reezyn adds
a short action clause, for example:

> Let me check. I'm reading session.ts.

If no reasoning is available, it falls back to tool descriptions:

> I'm searching the codebase, then running the tests.

When the tool input names a target file — including Codex patch text and
OpenCode's completed file list — the file is named instead:

> I'm updating session.ts.

Multiple active sessions are kept separate: the most recently active session
wins until it goes quiet.

## Privacy

- Transcripts and rollouts are read locally.
- Hook payloads are minimized before they touch disk. Only the session id,
  transcript path, tool name, and description-relevant tool arguments are kept.
  Keys that look like secrets are redacted.
- The spool directory is `~/.reezyn/events` (mode `0700`).
- Spoken lines are written to `~/.reezyn/reezyn.log` for debugging. Delete the
  log or set `REEZYN_HOME` if that is not appropriate for your machine.
- The only network request is the one-time Kokoro model download.
- No telemetry.

The Kokoro model is Apache-2.0; `kokoro-onnx` is MIT. Reezyn does not vendor
either.

## Development

```bash
uv sync
uv run pytest
uv run python -m compileall -q reezyn
```

The test suite covers speech cleanup and excerpting, burst coalescing and
deduplication, all five agent formats, hook installers, the cross-process
spool, and an end-to-end Claude hook → fake speaker run. The real Kokoro engine
and menu bar are exercised separately with `uv run reezyn` and
`uv run reezyn simulate`.

Layout:

```
reezyn/
  adapters/      Claude, Codex, Gemini, Cursor, and OpenCode event sources
  cli.py         command surface
  menu.py        rumps menu-bar app
  narration.py   excerpting, tool descriptions, coalescing queue
  service.py     adapter → queue wiring
  tts.py         Kokoro setup, streaming playback, interruption
  spool.py       cross-process event handoff
  installers.py  backed-up hook install/uninstall
```

## Known limitations

- This is an early macOS build, not a signed `.app` bundle.
- Claude may hide or redact chain-of-thought; when it does, Reezyn uses visible
  assistant text or a tool description.
- Codex encrypted reasoning cannot be read.
- Gemini transcript formats can drift; the extractor is intentionally tolerant
  but may fall back to tool descriptions.
- Cursor CLI's separate chat `store.db` is not read yet; only Cursor's agent
  JSONL transcript is watched.
- OpenCode is read from its SQLite schema, which can drift. Current v2 and the
  older message/part shape are supported; unknown block types are ignored.
- Reezyn expects the menu app to be running. Hooks are fast and fail silently;
  check `~/.reezyn/hook-errors.log` if events are not arriving.
