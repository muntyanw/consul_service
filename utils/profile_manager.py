"""profile_manager.py
~~~~~~~~~~~~~~~~~~~~~

Manage temporary Chrome user‑data directories so that each **UserConfig** works
in an isolated browser profile, as mandated by the technical specification:

> «…можна використовувати окремий профайл … копіювати шаблонну…»

Key points
~~~~~~~~~~
* **Template profile** – once prepared manually (clean cookies, zoom 90 %, no
  first‑run pop‑ups) and stored in ``settings.chrome_template``.
* **Per‑run scratch dir** – for each user the template is *copied* into a new
  `_tmp_<alias>_<pid>` folder under system temp‑dir.
* After user finished, the directory is **deleted** (or preserved if
  ``keep_debug=True``).

Public API
~~~~~~~~~~
```
with ProfileManager.prepare(user_cfg) as path:
    launch_chrome(path)  # --user-data-dir=path
```

This guarantees cleanup even on exceptions.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from utils.logger import setup_logger

LOGGER = setup_logger(__name__)

__all__ = ["prepare"]


def _template_path() -> Path:
    """Return absolute path to template chrome profile from settings.yaml."""
    from yaml import safe_load

    settings_file = Path(__file__).resolve().parent.parent / "settings.yaml"
    if not settings_file.exists():
        raise RuntimeError("settings.yaml not found – cannot locate template profile")
    with settings_file.open("rt", encoding="utf-8") as fh:
        cfg = safe_load(fh) or {}
    t = cfg.get("chrome_template")
    if not t:
        raise RuntimeError("'chrome_template' missing in settings.yaml")
    p = Path(t).expanduser().resolve()
    if not p.is_dir():
        raise RuntimeError(f"Template profile dir does not exist: {p}")
    return p


@contextmanager
def prepare(user_alias: str, keep_debug: bool = False) -> Iterator[Path]:
    """Context‑manager that copies template profile → temp dir and yields it.

    Parameters
    ----------
    user_alias : str
        Used in folder name to simplify debugging.
    keep_debug : bool, default *False*
        If *True*, directory is **not** removed on exit – handy when нужно
        посмотреть, что именно сохранилось в профиле.
    """

    template = _template_path()

    tmp_base = Path(tempfile.gettempdir()) / f"chrome_{user_alias}_{os.getpid()}"
    if tmp_base.exists():  # extremely unlikely, but be safe
        shutil.rmtree(tmp_base, ignore_errors=True)

    LOGGER.debug("Copy chrome template → %s", tmp_base)
    shutil.copytree(template, tmp_base)

    try:
        yield tmp_base
    finally:
        if keep_debug:
            LOGGER.info("Keeping profile for %s at %s (debug)", user_alias, tmp_base)
        else:
            LOGGER.debug("Removing temp profile %s", tmp_base)
            shutil.rmtree(tmp_base, ignore_errors=True)
