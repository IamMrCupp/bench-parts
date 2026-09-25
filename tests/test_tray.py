"""The tray app (#86): the threaded server, start at login, and the menu."""

from __future__ import annotations

import os
import plistlib
import socket
import urllib.request
from contextlib import contextmanager

import pytest

from bench_parts import auth, settings
from bench_parts.autostart import Autostart, launch_command
from bench_parts.server import ServerController, ServerState

# --- the server on a thread (real HTTP) ------------------------------------------------


def test_the_server_starts_serves_and_stops() -> None:
    controller = ServerController()
    state = controller.start("127.0.0.1", 0)
    try:
        assert state.running and state.port > 0
        with urllib.request.urlopen(f"{state.url}healthz", timeout=5) as resp:
            assert resp.status == 200
    finally:
        controller.stop()
    assert not controller.state.running


def test_a_taken_port_is_an_error_not_a_hang() -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        state = ServerController().start("127.0.0.1", port, timeout=5)
    assert not state.running
    assert str(port) in state.error


def test_the_lan_address_opens_on_loopback() -> None:
    assert ServerState(True, "0.0.0.0", 8000).url == "http://127.0.0.1:8000/"


# --- start at login ---------------------------------------------------------------------


def test_launch_command_for_each_kind_of_install() -> None:
    mac_app = "/Applications/bench parts.app/Contents/MacOS/bench parts"
    assert launch_command(mac_app, "darwin") == [
        "/usr/bin/open",
        "-a",
        "/Applications/bench parts.app",
    ]
    win_app = r"C:\Users\a\AppData\Local\Programs\bench parts\bench parts.exe"
    assert launch_command(win_app, "win32") == [win_app]
    assert launch_command("/usr/bin/python3", "linux") == [
        "/usr/bin/python3",
        "-m",
        "bench_parts.cli",
        "tray",
    ]


def test_macos_login_item_is_a_launch_agent(tmp_path) -> None:
    auto = Autostart("darwin", tmp_path, command=["/usr/bin/open", "-a", "/Applications/x.app"])
    assert not auto.enabled()
    auto.set_enabled(True)
    plist = tmp_path / "Library/LaunchAgents/com.iammrcupp.benchhud.bench-parts.plist"
    data = plistlib.loads(plist.read_bytes())
    assert data["ProgramArguments"] == ["/usr/bin/open", "-a", "/Applications/x.app"]
    assert data["RunAtLoad"] is True
    assert auto.enabled()
    auto.set_enabled(False)
    assert not auto.enabled()


def test_linux_login_item_is_an_xdg_autostart_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    command = ["/usr/bin/python3", "-m", "bench_parts.cli", "tray"]
    auto = Autostart("linux", tmp_path, command=command)
    auto.set_enabled(True)
    entry = (tmp_path / ".config/autostart/bench-parts.desktop").read_text()
    assert "Exec=/usr/bin/python3 -m bench_parts.cli tray" in entry
    auto.set_enabled(False)
    assert not auto.enabled()


