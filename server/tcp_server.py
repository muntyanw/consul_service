"""tcp_server.py
~~~~~~~~~~~~~~~~

Simple line‑based TCP server that accepts multiple clients and exposes three
commands recognised by the bot runtime:

* ``pause``  – set global flag *PAUSE* (main loop sleeps until *RESUME*)
* ``resume`` – clear *PAUSE*
* ``stop``   – set global flag *STOP*  (main loop exits gracefully)

The server runs in its own daemon thread and never touches GUI code; it only
updates *thread‑safe* ``threading.Event`` flags, which the manager polls.

Usage
-----
```python
from server.tcp_server import ControlServer, PAUSE_EVT, STOP_EVT
srv = ControlServer(host="0.0.0.0", port=4567)
srv.start()
# main loop ...
```

Clients may use *netcat* or any simple TCP tool:
```
$ nc localhost 4567
pause\n          # server replies "PAUSED"\n
resume\n         # "RESUMED"\n
stop\n           # "STOPPING" (server stays alive, but main loop will terminate)\n```
"""
from __future__ import annotations

import socketserver
import threading
from typing import Final

from utils.logger import setup_logger

LOGGER = setup_logger(__name__)

__all__ = [
    "ControlServer",
    "STOP_EVT",
    "PAUSE_EVT",
]

# ---------------------------------------------------------------------------
# Global control flags (thread‑safe Events)
# ---------------------------------------------------------------------------
STOP_EVT: Final[threading.Event] = threading.Event()
PAUSE_EVT: Final[threading.Event] = threading.Event()


# ---------------------------------------------------------------------------
# TCP request handler
# ---------------------------------------------------------------------------
class _Handler(socketserver.StreamRequestHandler):
    """One handler per client; processes newline‑terminated commands."""

    def handle(self) -> None:  # noqa: D401 – imperative style
        addr = f"{self.client_address[0]}:{self.client_address[1]}"
        LOGGER.info("Client connected: %s", addr)
        try:
            for line in self.rfile:  # type: ignore[assignment]
                cmd = line.decode().strip().lower()
                if not cmd:
                    continue
                LOGGER.debug("Command '%s' from %s", cmd, addr)
                match cmd:
                    case "pause":
                        PAUSE_EVT.set()
                        self._reply("PAUSED")
                    case "resume":
                        PAUSE_EVT.clear()
                        self._reply("RESUMED")
                    case "stop":
                        STOP_EVT.set()
                        self._reply("STOPPING")
                    case _:
                        self._reply("UNKNOWN")
        except ConnectionResetError:
            pass
        finally:
            LOGGER.info("Client disconnected: %s", addr)

    # ------------------------------------------------------------------
    def _reply(self, text: str) -> None:
        self.wfile.write((text + "\n").encode())  # type: ignore[arg-type]
        self.wfile.flush()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ThreadingTCPServer wrapper
# ---------------------------------------------------------------------------
class _ThreadedTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class ControlServer(threading.Thread):
    """Daemon thread that encapsulates the socketserver."""

    daemon = True

    def __init__(self, host: str = "0.0.0.0", port: int = 4567):
        super().__init__(name="ControlServer")
        self._srv = _ThreadedTCPServer((host, port), _Handler)

    # ------------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover
        LOGGER.info("TCP control server listening on %s:%d", *self._srv.server_address)
        with self._srv:
            self._srv.serve_forever(poll_interval=0.5)

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        LOGGER.info("Shutting down TCP control server …")
        self._srv.shutdown()
        self._srv.server_close()
