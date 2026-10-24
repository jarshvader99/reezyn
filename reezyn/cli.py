"""Reezyn command line entry points."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import __version__
from .config import app_home, load_config
from .events import Event
from .installers import (
    hook_command,
    install_hooks,
    is_installed,
    settings_path,
    uninstall_hooks,
)
from .runtime import is_running
from .sanitize import sanitize_hook_payload
from .spool import Spool


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "start"

    if command == "start":
        return cmd_start(args)
    if command == "hook":
        return cmd_hook(args)
    if command == "emit":
        return cmd_emit(args)
    if command == "ingest":
        return cmd_ingest(args)
    if command == "setup":
        return cmd_setup(args)
    if command == "install":
        return cmd_install(args)
    if command == "doctor":
        return cmd_doctor(args)
    if command == "simulate":
        return cmd_simulate(args)
    if command == "version":
        print(__version__)
        return 0
    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reezyn",
        description="A calm, local narrator for coding-agent reasoning.",
    )
    sub = parser.add_subparsers(dest="command")
    parser.set_defaults(voice=None, speed=None)

    start = sub.add_parser("start", help="Run the macOS menu-bar app")
    start.add_argument("--voice", help="Voice id, e.g. af_heart, em_alex, or ff_siwis")
    start.add_argument("--speed", type=float, help="Speech speed, 0.72–1.18")

    hook = sub.add_parser("hook", help="Receive one agent hook event on stdin")
    hook.add_argument("agent", choices=["claude", "gemini"])

    emit = sub.add_parser("emit", help="Send one generic agent event")
    emit.add_argument("--agent", default="generic")
    emit.add_argument("--session", default="")
    emit.add_argument("--text", default="", help="Reasoning excerpt")
    emit.add_argument("--tool", default="", help="Tool name")
    emit.add_argument("--input", default="{}", help="Tool input JSON")
    emit.add_argument("--kind", choices=["reasoning", "tool"], default=None)

    ingest = sub.add_parser("ingest", help="Read generic events as JSONL on stdin")
    ingest.add_argument("--agent", default="generic")

    setup = sub.add_parser("setup", help="Download the local Kokoro voice model")
    setup.add_argument("--model", choices=["int8", "fp16"], default="int8")

    install = sub.add_parser("install", help="Install or remove agent hooks")
    install.add_argument("agent", choices=["claude", "gemini"])
    install.add_argument("--uninstall", action="store_true")
    install.add_argument("--dry-run", action="store_true")

    doctor = sub.add_parser("doctor", help="Check local setup and integrations")
    doctor.add_argument("--json", action="store_true")

    simulate = sub.add_parser("simulate", help="Send a demo session to a running app")
    simulate.add_argument("--pause", type=float, default=0.45)

    sub.add_parser("version", help="Print the version")
    return parser


def cmd_start(args: argparse.Namespace) -> int:
    home = app_home()
    home.mkdir(parents=True, exist_ok=True)
    if is_running(home):
        print("Reezyn is already running in the menu bar.")
        return 0
    _configure_logging(home)
    config = load_config(home)
    if args.voice:
        from .config import VOICES, with_voice

        if args.voice in VOICES:
            config = with_voice(config, args.voice)
            config.save(home)
        else:
            print(f"Unknown voice {args.voice!r}. Choose from: {', '.join(VOICES)}", file=sys.stderr)
            return 2
    if args.speed is not None:
        from .config import with_speed

        config = with_speed(config, max(0.72, min(1.18, args.speed)))
        config.save(home)

    try:
        from .menu import run_menu

        run_menu(config=config, home=home)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logging.getLogger("reezyn").exception("Menu app failed")
        print(f"Reezyn could not start: {exc}", file=sys.stderr)
        print(f"Details: {home / 'reezyn.log'}", file=sys.stderr)
        return 1
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    """Never interfere with the agent, even if Reezyn is having a bad day."""

    gemini = args.agent == "gemini"
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not an object")
        clean = sanitize_hook_payload(args.agent, payload)
        Spool(app_home()).emit(args.agent, clean)
    except Exception as exc:
        _hook_log(args.agent, f"{type(exc).__name__}: {exc}")
    if gemini:
        # Gemini requires exactly one JSON object on stdout.
        print(json.dumps({"suppressOutput": True}))
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    try:
        tool_input = json.loads(args.input) if args.input else {}
    except json.JSONDecodeError as exc:
        print(f"Invalid --input JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(tool_input, dict):
        print("--input must be a JSON object", file=sys.stderr)
        return 2

    kind = args.kind
    if kind is None:
        kind = "tool" if args.tool else "reasoning"
    if kind == "tool" and not args.tool:
        print("A tool event needs --tool", file=sys.stderr)
        return 2
    if kind == "reasoning" and not args.text:
        print("A reasoning event needs --text", file=sys.stderr)
        return 2

    event = Event(
        agent=args.agent,
        kind=kind,
        text=args.text,
        session_id=args.session,
        tool_name=args.tool,
        tool_input=tool_input,
    )
    Spool(app_home()).emit("generic", event.to_dict())
    print("Event sent to the running Reezyn app." if _app_likely_running() else "Event queued; start Reezyn to hear it.")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    spool = Spool(app_home())
    count = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        value.setdefault("agent", args.agent)
        event = Event.from_dict(value)
        spool.emit("generic", event.to_dict())
        count += 1
    print(f"Queued {count} event(s).")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from .tts import download_models

    home = app_home()
    last = 0.0

    def progress(label: str, done: int, total: int) -> None:
        nonlocal last
        now = time.time()
        if now - last < 1.0 and done != total:
            return
        last = now
        pct = f" {done / total:5.1%}" if total else ""
        print(f"  {label}: {done / 1_000_000:6.1f} MB{pct}", flush=True)

    print(f"Downloading Kokoro {args.model} into {home / 'models'}")
    try:
        paths = download_models(home, args.model, progress)
    except Exception as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    if not paths.ready:
        print("Download finished but files are missing.", file=sys.stderr)
        return 1
    config = load_config(home)
    config.speech.model = args.model
    config.save(home)
    print("Voice model ready.")
    print("Next: `reezyn` to start the menu-bar app.")
    if not is_installed("claude") and not is_installed("gemini"):
        print("Then install hooks with `reezyn install claude` and/or `reezyn install gemini`.")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    try:
        if args.uninstall:
            result = uninstall_hooks(args.agent, dry_run=args.dry_run)
            if result.changed:
                print(f"Removed Reezyn's {args.agent} hook from {result.path}")
            else:
                print(f"No Reezyn {args.agent} hook found in {result.path}")
            return 0

        result = install_hooks(args.agent, dry_run=args.dry_run)
        if args.dry_run:
            print(json.dumps(_hook_snippet(result.settings, args.agent), indent=2, ensure_ascii=False))
            return 0
        if result.changed:
            print(f"Installed Reezyn's {args.agent} hook in {result.path}")
            if result.backup:
                print(f"Backup: {result.backup}")
            print(f"Command: {hook_command(args.agent)}")
            print("Start a new agent session to activate it.")
        else:
            print(f"Reezyn's {args.agent} hook is already installed in {result.path}")
        return 0
    except Exception as exc:
        print(f"Hook install failed: {exc}", file=sys.stderr)
        return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    from .adapters.codex import codex_home
    from .adapters.cursor import cursor_projects
    from .adapters.freebuff import freebuff_projects
    from .adapters.opencode import accessible as opencode_accessible
    from .adapters.opencode import opencode_db_path
    from .languages import pack_for_voice
    from .tts import model_paths

    home = app_home()
    config = load_config(home)
    language = pack_for_voice(config.speech.voice)
    paths = model_paths(home, config.speech.model)
    audio = _audio_status()
    codex_root = codex_home() / "sessions"
    codex_count = _count_codex_sessions(codex_root)
    cursor_root = cursor_projects()
    cursor_count = _count_cursor_transcripts(cursor_root)
    freebuff_root = freebuff_projects()
    freebuff_count = _count_freebuff_chats(freebuff_root)
    opencode_db = opencode_db_path()
    spool_count = len(list((home / "events").glob("*.json"))) if (home / "events").exists() else 0
    running = is_running(home)

    report: dict[str, Any] = {
        "version": __version__,
        "running": running,
        "python": sys.executable,
        "home": str(home),
        "config": str(home / "config.toml"),
        "voice": config.speech.voice,
        "language": language.code,
        "speed": config.speech.speed,
        "model": config.speech.model,
        "model_ready": paths.ready,
        "model_file": str(paths.model),
        "voices_file": str(paths.voices),
        "audio": audio,
        "claude_hook": {
            "installed": is_installed("claude"),
            "settings": str(settings_path("claude")),
        },
        "gemini_hook": {
            "installed": is_installed("gemini"),
            "settings": str(settings_path("gemini")),
        },
        "codex": {
            "sessions": str(codex_root),
            "rollouts": codex_count,
            "watching": config.agents.codex,
        },
        "cursor": {
            "projects": str(cursor_root),
            "transcripts": cursor_count,
            "watching": config.agents.cursor,
        },
        "opencode": {
            "database": str(opencode_db),
            "present": opencode_accessible(opencode_db),
            "watching": config.agents.opencode,
        },
        "freebuff": {
            "projects": str(freebuff_root),
            "chats": freebuff_count,
            "watching": config.agents.freebuff,
        },
        "ambience": {
            "enabled": config.ambience.enabled,
            "volume": config.ambience.volume,
        },
        "pending_events": spool_count,
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Reezyn {__version__} ({'running' if running else 'not running'})")
    print(f"  Python       {report['python']}")
    print(f"  State        {home}")
    print(f"  Config       {home / 'config.toml'}")
    print(f"  Voice        {config.speech.voice} ({language.name}) at {config.speech.speed:.2f}×")
    print(f"  Model        {'ready' if paths.ready else 'missing'} ({paths.model.name})")
    print(f"  Audio        {audio}")
    print(f"  Claude hook  {'installed' if report['claude_hook']['installed'] else 'not installed'}  ({settings_path('claude')})")
    print(f"  Gemini hook  {'installed' if report['gemini_hook']['installed'] else 'not installed'}  ({settings_path('gemini')})")
    print(f"  Codex        {codex_count} rollout file(s) under {codex_root}")
    print(f"  Cursor       {cursor_count} transcript file(s) under {cursor_root}")
    print(f"  Freebuff     {freebuff_count} chat snapshot(s) under {freebuff_root}")
    print(
        "  OpenCode     "
        + ("database found" if report["opencode"]["present"] else "database not found")
        + f"  ({opencode_db})"
    )
    print(
        "  Presence bed "
        + ("on" if config.ambience.enabled else "off")
        + f" at {config.ambience.volume:.2f}"
    )
    print(
        "  Lull banter  "
        + (
            f"on after {config.banter.after_s:.0f}s, {config.banter.max_lines} line(s)"
            if config.banter.enabled
            else "off"
        )
    )
    print(f"  Spool        {spool_count} pending event(s)")
    if not paths.ready:
        print("\nRun `reezyn setup` to install the voice model.")
    if not report["claude_hook"]["installed"] and not report["gemini_hook"]["installed"]:
        print("Install agent hooks with `reezyn install claude` or `reezyn install gemini`.")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    if not _app_likely_running():
        print("Reezyn does not appear to be running. Start `reezyn` first.", file=sys.stderr)
        return 1
    spool = Spool(app_home())
    events = _demo_events()
    for index, event in enumerate(events):
        spool.emit("generic", event.to_dict())
        time.sleep(args.pause if index % 3 else 1.0)
    print(f"Sent {len(events)} demo events.")
    return 0


def _demo_events() -> list[Event]:
    return [
        Event(
            agent="simulation",
            kind="reasoning",
            text=(
                "I'll start by reading the session middleware, since that's where "
                "the refresh token gets validated."
            ),
        ),
        Event(agent="simulation", kind="tool", tool_name="Read", tool_input={"path": "src/auth/session.ts"}),
        Event(agent="simulation", kind="tool", tool_name="Grep", tool_input={"pattern": "refreshToken"}),
        Event(
            agent="simulation",
            kind="reasoning",
            text=(
                "The refresh path is rotating the cookie but not invalidating the "
                "old token. I'll check the test expectations before editing."
            ),
        ),
        Event(agent="simulation", kind="tool", tool_name="Bash", tool_input={"command": "npm test -- auth"}),
        Event(
            agent="simulation",
            kind="reasoning",
            text=(
                "The tests confirm the old token is accepted. I'll update the "
                "validation to compare token versions."
            ),
        ),
        Event(agent="simulation", kind="tool", tool_name="Edit", tool_input={"file_path": "src/auth/session.ts"}),
        Event(
            agent="simulation",
            kind="reasoning",
            text="Now I'll rerun the auth suite to make sure refresh works and the stale token is rejected.",
        ),
        Event(agent="simulation", kind="tool", tool_name="Bash", tool_input={"command": "npm test -- auth"}),
    ]


def _configure_logging(home: Path) -> None:
    handler = RotatingFileHandler(
        home / "reezyn.log",
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger("reezyn")
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _hook_log(agent: str, message: str) -> None:
    # Hooks must not write to stdout unless the protocol requires it.
    try:
        home = app_home()
        home.mkdir(parents=True, exist_ok=True)
        with (home / "hook-errors.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {agent}: {message}\n")
    except OSError:
        pass


def _app_likely_running() -> bool:
    return is_running(app_home())


def _hook_snippet(settings: dict[str, Any], agent: str) -> dict[str, Any]:
    event = "PreToolUse" if agent == "claude" else "BeforeTool"
    hooks = settings.get("hooks")
    groups = hooks.get(event) if isinstance(hooks, dict) else None
    ours = []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                continue
            if any(
                isinstance(handler, dict)
                and isinstance(handler.get("command"), str)
                and "reezyn" in handler["command"]
                and f"hook {agent}" in handler["command"]
                for handler in handlers
            ):
                ours.append(group)
    return {"hooks": {event: ours}}


def _audio_status() -> str:
    try:
        import sounddevice as sd

        device = sd.query_devices(kind="output")
        return f"sounddevice → {device['name']}"
    except Exception:
        return "afplay fallback" if shutil.which("afplay") else "unavailable"


def _count_codex_sessions(root: Path) -> int:
    if not root.exists():
        return 0
    count = 0
    try:
        for _ in root.rglob("rollout-*.jsonl"):
            count += 1
            if count >= 999:
                break
    except OSError:
        pass
    return count


def _count_cursor_transcripts(root: Path) -> int:
    if not root.exists():
        return 0
    count = 0
    try:
        for path in root.rglob("*.jsonl"):
            if "agent-transcripts" not in path.parts:
                continue
            count += 1
            if count >= 999:
                break
    except OSError:
        pass
    return count


def _count_freebuff_chats(root: Path) -> int:
    if not root.exists():
        return 0
    count = 0
    try:
        for path in root.glob("*/chats/*/chat-messages.json"):
            if path.is_file():
                count += 1
                if count >= 999:
                    break
    except OSError:
        pass
    return count


if __name__ == "__main__":
    raise SystemExit(main())
