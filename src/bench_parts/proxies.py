"""Which reverse proxies are believed about who the client is.

Failed logins are counted per client address. Behind a reverse proxy every
browser arrives from the proxy, so one person's wrong guesses lock out the
whole bench. ``BENCH_PARTS_TRUSTED_PROXIES`` names the proxies whose
``X-Forwarded-For`` and ``X-Forwarded-Proto`` the server may believe.

Both ways of starting the server (``serve`` and the tray app) take their
options from here, so they can't disagree.
"""

from __future__ import annotations

import ipaddress
import os

#: Reverse proxies whose word about the client is believed. See
#: :func:`trusted_proxies`.
TRUSTED_PROXIES_ENV_VAR = "BENCH_PARTS_TRUSTED_PROXIES"


def trusted_proxies() -> str | None:
    """The proxies allowed to say who the client is, or ``None`` when unset.

    A comma-separated list of addresses and CIDR ranges, returned in the form
    uvicorn takes. From any peer that isn't listed the forwarded headers are
    still ignored.

    Raises ``ValueError`` for anything that isn't an address or a range, and
    for anything that trusts everyone. A header anyone can set must not pick a
    throttle bucket, and a typo must not quietly mean "trust nothing" while
    the bench stays lockable.
    """
    raw = os.environ.get(TRUSTED_PROXIES_ENV_VAR, "")
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if not entries:
        return None
    networks = []
    for entry in entries:
        if entry == "*":
            raise ValueError("'*' would trust every address")
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            raise ValueError(f"{entry!r} is not an address or a CIDR range") from None
        if network.prefixlen == 0:
            raise ValueError(f"{entry!r} would trust every address")
        # Normalised (10.42.1.5/16 -> 10.42.0.0/16): uvicorn parses ranges
        # strictly, and matches anything it can't parse as a literal string.
        networks.append(str(network))
    return ",".join(networks)


def server_options() -> dict[str, object]:
    """The uvicorn options that put :func:`trusted_proxies` into effect.

    Unset means nobody is believed. Left to itself uvicorn trusts forwarded
    headers from loopback, and on a one-machine install every browser is
    loopback: any of them could pick its own throttle bucket.
    """
    listed = trusted_proxies()
    return {"forwarded_allow_ips": listed} if listed else {"proxy_headers": False}