class FakeRegistry:
    """Just enough of winreg for the Run key."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    @contextmanager
    def CreateKey(self, _root, _path):
        yield "key"

    @contextmanager
    def OpenKey(self, _root, _path, *_args):
        yield "key"

    def SetValueEx(self, _key, name, _reserved, _kind, value):
        self.values[name] = value

    def QueryValueEx(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def DeleteValue(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_windows_login_item_is_a_run_key_value(tmp_path) -> None:
    reg = FakeRegistry()
    exe = r"C:\Program Files\bench parts\bench parts.exe"
    auto = Autostart("win32", tmp_path, registry=reg, command=[exe])
    auto.set_enabled(True)
    assert reg.values["bench-parts"] == f'"{exe}"'  # quoted: the path has spaces
    assert auto.enabled()
    auto.set_enabled(False)
    auto.set_enabled(False)  # already off: no error
    assert not auto.enabled()


# --- the menu ------------------------------------------------------------------------------


class FakeController:
    def __init__(self) -> None:
        self.state = ServerState(False, "127.0.0.1", 8000)
        self.starts: list[tuple[str, int]] = []

    def start(self, host, port):
        self.starts.append((host, port))
        self.state = ServerState(True, host, port)
        return self.state

    def stop(self):
        self.state = ServerState(False, self.state.host, self.state.port)


class FakeAutostart:
    supported = True

    def __init__(self, on: bool = False) -> None:
        self.on = on
        self.calls: list[bool] = []

    def enabled(self) -> bool:
        return self.on

    def set_enabled(self, on: bool) -> None:
        self.calls.append(on)
        self.on = on


@pytest.fixture
def env(monkeypatch):
    """The tray writes os.environ; give each test its own, with no password."""
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for key in (auth.ENV_VAR, auth.API_TOKEN_ENV_VAR, "BENCH_PARTS_HOST", "BENCH_PARTS_PORT"):
        os.environ.pop(key, None)
    return os.environ


def _tray(qtbot, data, *, password=None, autostart=None, shown=None):
    from bench_parts.tray import PartsTray

    tray = PartsTray(
        data,
        controller=FakeController(),
        autostart=autostart or FakeAutostart(),
        ask_password=lambda _p: password,
        show_token=lambda _p, token: (shown if shown is not None else []).append(token),
        open_url=lambda _url: True,
    )
    qtbot.addWidget(tray.menu)
    return tray


def test_no_password_means_no_server(qtbot, tmp_path, env) -> None:
    """Fail closed: cancelling the first-run prompt leaves it stopped, and says why."""
    tray = _tray(qtbot, tmp_path, password=None)
    tray.start()
    assert tray.controller.starts == []
    assert tray.status_action.text() == "Needs a password"
    assert not tray.open_action.isEnabled()


def test_first_run_sets_a_password_then_serves(qtbot, tmp_path, env) -> None:
    tray = _tray(qtbot, tmp_path, password="bench pw")
    tray.start()
    stored = settings.read_settings(tmp_path / "settings.env")[auth.ENV_VAR]
    assert auth.verify_password("bench pw", stored)
    assert env[auth.ENV_VAR] == stored  # the running app sees it without a restart
    assert tray.controller.starts == [("127.0.0.1", 8000)]
    assert tray.status_action.text().startswith("Running on this computer only")


def test_a_new_token_is_saved_live_and_shown(qtbot, tmp_path, env) -> None:
    shown: list[str] = []
    tray = _tray(qtbot, tmp_path, shown=shown)
    tray.new_token()
    token = settings.read_settings(tmp_path / "settings.env")[auth.API_TOKEN_ENV_VAR]
    assert shown == [token] and env[auth.API_TOKEN_ENV_VAR] == token


def test_sharing_on_the_lan_restarts_on_all_interfaces(qtbot, tmp_path, env) -> None:
    tray = _tray(qtbot, tmp_path, password="pw")
    tray.start()
    tray.share_action.setChecked(True)
    assert tray.controller.starts[-1] == ("0.0.0.0", 8000)
    assert settings.read_settings(tmp_path / "settings.env")["BENCH_PARTS_HOST"] == "0.0.0.0"
    assert "on your LAN" in tray.status_action.text()


def test_opening_the_menu_writes_nothing(qtbot, tmp_path, env) -> None:
    """The toggles' starting states mustn't fire their actions."""
    env["BENCH_PARTS_HOST"] = "0.0.0.0"
    autostart = FakeAutostart(on=True)
    tray = _tray(qtbot, tmp_path, autostart=autostart)
    assert tray.share_action.isChecked() and tray.autostart_action.isChecked()
    assert autostart.calls == []
    assert not (tmp_path / "settings.env").exists()


def test_start_at_login_toggles_the_login_item(qtbot, tmp_path, env) -> None:
    autostart = FakeAutostart()
    tray = _tray(qtbot, tmp_path, autostart=autostart)
    tray.autostart_action.setChecked(True)
    tray.autostart_action.setChecked(False)
    assert autostart.calls == [True, False]


def test_smoke_serves_and_exits_cleanly(qtbot, tmp_path, env, capsys) -> None:
    from bench_parts.tray import smoke

    assert smoke(tmp_path) == 0
    assert "SMOKE OK" in capsys.readouterr().out
    assert not (tmp_path / "settings.env").exists()  # its password is throwaway


def test_password_dialog_needs_two_matching_entries(qtbot) -> None:
    from PySide6.QtWidgets import QDialogButtonBox

    from bench_parts.tray import PasswordDialog

    dialog = PasswordDialog()
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    dialog.first.setText("abc")
    dialog.second.setText("abd")
    assert not ok.isEnabled()
    dialog.second.setText("abc")
    assert ok.isEnabled() and dialog.password == "abc"
