"""Start the tray app at login, the way each OS expects.

* **macOS:** a LaunchAgent plist in ``~/Library/LaunchAgents``.
* **Windows:** a value under ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``.
* **Linux:** an XDG autostart entry in ``~/.config/autostart``.

All per-user: nothing here needs admin rights. Qt-free, and every location is
injectable, so it's tested without touching the real login items.
"""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

LABEL = "com.iammrcupp.benchhud.bench-parts"
NAME = "bench-parts"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command(executable: str | None = None, platform: str = sys.platform) -> list[str]:
    """How the OS should start the tray app at login.

    A packaged app starts through its own launcher. A ``pip``/``uv tool``
    install starts the tray through the command.
    """
    exe = executable or sys.executable
    if platform == "darwin" and ".app/Contents/MacOS/" in exe:
        bundle = exe.split(".app/Contents/MacOS/")[0] + ".app"
        return ["/usr/bin/open", "-a", bundle]
    if platform == "win32" and not Path(exe).name.lower().startswith("python"):
        return [exe]  # the packaged app's own .exe
    return [exe, "-m", "bench_parts.cli", "tray"]


def _macos_plist(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_entry(home: Path, environ: dict | None = None) -> Path:
    env = os.environ if environ is None else environ
    base = env.get("XDG_CONFIG_HOME", "").strip()
    return (Path(base) if base else home / ".config") / "autostart" / f"{NAME}.desktop"


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg


class Autostart:
    """Enable, disable and check start-at-login for this user."""

    def __init__(
        self,
        platform: str = sys.platform,
        home: Path | None = None,
        registry=None,
        command: list[str] | None = None,
    ) -> None:
        self._platform = platform
        self._home = home or Path.home()
        self._registry = registry  # injectable stand-in for winreg
        self._command = command or launch_command(platform=platform)

    @property
    def supported(self) -> bool:
        return self._platform in ("darwin", "win32") or self._platform.startswith("linux")

    def enabled(self) -> bool:
        if self._platform == "darwin":
            return _macos_plist(self._home).exists()
        if self._platform == "win32":
            return self._read_run_value() is not None
        return _linux_entry(self._home).exists()

    def set_enabled(self, on: bool) -> None:
        if on:
            self._enable()
        else:
            self._disable()

    def _enable(self) -> None:
        if self._platform == "darwin":
            path = _macos_plist(self._home)
            path.parent.mkdir(parents=True, exist_ok=True)
            plist = {"Label": LABEL, "ProgramArguments": self._command, "RunAtLoad": True}
            path.write_bytes(plistlib.dumps(plist))
        elif self._platform == "win32":
            reg = self._winreg()
            with reg.CreateKey(reg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                value = " ".join(_quote(a) for a in self._command)
                reg.SetValueEx(key, NAME, 0, reg.REG_SZ, value)
        else:
            path = _linux_entry(self._home)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=bench parts\n"
                f"Exec={' '.join(_quote(a) for a in self._command)}\n"
                "X-GNOME-Autostart-enabled=true\n",
                "utf-8",
            )

    def _disable(self) -> None:
        if self._platform == "darwin":
            _macos_plist(self._home).unlink(missing_ok=True)
        elif self._platform == "win32":
            reg = self._winreg()
            try:
                with reg.OpenKey(reg.HKEY_CURRENT_USER, _RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                    reg.DeleteValue(key, NAME)
            except FileNotFoundError:
                pass
        else:
            _linux_entry(self._home).unlink(missing_ok=True)

    def _read_run_value(self) -> str | None:
        reg = self._winreg()
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                return reg.QueryValueEx(key, NAME)[0]
        except FileNotFoundError:
            return None

    def _winreg(self):
        if self._registry is not None:
            return self._registry
        import winreg  # Windows only

        return winreg
