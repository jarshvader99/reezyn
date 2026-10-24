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

Reezyn can also take the other side of the conversation. With voice input on and
the caret in a terminal prompt, say **"Reezyn"** and dictate; it types what you
say without submitting it. When an agent finishes a turn, Reezyn speaks the
final answer and reopens the microphone, so a follow-up question never needs
the keyboard. Speech recognition is Apple's on-device model, and no audio is
written to disk.

## Requirements

- macOS (the UI is a menu-bar app)
- Python 3.11–3.13
- [`uv`](https://docs.astral.sh/uv/)
- An audio output device

## Install

### From a disk image

Open `Reezyn-<version>.dmg`, drag Reezyn to Applications, and launch it. The
bundle carries its own Python and dependencies, so no `uv` or Python install is
needed. It downloads the Kokoro voice model on first use from **Download voice
model** in the menu; the same download is available from a terminal:

```bash
/Applications/Reezyn.app/Contents/MacOS/Reezyn setup
/Applications/Reezyn.app/Contents/MacOS/Reezyn install claude
```

### From a checkout

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

This adds four hooks to `~/.claude/settings.json`: `PreToolUse`,
`UserPromptSubmit`, `Stop`, and `Notification`. Each writes a minimized event
to Reezyn's local spool and exits immediately. For a tool call Reezyn reads the
session's `transcript_path` for the latest assistant reasoning that led to it;
for a prompt it keeps the developer's own words so it can recall earlier work
later (see [Memory](#memory)). `Stop` carries the end of a turn, so Reezyn can
speak the final answer and open the microphone; `Notification` carries a
permission prompt, which can be answered by voice (see
[Voice input](#voice-input)).

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

This adds four hooks to `~/.gemini/settings.json`: `BeforeTool`, with the same
backup, idempotency, and uninstall behavior; `BeforeAgent`, which carries the
prompt you just submitted so Reezyn can recall earlier work; `AfterAgent`,
which closes the turn with the final response for the conversational loop; and
`Notification`, which reports tool-permission prompts. For tool calls Reezyn
reads the hook's `transcript_path` and prefers explicit thought/reasoning parts.

An install from an earlier version only had the first two hooks, so rerun the
installer after upgrading to gain `Stop`/`AfterAgent` and `Notification`.

### Codex CLI

No install step. Reezyn watches new rollout files under
`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (or `$CODEX_HOME/sessions`).
Start Reezyn before starting Codex so it can follow the new rollout as it is
appended. It reads reasoning summaries, agent-reasoning events, the user message that
starts each turn, function calls, shell commands, patches, MCP calls, and web
searches.

Codex sometimes stores reasoning as opaque `encrypted_content`. Reezyn skips
that and uses the tool action instead.

### Cursor

No install step. Reezyn watches Cursor Agent transcripts under
`~/.cursor/projects/<project>/agent-transcripts/<session>/*.jsonl` (or
`$CURSOR_PROJECTS_DIR`). It reads your prompts, assistant `thinking`/`reasoning` blocks,
commentary, and `tool_use` / `tool_call` blocks. Text-only final answers are not
read back to you.

Cursor's separate CLI chat `store.db` is not read yet; the JSONL transcript is
watched when Cursor writes it.

### OpenCode

No install step. Reezyn reads OpenCode's local SQLite database read-only at
`${OPENCODE_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/opencode}/opencode.db`.
It supports the current v2 `session_message` schema (assistant `content` blocks
of `reasoning`, `text`, and `tool`, plus the `user` rows) and the older
`message` + `part` schema, where user text parts make up the prompt.
Recent assistant rows are re-scanned briefly so streamed text and tool state
updates are picked up; WAL data is read without copying or modifying the
database.

Start Reezyn before or during the OpenCode session. Existing history is not
replayed beyond the short recent window.

### Freebuff

No install step. Reezyn watches Freebuff chat snapshots under
`~/.config/manicode/projects/<project>/chats/<chat>/chat-messages.json` (or
`$FREEBUFF_PROJECTS_DIR`). Assistant messages carry ordered `reasoning` text,
commentary, and tool blocks. Reezyn reads your prompts, the reasoning, and the tool
calls, and skips the final answer, as it does for Cursor.

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

`kind` is `reasoning`, `tool`, or `prompt`. A tool event needs `tool_name`; a
reasoning or prompt event needs `text`. If no reasoning is available, Reezyn
describes the tool call instead.

Every event may carry a `project`: the repository root it belongs to. Hooks and
watchers fill it in automatically; `reezyn emit` and `reezyn ingest` default it
to the current directory. The project is what gives Reezyn memory — send
`--kind prompt` events with `--text` to give a custom agent the same recall that
Claude Code and Freebuff get.

## The menu bar

- **Listening** — idle, waiting for a worthwhile event.
- **Speaking** — the seedling breathes slowly and the status line displays the
  current line.
- **Pause narration** — stops the current audio and clears the queue.
- **Skip this thought** — interrupts the current utterance.
- **Recent thoughts** — the last six spoken lines.
- **Recap the last session** — speaks what you asked, what the agent concluded,
  and what it last touched, without making you re-read the transcript.
- **Recall when I re-ask** — on by default. When a new prompt overlaps work this
  project already settled, Reezyn quotes the earlier conclusion first.
- **Voice** — English (Heart, Michael, Bella, George, Emma), Spanish (Dora,
  Alex, Santa), or French (Siwis). The selected voice also selects the language
  Reezyn speaks around the agent's words.
- **Pace** — Calm (0.84×), Natural (0.94×), Brisk (1.04×).
- **System volume** — sets the Mac's output volume to 20%, 40%, 60%, 80%, or
  100%. The check mark follows the current output device.
- **Presence bed** — generated room tone with Off, Whisper, Soft, and Clear
  levels; ducks while narration plays.
- **Lull banter** — a short line when a tool call runs long, so a quiet stretch
  never feels stalled. On by default; the menu switches it off.
- **Voice input** — turns on hands-free dictation and the conversational loop.
  Off by default; see [Voice input](#voice-input).
- **Talk back after a turn** — speaks the agent's final answer (up to
  `max_answer_words`) and reopens the microphone after each turn. On when voice
  input is on.
- **Ask before sending** — after a dictated pause, asks whether to submit
  instead of pressing Return by itself. On by default; the hot keyword still
  sends immediately.
- **Voice permissions** — live status for Microphone, Speech Recognition, and
  Accessibility, with buttons that open the right System Settings panes.
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

## Audio devices

Reezyn follows the Mac's default input and output instead of pinning the
devices it started with. When you switch from AirPods to the built-in speakers
and microphone, the microphone engine is rebuilt on the new input and the next
line opens its output stream on the new default. That also releases a Bluetooth
headset from its low-quality hands-free mode, which an open input otherwise
keeps it in.

A line that is already playing is stopped when the output changes, because its
stream belongs to the old device; narration continues with the next line.
Switching the input cancels any half-heard utterance. `reezyn doctor` prints the
current device ids and output volume.

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

## Memory

Narration is ephemeral: once a line is spoken it is gone. Reezyn also keeps a
small ledger per project under `~/.reezyn/projects/`, holding what you asked,
what the agent concluded, and the actions it took. Two behaviors come from it.

**Recall at prompting time.** When a new prompt overlaps something this project
already settled, Reezyn speaks the earlier conclusion before the agent starts
working:

> Earlier today, the agent concluded: The refresh path rotates the cookie but
> never invalidates the old token. That is why the stale session still works.

That is the point where a developer usually re-asks, or re-derives, something
the agent already worked out. Recall is off the table for the turn in progress,
fires at most once per prompt, waits at least 90 seconds between lines, and
never repeats the same memory. A prompt with three or more distinctive terms
needs at least two to match, so one shared word is not enough to interrupt.

**Recap on demand.** **Recap the last session** in the menu (or `reezyn recap`)
speaks the last 45 minutes in three sentences: what you asked, what it
concluded, and the last action. It is the fastest way to catch up after a
context switch or before reviewing a diff.

Matching is lexical, not a second model: query terms are weighted by how rare
they are in this project's own history, with a light recency preference. The
spoken line is the agent's sentence trimmed to fit, never a paraphrase. That
keeps recall instant and offline, and keeps Reezyn from inventing an opinion
the agent never had.

```bash
uv run reezyn recap                      # this project, last 45 minutes
uv run reezyn recap --minutes 180
uv run reezyn recall "refresh token rotation"   # what does this project already know?
```

`reezyn recall` prints earlier conclusions and actions that match a query, with
timestamps. It is the same ledger the spoken recall uses, so researching a
feature starts from what the agent already found instead of from a blank
search.

Every agent Reezyn watches supplies prompts. Claude Code and Gemini CLI report
them through `UserPromptSubmit` and `BeforeAgent` hooks; Codex rollouts, Cursor
transcripts, OpenCode rows, and Freebuff snapshots carry your message in their
own format. Each prompt is
tied to a project — Codex's recorded `cwd`, OpenCode's session directory,
Cursor's project folder, or the hook's `cwd` — so switching agents does not
split the memory. Custom agents get the same behavior with
`reezyn emit --kind prompt`.

## Voice input

Hands-free input is off until you turn it on: choose **Voice input** in the menu
or run `uv run reezyn voice --enable`. It needs three macOS grants, shown under
**Voice permissions**: Microphone, Speech Recognition, and Accessibility.
Accessibility is what lets Reezyn check where the caret is and type into it.
Microphone and Speech may require restarting Reezyn after you grant them.

```bash
uv run reezyn voice           # config, permissions, wake word
uv run reezyn voice --json    # the same, for scripts
```

How it behaves:

- **Arming.** The microphone only listens for the wake word when a
  terminal-like text field has the caret — Terminal, iTerm2, VS Code, Cursor,
  Ghostty, Warp, WezTerm, kitty, Alacritty, or Hyper (`voice.allow_apps`).
  Anywhere else, nothing is transcribed.
- **Waking.** Say "Hey Reezyn" or just "Reezyn". Fuzzy matching accepts what
  Apple's recognizer guesses: "reason", "resin", "risen". The menu bar chirps
  once and the seedling shows the live transcript.
- **Drafting.** Words are typed at the caret but never submitted; the live
  transcript stays in the menu bar, not in the field. A one-second pause commits
  the sentence, and the next eight seconds accept follow-ups without the wake
  word.
- **Confirming.** After the pause, Reezyn asks "Ready to send it?" and keeps
  listening. Say yes or "send it" to press Return, "not yet" to keep the draft
  and keep talking. Six seconds of silence leaves the draft in place.
- **Sending at once.** End the sentence with the hot keyword — "…send it now",
  "send now", "send it" — and Reezyn types and submits in one breath, with no
  question and no wait.
- **Dictation.** Spoken punctuation works: "comma", "period", "new line",
  "underscore", "backtick". "Scratch that" backspaces exactly what Reezyn
  inserted; "never mind" stops listening; "send it" presses Return.- **Conversation** (Claude Code, Gemini CLI, Codex, Freebuff). When an agent
  finishes a turn, Reezyn speaks its final answer and keeps listening for a reply.
  In that window a sentence is typed and submitted on its own — the confirmation
  question is skipped, because a reply is already an answer and you may be away
  from the screen. A
  permission prompt is read aloud too: say "allow" or "deny" to press Return or
  Escape, or answer with a full sentence to type it instead.
- **Interrupting.** Saying the wake word while Reezyn is speaking stops the
  speech and starts listening.

Safety is layered: the app allowlist, the focused-control check, no automatic
submission outside the conversation window, and "scratch that". Synthetic
keystrokes go wherever focus is, so keep the allowlist to terminals and editors
you trust.

```toml
[voice]
enabled = false
conversation = true          # speak final answers and reopen the mic
wake_word = "hey reezyn"     # a bare "reezyn" also works
locale = ""                  # empty follows the selected voice's language
silence_ms = 1000            # pause that commits a sentence
wake_silence_ms = 700        # pause that ends a wake-word probe
max_utterance_s = 15.0
follow_up_s = 8.0            # no wake word needed for this long after speech
conversation_s = 12.0        # how long the mic stays open after a turn
auto_send = false            # never submit a dictation by default
auto_send_in_conversation = true
confirm_send = true          # ask "ready to send?" after a dictated pause
confirm_s = 6.0              # how long the answer window stays open
max_answer_words = 90        # how much of a final answer is spoken
ack_sound = true
require_text_field = true
allow_apps = ["com.apple.Terminal", "com.googlecode.iterm2", "com.microsoft.VSCode", "com.todesktop.230313mzl4w4u92", "com.mitchellh.ghostty", "dev.warp.Warp-Stable", "com.github.wez.wezterm", "net.kovidgoyal.kitty", "org.alacritty", "co.zeit.hyper"]
```

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

[recall]
enabled = true
threshold = 0.30        # lexical overlap needed before speaking; raise for silence
max_age_days = 30.0     # older memories are ignored and pruned
cooldown_s = 90.0       # minimum gap between spoken recalls
recap_minutes = 45.0    # default window for Recap the last session

[voice]
# See Voice input for the full set; the menu edits [voice] enabled/conversation.
```

The menu writes voice, pace, presence-bed, banter, recall, and voice-input
changes back here. Other edits take effect when the app restarts.

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
- The per-project ledger stores your prompts, the agent's conclusions, and a
  short action phrase per tool call in `~/.reezyn/projects/` (mode `0700`,
  pruned after 30 days). That is local memory; delete the directory to forget a
  project.
- Hook payloads are minimized before they touch disk. Only the session id,
  transcript path, tool name, and description-relevant tool arguments are kept.
  Keys that look like secrets are redacted.
- The spool directory is `~/.reezyn/events` (mode `0700`).
- Spoken lines are written to `~/.reezyn/reezyn.log` for debugging. Delete the
  log or set `REEZYN_HOME` if that is not appropriate for your machine.
- Voice input uses Apple's on-device recognizer. Microphone audio is streamed
  into the recognizer and never written to disk; transcripts are shown in the
  menu bar and typed into the focused app, not logged.
- Voice input only probes for its wake word while a terminal-like text field is
  focused, and only types into apps on `voice.allow_apps`.
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

### Building the app

```bash
./packaging/build_app.sh
```

This renders the icon, builds `Reezyn.app` with PyInstaller, signs it with the
Developer ID Application identity and the hardened runtime, and writes
`dist/Reezyn.app` plus `dist/Reezyn-<version>.dmg`. Set `REEZYN_SIGN_IDENTITY`
to sign with another identity. The bundle is arm64, matching the interpreter it
is built with; there is no universal build yet.

A Developer ID signature alone is not enough for Gatekeeper on another Mac.
Supply notarization credentials to submit and staple the disk image:

```bash
# Profile stored with `xcrun notarytool store-credentials`
NOTARY_PROFILE=reezyn ./packaging/build_app.sh

# Or an App Store Connect API key
NOTARY_KEY=~/AuthKey_XXXX.p8 NOTARY_KEY_ID=XXXX NOTARY_ISSUER=... \
  ./packaging/build_app.sh
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
  voice/         wake word, dictation, on-device ASR, typing guard, session loop
  spool.py       cross-process event handoff
  installers.py  backed-up hook install/uninstall
packaging/       PyInstaller spec, icon, entitlements, build/sign script
```

## Known limitations

- `Reezyn.app` is Developer ID-signed but only Gatekeeper-approved once the
  disk image is notarized; a build without notarization credentials is blocked
  on other Macs.
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
- Wake-word accuracy is the recognizer's, not a trained model's: "Reezyn" is
  not an English word, so contextual strings and fuzzy matching cover guesses
  like "reason" and "resin", but a quiet room and a clear "Reezyn" work best.
- On-device recognition depends on the language model macOS has downloaded for
  the selected voice's locale. If speech is unavailable, the menu reports the
  error instead of falling back to a network recognizer.
- The conversational loop needs a turn-end signal: the `Stop` (Claude) or
  `AfterAgent` (Gemini) hook — reinstall the hooks after upgrading — or, for
  Codex and Freebuff, the end of their watched transcripts. Cursor and OpenCode
  are narrated but do not reopen the microphone yet.
- Voice input requires the three TCC grants above. The DMG carries the
  microphone and speech usage strings plus the audio-input entitlement, but
  Accessibility is always a manual switch.
- Audio device changes are noticed within about two seconds. A narration line
  that is mid-flight when the output switches is cut rather than moved, and an
  utterance in progress when the input switches is discarded.
- Bluetooth headsets can still sound thin while their microphone is in use: the
  system itself switches them to hands-free mode. Picking the built-in
  microphone (or another input) avoids that.
