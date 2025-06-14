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
from datetime import date, datetime, timedelta
import time
from pathlib import Path
from typing import Sequence
import pytesseract

import pyautogui as pag
import pyperclip

from core import gui_driver as gd
from utils.logger import setup_logger
from bot_io.yaml_loader import UserConfig, YAMLLoader
from project_config import (LOG_LEVEL, USERS_DIR,
                            VISIT_CHECK_DAY_TEMPLATE_PATH, VISIT_CHECK_WEEK_TEMPLATE_PATH,
                            VISIT_CHECK_MONTH_TEMPLATE_PATH)

from datetime import datetime
from babel.dates import format_date

from utils.logger import setup_logger
from core.free_slot_db import FreeSlotRegistry


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
IMG_BTN_CONFIRM = "but_confirm.png"
FIELD_CHECK = "check.png"
IMG_BTN_COMEBACK = "comeback.png"
IMG_BTN_ITS_CLEAR = "its_clear.png"
IMG_BTN_RELOAD_PAGE = "reload_page.png"
IMG_BTN_MAKE_APPOINT_VISIT = "make_appoint_visit.png"
IMG_BTN_QUEUE = "queue.png"

WEEK_DAYS = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"]
MONTHS = ["січень", "лютий", "березень", "квітень", "травень", "червень", "липень", "серпень", "вересень", "жовтень", "листопад", "грудень"]

# ---------------------------------------------------------------------------
# # SlotFinder implementation
# ---------------------------------------------------------------------------
LOGGER = setup_logger(__name__)

free_slots = FreeSlotRegistry()

