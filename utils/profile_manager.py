from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import yaml
from utils.logger import setup_logger

LOGGER = setup_logger(__name__)

# Load global settings
_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.yaml"
try:
    with _SETTINGS_PATH.open("rt", encoding="utf-8") as fh:
        _SETTINGS = yaml.safe_load(fh) or {}
except FileNotFoundError:
    LOGGER.warning("settings.yaml not found at %s; defaulting to temporary profiles", _SETTINGS_PATH)
    _SETTINGS = {}

# Configuration flags
KEEP_PROFILES: bool = bool(_SETTINGS.get("keep_profiles", False))
TEMPLATE_PATH: Path = Path(_SETTINGS.get("chrome_template", "chrome_template/profile")).expanduser().resolve()
TEMPLATE_PATHS: Path = Path(_SETTINGS.get("chrome_templates", "chrome_template/profiles")).expanduser().resolve()

__all__ = ["prepare_profile"]

@contextmanager
def prepare_profile(user_alias: str) -> Iterator[Path]:
    """
    Context manager: yields a directory for Chrome user data.

    If KEEP_PROFILES is True, uses `profiles/<alias>` under project root;
    otherwise, copies TEMPLATE_PATH to a temp dir per session and deletes it.
    """
    if KEEP_PROFILES:
        # Persistent mode: one folder per user
        project_root = Path(__file__).resolve().parent.parent
        profiles_dir = TEMPLATE_PATHS
        profiles_dir.mkdir(parents=True, exist_ok=True)
        target_dir = profiles_dir / user_alias
        if not target_dir.exists():
            LOGGER.debug("Creating persistent profile for %s", user_alias)
            shutil.copytree(TEMPLATE_PATH, target_dir)
        else:
            LOGGER.debug("Reusing persistent profile for %s", user_alias)
        yield target_dir
    else:
        # Temporary mode: fresh copy each session
        tmp_base = Path(tempfile.gettempdir()) / f"chrome_{user_alias}_{os.getpid()}"
        if tmp_base.exists():
            shutil.rmtree(tmp_base, ignore_errors=True)
        LOGGER.debug("Copy chrome template to temp %s", tmp_base)
        shutil.copytree(TEMPLATE_PATH, tmp_base)
        try:
            yield tmp_base
        finally:
            LOGGER.debug("Removing temp profile %s", tmp_base)
            shutil.rmtree(tmp_base, ignore_errors=True)