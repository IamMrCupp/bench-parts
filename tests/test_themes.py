"""The bench look and the Appearance choice.

The look itself is checked in a browser; what's held here is the part that
can break quietly: a theme file reaching a page as anything but colours, and a
template painting a colour that isn't one of the --bw-* properties.
"""

import html
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bench_parts.app import app
from bench_parts.themes import (
    THEME_PROPERTIES,
    BrokenTheme,
    Theme,
    is_color,
    load_themes,
    parse_theme,
)

TEMPLATES = Path(__file__).parent.parent / "src" / "bench_parts" / "templates"


@pytest.mark.parametrize(
    "value",
    [
        "#fff",
        "#ffff",
        "#120d02",
        "#120d02cc",
        "rgb(1, 2, 3)",
        "rgba(0, 0, 0, 0.45)",
        "hsl(40 100% 50%)",
    ],
)
def test_colours_a_theme_may_use(value):
    assert is_color(value)


@pytest.mark.parametrize(
    "value",
    [
        "red",
        "var(--bw-bg)",
        "url(https://example.com/x.png)",
        "#12345",
        "rgb(1, 2, 3); background: red",
        "expression(alert(1))",
        "#fff</script><script>alert(1)</script>",
        12,
        None,
    ],
)
def test_anything_else_is_not_a_colour(value):
    assert not is_color(value)


def test_a_burnwright_theme_file_reads_as_is():
    text = json.dumps(
        {
            "name": "Amber terminal",
            "base": "dark",
            "colors": {"bg": "#120d02", "accent": "#ffb000", "hex-bg": "#150f02"},
        }
    )
    theme = parse_theme("amber.json", text)
    assert isinstance(theme, Theme)
    assert theme.name == "Amber terminal"
    assert theme.base == "dark"
    assert theme.colors == {"bg": "#120d02", "accent": "#ffb000", "hex-bg": "#150f02"}
    assert theme.ignored == ()


def test_bad_entries_are_dropped_and_named():
    theme = parse_theme(
        "x.json",
        json.dumps(
            {"base": "light", "colors": {"bg": "url(x)", "font": "#fff", "--bw-fg": "#000"}, "x": 1}
        ),
    )
    assert isinstance(theme, Theme)
    assert theme.name == "x"
    assert theme.colors == {"fg": "#000"}
    assert len(theme.ignored) == 3


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", '{"colors": {}}', '{"base": "sepia"}', '{"base": "dark", "colors": []}'],
)
def test_a_file_that_is_not_a_theme_says_why(text):
    assert isinstance(parse_theme("bad.json", text), BrokenTheme)


def test_load_reads_only_json_and_tolerates_a_missing_folder(tmp_path):
    assert load_themes(tmp_path / "nowhere") == ([], [])
    (tmp_path / "b.json").write_text('{"name": "Bravo", "base": "light"}')
    (tmp_path / "a.json").write_text('{"name": "Alpha", "base": "dark"}')
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "notes.txt").write_text("hello")
    themes, broken = load_themes(tmp_path)
    assert [t.name for t in themes] == ["Alpha", "Bravo"]
    assert [b.file for b in broken] == ["broken.json"]


def test_themes_reach_the_sign_in_page_as_json(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_PARTS_THEMES_DIR", str(tmp_path))
    (tmp_path / "amber.json").write_text(
        json.dumps({"name": "Amber </script>", "base": "dark", "colors": {"bg": "#120d02"}})
    )
    (tmp_path / "broken.json").write_text("{")
    page = TestClient(app).get("/login").text

    block = re.search(r"<html lang=\"en\" data-themes='([^']*)'>", page)
    assert block, "the themes attribute is missing"
    assert json.loads(html.unescape(block.group(1))) == [
        {"id": "amber.json", "name": "Amber </script>", "base": "dark", "colors": {"bg": "#120d02"}}
    ]
    # The name can't end the attribute or open a tag.
    assert "Amber </script>" not in page
    # Which files are broken, and where they are, is for someone signed in.
    assert "broken.json" not in page
    assert str(tmp_path) not in page


def test_every_page_carries_the_look_and_the_choice():
    client = TestClient(app)
    page = client.get("/login").text
    assert "--bw-accent" in page
    assert 'id="appearance"' in page
    assert ':root[data-theme="dark"]' in page
    # External, so the CSP's script-src 'self' still holds with no inline script.
    assert '<script src="/static/theme.js"></script>' in page
    script = client.get("/static/theme.js")
    assert script.status_code == 200
    assert "data-theme" in script.text


def test_the_stylesheet_names_every_colour_it_uses_from_the_palette():
    """Colours are --bw-* properties, defined once: nothing hard-coded below them."""
    base = (TEMPLATES / "base.html").read_text()
    css = base.split("<style>", 1)[1].split("</style>", 1)[0]
    rules = css.split("/* palette: aliases onto the --bw-* set */", 1)[1]
    literal = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", rules)
    assert literal == [], f"hard-coded colours in base.html rules: {literal}"
    used = set(re.findall(r"var\(--bw-([a-z-]+)\)", css))
    assert used <= set(THEME_PROPERTIES)
