"""Label bridge — render a drawer label locally, hand it to a printer command.

Hardware-free: HTTP goes through an injected opener, the printer command is a
spy, and rendering is checked structurally (size, ink, content) rather than
pixel-perfect.
"""

import io
import json
import urllib.error

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from bench_parts import label_bridge  # noqa: E402

_PART = {
    "id": 7,
    "description": "Dual N-ch MOSFET",
    "mpn": "IRF7910",
    "category_label": "MOSFET",
    "location": "Drawer A3",
    "url": "http://parts:8000/parts/7",
}


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _opener(payload: dict):
    def _open(request, _timeout):
        assert request.get_header("Authorization") == "Bearer tok"
        return _FakeResponse(json.dumps(payload))

    return _open


def test_fetch_part_hits_the_api_with_the_token() -> None:
    part = label_bridge.fetch_part("http://parts:8000", "tok", 7, opener=_opener(_PART))

    assert part["mpn"] == "IRF7910"


def test_fetch_part_raises_a_clear_error_on_http_failure() -> None:
    def _refuse(request, _timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    with pytest.raises(label_bridge.BridgeError, match="404"):
        label_bridge.fetch_part("http://parts:8000", "tok", 999, opener=_refuse)


def test_render_label_is_the_requested_size_and_has_ink() -> None:
    png = label_bridge.render_label(_PART, width=384, height=240)

    image = Image.open(io.BytesIO(png))
    assert image.size == (384, 240)
    grey = image.convert("L")
    extrema = grey.getextrema()
    assert extrema[0] < 64, "no dark pixels — QR or text missing"
    assert extrema[1] > 192, "no light pixels — background missing"


def test_render_label_survives_a_part_with_no_mpn_or_location() -> None:
    bare = {"id": 9, "description": "Salvaged inductor", "url": "http://parts:8000/parts/9"}

    png = label_bridge.render_label(bare, width=384, height=240)

    assert Image.open(io.BytesIO(png)).size == (384, 240)


def test_qr_occupies_the_left_of_the_label() -> None:
    png = label_bridge.render_label(_PART, width=384, height=240)

    grey = Image.open(io.BytesIO(png)).convert("L")
    left = grey.crop((0, 0, 240, 240))
    assert left.getextrema()[0] < 64, "left side blank — QR not drawn"


def test_main_writes_a_png_in_render_only_mode(tmp_path, monkeypatch) -> None:
    out = tmp_path / "label.png"
    monkeypatch.setattr(label_bridge, "fetch_part", lambda *a, **k: dict(_PART))

    rc = label_bridge.main(
        ["7", "--base-url", "http://parts:8000", "--token", "tok", "--out", str(out)]
    )

    assert rc == 0
    assert Image.open(out).size == (384, 240)


def test_main_invokes_the_print_command_with_the_png_path(tmp_path, monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(label_bridge, "fetch_part", lambda *a, **k: dict(_PART))
    monkeypatch.setattr(
        label_bridge.subprocess, "run", lambda cmd, check: calls.append(cmd) or None
    )

    rc = label_bridge.main(
        [
            "7",
            "--base-url",
            "http://parts:8000",
            "--token",
            "tok",
            "--print-cmd",
            "niimprintx print -m b1 -i {png}",
        ]
    )

    assert rc == 0
    assert len(calls) == 1
    assert calls[0][:4] == ["niimprintx", "print", "-m", "b1"]
    assert calls[0][-1].endswith(".png")  # {png} substituted with the rendered file


def test_main_requires_out_or_print_cmd(monkeypatch, capsys) -> None:
    monkeypatch.setattr(label_bridge, "fetch_part", lambda *a, **k: dict(_PART))

    rc = label_bridge.main(["7", "--base-url", "http://parts:8000", "--token", "tok"])

    assert rc == 2
    assert "--out or --print-cmd" in capsys.readouterr().err


def test_main_reads_env_for_base_url_and_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BENCH_PARTS_URL", "http://parts:8000")
    monkeypatch.setenv("BENCH_PARTS_API_TOKEN", "tok")
    seen: dict = {}

    def spy(base_url, token, part_id, **kw):
        seen.update(base_url=base_url, token=token)
        return dict(_PART)

    monkeypatch.setattr(label_bridge, "fetch_part", spy)
    out = tmp_path / "l.png"

    rc = label_bridge.main(["7", "--out", str(out)])

    assert rc == 0
    assert seen == {"base_url": "http://parts:8000", "token": "tok"}


def test_d110_preset_renders_narrow_tape_size(tmp_path, monkeypatch) -> None:
    out = tmp_path / "narrow.png"
    monkeypatch.setattr(label_bridge, "fetch_part", lambda *a, **k: dict(_PART))

    rc = label_bridge.main(
        ["7", "--base-url", "u", "--token", "t", "--preset", "d110", "--out", str(out)]
    )

    assert rc == 0
    assert Image.open(out).size == (320, 96)


def test_explicit_size_overrides_the_preset(tmp_path, monkeypatch) -> None:
    out = tmp_path / "custom.png"
    monkeypatch.setattr(label_bridge, "fetch_part", lambda *a, **k: dict(_PART))

    rc = label_bridge.main(
        [
            "7",
            "--base-url",
            "u",
            "--token",
            "t",
            "--preset",
            "d110",
            "--width",
            "300",
            "--out",
            str(out),
        ]
    )

    assert rc == 0
    assert Image.open(out).size == (300, 96)


def test_qr_uses_full_height_on_short_labels() -> None:
    png = label_bridge.render_label(_PART, width=320, height=96)

    grey = Image.open(io.BytesIO(png)).convert("L")
    # bottom-left corner would be blank at the 70% ratio; full-height QR inks it
    corner = grey.crop((6, 70, 60, 90))
    assert corner.getextrema()[0] < 64, "QR did not use the full height on narrow tape"
