"""slot_finder.py
~~~~~~~~~~~~~~~~

High‑level *business* logic that drives the 4‑step wizard on e‑consul.gov.ua.
Работает поверх ``core.gui_driver`` и использует ``UserConfig``.

API
---
```
from core.slot_finder import SlotFinder
ok = SlotFinder().work(user_cfg)
```
* Возвращает **True**, если слот успешно забронирован – user «исчерпан».
* Возвращает **False**, если пройти все даты/консульства не удалось – user
  остаётся в очереди и будет повторно обработан позже.

> **Важно**: реализация deliberately упрощена – многие координаты и имена PNG
> шаблонов нужно будет донастроить после первого «полёвого» запуска.
"""
from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path
from typing import Sequence

import pyautogui as pag
import pyperclip

from core import gui_driver as gd
from utils.logger import setup_logger
from bot_io.yaml_loader import UserConfig
from project_config import LOG_LEVEL, USERS_DIR

from datetime import datetime
from babel.dates import format_date


try:
    # lazy import to avoid circular dep when hooks file ещё не создан
    from core.specialized_hooks import (
        next_user_hook as _next_user,
        slot_found_hook as _slot_found,
        slot_obtained_hook as _slot_obtained,
        error_hook as _error_hook,
    )
except ModuleNotFoundError:  # tests can monkey‑patch later
    def _next_user(*a, **kw):
        pass

    def _slot_found(*a, **kw):
        pass

    def _slot_obtained(*a, **kw):
        pass

    def _error_hook(*a, **kw):
        pass

LOGGER = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Constants – names of PNG templates (must exist in assets/)
# ---------------------------------------------------------------------------
BTN_PERSONAL_KEY = "btn_personal_key.png"
FIELD_KEY_PATH = "field_key_path.png"
FIELD_KEY_PASS = "field_key_pass.png"
BTN_LOGIN = "btn_login.png"
WELCOME_BANNER = "banner_welcome.png"

BTN_VISIT = "btn_visit.png"  # «Запис на візит»
BTN_BOOK = "btn_book.png"    # «Записатись на візит»

# --- wizard steps -----------------------------------------------------------
# шаблоны полей; будут уточняться в runtime
FIELD_BIRTHDATE = "field_birthdate.png"
FIELD_GENDER = "field_gender.png"
FIELD_COUNTRY = "field_country.png"
FIELD_CONSULATE = "field_consulate.png"
FIELD_SERVICE = "field_service.png"
FIELD_PERSON_NAME = "field_person_name.png"
BTN_NEXT = "btn_next.png"

# --- calendar ---------------------------------------------------------------
BTN_MODE_DAY = "btn_mode_day.png"
LBL_NO_SLOTS_DAY = "lbl_no_slots_day.png"
LBL_NO_SLOTS_ALL = "lbl_no_slots_all.png"
BTN_NEXT_DAY = "btn_next_day.png"
SLOT_ANY_TIME = "slot_any.png"  # условный шаблон «07:40»

# ---------------------------------------------------------------------------
# SlotFinder implementation
# ---------------------------------------------------------------------------


