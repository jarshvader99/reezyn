"""Entry point for the bundled Reezyn.app.

The bundle is the interpreter for every Reezyn command, so the same binary
starts the menu-bar app (no arguments) and runs `hook`, `doctor`, and the rest
of the CLI subcommands the agents call.
"""

from reezyn.cli import main

raise SystemExit(main())
