"""html_logger.py
~~~~~~~~~~~~~~~~~

Lightweight HTML log generator used by *specialized hooks* to append rich
entries (text + optional screenshot thumbnail).  Файл создаётся один раз при
первом обращении и заполняется на лету; чтение лога браузером **не** блокирует
письмо: записи просто дописываются в конец.

Features
--------
* Один глобальный ``HtmlLogger`` на весь процесс.
* Каждый вызов ``add(text, screenshot)`` вставляет `<div class="entry">…`.
* Скриншоты копируются в ``log_dir/img/`` с уникальным именем.
* Простейший CSS и JS «lightbox»: клик по превью открывает полноразмер.
* Потокобезопасность – линейная запись через ``threading.Lock``.

Environment / settings
----------------------
* ``HTML_LOG_DIR`` env var (default *./html_log*) – папка, где лежит `session_<ts>.html`.

Usage
-----
```python
from io.html_logger import html_log
html_log.add("Знайдено слот", screenshot_path)
```
"""
from __future__ import annotations

import os
import shutil
import threading
from datetime import datetime as _dt
from pathlib import Path
from typing import Final, Optional
from project_config import LOG_LEVEL, HTML_LOG_DIR

from utils.logger import setup_logger

LOGGER = setup_logger(__name__)

__all__ = ["html_log"]


class _HtmlLogger:
    """Internal singleton that appends HTML blocks + saves images."""

    _lock = threading.Lock()

    def __init__(self) -> None:
        ts = _dt.utcnow().strftime("%Y%m%dT%H%M%SZ")
        base_dir = HTML_LOG_DIR
        base_dir.mkdir(parents=True, exist_ok=True)
        self._img_dir = base_dir / "img"
        self._img_dir.mkdir(exist_ok=True)
        self._file = base_dir / f"session_{ts}.html"
        self._init_file()

    # ------------------------------------------------------------------
    def _init_file(self) -> None:
        css = """
        body{font-family:Arial,Helvetica,sans-serif;margin:0;padding:1rem;background:#f4f4f4}
        .entry{background:#fff;margin:0.5rem 0;padding:0.5rem;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.1)}
        .entry.info{border-left:4px solid #2196F3}
        .entry.success{border-left:4px solid #4CAF50}
        .entry.error{border-left:4px solid #f44336}
        img{max-width:160px;cursor:pointer;border-radius:4px}
        #overlay{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.8);display:none;align-items:center;justify-content:center}
        #overlay img{max-width:90%;max-height:90%;}
        """
        js = """
        document.addEventListener('click',e=>{
            if(e.target.tagName==='IMG'&&e.target.dataset.full){
                const ov=document.getElementById('overlay');
                ov.querySelector('img').src=e.target.dataset.full;
                ov.style.display='flex';
            }else if(e.target.id==='overlay'){e.target.style.display='none';}
        });
        document.addEventListener('keyup',e=>{if(e.key==='Escape')document.getElementById('overlay').style.display='none';});
        """
        with self._file.open("wt", encoding="utf-8") as fh:
            fh.write(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Log {self._file.stem}</title><style>{css}</style></head><body>")
            fh.write("<div id='overlay'><img src=''/></div>")
            fh.write(f"<h1>Session log – {self._file.stem}</h1>")
            fh.write("<script>" + js + "</script>")

    # ------------------------------------------------------------------
    def add(self, text: str, level: str = "info", screenshot: Optional[Path] = None) -> None:
        ts = _dt.utcnow().isoformat(timespec="seconds")
        img_tag = ""
        if screenshot and screenshot.exists():
            dest = self._img_dir / screenshot.name
            try:
                shutil.copy2(screenshot, dest)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Cannot copy screenshot to html log: %s", exc)
            img_tag = (
                f"<img src='img/{dest.name}' data-full='img/{dest.name}' alt='scr' />"
            )
        block = (
            f"<div class='entry {level}'><b>{ts}</b> – {text} {img_tag}</div>\n"
        )
        with self._lock, self._file.open("a", encoding="utf-8") as fh:
            fh.write(block)


# singleton instance
html_log: Final[_HtmlLogger] = _HtmlLogger()
