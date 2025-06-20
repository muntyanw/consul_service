"""specialized_hooks.py
~~~~~~~~~~~~~~~~~~~~~~

*Спеціалізовані функції* з техзавдання.  Вони не повинні зупиняти головний
потік надовго, але боту слід «дочекатися» їхнього завершення перед тим, як
продовжити — тут це реалізовано звичайним блокуючим викликом (немає async).

Функції лише виводять повідомлення у консоль, додають запис у HTML‑лог та у
текстовий лог (rotating).  Скриншот, якщо передано, копіюється у під‑каталог
`img/` поряд з HTML‑файлом (див. ``io.html_logger``).

API, яке використовують інші модулі
-----------------------------------
* ``next_user_hook(alias)``
* ``slot_found_hook(country, consulate, service, date, time, screenshot)``
* ``# The `slot_obtained_hook` function is a specialized function in the code that is called when a
# slot has been successfully obtained or booked for a user. It logs a success message indicating
# that a slot has been reserved for a specific user at a particular date and time. Additionally,
# it adds this information to the HTML log with a success level and optionally includes a
# screenshot of the booking process.
slot_obtained_hook(alias, date, time, screenshot)``
* ``error_hook(text, screenshot)``

Якщо в майбутньому знадобиться більш складна інтеграція (наприклад, Telegram
повідомлення), — достатньо змінити реалізацію цих функцій, не торкаючись
``SlotFinder`` чи ``manager``.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional

from bot_io.html_logger import html_log
from utils.logger import setup_logger

import traceback

LOGGER = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Helper: formatted timestamp
# ---------------------------------------------------------------------------

def _ts() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------------------------------------------------------
# Hooks implementation
# ---------------------------------------------------------------------------

def next_user_hook(alias: str) -> None:
    msg = f"▶ Перехід до користувача: <b>{alias}</b>"
    LOGGER.info(msg)
    html_log.add(msg, level="info")


def slot_found_hook(
    alias: str,
    country: str,
    consulate: str,
    service: str,
    dt: _dt.date,
    time_: str,
    screenshot: Optional[Path] = None,
) -> None:
    msg = (
        f"‎🕓 Знайдено слот – {country} / {consulate} / {service} – "
        f"<b>{dt} {time_}</b>"
    )
    LOGGER.info(msg)
    html_log.add(msg, level="info", screenshot=screenshot)


def slot_obtained_hook(
    alias: str,
    country: str,
    consulate: str,
    service: str,
    dt: _dt.date,
    time_: str,
    screenshot: Optional[Path] = None
) -> None:
    msg = f"✅ Слот {country} - {consulate} - {service} - {dt} - {time_} заброньовано для <b>{alias}</b>"
    LOGGER.info(msg)
    html_log.add(msg, level="success", screenshot=screenshot)


def error_hook(text: str, screenshot: Optional[Path] = None) -> None:
    # Получаем стек вызовов
    tb = traceback.extract_stack()[:-1]  # убираем текущий вызов hook'а
    last = tb[-1] if tb else None

    location = f"{last.filename}:{last.lineno}" if last else "unknown location"
    msg = f"❌ ПОМИЛКА – {text} (в файлі {location})"

    LOGGER.error(msg)
    html_log.add(msg, level="error", screenshot=screenshot)
