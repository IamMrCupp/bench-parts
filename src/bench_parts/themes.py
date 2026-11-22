"""Custom themes: burnwright's theme files, read from a folder on the server.

The look is burnwright's and benchhud's: every colour on a page is one of the
``--bw-*`` custom properties in ``templates/base.html``, light by default and
dark under ``data-theme="dark"``. The Appearance choice in the footer picks
System, Light, Dark or one of these themes, and is kept in the browser.

A theme is one JSON file, the same format burnwright reads, so a file written
for burnwright drops straight in::

    {"name": "Amber terminal", "base": "dark", "colors": {"bg": "#120d02", ...}}

They live in ``$BENCH_PARTS_THEMES_DIR``, else a ``themes`` folder in the data
folder. Read on each page, so a new or edited file shows up on the next load.

**Colours and nothing else.** Names are checked against the list below and
values against a strict idea of a colour (hex, or ``rgb()`` / ``hsl()`` with
plain numbers), so a file can't reach the layout or load anything. The values
land in a page's JSON block and from there in ``style.setProperty``; this
check is what keeps them colours.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from bench_parts.settings import data_dir

THEMES_DIR_ENV = "BENCH_PARTS_THEMES_DIR"

#: Every colour a theme can set: burnwright's ``--bw-*`` properties, without
#: the prefix. The whole list, so a burnwright theme file reads here without
#: complaint; this app only paints with the first seventeen.
THEME_PROPERTIES = (
    "bg",
    "panel",
    "border",
    "fg",
    "muted",
    "control-bg",
    "control-border",
    "control-hover",
    "accent",
    "accent-soft",
    "danger",
    "danger-soft",
    "warning",
    "warning-soft",
    "warning-border",
    "ok",
    "ok-soft",
    "backdrop",
    "shadow",
    "hex-bg",
    "hex-header-bg",
    "hex-muted",
    "hex-edited-fg",
    "hex-edited-bg",
    "hex-selected-bg",
    "hex-cursor",
    "hex-cursor-fg",
    "hex-cursor-dim",
    "diff-changed-bg",
    "diff-changed-fg",
    "diff-extra-bg",
    "diff-extra-fg",
    "diff-missing-bg",
    "diff-missing-fg",
)

NAME_LIMIT = 60
VALUE_LIMIT = 64
#: A themes folder with more than this many files is somebody's mistake.
FILE_LIMIT = 50
SIZE_LIMIT = 64 * 1024

_HEX = re.compile(r"^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.IGNORECASE)
_NUMBER = r"(?:none|[-+]?(?:\d+\.?\d*|\.\d+)(?:%|deg|grad|rad|turn)?)"
_SEPARATOR = r"(?:\s*,\s*|\s*/\s*|\s+)"
_FUNCTION = re.compile(
    rf"^(?:rgb|hsl)a?\(\s*{_NUMBER}(?:{_SEPARATOR}{_NUMBER}){{2,3}}\s*\)$", re.IGNORECASE
)


@dataclass(frozen=True)
class Theme:
    """A theme file that was read and can be used."""

    #: The file's name, ``amber.json``: what the browser stores the choice by.
    id: str
    name: str
    base: str
    colors: dict[str, str] = field(default_factory=dict)
    #: What the file had that wasn't taken, in words, for whoever wrote it.
    ignored: tuple[str, ...] = ()

    def as_json(self) -> dict:
        return {"id": self.id, "name": self.name, "base": self.base, "colors": self.colors}


@dataclass(frozen=True)
class BrokenTheme:
    file: str
    reason: str


def is_color(value: object) -> bool:
    """Whether ``value`` is a colour a theme may use. Narrower than CSS on purpose."""
    if not isinstance(value, str) or len(value) > VALUE_LIMIT:
        return False
    text = value.strip()
    return bool(_HEX.match(text) or _FUNCTION.match(text))


def parse_theme(file: str, text: str) -> Theme | BrokenTheme:
    """One theme file: a usable theme, or why it isn't one."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        return BrokenTheme(file, f"it isn't valid JSON ({exc})")
    if not isinstance(data, dict):
        return BrokenTheme(file, "a theme is a JSON object: { … }")

    base = data.get("base")
    if base not in ("light", "dark"):
        return BrokenTheme(file, '"base" has to be "light" or "dark"')
    colors = data.get("colors", {})
    if not isinstance(colors, dict):
        return BrokenTheme(file, '"colors" has to be an object: { "bg": "#101418", … }')

    ignored: list[str] = []
    name = data.get("name")
    shown = re.sub(r"\.json$", "", file, flags=re.IGNORECASE) or file
    if isinstance(name, str) and name.strip():
        shown = name.strip()[:NAME_LIMIT]
    elif name is not None:
        ignored.append("\"name\" isn't text, so the file's name is used")
    for key in data:
        if key not in ("name", "base", "colors"):
            ignored.append(f'"{key}" isn\'t part of a theme')

    taken: dict[str, str] = {}
    for key, value in colors.items():
        prop = key.removeprefix("--bw-") if isinstance(key, str) else None
        if prop not in THEME_PROPERTIES:
            ignored.append(f'"{key}" isn\'t a theme colour')
        elif not is_color(value):
            ignored.append(f'"{key}" isn\'t a colour a theme can use')
        else:
            taken[prop] = value.strip()

    return Theme(id=file, name=shown, base=base, colors=taken, ignored=tuple(ignored))


def themes_dir(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(THEMES_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return data_dir(env) / "themes"


def load_themes(folder: Path) -> tuple[list[Theme], list[BrokenTheme]]:
    """Every ``*.json`` in ``folder``, sorted by name. A missing folder is no themes."""
    try:
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".json")
    except OSError:
        return [], []
    themes: list[Theme] = []
    broken: list[BrokenTheme] = []
    for path in files[:FILE_LIMIT]:
        try:
            if path.stat().st_size > SIZE_LIMIT:
                broken.append(BrokenTheme(path.name, "it's too big to be a theme"))
                continue
            text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            broken.append(BrokenTheme(path.name, f"it couldn't be read ({exc.__class__.__name__})"))
            continue
        parsed = parse_theme(path.name, text)
        (themes if isinstance(parsed, Theme) else broken).append(parsed)
    themes.sort(key=lambda t: t.name.lower())
    return themes, broken
