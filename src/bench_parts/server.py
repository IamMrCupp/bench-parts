"""Run the web app on a background thread, for the tray app.

``serve`` hands the process to uvicorn. The tray app can't: its main thread
belongs to the Qt event loop. So this runs a :class:`uvicorn.Server` on a thread
of its own. uvicorn only installs signal handlers on the main thread, so it's
safe there.

Qt-free, so it's tested with real HTTP and no display.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ServerState:
    running: bool
    host: str
    port: int
    error: str | None = None

    @property
    def url(self) -> str:
        # 0.0.0.0 isn't an address a browser can open; this machine can always
        # reach it on loopback.
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{host}:{self.port}/"


class ServerController:
    """Start and stop the app on a thread. Not thread-safe: call it from one thread."""

    def __init__(self, app_factory=None) -> None:
        # Imported late: bench_parts.app reads the password at import and refuses
        # without one, and the tray may have to ask for it first.
        self._app_factory = app_factory or _import_app
        self._server = None
        self._thread: threading.Thread | None = None
        self._state = ServerState(running=False, host="127.0.0.1", port=8000)

    @property
    def state(self) -> ServerState:
        server, thread = self._server, self._thread
        if server is not None and thread is not None and not thread.is_alive():
            # It stopped on its own (it can't bind, or it crashed).
            self._state = ServerState(False, self._state.host, self._state.port, self._state.error)
        return self._state

    def start(self, host: str, port: int, timeout: float = 10.0) -> ServerState:
        """Start serving. Returns once it's listening, or with ``error`` set."""
        import uvicorn

        self.stop()
        config = uvicorn.Config(self._app_factory(), host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        failure: list[str] = []

        def run() -> None:
            try:
                server.run()
            except SystemExit:  # uvicorn exits when it can't bind
                failure.append(f"couldn't listen on {host}:{port} (is the port in use?)")
            except Exception as exc:  # pragma: no cover - a crash inside uvicorn
                failure.append(str(exc))

        thread = threading.Thread(target=run, name="bench-parts-server", daemon=True)
        self._server, self._thread = server, thread
        thread.start()
        deadline = time.monotonic() + timeout
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if server.started:
            bound = _bound_port(server) or port
            self._state = ServerState(True, host, bound)
        else:
            error = failure[0] if failure else f"didn't start within {timeout:.0f}s"
            self._state = ServerState(False, host, port, error)
            self.stop()
        return self._state

    def stop(self, timeout: float = 5.0) -> None:
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=timeout)
        if self._state.running:
            self._state = ServerState(False, self._state.host, self._state.port)


def _import_app():
    from bench_parts.app import app

    return app


def _bound_port(server) -> int | None:
    """The real port, when asked for port 0 (tests)."""
    try:
        return server.servers[0].sockets[0].getsockname()[1]
    except (AttributeError, IndexError):
        return None
