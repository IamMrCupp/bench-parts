"""The menu-bar / tray app: bench-parts as something you double-click.

It runs the web app in-process (:mod:`bench_parts.server`) and puts a small menu in
the menu bar (macOS) or system tray (Windows, Linux): status, Open in browser,
Share on the LAN, Start at login, Set password, New API token, Open data folder
and Quit. Settings live in the same data folder and ``settings.env`` as
``bench-parts serve`` (see :mod:`bench_parts.settings`), so the two are
interchangeable.

**Still fail-closed.** With no password it won't serve: the first launch asks for
one, and until it's set the menu says so.

``BENCH_PARTS_TRAY_SMOKE=1`` builds the menu, starts the server on a free port, fetches
``/healthz`` and exits 0, so CI can prove a packaged app really serves, with
nobody clicking.
"""

from __future__ import annotations

import os
import secrets
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from bench_parts import __version__, auth, settings
from bench_parts.autostart import Autostart
from bench_parts.server import ServerController

HOST_ENV = "BENCH_PARTS_HOST"
PORT_ENV = "BENCH_PARTS_PORT"
SMOKE_ENV = "BENCH_PARTS_TRAY_SMOKE"
_LOOPBACK = "127.0.0.1"
_LAN = "0.0.0.0"

#: Asks for a new password; returns it, or None if cancelled.
AskPassword = Callable[[QWidget | None], "str | None"]
#: Shows a freshly made API token.
ShowToken = Callable[[QWidget | None, str], None]


def tray_icon() -> QIcon:
    """A chip drawn in black: a template image the macOS menu bar tints."""
    pixmap = QPixmap(44, 44)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("black"), 3.5)
    p.setPen(pen)
    p.drawRoundedRect(QRectF(12, 12, 20, 20), 3, 3)
    for offset in (17.0, 22.0, 27.0):  # three pins on each side
        p.drawLine(QPointF(offset, 5), QPointF(offset, 12))
        p.drawLine(QPointF(offset, 32), QPointF(offset, 39))
        p.drawLine(QPointF(5, offset), QPointF(12, offset))
        p.drawLine(QPointF(32, offset), QPointF(39, offset))
    p.end()
    icon = QIcon(pixmap)
    icon.setIsMask(True)
    return icon


