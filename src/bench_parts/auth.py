"""Password hashing for the single shared password.

**One password, not a user system.** This service is used by two people standing
at the same bench; accounts, roles and reset flows would be machinery for a
problem that doesn't exist here. Lifted from benchhud-intake's ``auth.py``;
lower stakes here (no PII), but an inventory writable by anyone on the LAN is
still not what you want.

**Why ``hashlib.scrypt`` rather than argon2 or bcrypt.** The password is verified
a handful of times a day, on a Raspberry Pi, against a threat model whose
realistic attack is someone on the LAN guessing — which rate limiting addresses,
not hash choice. A third-party dependency would buy a marginally better
algorithm and cost an extra thing to keep patched on an arm64 image. scrypt is
memory-hard, in the standard library, and more than proportionate.

The plaintext password never appears in this repo, in the image, or in the
Kubernetes manifests — only the hash, injected as ``BENCH_PARTS_PASSWORD_HASH``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

ENV_VAR = "BENCH_PARTS_PASSWORD_HASH"
SESSION_ENV_VAR = "BENCH_PARTS_SESSION_SECRET"
COOKIE_SECURE_ENV_VAR = "BENCH_PARTS_COOKIE_SECURE"

# scrypt parameters. n=16384 keeps verification around ~50ms on a Pi 4 — fast
# enough that a login doesn't feel slow, slow enough that offline guessing is
# expensive. Stored alongside each hash so these can be raised later without
# invalidating an existing password.
_N = 16384
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16
_PREFIX = "scrypt"


def hash_password(password: str) -> str:
    """Hash a password for storage in ``BENCH_PARTS_PASSWORD_HASH``.

    Format: ``scrypt$n$r$p$salt_hex$key_hex``. Self-describing, so raising the
    work factor later doesn't lock anyone out of an existing deployment.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)

    return f"{_PREFIX}${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash.

    Returns ``False`` for anything malformed rather than raising: a corrupted
    env var must **deny**, never crash into a state where the caller might treat
    the error as a pass.
    """
    try:
        prefix, n, r, p, salt_hex, key_hex = stored.split("$")
        if prefix != _PREFIX:
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(key_hex)
        n, r, p = int(n), int(r), int(p)
    except (ValueError, AttributeError):
        return False

    try:
        candidate = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected))
    except ValueError:
        # Nonsensical parameters in a hand-edited env var.
        return False

    # Constant-time: a timing side channel on a LAN is unlikely to be practical,
    # but avoiding it costs nothing.
    return hmac.compare_digest(candidate, expected)


def configured_hash() -> str:
    """The hash this deployment was configured with.

    Raises when it's missing or blank. **This is deliberate and load-bearing:**
    the alternative is a misconfigured deploy quietly letting anyone on the LAN
    read and rewrite the inventory. A service that won't start is a visible,
    fixable problem; one that starts wide open is not.
    """
    value = os.environ.get(ENV_VAR, "").strip()
    if not value:
        raise RuntimeError(
            f"{ENV_VAR} is not set. This service refuses to start without a "
            f"password. Generate one with:\n"
            f"    uv run python -m bench_parts.auth"
        )
    return value


def session_secret() -> str:
    """The key the session cookie is signed with.

    Deliberately **not** a per-process random default. That looks fine in
    development and then logs everyone out unpredictably across a restart or a
    second worker — a bug that presents as "the app randomly forgets me".

    Falls back to a value derived from the password hash so a deployment needs
    exactly one configured secret to work. Rotating the password therefore
    invalidates existing sessions, which is the behaviour you'd want anyway.
    """
    explicit = os.environ.get(SESSION_ENV_VAR, "").strip()
    if explicit:
        return explicit

    return hashlib.sha256(f"session:{configured_hash()}".encode()).hexdigest()


API_TOKEN_ENV_VAR = "BENCH_PARTS_API_TOKEN"


def valid_api_token(authorization: str | None) -> bool:
    """Check an ``Authorization: Bearer <token>`` header against the configured
    API token.

    **Fails closed.** If ``BENCH_PARTS_API_TOKEN`` is unset, this always returns
    ``False`` — a deployment that hasn't configured a token must reject bearer
    auth outright, never treat "no token" as "any token". The API token is
    optional (the standalone app runs fine without benchhud polling it), so
    unlike the password its absence doesn't stop startup — it just means the API
    is only reachable with a session.
    """
    configured = os.environ.get(API_TOKEN_ENV_VAR, "").strip()
    if not configured or not authorization:
        return False

    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False

    return hmac.compare_digest(presented, configured)


def cookie_secure() -> bool:
    """Whether to mark the session cookie ``secure``.

    **Off by default, and that's deliberate.** This service is LAN-only over
    plain HTTP by design; a ``secure`` cookie would never be transmitted, so
    login would appear to succeed and then silently fail on every subsequent
    request. Turn it on with ``BENCH_PARTS_COOKIE_SECURE=1`` once something
    terminates TLS in front of it.
    """
    return os.environ.get(COOKIE_SECURE_ENV_VAR, "").strip().lower() not in ("", "0", "false", "no")


def csrf_token(session: dict) -> str:
    """A per-session CSRF token, minted on first use.

    Stored in the session rather than derived from it, so it can't be
    reconstructed by anyone who learns how the session is built. A stolen token
    from a different session is useless because it simply won't match.
    """
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def csrf_valid(session: dict, submitted: str | None) -> bool:
    """Check a submitted token against the session's.

    Constant-time, and ``False`` when either side is missing — an absent token
    must fail closed rather than being treated as "nothing to compare".
    """
    expected = session.get("csrf")
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)


def _main() -> None:
    """Generate a hash to paste into the environment or a Kubernetes Secret."""
    import getpass

    password = getpass.getpass("New bench-parts password: ")
    if password != getpass.getpass("Again: "):
        raise SystemExit("passwords did not match")
    if not password.strip():
        raise SystemExit("password must not be blank")

    print(f"\n{ENV_VAR}={hash_password(password)}")


if __name__ == "__main__":
    _main()
