"""Where a standalone install keeps its data and its settings.

On the cluster everything arrives as environment variables and the database
sits on an NFS volume. Installed as an app (``uv tool install``, the tray app,
a .deb service) there's no deployment to set those, so this supplies the
defaults: a per-OS data folder for the database, and a settings
file in it holding the secrets.

**Environment variables always win.** The settings file only fills in what the
environment leaves unset, so a container or a Kubernetes Secret behaves exactly
as before, and ``BENCH_PARTS_PASSWORD_HASH=… bench-parts serve`` still works.

The file is ``KEY=value`` lines using the environment variable names, readable
by hand and usable as a Docker ``env_file``. Values are taken literally: a
password hash contains ``$``, so nothing here expands it. The file is created
owner-only (0600), since it holds the password hash and the API token.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from pathlib import Path

APP_NAME = "bench-parts"
DATA_DIR_ENV = "BENCH_PARTS_DATA_DIR"
DB_PATH_ENV = "BENCH_PARTS_DB_PATH"
SETTINGS_FILE = "settings.env"


def data_dir(
    environ: MutableMapping[str, str] | None = None,
    platform: str = sys.platform,
    home: Path | None = None,
) -> Path:
    """The data folder: ``$BENCH_PARTS_DATA_DIR``, else the platform's per-user app data."""
    env = os.environ if environ is None else environ
    override = env.get(DATA_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    home = home or Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if platform == "win32":
        base = env.get("APPDATA", "").strip()
        return (Path(base) if base else home / "AppData" / "Roaming") / APP_NAME
    base = env.get("XDG_DATA_HOME", "").strip()
    return (Path(base) if base else home / ".local" / "share") / APP_NAME


def settings_path(data: Path) -> Path:
    return data / SETTINGS_FILE


def read_settings(path: Path) -> dict[str, str]:
    """``KEY=value`` pairs from ``path``; blank lines and ``#`` comments skipped.

    An unreadable file counts as empty. That's the .deb's service: systemd reads
    the root-owned settings.env and passes its values in as environment
    variables, so the service user needn't (and can't) read it itself.
    """
    try:
        text = path.read_text("utf-8")
    except (FileNotFoundError, PermissionError):
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_setting(path: Path, key: str, value: str) -> None:
    """Set one key, keeping the others. Written atomically and owner-only."""
    values = read_settings(path)
    values[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# bench-parts settings. Environment variables override these.\n"
    body += "".join(f"{k}={v}\n" for k, v in values.items())
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, path)
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def apply(data: Path, environ: MutableMapping[str, str] | None = None) -> None:
    """Fill the environment from the settings file and the data folder's defaults.

    Never overwrites a variable that's already set.
    """
    env = os.environ if environ is None else environ
    for key, value in read_settings(settings_path(data)).items():
        env.setdefault(key, value)
    env.setdefault(DB_PATH_ENV, str(data / "parts.db"))
