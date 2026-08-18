"""Password hashing.

The most important test in here is `test_missing_hash_raises`: a deploy that
loses its password config must fail loudly rather than quietly serving customer
records to anyone who asks. Silent-allow is the worst failure available to this
app, and it's the kind nobody notices.
"""

import pytest

from bench_parts.auth import configured_hash, hash_password, verify_password

# --- round trip -------------------------------------------------------------


def test_hash_then_verify() -> None:
    assert verify_password(
        "correct horse battery staple", hash_password("correct horse battery staple")
    )


def test_wrong_password_is_rejected() -> None:
    assert not verify_password("hunter2", hash_password("correct horse"))


def test_empty_password_never_verifies() -> None:
    """Guards the case where a blank form field meets a blank config."""
    stored = hash_password("real password")

    assert not verify_password("", stored)


def test_hashes_are_salted() -> None:
    """The same password twice must produce different hashes, or the file
    leaks which accounts share a password — and here, that the password never
    changed."""
    assert hash_password("same") != hash_password("same")


def test_hash_does_not_contain_the_password() -> None:
    assert "swordfish" not in hash_password("swordfish")


def test_unicode_passwords_work() -> None:
    # Accents, CJK and an emoji — the realistic ways a password stops being ASCII.
    mixed = "pässwörd 日本 🎛"

    assert verify_password(mixed, hash_password(mixed))


def test_long_password_works() -> None:
    long = "a" * 4096

    assert verify_password(long, hash_password(long))


# --- malformed stored hashes ------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    ["", "garbage", "scrypt$notanumber$aa$bb", "$$$", "scrypt$16384$onlytwo"],
)
def test_malformed_stored_hash_does_not_verify(stored) -> None:
    """A corrupted env var must deny, not crash and not allow."""
    assert not verify_password("anything", stored)


# --- configuration ----------------------------------------------------------


def test_configured_hash_reads_the_env_var(monkeypatch) -> None:
    monkeypatch.setenv("BENCH_PARTS_PASSWORD_HASH", "scrypt$16384$8$1$aabb$ccdd")

    assert configured_hash() == "scrypt$16384$8$1$aabb$ccdd"


def test_missing_hash_raises(monkeypatch) -> None:
    """**The one that matters.** No password configured must be a hard failure,
    never a quiet allow-all."""
    monkeypatch.delenv("BENCH_PARTS_PASSWORD_HASH", raising=False)

    with pytest.raises(RuntimeError, match="BENCH_PARTS_PASSWORD_HASH"):
        configured_hash()


def test_blank_hash_raises(monkeypatch) -> None:
    """An env var set to empty string is a misconfiguration, not 'no password'."""
    monkeypatch.setenv("BENCH_PARTS_PASSWORD_HASH", "   ")

    with pytest.raises(RuntimeError):
        configured_hash()


def test_error_message_names_the_variable(monkeypatch) -> None:
    """Whoever hits this is deploying at the time and needs the fix, not a
    stack trace to interpret."""
    monkeypatch.delenv("BENCH_PARTS_PASSWORD_HASH", raising=False)

    with pytest.raises(RuntimeError) as exc:
        configured_hash()

    assert "BENCH_PARTS_PASSWORD_HASH" in str(exc.value)
