"""``python -m bench_parts`` (and the packaged app): the tray app, or a command."""

import sys

from bench_parts.entry import main

raise SystemExit(main(sys.argv[1:]))
