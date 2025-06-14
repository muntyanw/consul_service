"""logger.py
~~~~~~~~~~~~

Project‑wide helper that configures **console + rotating file** logging based on
environment variables.  Код предоставлен пользователем; добавлены минимальные
doc‑string и type‑hints.

Environment variables
---------------------
* ``LOG_LEVEL`` – DEBUG / INFO / WARNING / ERROR (default INFO)
* ``LOG_FILE``  – path to log‑file; may be relative (to project root) or absolute

Usage
-----
```python
from utils.logger import setup_logger
LOGGER = setup_logger(__name__)
```

Calling ``setup_logger(__name__)`` many times is safe – существующие хендлеры
очищаются, повторной конфигурации не будет.
"""
from __future__ import annotations
import sys
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final
from project_config import LOG_LEVEL

def get_log_path() -> Path:
    if getattr(sys, 'frozen', False):  # если запущен из .exe
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).resolve().parent.parent  # каталог проекта в режиме разработки

    log_file = os.getenv("LOG_FILE", "app.log")  # имя файла лога по умолчанию
    log_path = Path(log_file)

    if not log_path.is_absolute():
        log_path = base_dir / log_path

    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def setup_logger(name: str) -> logging.Logger:  # noqa: D401 – imperative style
    """Return configured ``logging.Logger`` instance shared across project."""
    numeric_level: int = getattr(logging, LOG_LEVEL, logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(numeric_level)

    # Clear duplicate handlers if re‑invoked
    if logger.hasHandlers():
        logger.handlers.clear()

    fmt = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    formatter = logging.Formatter(fmt)

    # --- Console handler ---------------------------------------------
    console = logging.StreamHandler()
    console.setLevel(numeric_level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # --- Rotating file handler ---------------------------------------
    log_path = get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=10 * 1024 * 1024,  # 10 MiB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
