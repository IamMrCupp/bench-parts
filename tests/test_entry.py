"""The packaged app's entry point: tray on a double-click, the CLI with a command."""

from __future__ import annotations

import subprocess
import sys

from bench_parts import entry


def test_no_arguments_is_the_tray_app() -> None:
    assert not entry.is_command_line([])


def test_a_launchers_own_arguments_are_still_the_tray_app() -> None:
    assert not entry.is_command_line(["-psn_0_12345"])
    assert not entry.is_command_line(["--some-launcher-flag"])


def test_every_cli_command_is_the_command_line() -> None:
    for command in ("serve", "set-password", "new-token", "paths", "tray"):
        assert entry.is_command_line([command])
    assert entry.is_command_line(["--data-dir", "/tmp/x", "paths"])
    assert entry.is_command_line(["--help"])


def test_the_commands_run_without_qt(tmp_path) -> None:
    """``python -m bench_parts <command>`` is what the packaged app's binary runs."""
    code = (
        "import sys, runpy\n"
        "sys.argv = ['bench_parts'] + sys.argv[1:]\n"
        "try:\n    runpy.run_module('bench_parts', run_name='__main__')\n"
        "except SystemExit as e:\n    assert e.code == 0, e.code\n"
        "assert not any(m.startswith('PySide6') for m in sys.modules), 'Qt was imported'\n"
    )
    env_dir = str(tmp_path / "data")
    done = subprocess.run(
        [sys.executable, "-c", code, "--data-dir", env_dir, "paths"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert env_dir in done.stdout


def test_a_password_and_a_token_can_be_set_through_the_entry_point(tmp_path) -> None:
    data = str(tmp_path / "data")
    run = [sys.executable, "-m", "bench_parts", "--data-dir", data]
    done = subprocess.run(
        [*run, "set-password", "--stdin"],
        input="correct horse battery\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    done = subprocess.run(
        [*run, "new-token", "--print"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    token = done.stdout.strip()
    assert len(token) >= 20 and "\n" not in token
    settings = (tmp_path / "data" / "settings.env").read_text()
    assert "BENCH_PARTS_PASSWORD_HASH=" in settings
    assert f"BENCH_PARTS_API_TOKEN={token}" in settings


def test_a_stream_that_is_not_the_terminal_is_replaced(monkeypatch) -> None:
    """The packaged macOS app's stdout is a system-log writer with no file descriptor."""

    class LogWriter:
        def write(self, text: str) -> int:
            return len(text)

    monkeypatch.setattr(sys, "stdout", LogWriter())
    entry.use_terminal_streams()
    assert sys.stdout.fileno() == 1


def test_a_stream_that_is_the_terminal_is_left_alone(monkeypatch) -> None:
    class Real:
        def fileno(self) -> int:
            return 1

    real = Real()
    monkeypatch.setattr(sys, "stdout", real)
    entry.use_terminal_streams()
    assert sys.stdout is real
