"""Render a drawer label locally and hand it to a label-printer command.

The app pod has no Bluetooth or USB, so printing to the shop's NIIMBOT (or
any other labeler) belongs to this small bridge, run on a machine near the
printer::

    bench-parts-label 54 --print-cmd "niimprintx print -m b1 -i {png}"
    bench-parts-label 54 --out label.png        # render-only, any printer later

It is a *client* of the parts API, exactly like benchhud: it fetches the part
over HTTP with the bearer token (``--base-url``/``--token`` or the
``BENCH_PARTS_URL`` / ``BENCH_PARTS_API_TOKEN`` env vars) and composes the
same label the web page shows — QR of the part's URL on the left, description
/ MPN / category · location on the right.

Two deliberate boundaries:

* **Pillow is an extra** (``bench-parts[label]``) — the server image never
  carries it.
* **The printer CLI is a subprocess, not a dependency.** The workable NIIMBOT
  libraries are git-only (nothing real on PyPI), so the bridge shells out to
  whatever ``--print-cmd`` names, with ``{png}`` substituted by the rendered
  file. Swapping labelers is a config change, not a code change.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import segno


class BridgeError(Exception):
    """Anything that should reach the operator as a message, not a traceback."""


Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_part(
    base_url: str,
    token: str,
    part_id: int,
    *,
    opener: Opener = _default_opener,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """GET the part JSON, or raise :class:`BridgeError` with a usable message."""
    url = base_url.rstrip("/") + f"/api/parts/{part_id}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with opener(request, timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"bench-parts answered HTTP {exc.code} for part {part_id}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise BridgeError(f"bench-parts unreachable at {base_url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BridgeError("bench-parts sent an unexpected response")
    return payload


def _font(size: int):
    """A truetype font when the OS has one, else Pillow's scalable default."""
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # the bench box (Ubuntu)
        "/System/Library/Fonts/Helvetica.ttc",  # the dev Mac
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render_label(part: dict[str, Any], *, width: int, height: int) -> bytes:
    """Compose the label as a 1-bit-friendly PNG: QR left, text right.

    Mirrors the web label page's layout so a drawer looks the same whichever
    way it was printed. Pure black on white — thermal printers dither greys
    into mud.
    """
    from PIL import Image, ImageDraw

    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)

    # QR on the left at ~70% of label height — at B1 scale that's ~20 mm,
    # comfortably scannable, and it leaves the text room to be readable.
    margin = max(4, height // 24)
    qr_size = min(height - 2 * margin, int(height * 0.7))
    qr = segno.make(part.get("url") or f"part {part['id']}", error="m")
    buf = io.BytesIO()
    scale = max(1, qr_size // (qr.symbol_size(border=0)[0]))
    qr.save(buf, kind="png", scale=scale, border=0)
    qr_image = Image.open(buf).convert("L")
    image.paste(qr_image, (margin, (height - qr_image.height) // 2))

    text_x = margin + qr_size + margin
    text_width = width - text_x - margin
    big, small = _font(height // 8), _font(height // 11)

    y = margin
    for line in _clamp(
        _wrap(draw, part.get("description", ""), big, text_width), 3, draw, big, text_width
    ):
        draw.text((text_x, y), line, font=big, fill=0)
        y += int(height // 8 * 1.25)
    if part.get("mpn"):
        draw.text((text_x, y), _ellipsize(draw, part["mpn"], small, text_width), font=small, fill=0)
        y += int(height // 11 * 1.4)
    meta = " · ".join(x for x in (part.get("category_label"), part.get("location")) if x)
    if meta:
        for line in _clamp(_wrap(draw, meta, small, text_width), 2, draw, small, text_width):
            draw.text((text_x, y), line, font=small, fill=0)
            y += int(height // 11 * 1.4)

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _ellipsize(draw, text: str, font, max_width: int) -> str:
    """Trim to fit, with an ellipsis — a clipped label lies about the part."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _clamp(lines: list[str], limit: int, draw, font, max_width: int) -> list[str]:
    """At most ``limit`` lines; the last one carries an ellipsis when cut."""
    if len(lines) <= limit:
        return [_ellipsize(draw, line, font, max_width) for line in lines]
    kept = lines[: limit - 1]
    return [_ellipsize(draw, line, font, max_width) for line in kept] + [
        _ellipsize(draw, " ".join(lines[limit - 1 :]), font, max_width)
    ]


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    """Greedy word wrap by rendered width."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench-parts-label",
        description="Render a bench-parts drawer label and print it or save it.",
    )
    parser.add_argument("part_id", type=int)
    parser.add_argument("--base-url", default=os.environ.get("BENCH_PARTS_URL", ""))
    parser.add_argument("--token", default=os.environ.get("BENCH_PARTS_API_TOKEN", ""))
    parser.add_argument("--width", type=int, default=384, help="label width in px")
    parser.add_argument("--height", type=int, default=240, help="label height in px")
    parser.add_argument("--out", help="write the PNG here instead of printing")
    parser.add_argument(
        "--print-cmd",
        default=os.environ.get("BENCH_PARTS_LABEL_CMD", ""),
        help="printer command; {png} is replaced by the rendered file path",
    )
    args = parser.parse_args(argv)

    if not args.base_url or not args.token:
        print("set --base-url/--token or BENCH_PARTS_URL/BENCH_PARTS_API_TOKEN", file=sys.stderr)
        return 2
    if not args.out and not args.print_cmd:
        print("nothing to do: pass --out or --print-cmd", file=sys.stderr)
        return 2

    try:
        part = fetch_part(args.base_url, args.token, args.part_id)
        png = render_label(part, width=args.width, height=args.height)
    except BridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "wb") as fh:
            fh.write(png)
        print(f"wrote {args.out}")
        return 0

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(png)
        path = fh.name
    command = [path if piece == "{png}" else piece for piece in shlex.split(args.print_cmd)]
    if path not in command:
        command.append(path)  # no {png} placeholder → append the file
    try:
        subprocess.run(command, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"printer command failed: {exc}", file=sys.stderr)
        return 1
    finally:
        os.unlink(path)
    print(f"printed part {args.part_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
