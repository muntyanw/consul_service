"""tcp_server.py
~~~~~~~~~~~~~~~~

Simple line-based TCP server that accepts multiple clients and exposes three
commands recognised by the bot runtime:

* `pause`: set global flag PAUSE  (main loop sleeps until resume)
* `resume`: clear PAUSE
* `stop`: set global flag STOP   (main loop exits gracefully)

The server runs in its own daemon thread and only updates thread-safe Events,
which the manager polls.
"""

from __future__ import annotations
import threading
import socketserver
from typing import Final
from utils.logger import setup_logger
import os

LOGGER = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Global control flags
# ---------------------------------------------------------------------------
STOP_EVT: Final[threading.Event] = threading.Event()
PAUSE_EVT: Final[threading.Event] = threading.Event()


# ---------------------------------------------------------------------------
# TCP request handler
# ---------------------------------------------------------------------------
class _Handler(socketserver.StreamRequestHandler):
    """Handles a single TCP client connection and processes newline-terminated commands."""

    def handle(self) -> None:
        addr = f"{self.client_address[0]}:{self.client_address[1]}"
        LOGGER.info("Client connected: %s", addr)
        try:
            for line in self.rfile:  # type: ignore[assignment]
                cmd = line.decode(errors="ignore").strip().lower()
                if not cmd:
                    continue
                LOGGER.debug("Command '%s' from %s", cmd, addr)

                if cmd == "pause":
                    PAUSE_EVT.set()
                    self._reply("PAUSED")

                elif cmd == "resume":
                    PAUSE_EVT.clear()
                    self._reply("RESUMED")

                elif cmd == "stop":
                    STOP_EVT.set()
                    self._reply("STOPPING")
                    threading.Thread(target=lambda: os._exit(0), daemon=True).start()

                else:
                    self._reply("UNKNOWN")

        except ConnectionResetError:
            pass
        finally:
            LOGGER.info("Client disconnected: %s", addr)

    def _reply(self, text: str) -> None:
        self.wfile.write((text + "\n").encode())  # type: ignore[arg-type]
        self.wfile.flush()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ThreadingTCPServer subclass allows address reuse
# ---------------------------------------------------------------------------
class _ThreadedTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Control server running in background thread
# ---------------------------------------------------------------------------
class ControlServer(threading.Thread):
    daemon = True

    def __init__(self, host: str = "0.0.0.0", port: int = 4567):
        super().__init__(name="ControlServer")
        self._srv = _ThreadedTCPServer((host, port), _Handler)

    def run(self) -> None:  # pragma: no cover
        LOGGER.info("TCP control server listening on %s:%d", *self._srv.server_address)
        # serve_forever проверяет флаг shutdown каждые 0.5 сек
        self._srv.serve_forever(poll_interval=0.5)

    def shutdown(self) -> None:
        """Stop the control server and wait for thread to finish."""
        LOGGER.info("Shutting down TCP control server …")
        # прерываем serve_forever
        self._srv.shutdown()
        # закрываем слушающий сокет
        self._srv.server_close()
        # ждём завершения run()
        self.join()
