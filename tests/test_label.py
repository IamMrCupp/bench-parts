"""Printable QR label — scan a drawer, land on the part."""


def _mk(store, **kw):
    defaults = {
        "description": "Dual MOSFET",
        "category": "mosfet",
        "mpn": "IRF7910",
        "location": "A3",
    }
    defaults.update(kw)
    return store.create_part(**defaults)


def test_label_page_shows_the_part_and_a_qr(client, store) -> None:
    part = _mk(store)

    r = client.get(f"/parts/{part.id}/label")

    assert r.status_code == 200
    assert "Dual MOSFET" in r.text
    assert "IRF7910" in r.text
    assert "A3" in r.text
    assert "<svg" in r.text  # the QR, rendered server-side


def test_label_qr_encodes_the_part_url(client, store, monkeypatch) -> None:
    import bench_parts.app as app_module

    part = _mk(store)
    seen = {}
    real = app_module.qr_svg

    def spy(data: str) -> str:
        seen["data"] = data
        return real(data)

    monkeypatch.setattr(app_module, "qr_svg", spy)

    client.get(f"/parts/{part.id}/label")

    assert seen["data"].endswith(f"/parts/{part.id}")


def test_label_handles_a_part_with_no_mpn_or_location(client, store) -> None:
    part = store.create_part(description="Salvaged inductor", category="inductor")

    r = client.get(f"/parts/{part.id}/label")

    assert r.status_code == 200
    assert "Salvaged inductor" in r.text


def test_label_unknown_part_is_404(client) -> None:
    assert client.get("/parts/9999/label").status_code == 404


def test_label_is_gated(anonymous) -> None:
    assert anonymous.get("/parts/1/label", follow_redirects=False).status_code == 303


def test_detail_page_links_to_the_label(client, store) -> None:
    part = _mk(store)

    assert f'href="/parts/{part.id}/label"' in client.get(f"/parts/{part.id}").text


def test_label_page_has_no_inline_script_handlers(client, store) -> None:
    """CSP is `script-src 'self'` — an onclick would render but never fire."""
    part = _mk(store)

    assert "onclick" not in client.get(f"/parts/{part.id}/label").text
