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

import os
import re
import datetime as _dt
import time
from pathlib import Path
from typing import Sequence
import pytesseract

import pyautogui as pag
import pyperclip

from core import gui_driver as gd
from utils.logger import setup_logger
from bot_io.yaml_loader import UserConfig
from project_config import (LOG_LEVEL, USERS_DIR,
                            VISIT_CHECK_DAY_TEMPLATE_PATH, VISIT_CHECK_WEEK_TEMPLATE_PATH,
                            VISIT_CHECK_MONTH_TEMPLATE_PATH)

from datetime import datetime
from babel.dates import format_date

from utils.logger import setup_logger


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
IMG_BTN_DALI = "but_dali.png"
FIELD_CHECK = "check.png"

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
# # SlotFinder implementation
# ---------------------------------------------------------------------------
LOGGER = setup_logger(__name__)

class SlotFinder:
    """Encapsulates wizard navigation and calendar scanning."""
    
    slots_found = []

    def __init__(self, fast_delay: float = 0.4, slow_delay: float = 1.2):
        self.fast = fast_delay  # small waits between field fills
        self.slow = slow_delay  # waits for page loads

    # ------------------------------------------------------------------
    def work(self, user: UserConfig) -> bool:  # noqa: C901 (complexity OK here)
        """Return *True* if slot booked, else *False* (user remains)."""
        LOGGER.debug("Start SlotFinder for %s", user.alias)
        try:

            _next_user(user.alias)
            
            self.find_free_slot_month()
            
            
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
    # Heloers
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
    
    def is_appointment_visit(self):
        gd.scroll(-800)
        LOGGER.debug("check is appointment for a visit")
        if not gd.find_text_any(["Запис на візит"], 
                            timeout = 6, lang="ukr", 
                            conf_threshold=0.6, 
                            scope=(140, 180, 680, 300)):
            
            LOGGER.error("not is appointment for a visit")
            return False
        return True
    
    def is_page_find_slots(self) -> bool:
        gd.scroll(800)
        LOGGER.debug("check is find slots")
        if not gd.find_text_any(["Дата та час візиту"], 
                            timeout = 6, lang="ukr", 
                            conf_threshold=0.6, 
                            scope=(160, 320, 650, 490)):
            
            LOGGER.error("not is appointment for a visit")
            return False
        return True
        
    def i_no_robot(self) -> bool:
        if not gd.click_text("Я не робот", 
            timeout = 6, lang="ukr", 
            conf_threshold=0.6, 
            scope=(800, 570, 1100, 620), is_debug=True):
            _error_hook("field i_no_robot", gd.take_screenshot())
            return False
        
    def check_consulates(self, country: str, cons: str) -> bool:
        gd.scroll(-800)
         
        if not gd.click_text("Країна", 
            timeout = 6, lang="ukr", 
            conf_threshold=0.6, 
            scope=(170, 640, 360, 680)):
            _error_hook("field country missing", gd.take_screenshot())
            return False
        
        time.sleep(self.fast)
        
        pyperclip.copy(country)
        time.sleep(self.fast)
        pag.hotkey('ctrl', 'v')
        time.sleep(self.fast)
        pag.press('enter')
        time.sleep(self.fast)
        pag.press('tab')
        
        pyperclip.copy(cons)
        time.sleep(self.fast)
        pag.hotkey('ctrl', 'v')
        time.sleep(self.fast)
        pag.press('enter')
        time.sleep(self.fast)
        #pag.press('enter')
        
        pag.press('tab')
        pag.press('tab')
        pag.press('enter')
        
        #gd.scroll(-800)
       
        #if not gd.click_image(IMG_BTN_DALI, scope=(376, 720, 560, 900), plus_y=20):
        #        _error_hook("button image Next after type cons missing", gd.take_screenshot())
        #        return False
            
        time.sleep(self.slow)
        
        if not self.is_appointment_visit():
            gd.scroll(-800)
            if not gd.click_image(IMG_BTN_DALI, scope=(376, 720, 560, 900), plus_y= 20):
                _error_hook("button image Next after type cons missing", gd.take_screenshot())
                
                time.sleep(self.slow)
                 
                return False
        
        return True
        
    def check_consular_service(self, consular_service:str, for_myself: bool)-> bool:
        gd.scroll(-800)
        
        time.sleep(self.fast)
        pag.press('tab')
        pag.press('tab')
        
        pyperclip.copy(consular_service)
        time.sleep(self.fast)
        pag.hotkey('ctrl', 'v')
        time.sleep(self.fast)
        pag.press('enter')
        time.sleep(self.fast)
                
        
        if for_myself:
            LOGGER.debug("find check for myself")
            is_checked = gd.detect_checkbox_type_from_frame(scope=(180, 610, 220, 650), is_debug=True)
            if is_checked == "none":
                _error_hook("field check consulate for myself missing", gd.take_screenshot())
                return False
            elif is_checked == "empty":
                LOGGER.debug("check for myself not found, try to click text")
                if not gd.click_text("Для себе", 
                    timeout = 6, lang="ukr", 
                    conf_threshold=0.6, 
                    scope=(140, 600, 600, 650)):
                
                    _error_hook("field check consulate for myself missing", gd.take_screenshot())
                    return False

        else:
            LOGGER.debug("find check for children")
            is_checked = gd.detect_checkbox_type_from_frame(scope=(180, 675, 220, 720), is_debug=True)
            if is_checked == "none":
                _error_hook("field check consulate for myself missing", gd.take_screenshot())
                return False
            elif is_checked == "empty":
                LOGGER.debug("check for children not found, try to click text")   
                if not gd.click_text("Для своєї дитини", 
                    timeout = 6, lang="ukr", 
                    conf_threshold=0.6, 
                    scope=(180, 675, 220, 720)):
                    
                    _error_hook("field check consulate for myself missing", gd.take_screenshot())
                    return False
        
            else:
                LOGGER.debug("check for children checked")
        
                
        time.sleep(self.fast)
        
        LOGGER.debug("click Dali")
        if not gd.click_image(name = IMG_BTN_DALI, scope=(370, 740, 570, 840)):
            _error_hook("button image Next after type cons missing", gd.take_screenshot())
            return False
        
        # time.sleep(8)
    
        # if not self.is_page_find_slots():
        #     gd.scroll(-800)
        #     if not gd.click_image(name = IMG_BTN_DALI, scope=(370, 740, 570, 840)):
        #         _error_hook("button image Next after type cons missing", gd.take_screenshot())
                
        #         return False
            
        #     time.sleep(8)
        
        return True

    def fill_data_personal(self, user: UserConfig) -> bool:
        LOGGER.debug("fill step 1 – personal data")

        time.sleep(self.slow)
        gd.scroll(-900)  
        time.sleep(self.fast)
            
        if not gd.click_text("день", 
                            timeout = 6, lang="ukr", 
                            conf_threshold=0.6, 
                            scope=(100, 500, 400, 700)):
                _error_hook("birthdate field day missing", gd.take_screenshot())
                return False
            
        day_number = user.birthdate.strftime("%d") 
        gd.type_text(day_number)
        time.sleep(self.fast)
        
        if not gd.click_text("місяць", 
                        timeout = 6, lang="ukr", 
                        conf_threshold=0.6, 
                        scope=(160, 500, 400, 700)):
            _error_hook("birthdate field month missing", gd.take_screenshot())
            return False
        
        time.sleep(self.fast)
        
        formatted = format_date(user.birthdate, format='d MMMM', locale='uk')
        month_in_genitive = formatted.split()[1]
        
        if not gd.click_text(month_in_genitive, 
                        timeout = 6, lang="ukr", 
                        conf_threshold=0.6, 
                        scope=(200, 500, 500, 1160)):
            
            #если месяц не найден скролим список и ищем опять
            gd._human_move(280, 600)
            
            gd.scroll(-100)
                
            if not gd.click_text(month_in_genitive, 
                timeout = 6, lang="ukr", 
                conf_threshold=0.6, 
                scope=(200, 500, 500, 1160)):
    
                _error_hook("birthdate field number month missing", gd.take_screenshot())
                return False
            
        
        if not gd.click_text("рік", 
                    timeout = 6, lang="ukr", 
                    conf_threshold=0.6, 
                    scope=(480, 500, 600, 600)):
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
                    scope=(190, 400, 490, 910)):
                _error_hook("gender field missing", gd.take_screenshot())
                return False
        else:
            if not gd.click_text("Жіноча", 
                timeout = 6, lang="ukr", 
                conf_threshold=0.6, 
                scope=(190, 400, 490, 910)):
                _error_hook("gender field missing", gd.take_screenshot())
                return False
            
        time.sleep(self.fast)
        
        LOGGER.debug("Find and click Dali")
        if not gd.click_image(IMG_BTN_DALI, scope=(190, 760, 500, 860), is_debug=False, multiscale=False):
            _error_hook("button image Next after gender missing", gd.take_screenshot())
            return False
        
        # pag.press('tab')
        # pag.press('enter')
        
            
        time.sleep(6)
      
    def is_not_free_time(self) -> bool:
        gd.scroll(-800) 
        if not gd.click_text("зараз немає вільного часу", 
                timeout = 6, lang="ukr", 
                conf_threshold=0.6, 
                scope=(160, 490, 1100, 620)):
                _error_hook("gender field missing", gd.take_screenshot())
                return False
            
    def wait_process_find_free_slots(self) -> bool:
        gd.scroll(-800) 
        count = 0
        
        while count < 10 and gd.click_text("Відбувається пошук активних", 
                timeout = 6, lang="ukr", 
                conf_threshold=0.6, 
                scope=(160, 490, 1100, 620)):
                
                LOGGER.debug(f"attempt {count}")            
                count += 1
                time.sleep(6)
        
        if count < 10:
            return True
        
        return False

    def click_wisard_month(self) -> bool:
        gd.scroll(800)
    
    def  select_type_show_slots_month(self):
        gd.scroll(800)
        
        if VISIT_CHECK_MONTH_TEMPLATE_PATH != gd.detect_image_from_frame([VISIT_CHECK_DAY_TEMPLATE_PATH, VISIT_CHECK_WEEK_TEMPLATE_PATH,VISIT_CHECK_MONTH_TEMPLATE_PATH],
                                          scope=(1140,430,1340,560), is_debug=True):
            gd.click(1280, 480)
                
        time.sleep(self.slow)
        gd.human_move(1480, 480)
    
    def normalize_date_token(self, tok: str) -> str:
        """
        Нормализует «кривые» даты вида:
        - заменяет кириллическую «З» или латинскую «Z» на цифру «3»
        - если осталось «0.mm.yyyy» (односимвольный день «0»), преобразует в «30.mm.yyyy»
        """
        t = tok.strip()

        # 1) Заменяем кириллическую З или латинскую Z на цифру 3
        t = t.replace('З', '3').replace('z', '3').replace('Z', '3')

        # 2) Если получилось «0.mm.yyyy» (без ведущей цифры дня), добавляем «3» спереди
        #    Напр.: «0.06.2025» → «30.06.2025»
        if re.fullmatch(r'0\.\d{2}\.\d{4}', t):
            t = '30' + t[1:]

        return t


    def normalize_tokens(self, tokens: List[str]) -> List[str]:
        """
        Применяет normalize_date_token ко всем токенам в списке.
        """
        return [self.normalize_date_token(tok) for tok in tokens]

    def parse_date_slots(self, tokens: List[str]) -> List[Tuple[str, str, int]]:
        """
        Из списка токенов (после нормализации) собирает кортежи
        (start_date, end_date, slots_count).
        """
        
        tokens = self.normalize_tokens(tokens)
        
        date_re = re.compile(r'^\d{2}\.\d{2}\.\d{4}$')
        result: List[Tuple[str, str, int]] = []
        i = 0
        n = len(tokens)

        while i < n:
            if i + 2 < n and date_re.match(tokens[i]) and tokens[i+1] == '-' and date_re.match(tokens[i+2]):
                start_date = tokens[i]
                end_date = tokens[i+2]

                # Ищём следующее чисто цифровое значение
                j = i + 3
                slots_count = None
                while j < n:
                    tok = tokens[j].strip()
                    if tok.isdigit():
                        slots_count = int(tok)
                        i = j
                        break
                    j += 1

                if slots_count is not None:
                    result.append((start_date, end_date, slots_count))
            i += 1

        return result

    def extract_slots_info(self, is_debug: bool = False) -> list[dict]:
        """
        Сквозная обработка скрина: ищем все строки вида "<число> ВІЛЬНИХ СЛОТІВ",
        достаём перед ними ближайшую (по вертикали) строку с диапазоном дат,
        возвращаем список словарей с полями:
        - 'date_range': строка вида "02.06.2025 — 08.06.2025" или None, если не найдено
        - 'slots_count': int (например, 16, 52, и т.д.)
        - 'bbox': (x, y, w, h) — координаты найденной фразы "16 ВІЛЬНИХ СЛОТІВ"
        """
        
        gd.contrlScroll(300)
        gd.contrlScroll(300)
        gd.contrlScroll(300)
        
        pytesseract.pytesseract.tesseract_cmd = r"C:/Program Files/Tesseract-OCR/tesseract.exe"
        
        image_bgr = gd.screen(scope = (100,180,470,1020), is_debug=is_debug, process_for_read = True)
        
        gd.contrlScroll(-300)
        gd.contrlScroll(-300)
        gd.contrlScroll(-300)
        
        # 1) Получаем данные OCR (каждое слово + координаты)
        #    output_type=DICT вернёт словарь со списками для каждого атрибута
        ocr_data = pytesseract.image_to_data(
            image_bgr,
            lang='ukr',                       # язык украинский (добавьте при необходимости 'ukr' в Tesseract)
            output_type=pytesseract.Output.DICT
        )

        results =  self.parse_date_slots(ocr_data["text"])      

        return results
    
    def find_free_slot_month(self):
        gd.scroll(-800)
        time.sleep(self.slow)
        
        month_data = self.extract_slots_info(is_debug = True)
        
        for week in 
        
        time.sleep(1)
        
    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------
    
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
        
        time.sleep(self.fast)
        pyperclip.copy(user.key_path)
        time.sleep(self.fast)
        pag.hotkey('ctrl', 'v')
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
        
        gd.scroll(-10) 
        
        if not gd.click_text("Запис на візит", 
                             timeout = 6, lang="ukr", 
                             conf_threshold=0.6, 
                             scope=(560, 100, 690, 160)):
                    _error_hook("btn visit wizard not found", gd.take_screenshot())
                    return False
                
        time.sleep(6)
        
        if not gd.click_text("Записатись на візит", 
                             timeout = 6, lang="ukr", 
                             conf_threshold=0.6, 
                             scope=(140, 300, 1200, 360)):
                    _error_hook("btn visit wizard 2 not found", gd.take_screenshot())
                    return False
                
        time.sleep(6)

        return True
   
    # ------------------------------------------------------------------
    def _fill_steps(self, user: UserConfig) -> bool:
        
        self.fill_data_personal(user)
        
        # --- step 2 ----------------------------------------------------
        for consular_service in user.services:
            for cons in user.consulates:
                if self.check_consulates(user.country, cons):
                    if self.check_consular_service(consular_service, user.for_myself):
                        LOGGER.debug("Start wait find free slots")
                        if not self.wait_process_find_free_slots():
                            _error_hook("error open page find slot", gd.take_screenshot())
                            return False
                        
                        LOGGER.debug("Check is_page_find_slots")
                        if self.is_page_find_slots():
                            LOGGER.debug("is page find slots")
                            LOGGER.debug("select type show slots month")
                            self.select_type_show_slots_month()
                            LOGGER.debug("start find_free_slot_month")
                            self.find_free_slot_month()
                            
                        else:
                            _error_hook("open page find slots failed", gd.take_screenshot())
                            return False
                            
                    else:
                        _error_hook("check consular service failed", gd.take_screenshot())
                        return False
                    
                
                else:
                    _error_hook("check_consulates failed", gd.take_screenshot())
                    return False

      
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
                LOGGER.debug("No slots in calendar – exit user")
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
                    LOGGER.debug("Slot %s earlier than min_date – skip", slot_date)
                    pag.press("esc")  # закрыть диалог слота

            # если дошли сюда – slot был невалиден или не найден → next day
            gd.click_image(BTN_NEXT_DAY, timeout=2)
            delta_days += 1
            if delta_days > 120:
                return False
