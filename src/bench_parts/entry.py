"""What the packaged app runs: the tray app, or the command line.

A double-click passes no arguments and gets the menu-bar / tray app. With a
command it's the same ``bench-parts`` command line the other installs have, so the
packaged app can be set up from a script (bench-setup does this on macOS):

    "/Applications/bench parts.app/Contents/MacOS/bench parts" set-password --stdin
    "/Applications/bench parts.app/Contents/MacOS/bench parts" new-token --print

Qt is only imported on the tray path, so the commands run with no display.
"""

from __future__ import annotations

from collections.abc import Sequence

#: First arguments that mean "the command line". Anything else, and nothing at
#: all, is the tray app: a launcher can pass arguments of its own.
COMMANDS = frozenset({"serve", "set-password", "new-token", "paths", "tray"})
_OPTIONS = frozenset({"-h", "--help", "--data-dir"})


def is_command_line(argv: Sequence[str]) -> bool:
    return bool(argv) and (argv[0] in COMMANDS or argv[0] in _OPTIONS)


def main(argv: Sequence[str]) -> int:
    if is_command_line(argv):
        from bench_parts.cli import main as cli_main

        return cli_main(list(argv))
    from bench_parts.tray import main as tray_main

    return tray_main()
