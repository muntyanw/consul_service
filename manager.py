"""
manager.py
~~~~~~~~~~

Entry-point module that orchestrates the complete e-consul booking bot:
1. Initial scan of the users YAML directory → build processing deque.
2. Start background threads:
   * ConfigWatcher – hot-reload YAML changes.
   * ControlServer – TCP interface (pause / resume / stop).
3. Iterate over users one-by-one; for each:
   * launch Chrome inside gui_driver.chrome_session (isolated profile);
   * invoke SlotFinder.work;
   * on success – drop user; on failure – append back to queue tail.
4. Respect global flags PAUSE_EVT and STOP_EVT (set by TCP server).
"""

from __future__ import annotations

import os
import signal
import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, Optional

from core.gui_driver import chrome_session
from core.slot_finder import SlotFinder
from bot_io.config_watcher import ChangeEvent, ChangeKind, ConfigWatcher
from bot_io.yaml_loader import UserConfig, YAMLLoader, ConfigError
from server.tcp_server import ControlServer, PAUSE_EVT, STOP_EVT
from utils.logger import setup_logger
from project_config import USERS_DIR, KEYS_DIR, LOG_LEVEL
from core.slot_finder import free_slots


LOGGER = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Helper container – thread-safe queue wrapper
# ---------------------------------------------------------------------------
class UserQueue:
    """Thread-safe deque with alias → config mapping."""

    def __init__(self, initial: list[UserConfig]):
        self._dq: deque[UserConfig] = deque(initial)
        self._map: Dict[str, UserConfig] = {u.alias: u for u in initial}
        self._lock = threading.Lock()

    def pop_left(self) -> Optional[UserConfig]:
        with self._lock:
            return self._dq.popleft() if self._dq else None

    def append(self, user: UserConfig) -> None:
        with self._lock:
            if user.alias not in self._map:
                self._dq.append(user)
                self._map[user.alias] = user

    def remove(self, alias: str) -> None:
        with self._lock:
            self._dq = deque(u for u in self._dq if u.alias != alias)
            self._map.pop(alias, None)

    def update(self, user: UserConfig) -> None:
        """Replace existing config in-place (position preserved)."""
        with self._lock:
            if user.alias in self._map:
                self._dq = deque(
                    user if u.alias == user.alias else u for u in self._dq
                )
                self._map[user.alias] = user
            else:
                self.append(user)

    def exists(self, alias: str) -> bool:
        return alias in self._map

    def __bool__(self) -> bool:
        return bool(self._dq)


# ---------------------------------------------------------------------------
# Watcher callback
# ---------------------------------------------------------------------------
def _on_yaml_change(
    evt: ChangeEvent, loader: YAMLLoader, queue: UserQueue
) -> None:
    try:
        if evt.kind == ChangeKind.DELETED:
            queue.remove(evt.path.stem)
            LOGGER.info("YAML deleted → remove user %s", evt.path.stem)
            return

        cfg = loader._parse_file(evt.path)  # type: ignore[protected-access]
        if evt.kind == ChangeKind.CREATED:
            queue.append(cfg)
            LOGGER.info("YAML created → add user %s", cfg.alias)
        else:
            queue.update(cfg)
            LOGGER.info("YAML modified → update user %s", cfg.alias)

    except ConfigError as exc:
        LOGGER.warning("Invalid YAML on %s: %s", evt.path.name, exc)


# ---------------------------------------------------------------------------
# Graceful shutdown helpers
# ---------------------------------------------------------------------------
def _install_signal_handlers() -> None:
    def _sig_handler(signum, _frame) -> None:
        LOGGER.info("Received signal %s – setting STOP event", signum)
        STOP_EVT.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, _sig_handler)


# ---------------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------------
def main() -> None:

    users_dir = USERS_DIR
    keys_base = KEYS_DIR

    try:
        loader = YAMLLoader(users_dir, keys_base)
        queue = UserQueue(loader.load())
    except ConfigError as exc:
        LOGGER.error("Failed to load configs: %s", exc)
        return

    # Start background services
    watcher = ConfigWatcher(
        users_dir, lambda evt: _on_yaml_change(evt, loader, queue)
    )
    watcher.start()

    ctrl_srv = ControlServer()
    ctrl_srv.start()

    _install_signal_handlers()

    finder = SlotFinder()

    LOGGER.info("=== Bot started. Users in queue: %d ===", len(queue._dq))

    while not STOP_EVT.is_set():
        if PAUSE_EVT.is_set():
            time.sleep(0.5)
            continue

        with queue._lock:
            priority_users = [u for u in list(queue._dq) if free_slots.has_match(u)]
            for u in priority_users:
                queue._dq.remove(u)
                queue._dq.appendleft(u)
                
        user = queue.pop_left()
        if not user:
            time.sleep(2)
            continue

        LOGGER.info("Processing user %s", user.alias)
        with chrome_session(user.alias):
            booked = finder.work(user)

        if booked:
            LOGGER.info("User %s completed – removed from queue", user.alias)
            queue.remove(user.alias)
        else:
            if queue.exists(user.alias):
                queue.append(user)
                LOGGER.info("User %s re-queued", user.alias)

    LOGGER.info("Stop flag received – shutting down…")
    watcher.close()
    ctrl_srv.shutdown()


if __name__ == "__main__":
    main()
