# project_config.py

from __future__ import annotations
import yaml
from pathlib import Path
from typing import Any, Dict

# -------------------------------------------------------------------
# 1) Определяем путь до settings.yaml (корень проекта)
# -------------------------------------------------------------------
_SETTINGS_PATH = Path(__file__).resolve().parent / "settings.yaml"

# -------------------------------------------------------------------
# 2) Загружаем YAML-данные при импорте (одно чтение)
# -------------------------------------------------------------------
try:
    with _SETTINGS_PATH.open("rt", encoding="utf-8") as _fh:
        _RAW_SETTINGS: Dict[str, Any] = yaml.safe_load(_fh) or {}
except FileNotFoundError:
    raise RuntimeError(f"settings.yaml not found at {_SETTINGS_PATH}")

# -------------------------------------------------------------------
# 3) “Высвобождаем” из _RAW_SETTINGS нужные переменные в виде констант
# -------------------------------------------------------------------
# Пример: пути к каталогам
USERS_DIR: Path = Path(_RAW_SETTINGS.get("users_dir", "users_cfg")).expanduser().resolve()
KEYS_DIR:  Path = Path(_RAW_SETTINGS.get("keys_dir", "keys")).expanduser().resolve()

# Путь до шаблона профиля Chrome
CHROME_TEMPLATE: Path = Path(_RAW_SETTINGS.get("chrome_template", "chrome_template/profile")) \
                            .expanduser().resolve()
CHROME_TEMPLATES: Path = Path(_RAW_SETTINGS.get("chrome_templates", "chrome_template/profiles")) \
                            .expanduser().resolve()

# Флаг: сохраняем ли профили между запусками
KEEP_PROFILES: bool = bool(_RAW_SETTINGS.get("keep_profiles", False))

# Порт TCP-контроля (pause/resume/stop)
CONTROL_PORT: int = int(_RAW_SETTINGS.get("control_port", 4567))

# Параметры для целевого монитора
MONITOR_WIDTH:  int = int(_RAW_SETTINGS.get("monitor_width", 1920))
MONITOR_HEIGHT: int = int(_RAW_SETTINGS.get("monitor_height", 1080))
MONITOR_INDEX: int = int(_RAW_SETTINGS.get("monitor_index", 1))

TEMPLATE_DIR: Path = Path(_RAW_SETTINGS.get("ui_images", "")).expanduser().resolve()

# Настройки HTML-логгера
HTML_LOG_DIR: Path = Path(_RAW_SETTINGS.get("html_log_dir", "data/html_log")).expanduser().resolve()

# Уровень логирования (строка, например "DEBUG", "INFO")
LOG_LEVEL: str = str(_RAW_SETTINGS.get("log_level", "INFO")).upper()

TESSDATA_PREFIX: str = str(_RAW_SETTINGS.get("tessdata_prefix", "C:\\Program Files\\Tesseract-OCR")).upper()
TESSERCAT_CMD: str = str(_RAW_SETTINGS.get("tesseract_cmd", "C:\\Program Files\\Tesseract-OCR\\tesseract.exe")).upper()