class SlotFinder:
    """Encapsulates wizard navigation and calendar scanning."""
    
    slots_found = []

    def __init__(self, fast_delay: float = 0.4, slow_delay: float = 1.2, s_slow_delay: float = 4.8):
        self.fast = fast_delay  # small waits between field fills
        self.slow = slow_delay  # waits for page loads
        self.s_slow = s_slow_delay  # waits for page loads

    # ------------------------------------------------------------------
    def work(self, user: UserConfig) -> bool:  # noqa: C901 (complexity OK here)
        """Return *True* if slot booked, else *False* (user remains)."""
        LOGGER.debug("Start SlotFinder for %s", user.alias)
        try:
            _next_user(user.alias)
            
            if not self._login(user):
                return False
            
            return self._find_slots(user)
        
        except Exception as exc:  # noqa: BLE001
            scr = gd.take_screenshot()
            _error_hook(f"Exception in SlotFinder: {exc}", scr)
            return False
        
    # ------------------------------------------------------------------
    # Heloers
    # ------------------------------------------------------------------
    
    def _is_login(self) -> bool:
            LOGGER.debug("check is login")
            gd.scroll(2000)
            gd.pause(self.slow)
            if not gd.find_text_any(["Вітаємо", "Вітаємо.", "Вітаємо,"], 
                                count = 2, 
                                pause_attempt_sec=4,
                                lang="ukr", 
                                scope=(240, 220, 540, 340), is_debug=False):
                
                LOGGER.error("not logged in – welcome banner not found")
                return False
            return True
        
    def is_success_blocked_slot(self) -> bool:
            LOGGER.debug("check success_blocked_slot")
            if not gd.find_text("ми обробляємо", 
                                count = 4, 
                                pause_attempt = 4,
                                lang="ukr", 
                                scope=(610, 440, 1300, 580)):
                
                LOGGER.error("not success_blocked_slot")
                return False
            return True
    
    def is_appointment_visit(self):
        gd.scroll(-800)
        gd.pause(self.slow)
        LOGGER.debug("check is appointment for a visit")
        if not gd.find_text_any(["Запис на візит"], 
                            count = 3, lang="ukr", 
                            scope=(140, 180, 680, 300)):
            
            LOGGER.error("not is appointment for a visit")
            return False
        return True
    
    def is_page_find_slots(self) -> bool:
        gd.scroll(800)
        LOGGER.debug("check is find slots")
        if not gd.find_text_any(["Дата та час візиту"], 
                            count = 10,
                            pause_attempt_sec=6,
                            lang="ukr", 
                            scope=(160, 320, 650, 490)):
            
            LOGGER.error("not is appointment for a visit")
            return False
        
        LOGGER.debug("yes - is page find slots")
        gd.pause(self.slow)
        return True
        
    def i_no_robot(self, count_attempt_find:int = 4, is_debug: bool = False) -> bool:
        gd.pause(self.slow)
        if not gd.click_text(["Я не", "я неробот", "янеробот"], 
            count_attempt_find=count_attempt_find,
            pause_attempt = 4,
            lang="ukr", 
            scope=(730, 554, 1300, 790), is_debug=is_debug):
            
                _error_hook("field i_no_robot missing", gd.take_screenshot())
                return False
        return True
        
    def check_consulates(self, country: str, cons: str) -> bool:
        
        LOGGER.debug(f"Start fill consulates {country}, {cons}")
        
        gd.scroll(-800)
         
        if not gd.click_text("Країна", 
            count_attempt_find=2,
            lang="ukr", 
            scope=(170, 640, 360, 680)):
            
            _error_hook("field country missing", gd.take_screenshot())
            return False
        
        gd.pause(self.fast)
        
        pyperclip.copy(country)
        gd.pause(self.fast)
        pag.hotkey('ctrl', 'v')
        gd.pause(self.fast)
        pag.press('enter')
        gd.pause(self.fast)
        pag.press('tab')
        
        pyperclip.copy(cons)
        gd.pause(self.fast)
        pag.hotkey('ctrl', 'v')
        gd.pause(self.fast)
        pag.press('enter')
        gd.pause(self.fast)
        #pag.press('enter')
        
        pag.press('tab')
        pag.press('tab')
        pag.press('enter')
        
        gd.pause(self.slow)
        
        if not self.is_appointment_visit():
            gd.scroll(-800)
            if not gd.click_image(IMG_BTN_DALI, scope=(376, 720, 560, 900), plus_y= 20):
                _error_hook("button image Next after type cons missing", gd.take_screenshot())
                
                gd.pause(self.slow)
                 
                return False
        
        return True
        
    def check_consular_service(self, consular_service:str, for_myself: bool)-> bool:
        
        LOGGER.debug(f"Start check consular service: {consular_service}, for_myself: {for_myself}")
        
        gd.scroll(-2000)
        
        gd.pause(self.slow)
        #pag.press('tab')
        #pag.press('tab')
        
        pos = gd.click_text("Виберіть послугу", 
                count_attempt_find=2,
                pause_attempt=4,
                lang="ukr", 
                scope=(130, 470, 390, 550))
        if not pos:
                
                _error_hook("field check consulate for myself missing", gd.take_screenshot())
                return False
        
        
        gd.pause(self.slow)
        x, y = pos
        
        gd.pause(self.fast)
        #gd.human_move_diff(0, -30)
        #gd.pause(self.slow)
        LOGGER.debug(f"copy to clipboard {consular_service}")
        pyperclip.copy(consular_service)
        gd.click(x, y)
        gd.pause(self.fast)
        pag.hotkey('ctrl', 'v')
        gd.pause(self.slow)
        gd.pause(self.slow)
        LOGGER.debug(f"press enter")
        pag.press('enter')
        gd.pause(self.s_slow)
        gd.pause(self.s_slow)
        
        if for_myself:
            LOGGER.debug("find check for myself")
            is_checked = gd.detect_checkbox_type_from_frame(scope=(180, 610, 220, 650), is_debug=False)
            if is_checked == "none":
                _error_hook("field check consulate for myself missing", gd.take_screenshot())
                return False
            elif is_checked == "empty":
                LOGGER.debug("check for myself not found, try to click text")
                if not gd.click_text("Для себе", 
                    lang="ukr", 
                    scope=(140, 600, 600, 650)):
                
                    _error_hook("field check consulate for myself missing", gd.take_screenshot())
                    return False

        else:
            LOGGER.debug("find check for children")
            is_checked = gd.detect_checkbox_type_from_frame(scope=(180, 675, 220, 720), is_debug=False)
            if is_checked == "none":
                _error_hook("field check consulate for myself missing", gd.take_screenshot())
                return False
            elif is_checked == "empty":
                LOGGER.debug("check for children not found, try to click text")   
                if not gd.click_text("Для своєї дитини", 
                    lang="ukr", 
                    scope=(180, 675, 220, 720)):
                    
                    _error_hook("field check consulate for myself missing", gd.take_screenshot())
                    return False
        
            else:
                LOGGER.debug("check for children checked")
        
                
        gd.pause(self.fast)
        
        LOGGER.debug("click Dali")
        if not gd.click_image(name = IMG_BTN_DALI, scope=(370, 740, 570, 840)):
            _error_hook("button image Next after type cons missing", gd.take_screenshot())
            return False
    
        return True

    def fill_data_personal(self, user: UserConfig) -> bool:
        LOGGER.debug("fill step 1 – personal data")

        gd.pause(self.slow)
        gd.scroll(-2000)  
        gd.pause(self.fast)
            
        if not gd.click_text("день", 
                            lang="ukr",
                            count_attempt_find=4,
                            pause_attempt=4, 
                            scope=(100, 500, 400, 700)):
            
                gd.reload_page()
                
                if not gd.click_text("день", 
                            lang="ukr",
                            count_attempt_find=4,
                            pause_attempt=4, 
                            scope=(100, 500, 400, 700)):
        
                
                    _error_hook("birthdate field day missing", gd.take_screenshot())
                    return False
                
        day_number = user.birthdate.strftime("%d") 
        gd.type_text(day_number)
        gd.pause(self.fast)
        
        if not gd.click_text("місяць", 
                        count_attempt_find=2,
                        lang="ukr", 
                        scope=(260, 520, 400, 570), is_debug=False):
            _error_hook("birthdate field month missing", gd.take_screenshot())
            return False
        
        gd.pause(self.fast)
        
        formatted = format_date(user.birthdate, format='d MMMM', locale='uk')
        month_in_genitive = formatted.split()[1]
        
        if not gd.click_text(month_in_genitive, 
                        lang="ukr", 
                        scope=(200, 500, 500, 1160)):
            
            #если месяц не найден скролим список и ищем опять
            gd.human_move_diff(0, 60)
            gd.pause(self.fast)
            gd.scroll(-100)
            gd.pause(self.slow)
                
            if not gd.click_text(month_in_genitive, 
                lang="ukr", 
                scope=(200, 500, 500, 1160), is_debug=False):
    
                _error_hook("birthdate field number month missing", gd.take_screenshot())
                return False
            
        
        if not gd.click_text("рік", 
                    lang="ukr", 
                    scope=(480, 500, 600, 600)):
            _error_hook("birthdate field year missing", gd.take_screenshot())
            return False
        
        gd.pause(self.fast)
        
        year = user.birthdate.year
        gd.type_text(str(year))
        
        gd.pause(self.fast)
        
        if user.gender == "Male":
            if not gd.click_text("Чоловіча", 
                    lang="ukr", 
                    scope=(190, 400, 490, 910)):
                _error_hook("gender field missing", gd.take_screenshot())
                return False
        else:
            if not gd.click_text("Жіноча", 
                lang="ukr", 
                scope=(190, 400, 490, 910)):
                _error_hook("gender field missing", gd.take_screenshot())
                return False
            
        gd.pause(self.fast)
        
        LOGGER.debug("Find and click Dali")
        if not gd.click_image(IMG_BTN_DALI, scope=(190, 760, 500, 860), 
                              is_debug=False, multiscale=False, confidence=0.6):
            _error_hook("button image Next after gender missing", gd.take_screenshot())
            return False
        
        gd.pause(self.slow)
        gd.pause(self.slow)
        gd.pause(self.slow)
        
        return True
      
    def is_not_free_time(self) -> bool:
        gd.scroll(-800) 
        if not gd.click_text("зараз немає вільного часу", 
                lang="ukr", 
                scope=(160, 490, 1100, 620)):
                _error_hook("gender field missing", gd.take_screenshot())
                return False
            
    def wait_process_find_free_slots(self) -> bool|None:
        
        LOGGER.debug("Start wait find free slots")
        gd.pause(self.s_slow)
        gd.scroll(-800) 
        count = 0
        
        while count < 20:
            LOGGER.debug(f"attempt {count}")            
            count += 1
            
            if gd.find_text("На жаль", 
                lang="ukr", 
                scope=(160, 490, 1100, 620), is_debug=False):
                
                LOGGER.debug(f"No free slots - exit")
                return None
            
            if gd.find_image(IMG_BTN_QUEUE, scope=(370, 720, 780, 830)):
                LOGGER.debug(f"No free slots - exit")
                return None
            
            if not gd.find_text("Відбувається пошук активних", 
                lang="ukr", 
                scope=(160, 490, 1100, 620), is_debug=False):
            
                return True
            
            gd.pause(self.s_slow)
            gd.pause(self.s_slow)
            gd.pause(self.s_slow)
            
               
        return False

    def select_type_show_slots_month(self):
        LOGGER.debug("select type show slots month")
        
        gd.click(20, 480)
        gd.scroll(800)
        
        if VISIT_CHECK_MONTH_TEMPLATE_PATH != gd.detect_image_from_frame(
            [VISIT_CHECK_DAY_TEMPLATE_PATH, VISIT_CHECK_WEEK_TEMPLATE_PATH,VISIT_CHECK_MONTH_TEMPLATE_PATH],
            scope=(1140,430,1340,560), is_debug=False):
            
            gd.click(1280, 480)
                
        gd.pause(self.slow)
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
        
        LOGGER.debug(f"start extract_slots_info")
        
        gd.click(20, 200)
        
        gd.contrlScroll(300)
        gd.pause(self.slow)
        gd.contrlScroll(300)
        gd.pause(self.slow)
        gd.contrlScroll(300)
        gd.pause(self.slow)
        gd.pause(self.slow)
        pytesseract.pytesseract.tesseract_cmd = r"C:/Program Files/Tesseract-OCR/tesseract.exe"
        
        image_bgr = gd.screen(scope = (100,180,470,1020), is_debug=is_debug, process_for_read = True)
        
        gd.contrlScroll(-300)
        gd.pause(self.slow)
        gd.contrlScroll(-300)
        gd.pause(self.slow)
        gd.contrlScroll(-300)
        gd.pause(self.slow)
        
        # 1) Получаем данные OCR (каждое слово + координаты)
        #    output_type=DICT вернёт словарь со списками для каждого атрибута
        ocr_data = pytesseract.image_to_data(
            image_bgr,
            lang='ukr',                       # язык украинский (добавьте при необходимости 'ukr' в Tesseract)
            output_type=pytesseract.Output.DICT
        )

        results =  self.parse_date_slots(ocr_data["text"])      
        LOGGER.debug(f"results: {results}")
        return results
    
    def find_first_free_slot_in_day_week(self, user: UserConfig, consulate:str, service:str, dt: date, scope: tuple[int, int, int, int] = None) ->bool|None:
        
        if dt and dt <= user.min_date:
            pos_first_free =  gd.find_first_free_slot_in_day_week(scope, is_debug=False)
            if pos_first_free:
                x, y = pos_first_free
                time_slot = gd.read_text("ukr", scope = (x, y, x + 120, y + 40))
                LOGGER.debug(f"найден свободный слот {time_slot}")
                
                _slot_found(user.alias, user.country, consulate, service, dt, time_slot)
                
                gd.click(x + 60, y + 20)
                gd.pause(self.fast)
                gd.scroll(-3000)
       
                if not gd.click_image(IMG_BTN_CONFIRM, confidence=0.6, scope=(376, 720, 640, 900), plus_y=20):
                        _error_hook("button CONFIRM slot missing", gd.take_screenshot())
                        return None
                    
                gd.pause(self.slow)
                
                if not self.i_no_robot(count_attempt_find=1, is_debug=False):
                    
                    if not gd.click_image(IMG_BTN_CONFIRM, confidence=0.6, scope=(376, 720, 640, 900), plus_y=20):
                        _error_hook("button CONFIRM slot missing", gd.take_screenshot())
                        return None
                    
                    if not self.i_no_robot(is_debug=False):
                        return None
                
                gd.pause(self.slow)
                
                if not self.is_success_blocked_slot():
                    
                    _error_hook("no blocked slot", gd.take_screenshot())
                    return None
                
                if not gd.click_image(IMG_BTN_ITS_CLEAR, scope=(800, 650, 1100, 750)):
                        _error_hook("button ITS CLEAR after blocked slot missing", gd.take_screenshot())
                        return None
                
                YAMLLoader.record_booked_slot(user, consulate, service, dt, time_slot)
                free_slots.remove(user.country, consulate, service, dt)
                
                _slot_obtained(user.alias, user.country, consulate, service, dt, time_slot)
                
                
                return True
            
            else:
                LOGGER.debug(f"не найден свободный слот в {dt}")
                
        
        return False
    
    def find_next_day_in_week(self, number_day: int) -> tuple[int, int]|None:
        stop = False
        while not stop:
            y_min = 0
            y_max = 0
            
            gd.scroll(-200)
            gd.pause(self.slow)
            
            if not gd.click_text("Показати ще", 
                lang="ukr", 
                scope=(170, 100, 400, 1030), is_debug=False):
                
                gd.pause(self.fast)
                gd.human_move_diff(-60,0)
                
            else:
                #gd.click(12, 400)
                gd.pause(self.fast)
            
            if number_day < 4:
                LOGGER.debug(f"поиск {WEEK_DAYS[number_day + 1]} для того чтобы определить промежуток с слотами")
                pos = gd.find_text_any([WEEK_DAYS[number_day + 1], WEEK_DAYS[number_day + 1] + ","], 
                        count = 1, lang="ukr", 
                        scope=(170, 0, 330, 1130), is_debug=False)
                
                if pos:
                    x, y = pos
                    y_max = y - 20
                        
                    
                else:
                    LOGGER.debug(f"не найдена {WEEK_DAYS[number_day + 1]}")
                    
            else:
                LOGGER.debug("это уже был четверг и не надо искать суботу, надо искать кнопку")
                pos = gd.find_image(IMG_BTN_COMEBACK, scope=(170, 660, 700, 990))
                
                if pos:
                    x, y = pos
                    y_max = y
                    
                    
            if y_max > 0:    
                
                LOGGER.debug(f"найдена {WEEK_DAYS[number_day + 1]}, будем искать {WEEK_DAYS[number_day]}")
                
                pos = gd.find_text_any([WEEK_DAYS[number_day], WEEK_DAYS[number_day] + ","], 
                    count = 1, lang="ukr", 
                    scope=(170, 10, 330, 1000), is_debug=False)
                
                if pos:
                        x, y = pos
                        y_min = y
                        stop = True
                else:
                    _error_hook(f"не найдена {WEEK_DAYS[number_day]}", gd.take_screenshot())
                    return None
                
        LOGGER.debug(f"найдены границы {WEEK_DAYS[number_day]} - y_min:{y_min} y_max:{y_max}, скролл окончен, можно искать слоты")
        return y_min, y_max
    
    def find_free_slot_week(self, user: UserConfig, consulate:str, service:str, date_min_week_str:str, date_min_week: date)->bool|None:
        
        gd.click(20,200)
        gd.scroll(-2000)
        
        if not gd.click_text(date_min_week_str, 
            lang="ukr", 
            scope=(160, 160, 340, 740), is_debug=False):
            
            _error_hook("not found date_min_week", gd.take_screenshot())
            return False
        
        gd.pause(self.fast)
        
        gd.pause(self.fast)
        gd.click(12, 200)
        gd.pause(self.fast)
        gd.scroll(6000)
        
        dt = date_min_week
        is_found = False
        
        y_min = 140
        y_max = 750
        
        # если это первая неделя от сейчас то первый день доступный для бронирования может быть не понедельник, а следующий от сегодня
        start = 0
        now = datetime.now().date()
        if date_min_week <= now:
            start = now.day - date_min_week.day + 1
        
        for number_day, day in enumerate(WEEK_DAYS, start=start):
            if day == "субота":
                break
            
            dt = dt + timedelta(days=number_day-1)
           
            pos = self.find_next_day_in_week(number_day)
            if pos != None:
                y_min, y_max = pos
            else:
                return None # is error from find days in weeks
            
            result = self.find_first_free_slot_in_day_week(user, consulate, service, dt, scope = (160, y_min, 780, y_max))
            if result == None:
                return None
            elif result == True:
                return True
            
        return False
    
    def find_free_slot_month(self, user: UserConfig, consulate:str, service:str) -> bool|None:
        
        LOGGER.debug("start find free slot in month")
        
        gd.scroll(-2000)
        gd.pause(self.fast)
        
        month_data = self.extract_slots_info(is_debug = False)
        
        is_found = False
         
        for week_data in month_data:
            date_min_week_str, date_max_week_str, count_slots = week_data
            date_min_week = datetime.strptime(date_min_week_str, "%d.%m.%Y").date()
            if count_slots > 0 and user.min_date >= date_min_week:
                
                free_slots.add(user.country, consulate, service, date_min_week_str)
                gd.pause(self.slow)
                is_found = self.find_free_slot_week(user, consulate, service, date_min_week_str, date_min_week)
                
                if is_found == None:
                    break
                
        return is_found
    
    def find_free_slot_months(self, user: UserConfig, consulate:str, service:str) -> bool:
        
        gd.click(20, 200)
        gd.pause(self.fast)
        gd.scroll(2000)
        gd.pause(self.fast)
        
        end_year = False
        self.select_type_show_slots_month()
        
        text_button_month = gd.read_text("ukr", scope = (180, 520, 320, 610), 
                                         is_debug=False)
        current_month_name = [t.strip().lower() for t in text_button_month if t != ""][0]
        current_month_number = MONTHS.index(current_month_name) + 1
        
        is_found = False
        while not is_found and not end_year:
            
            is_found = self.find_free_slot_month(user, consulate, service)
            
            if is_found or is_found == None:
                break
            
            self.select_type_show_slots_month()
            
            gd.pause(self.s_slow)
            
            gd.click(1290, 640)
            
            gd.pause(self.s_slow)
            
            LOGGER.debug(f"end_year: {end_year}")
            LOGGER.debug(f"Select next month: {current_month_name}")
            
            gd.pause(self.slow)
            
            current_month_number += 1
            current_month_name = MONTHS[current_month_number - 1]
            
            if not end_year:
                if not gd.click_text(current_month_name, 
                    lang="ukr", 
                    scope=(300, 500, 1000, 640), is_debug=False):
                
                    _error_hook(f"not find button month {current_month_name}", gd.take_screenshot())
                    
            gd.pause(self.s_slow)
            gd.pause(self.s_slow)
            
            #грудня на сайті нема
            end_year = (current_month_number == 11)
            
            gd.pause(self.s_slow)
            
        return is_found
        
    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------
    
    def _login(self, user: UserConfig) -> bool:
        
        if self._is_login():
            return True  
        
        LOGGER.debug("login step – personal key")
        
        if not gd.click_text("Особистий ключ", 
                             count_attempt_find=4,
                             pause_attempt=4,
                             lang="ukr", 
                             scope=(600, 420, 940, 720), is_debug=False):
            
            _error_hook("personal login button not found", gd.take_screenshot())
            return False
            
        gd.pause(self.slow)
        gd.pause(self.slow)
        
        if not gd.click_text("Оберіть ключ на своєму носієві", 
                             lang="ukr",
                             scope=(700, 420, 1200, 620)):
            
            _error_hook("personal select key button not found", gd.take_screenshot())
        
        gd.pause(self.slow)
        LOGGER.debug(f"user.key_path: {user.key_path}")
        pyperclip.copy(user.key_path)
        gd.pause(self.slow)
        pag.hotkey('ctrl', 'v')
        gd.pause(self.slow)
        pag.press('enter')
        gd.pause(self.slow)
                
        LOGGER.debug(f"paste pass")
        pyperclip.copy(user.key_password)
        gd.pause(self.slow)
        pag.hotkey('ctrl', 'v')
        gd.pause(self.slow)
        pag.press('enter')
        gd.pause(self.slow)
        
        gd.pause(self.slow)
        gd.pause(self.slow)
        gd.pause(self.slow)
        gd.pause(self.slow)
        gd.pause(self.slow)
        gd.pause(self.slow)

        if self._is_login():
            return True 

        _error_hook("Error login", gd.take_screenshot())
        return False

    # ------------------------------------------------------------------
    def open_visit_wizard(self) -> bool:
        
        LOGGER.debug("open visit wizard")
        
        gd.scroll(-10) 
        
        if not gd.click_text("Запис на візит", 
                             lang="ukr", 
                             scope=(560, 100, 690, 160)):
                    _error_hook("btn visit wizard not found", gd.take_screenshot())
                    
                    gd.pause(self.s_slow)
                    gd.pause(self.s_slow)
                    
                    if not gd.click_text("Запис на візит", 
                            lang="ukr", 
                            scope=(560, 100, 690, 160)):
                        
                        _error_hook("btn visit wizard not found", gd.take_screenshot())
                        return False
                
        gd.pause(self.s_slow)
        gd.pause(self.s_slow)
        gd.pause(self.slow)
        gd.pause(self.slow)
        
        if not gd.click_image(name = IMG_BTN_MAKE_APPOINT_VISIT, 
                            confidence = 0.5,
                            plus_y=-40,
                            scope=(140, 280, 540, 360), is_debug=False):
            
            gd.reload_page()
            
            gd.pause(self.s_slow)
            gd.pause(self.s_slow)
            
            if not gd.click_image(name = IMG_BTN_MAKE_APPOINT_VISIT, 
                            confidence = 0.5,
                            plus_y=-40,
                            scope=(140, 280, 540, 360), is_debug=False):
            
                _error_hook("btn visit wizard 2 not found", gd.take_screenshot())
                return False
            
        gd.pause(self.slow)
        gd.pause(self.slow)
        gd.pause(self.slow)
        gd.pause(self.slow)

        return True
   
    # ------------------------------------------------------------------
    def _find_slots(self, user: UserConfig) -> bool:
        
        for consular_service in user.services:
            for cons in user.consulates:
                
                if not self.open_visit_wizard():
                    return False
                
                self.fill_data_personal(user)
                
                if self.check_consulates(user.country, cons):
                    if self.check_consular_service(consular_service, user.for_myself):
                        
                        if self.is_page_find_slots():
                            
                            result_wait = self.wait_process_find_free_slots() 
                            if result_wait == None:
                                break
                            
                            if not result_wait:
                                gd.reload_page()
                                
                                result_wait = self.wait_process_find_free_slots() 
                                if result_wait == None:
                                    break
                            
                                if not result_wait:
                                    _error_hook("error open page find slot", gd.take_screenshot())
                                    return False
                            
                            is_found = self.find_free_slot_months(user, cons, consular_service)
                            
                            if is_found == None:
                                LOGGER.debug("find_free_slot_months exit with error")
                                return None
                            
                        else:
                            _error_hook("open page find slots failed", gd.take_screenshot())
                            return False
                            
                    else:
                        _error_hook("check consular service failed", gd.take_screenshot())
                        return False
                
                else:
                    _error_hook("check_consulates failed", gd.take_screenshot())
                    return False
      
        return is_found