class SlotFinder:
    """Encapsulates wizard navigation and calendar scanning."""

    def __init__(self, fast_delay: float = 0.4, slow_delay: float = 1.2):
        self.fast = fast_delay  # small waits between field fills
        self.slow = slow_delay  # waits for page loads

    # ------------------------------------------------------------------
    def work(self, user: UserConfig) -> bool:  # noqa: C901 (complexity OK here)
        """Return *True* if slot booked, else *False* (user remains)."""
        LOGGER.info("Start SlotFinder for %s", user.alias)
        try:
            _next_user(user.alias)
            if not self._login(user):
                return False
            if not self._open_visit_wizard():
                return False
            if not self._fill_steps(user):
                return False
            return self._scan_calendar(user)
        except Exception as exc:  # noqa: BLE001
            scr = gd.take_screenshot()
            _error_hook(f"Exception in SlotFinder: {exc}", scr)
            return False

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    def _is_login(self) -> bool:
            LOGGER.debug("check is login")
            if not gd.find_text_any(["Вітаємо", "Вітаємо.", "Вітаємо,"], 
                                timeout = 6, lang="ukr", 
                                conf_threshold=0.6, 
                                scope=(270, 240, 540, 300)):
                
                LOGGER.error("not logged in – welcome banner not found")
                return False
            return True
    
    def _login(self, user: UserConfig) -> bool:
        
        if self._is_login():
            return True  
        
        LOGGER.debug("login step – personal key")
        
        if not gd.click_text("Особистий ключ", 
                             timeout = 6, lang="ukr", 
                             conf_threshold=0.6, 
                             scope=(600, 420, 940, 520)):
            
            _error_hook("personal login button not found", gd.take_screenshot())
            
        time.sleep(self.slow)
        
        if not gd.click_text("Оберіть ключ на своєму носієві", 
                             timeout = 6, lang="ukr", conf_threshold=0.6, 
                             scope=(700, 420, 1200, 620)):
            
            _error_hook("personal select key button not found", gd.take_screenshot())
        #time.sleep(self.slow)
        
        #вставка пути к ключу
        time.sleep(self.fast)
        #pyperclip.copy(user.key_path)
        #time.sleep(self.fast)
        #pag.press('enter')
        #pag.hotkey('ctrl', 'v')
        pag.typewrite(str(user.key_path), interval=0.02)
        time.sleep(self.fast)
        pag.press('enter')
        time.sleep(self.fast)
                
        #вставка пароля
        pyperclip.copy(user.key_password)
        time.sleep(self.fast)
        pag.hotkey('ctrl', 'v')
        time.sleep(self.fast)
        pag.press('enter')
        time.sleep(self.fast)
        
        time.sleep(8)

        if self._is_login():
            return True 

        _error_hook("Error login", gd.take_screenshot())
        return False

    # ------------------------------------------------------------------
    def _open_visit_wizard(self) -> bool:
        
        LOGGER.debug("open visit wizard")
        
        if not gd.click_text("Запис на візит", 
                             timeout = 6, lang="ukr", 
                             conf_threshold=0.6, 
                             scope=(210, 100, 480, 160)):
                    _error_hook("btn visit wizard not found", gd.take_screenshot())
                    return False
                
        time.sleep(6)
        
        if not gd.click_text("Записатись на візит", 
                             timeout = 6, lang="ukr", 
                             conf_threshold=0.6, 
                             scope=(540, 300, 690, 360)):
                    _error_hook("btn visit wizard 2 not found", gd.take_screenshot())
                    return False
                
        time.sleep(6)

        return True

    # ------------------------------------------------------------------
    def _fill_steps(self, user: UserConfig) -> bool:
        LOGGER.debug("fill step 1 – personal data")
        
        gd.scroll(10)  
            
        if not gd.click_text("день", 
                            timeout = 6, lang="ukr", 
                            conf_threshold=0.6, 
                            scope=(170, 530, 260, 560)):
                _error_hook("birthdate field day missing", gd.take_screenshot())
                return False
            
        day_number = user.birthdate.strftime("%d") 
        gd.type_text(day_number)
        time.sleep(self.fast)
        
        if not gd.click_text("місяць", 
                        timeout = 6, lang="ukr", 
                        conf_threshold=0.6, 
                        scope=(260, 530, 450, 560)):
            _error_hook("birthdate field month missing", gd.take_screenshot())
            return False
        
        time.sleep(self.fast)
        
        formatted = format_date(user.birthdate, format='d MMMM', locale='uk')
        month_in_genitive = formatted.split()[1]
        
        if not gd.click_text(month_in_genitive, 
                        timeout = 6, lang="ukr", 
                        conf_threshold=0.6, 
                        scope=(260, 570, 390, 960)):
            
            #если месяц не найден скролим список и ищем опять
            gd._human_move(280, 600)
            
            gd.scroll(2)
                
            if not gd.click_text(month_in_genitive, 
                timeout = 6, lang="ukr", 
                conf_threshold=0.6, 
                scope=(260, 570, 390, 960)):
    
                _error_hook("birthdate field number month missing", gd.take_screenshot())
                return False
            
        
        if not gd.click_text("рік", 
                    timeout = 6, lang="ukr", 
                    conf_threshold=0.6, 
                    scope=(480, 530, 550, 560)):
            _error_hook("birthdate field year missing", gd.take_screenshot())
            return False
        
        time.sleep(self.fast)
        
        year = user.birthdate.year
        gd.type_text(str(year))
        
        time.sleep(self.fast)
        
        if user.gender == "Male":
            if not gd.click_text("Чоловіча", 
                    timeout = 6, lang="ukr", 
                    conf_threshold=0.6, 
                    scope=(190, 660, 490, 710)):
                _error_hook("gender field missing", gd.take_screenshot())
                return False
        else:
            if not gd.click_text("Жіноча", 
                timeout = 6, lang="ukr", 
                conf_threshold=0.6, 
                scope=(190, 660, 490, 710)):
                _error_hook("gender field missing", gd.take_screenshot())
                return False
            
        time.sleep(self.slow)
        
        if not gd.click_text("Далі", 
            timeout = 6, lang="ukr", 
            conf_threshold=0.6, 
            scope=(190, 770, 360, 820)):
            _error_hook("button Next after gender missing", gd.take_screenshot())
            return False
        
        time.sleep(self.slow)
        
        gd.scroll(10)
        
        # --- step 2 ----------------------------------------------------
        
        if not gd.click_text("Країна", 
            timeout = 6, lang="ukr", 
            conf_threshold=0.6, 
            scope=(170, 640, 360, 680)):
            _error_hook("field country missing", gd.take_screenshot())
            return False
        
        time.sleep(self.fast)
        
        gd.type_text(user.country)
        time.sleep(self.fast)
        pag.press("enter")
        
        gd.scroll(10)
        

        for cons in user.consulates:
            gd.click_image(FIELD_CONSULATE)
            gd.type_text(cons)
            pag.press("enter")
        gd.click_image(BTN_NEXT)
        time.sleep(self.slow)

        # --- step 3 ----------------------------------------------------
        gd.click_image(FIELD_SERVICE)
        gd.type_text(user.service)
        pag.press("enter")
        time.sleep(self.fast)

        if user.surname:  # значит «для іншої особи»
            gd.click_image(FIELD_PERSON_NAME)
            full_name = " ".join(filter(None, [user.surname, user.name, user.patronymic]))
            gd.type_text(full_name)
            time.sleep(self.fast)

        gd.click_image(BTN_NEXT)
        time.sleep(self.slow)
        return True

    # ------------------------------------------------------------------
    # Calendar scanning -------------------------------------------------
    # ------------------------------------------------------------------
    def _scan_calendar(self, user: UserConfig) -> bool:
        LOGGER.debug("calendar scanning start")
        gd.click_image(BTN_MODE_DAY, timeout=4)  # switch to daily
        earliest = user.earliest_allowed
        today = _dt.date.today()
        delta_days = 0
        while True:
            if gd.click_image(LBL_NO_SLOTS_ALL, timeout=1, confidence=0.9):
                LOGGER.info("No slots in calendar – exit user")
                return False

            if gd.click_image(LBL_NO_SLOTS_DAY, timeout=1):
                gd.click_image(BTN_NEXT_DAY, timeout=2)
                delta_days += 1
                if delta_days > 120:  # safety limit
                    return False
                continue

            # пытаемся кликнуть любой тайм-слот
            if gd.click_image(SLOT_ANY_TIME, timeout=2, confidence=0.8):
                slot_date = today + _dt.timedelta(days=delta_days)
                _slot_found(user.country, user.consulates[0], user.service, slot_date, "time???", gd.take_screenshot())
                if slot_date >= earliest:
                    pag.press("enter")  # подтвердить дату
                    time.sleep(1)
                    # captcha «я не робот» придётся заверять вручную или RU‑Captcha API
                    _slot_obtained(user.alias, slot_date, "time???", gd.take_screenshot())
                    return True
                else:
                    LOGGER.info("Slot %s earlier than min_date – skip", slot_date)
                    pag.press("esc")  # закрыть диалог слота

            # если дошли сюда – slot был невалиден или не найден → next day
            gd.click_image(BTN_NEXT_DAY, timeout=2)
            delta_days += 1
            if delta_days > 120:
                return False
