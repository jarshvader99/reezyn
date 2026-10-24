# Reezyn

Reezyn is a calm, local narrator for the reasoning behind coding-agent tool
calls. It lives in the macOS menu bar, listens to Claude Code, Codex CLI, Gemini
CLI, Cursor, OpenCode, Freebuff, and generic event streams, and speaks short
excerpts of the agent's own thoughts in an open-source Kokoro voice.

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

### Freebuff

No install step. Reezyn watches Freebuff chat snapshots under
`~/.config/manicode/projects/<project>/chats/<chat>/chat-messages.json` (or
`$FREEBUFF_PROJECTS_DIR`). Assistant messages carry ordered `reasoning` text,
commentary, and tool blocks. Reezyn reads the reasoning and tool calls and
skips the final answer, as it does for Cursor.

Freebuff rewrites the snapshot as a turn advances rather than appending to it,
so Reezyn re-reads a chat only when its size or mtime changes and speaks each
block once. A trailing thought is held for one poll, so a sentence that is
still streaming is spoken complete instead of being cut off.

Start Reezyn before or during the Freebuff session. Chats that already exist
when Reezyn starts are not replayed.

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
- **Speaking** — the seedling breathes slowly and the status line displays the
  current line.
- **Pause narration** — stops the current audio and clears the queue.
- **Skip this thought** — interrupts the current utterance.
- **Recent thoughts** — the last six spoken lines.
- **Voice** — English (Heart, Michael, Bella, George, Emma), Spanish (Dora,
  Alex, Santa), or French (Siwis). The selected voice also selects the language
  Reezyn speaks around the agent's words.
- **Pace** — Calm (0.84×), Natural (0.94×), Brisk (1.04×).
- **Presence bed** — generated room tone with Off, Whisper, Soft, and Clear
  levels; ducks while narration plays.
- **Lull banter** — a short line when a tool call runs long, so a quiet stretch
  never feels stalled. On by default; the menu switches it off.
- **Install agent hooks** — writes the Claude or Gemini integration after a
  confirmation dialog. Codex, Cursor, OpenCode, and Freebuff are watched
  automatically.
- **Download voice model** — appears while the model is missing.
- **Simulate a session** — a local demo through the real pipeline.
- **Open Reezyn folder** — `~/.reezyn`.

The status item is a small template seedling. It stays still while idle, breathes
slowly while Reezyn is speaking, dims when narration is paused, and stays static
when macOS Reduce Motion is enabled. The icon carries the identity, so the word
`reezyn` is no longer in the menu bar; compact glyphs still mark paused (`⏸`),
setup or speech errors (`!`), and downloads (`↓`).

`reezyn doctor` is the text version of the same state.

## Languages

Reezyn speaks around the agent's own words, and the selected voice decides the
language of that framing. English voices use English connectors and tool
phrases; Spanish and French voices use theirs. The agent's reasoning itself is
not translated: a Spanish or French voice will read English reasoning with that
language's pronunciation, so ask the agent to think in the language you want to
hear.

Language behavior lives in `reezyn/languages.py`. Each `LanguagePack` supplies
the Kokoro language, its voices, sentence and word rules, connectors, and tool
phrases. Adding a language means adding one pack there; the menu, speaker, and
narration queue resolve everything else from the selected voice. Kokoro ships
voices for several more languages; English, Spanish, and French have packs so
far.

```bash
uv run reezyn start --voice em_alex   # start with Alex, Spanish
uv run reezyn start --voice ff_siwis  # start with Siwis, French
```

`--voice` is applied at startup, so stop the menu app before using it; while
Reezyn is running, choose the voice from the menu instead.

## Presence bed

The presence bed is a quiet room tone generated on this machine: no samples,
no download, no network. Turn it on from the menu. It fades in, sits low under
everything, and drops to a quarter of its level while a line is spoken, then
eases back. It is deliberately noise rather than music or binaural tones: there
is nothing to license and nothing competing with the agent's voice.

It is off by default. Choose **Presence bed → Off / Whisper / Soft / Clear** in
the menu; picking a level also turns the bed on, and Off preserves the level for
next time. Set `[ambience] enabled = true` to start with it on. The bed uses
`sounddevice` like the voice; when speech falls back to `afplay`, the two
streams cannot be mixed and the bed stays silent.

## Lull banter

Long tool calls produce no reasoning to narrate. Once the agent has gone quiet
for 30 seconds, Reezyn fills the gap with one short, calm line — "Still here.
Letting this one run." If the silence continues it offers a second line a
minute or so later, then stops until fresh activity arrives. A new agent event
resets the count, so the banter tracks the real rhythm of the session instead
of talking to an idle laptop.

These lines are Reezyn's own presence, not invented agent thoughts: they never
claim to know what the agent is doing, only that it is still working. Every
language pack carries its own set, so banter follows the selected voice.
Toggle **Lull banter** in the menu, or set `[banter] enabled = false`.

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

[banter]
enabled = true
after_s = 30.0          # silence before the first line
repeat_s = 75.0         # silence between later lines
max_lines = 2           # per stretch of activity; 0 is silent

[agents]
claude = true
codex = true
gemini = true
cursor = true
opencode = true
freebuff = true

[ambience]
enabled = false
volume = 0.35           # 0..1; Whisper .15, Soft .35, Clear .55
```

The menu writes voice, pace, presence-bed, and banter changes back here. Other
edits take effect when the app restarts.

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
deduplication, all six agent formats, hook installers, the cross-process
spool, and an end-to-end Claude hook → fake speaker run. The real Kokoro engine
and menu bar are exercised separately with `uv run reezyn` and
`uv run reezyn simulate`.

Layout:

```
reezyn/
  adapters/      Claude, Codex, Gemini, Cursor, OpenCode, and Freebuff sources
  cli.py         command surface
  menu.py        rumps menu-bar app
  narration.py   excerpting, tool descriptions, coalescing queue
  languages.py   voice packs: language, voices, phrasing, text rules
  status_icon.py seedling template icon and breathing curve
  ambience.py    generated presence bed with ducking
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
- Reezyn does not translate. A language pack only frames the agent's own
  words, so English reasoning read by a Spanish or French voice is pronounced,
  not translated; ask the agent to think in the language you want to hear.
- OpenCode is read from its SQLite schema, which can drift. Current v2 and the
  older message/part shape are supported; unknown block types are ignored.
- Freebuff's `chat-messages.json` snapshot is a local UI cache and can change
  shape between releases. Reezyn ignores unknown block types, and a chat that
  has already been compacted exposes only the messages Freebuff kept.
- The presence bed needs `sounddevice`. With the `afplay` speech fallback it is
  unavailable, and it follows the current default output device rather than a
  device chosen per utterance.
- Reezyn expects the menu app to be running. Hooks are fast and fail silently;
  check `~/.reezyn/hook-errors.log` if events are not arriving.
