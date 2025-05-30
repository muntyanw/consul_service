"""config_watcher.py
~~~~~~~~~~~~~~~~~~~~

Thread‑based helper that *continuously* watches the directory with user YAML
files and triggers callbacks when something is **created, modified или deleted**.

Згідно з ТЗ: «Система має автоматично слідкувати за змістом каталогу…».
Модуль не залежить від GUI і тестується окремо.

Usage example (simplified):

```python
from io.config_watcher import ConfigWatcher, ChangeEvent
from utils.logger import setup_logger

log = setup_logger(__name__)

def on_change(evt: ChangeEvent):
    log.info("%s %s", evt.kind, evt.path.name)

watcher = ConfigWatcher(pathlib.Path("users_cfg"), on_change)
watcher.start()
```

Author: chatGPT‑assistant, 2025‑05‑30
"""
from __future__ import annotations

import pathlib as _pl
import threading as _th
from dataclasses import dataclass
from enum import Enum, auto
from queue import Queue, Empty
from typing import Callable, Iterable

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from utils.logger import setup_logger

LOGGER = setup_logger(__name__)

__all__ = [
    "ChangeKind",
    "ChangeEvent",
    "ConfigWatcher",
]


class ChangeKind(Enum):
    CREATED = auto()
    MODIFIED = auto()
    DELETED = auto()


@dataclass(slots=True, frozen=True)
class ChangeEvent:
    """Struct passed to callbacks when YAML file changes."""

    path: _pl.Path
    kind: ChangeKind


class _YAMLHandler(FileSystemEventHandler):
    """Internal watchdog handler that pushes *only* ``*.yaml`` events to queue."""

    def __init__(self, queue: Queue[ChangeEvent]):
        self._q = queue

    # --- mapping watchdog → ChangeKind -------------------------------
    def on_created(self, event: FileSystemEvent):
        self._maybe_push(event, ChangeKind.CREATED)

    def on_modified(self, event: FileSystemEvent):
        self._maybe_push(event, ChangeKind.MODIFIED)

    def on_deleted(self, event: FileSystemEvent):
        self._maybe_push(event, ChangeKind.DELETED)

    # ----------------------------------------------------------------
    def _maybe_push(self, event: FileSystemEvent, kind: ChangeKind):
        if event.is_directory:
            return
        path = _pl.Path(event.src_path)
        if path.suffix.lower() != ".yaml":
            return
        self._q.put(ChangeEvent(path=path, kind=kind))


class ConfigWatcher(_th.Thread):
    """Separate thread that monitors a directory and notifies client code.

    Parameters
    ----------
    users_dir : Path
        Directory to watch (non‑recursive).
    callback  : Callable[[ChangeEvent], None]
        Function invoked **in this thread** for *each* change event.
    poll_idle : float, default 0.1
        Time (seconds) to sleep between internal queue polls.
    """

    daemon = True  # exits together with main program

    def __init__(
        self,
        users_dir: _pl.Path,
        callback: Callable[[ChangeEvent], None],
        poll_idle: float = 0.1,
    ) -> None:
        super().__init__(name="ConfigWatcher")
        self._dir = users_dir.expanduser().resolve()
        self._cb = callback
        self._idle = poll_idle
        self._queue: Queue[ChangeEvent] = Queue()
        self._observer: Observer | None = None
        self._stop_evt = _th.Event()

    # ----------------------------------------------------------------
    def run(self):
        if not self._dir.is_dir():
            LOGGER.error("ConfigWatcher: directory does not exist: %s", self._dir)
            return

        handler = _YAMLHandler(self._queue)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._dir), recursive=False)
        self._observer.start()
        LOGGER.info("ConfigWatcher started for %s", self._dir)

        try:
            while not self._stop_evt.is_set():
                try:
                    evt = self._queue.get(timeout=self._idle)
                except Empty:
                    continue
                try:
                    self._cb(evt)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception("Callback raised: %s", exc)
        finally:
            self._observer.stop()
            self._observer.join(timeout=5)
            LOGGER.info("ConfigWatcher stopped")

    # ----------------------------------------------------------------
    def close(self):
        """Request graceful shutdown and wait for thread to finish."""
        self._stop_evt.set()
        self.join()

    # ----------------------------------------------------------------
    # Convenience helper used by tests
    # ----------------------------------------------------------------
    @staticmethod
    def collect_changes(dir_: _pl.Path, timeout: float = 1.0) -> list[ChangeEvent]:
        """Blocking helper that returns the list of events during *timeout*.

        Полезно в unit‑тестах: создаём watcher, вносим изменения в FS и
        получаем список событий.
        """
        changes: list[ChangeEvent] = []

        def _cb(evt: ChangeEvent):
            changes.append(evt)

        watcher = ConfigWatcher(dir_, _cb, poll_idle=0.05)
        watcher.start()
        _th.Event().wait(timeout)
        watcher.close()
        return changes


# --------------------------------------------------------------------
# CLI demo (only prints events)
# --------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import argparse, time

    parser = argparse.ArgumentParser("Watch YAML directory and print events")
    parser.add_argument("path", help="Directory with *.yaml files")
    args = parser.parse_args()

    def printer(evt: ChangeEvent):
        LOGGER.info("%-8s %s", evt.kind.name, evt.path.name)

    w = ConfigWatcher(_pl.Path(args.path), printer)
    w.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        w.close()