class PasswordDialog(QDialog):
    """Two masked fields that have to match; OK stays disabled until they do."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("bench parts password")
        self.first = QLineEdit()
        self.first.setEchoMode(QLineEdit.EchoMode.Password)
        self.second = QLineEdit()
        self.second.setEchoMode(QLineEdit.EchoMode.Password)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        note = QLabel(
            "bench-parts won't serve your inventory, or its API, without a password. "
            "It's stored only as a hash."
        )
        note.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Password", self.first)
        form.addRow("Again", self.second)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        for field in (self.first, self.second):
            field.textChanged.connect(self._check)
        self._check()

    def _check(self) -> None:
        ok = bool(self.first.text().strip()) and self.first.text() == self.second.text()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    @property
    def password(self) -> str:
        return self.first.text()


def ask_password(parent: QWidget | None = None) -> str | None:
    dialog = PasswordDialog(parent)
    return dialog.password if dialog.exec() == QDialog.DialogCode.Accepted else None


def show_token(parent: QWidget | None, token: str) -> None:
    QGuiApplication.clipboard().setText(token)
    QMessageBox.information(
        parent,
        "New API token",
        "Copied to the clipboard. Paste it into benchhud: Preferences → Inventory → Token."
        f"\n\n{token}\n\nThe old token, if any, has stopped working.",
    )


class PartsTray:
    """The menu and what its items do. Dialogs are injectable for tests."""

    def __init__(
        self,
        data: Path,
        *,
        controller: ServerController | None = None,
        autostart: Autostart | None = None,
        ask_password: AskPassword = ask_password,
        show_token: ShowToken = show_token,
        open_url: Callable[[QUrl], bool] = QDesktopServices.openUrl,
    ) -> None:
        self.data = data
        self.controller = controller or ServerController()
        self.autostart = autostart or Autostart()
        self._ask_password = ask_password
        self._show_token = show_token
        self._open_url = open_url

        self.menu = QMenu()
        self.status_action = self.menu.addAction("Starting…")
        self.status_action.setEnabled(False)
        self.open_action = self.menu.addAction("Open in browser", self.open_in_browser)
        self.menu.addSeparator()
        self.share_action = self._checkable("Share on the LAN", self.host == _LAN, self.set_shared)
        self.autostart_action = self._checkable(
            "Start at login", self.autostart.enabled(), self.autostart.set_enabled
        )
        self.autostart_action.setVisible(self.autostart.supported)
        self.menu.addSeparator()
        self.menu.addAction("Set password…", self.set_password)
        self.menu.addAction("New API token…", self.new_token)
        self.menu.addAction("Open data folder", self.open_data_folder)
        self.menu.addSeparator()
        self.menu.addAction(f"Quit bench parts {__version__}", self.quit)

        self.icon = QSystemTrayIcon(tray_icon())
        self.icon.setContextMenu(self.menu)
        self.refresh()

    def _checkable(self, text: str, checked: bool, slot) -> QAction:
        """A toggle whose starting state doesn't fire its slot (no writes on launch)."""
        action = self.menu.addAction(text)
        action.setCheckable(True)
        action.setChecked(checked)
        action.toggled.connect(slot)
        return action

    # --- settings -----------------------------------------------------------

    @property
    def settings_file(self) -> Path:
        return settings.settings_path(self.data)

    @property
    def host(self) -> str:
        return os.environ.get(HOST_ENV, _LOOPBACK)

    @property
    def port(self) -> int:
        try:
            return int(os.environ.get(PORT_ENV, "8000"))
        except ValueError:
            return 8000

    def _save(self, key: str, value: str) -> None:
        # The running app reads these from the environment on each request, so
        # a new password or token takes effect without a restart.
        settings.write_setting(self.settings_file, key, value)
        os.environ[key] = value

    @property
    def has_password(self) -> bool:
        return bool(os.environ.get(auth.ENV_VAR, "").strip())

    # --- actions --------------------------------------------------------------

    def start(self) -> None:
        """Serve, asking for a password first if there isn't one."""
        if not self.has_password:
            self.set_password()  # which starts the server once there's a password
            self.refresh()
            return
        self.controller.start(self.host, self.port)
        self.refresh()

    def set_password(self) -> None:
        password = self._ask_password(None)
        if not password:
            return
        self._save(auth.ENV_VAR, auth.hash_password(password))
        if not self.controller.state.running:
            self.controller.start(self.host, self.port)
        self.refresh()

    def new_token(self) -> None:
        token = secrets.token_urlsafe(32)
        self._save(auth.API_TOKEN_ENV_VAR, token)
        self._show_token(None, token)

    def set_shared(self, shared: bool) -> None:
        self._save(HOST_ENV, _LAN if shared else _LOOPBACK)
        if self.controller.state.running:
            self.controller.start(self.host, self.port)
        self.refresh()

    def open_in_browser(self) -> None:
        self._open_url(QUrl(self.controller.state.url))

    def open_data_folder(self) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        self._open_url(QUrl.fromLocalFile(str(self.data)))

    def quit(self) -> None:
        self.controller.stop()
        self.icon.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def refresh(self) -> None:
        state = self.controller.state
        if not self.has_password:
            text = "Needs a password"
        elif state.running:
            where = "on your LAN" if state.host == _LAN else "on this computer only"
            text = f"Running {where}: {state.url}"
        elif state.error:
            text = f"Stopped: {state.error}"
        else:
            text = "Stopped"
        self.status_action.setText(text)
        self.open_action.setEnabled(state.running)
        self.icon.setToolTip(f"bench parts: {text}")


def smoke(data: Path) -> int:
    """Build the menu, serve on a free port, fetch /healthz, exit. For CI."""
    os.environ.setdefault(auth.ENV_VAR, auth.hash_password(secrets.token_urlsafe(16)))
    tray = PartsTray(data, ask_password=lambda _p: None, show_token=lambda _p, _t: None)
    state = tray.controller.start(_LOOPBACK, 0)
    if not state.running:
        print(f"SMOKE FAIL: {state.error}", file=sys.stderr)
        return 1
    try:
        with urllib.request.urlopen(f"{state.url}healthz", timeout=10) as resp:
            body = resp.read().decode()
    finally:
        tray.controller.stop()
    print(f"SMOKE OK {state.url} healthz={body.strip()} menu={len(tray.menu.actions())} items")
    return 0


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)  # a tray app has no window to close
    app.setApplicationName("bench parts")
    data = settings.data_dir()
    data.mkdir(parents=True, exist_ok=True)
    settings.apply(data)
    if os.environ.get(SMOKE_ENV):
        return smoke(data)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "bench parts",
            "There's no menu bar or system tray to live in. Run `bench-parts serve` instead.",
        )
        return 1
    tray = PartsTray(data)
    tray.icon.show()
    tray.start()
    return app.exec()
