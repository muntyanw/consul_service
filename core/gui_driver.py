"""
core/gui_driver.py
~~~~~~~~~~~~~~~~~~

Low-level wrapper around PyAutoGUI + OpenCV (и опционально OCR), 
адаптирован для работы на одном мониторе 1920×1080 в мульти-мониторной конфигурации.

* Определяет целевой монитор по разрешению TARGET_RES.
* Все скриншоты берутся только из этого монитора (с region).
* Координаты кликов и поиска смещаются обратно в глобальные (с учётом x, y целевого монитора).
"""

from __future__ import annotations

import os
import random
import subprocess
import time
from pathlib import Path
from typing import Final, Iterator, Tuple
import datetime as _dt
from datetime import date, datetime
import re

import cv2
import numpy as np
import pyautogui as pag  
from contextlib import contextmanager
import pytesseract
import matplotlib.pyplot as plt
import mss
import mss.tools
import ctypes
from ctypes import wintypes
from typing import Iterable
from difflib import SequenceMatcher

from utils.logger import setup_logger
from utils.profile_manager import prepare_profile
from project_config import (LOG_LEVEL, TEMPLATE_DIR,
                            MONITOR_WIDTH, MONITOR_HEIGHT,
                            MONITOR_INDEX,TESSERCAT_CMD,
                            TESSDATA_PREFIX, CHECK_EMPTY_TEMPLATE_PATH,
                            CHECK_CHECKED_TEMPLATE_PATH)

from pytesseract import Output
import logging

LOGGER = setup_logger(__name__)
pag.FAILSAFE = True  # оставить возможность «движения мыши в угол для экстренной остановки»

# ---------------------------------------------------------------------------
# Constants: ищем монитор с разрешением необходимым для работы
# ---------------------------------------------------------------------------
TARGET_RES: Final[Tuple[int, int]] = (MONITOR_WIDTH, MONITOR_HEIGHT)

with mss.mss() as sct:
    monitors = sct.monitors  # список словарей; monitors[0] — весь виртуальный экран
    # monitors[1] — первый физический экран; monitors[2] — второй и т.д.
    # Мы ожидаем MONITOR_INDEX 1-based
    if 1 <= MONITOR_INDEX < len(monitors):
        mon = monitors[MONITOR_INDEX]
        MON_X, MON_Y, MON_W, MON_H = mon["left"], mon["top"], mon["width"], mon["height"]
        LOGGER.debug("Using MSS monitor #%d: offset (%d,%d), size %dx%d",
                    MONITOR_INDEX, MON_X, MON_Y, MON_W, MON_H)
    else:
        # fallback: если указанный индекс вне диапазона — берем первый монитор
        mon = monitors[1]
        MON_X, MON_Y, MON_W, MON_H = mon["left"], mon["top"], mon["width"], mon["height"]
        LOGGER.warning("monitor_index=%d is invalid, using primary monitor #%d", MONITOR_INDEX, 1)

def pause(amount):
    LOGGER.debug(f"pause {amount} second")
    time.sleep(amount)
    
def _get_monitor_region(scope) -> dict:
    if scope != None:
        left, bottom, right, top = scope
        monitor_region = {
            "top": bottom,
            "left": MON_X + left,
            "width" :right - left,
            "height": top - bottom
        }
    else:
        monitor_region = {
            "top": MON_Y,
            "left": MON_X,
            "width": MON_W,
            "height": MON_H
        }
    return monitor_region
# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def arrays_fuzzy_equal(window: List[str], query_words: List[str], threshold: float = 0.7) -> bool:
    """
    Считает два массива «равными», если они одинаковой длины, и для каждой позиции i:
      отношение похожести (SequenceMatcher) на строках w[i] и q[i] ≥ threshold.
    Пустые строки считаются непохожими на непустые (только обе пустые → похожесть = 1.0).

    :param window:      первый список строк
    :param query_words: второй список строк
    :param threshold:   минимальный порог похожести (по умолчанию 0.7)
    :return: True, если все парные строковые элементы похожи ≥ threshold
    """
    if len(window) != len(query_words):
        return False

    count_equal = 0
    
    for w, q in zip(window, query_words):
        # Если обе строки пустые, считаем их идентичными
        if not w and not q:
            count_equal += 1
            continue

        # Если одна пустая, а вторая нет → похожесть 0
        if not w or not q:
            continue

        ratio = SequenceMatcher(None, w, q).ratio()
        if ratio >= threshold:
            count_equal += 1

    return count_equal/len(window) >= threshold

def arrays_fuzzy_equal_as_one_str(window: List[str], query_words: List[str], threshold: float = 0.7) -> bool:
    """
    Преобразует два массива в строки и сравнивает их
    
    :param window:      первый список строк
    :param query_words: второй список строк
    :param threshold:   минимальный порог похожести (по умолчанию 0.7)
    :return: True, если все парные строковые элементы похожи ≥ threshold
    """
    if len(window) != len(query_words):
        return False

    str_window = "".join(window)
    str_query_words = "".join(query_words)

    ratio = SequenceMatcher(None, str_window, str_query_words).ratio()
    
    return ratio >= threshold

def launch_chrome(profile_dir: Path, url: str = "https://e-consul.gov.ua/messages") -> subprocess.Popen:
    """
    The function `launch_chrome` launches Chrome with specified profile directory, window size, and
    position on the screen.
    
    :param profile_dir: The `profile_dir` parameter in the `launch_chrome` function is expected to be a
    `Path` object representing the directory where Chrome will store user profile data. This directory
    will be used as the user data directory for the Chrome instance being launched
    :type profile_dir: Path
    :param url: The `url` parameter in the `launch_chrome` function is a string that represents the URL
    of the website you want to open in the Chrome browser. In the provided code snippet, the default URL
    is set to "https://e-consul.gov.ua/", but you can pass a different URL, defaults to
    https://e-consul.gov.ua/
    :type url: str (optional)
    :return: The function `launch_chrome` returns a `subprocess.Popen` object, which represents a
    process that has been launched to run the Chrome browser with specific parameters such as window
    size, position, and URL.
    """
    """
    Launch Chrome at 1920×1080 on the monitor matching TARGET_RES (или primary).
    """
    chrome_path = _detect_chrome()

    width, height = MON_W, MON_H
    offset_x, offset_y = MON_X, MON_Y

    cmd = [
        str(chrome_path),
        f"--user-data-dir={profile_dir}",
        "--new-window",
        "--start-maximized",
        #f"--window-size={width},{height}",
        #f"--window-position={offset_x},{offset_y}",
        url,
    ]
    LOGGER.debug("Run Chrome at %dx%d+%d+%d: %s", width, height, offset_x, offset_y, cmd)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def detect_checkbox_type_from_frame(scope: tuple[int, int, int, int] = None,
                is_debug: bool = False) -> str:
    """
        frame_bgr: кадр экрана (numpy.ndarray в формате BGR)
        empty_template_path: путь до шаблона пустого квадратика
        checked_template_path: путь до шаблона квадратика с галочкой
        threshold: минимальное значение совпадения (0.0–1.0)
        
        Вернёт:
        - "empty", если на экране найден пустой квадратик
        - "checked", если найден квадратик с галочкой
        - "none", если ни один из шаблонов не нашёлся (маximальный коэффициент < threshold)
    """
    frame_bgr = screen(scope)
    
    if is_debug:
        show_image(frame_bgr)
        time.sleep(0.5)
    

    # Загружаем оба шаблона сразу в градациях серого
    templ_empty = cv2.imread(str(TEMPLATE_DIR / CHECK_EMPTY_TEMPLATE_PATH))
    
    templ_checked = cv2.imread(str(TEMPLATE_DIR / CHECK_CHECKED_TEMPLATE_PATH))
    
    if templ_empty is None:
        raise FileNotFoundError(f"Не найден шаблон «пустой» по пути {TEMPLATE_DIR / CHECK_EMPTY_TEMPLATE_PATH}")
    if templ_checked is None:
        raise FileNotFoundError(f"Не найден шаблон «с галочкой» по пути {TEMPLATE_DIR / CHECK_CHECKED_TEMPLATE_PATH}")

    if is_debug:
        show_image(templ_empty)
        time.sleep(0.5)
        show_image(templ_checked)
        time.sleep(0.5)
    
    # 1) Поиск пустого квадратика
    res_empty = cv2.matchTemplate(frame_bgr, templ_empty, cv2.TM_CCOEFF_NORMED)
    _, max_val_empty, _, _ = cv2.minMaxLoc(res_empty)

    # 2) Поиск квадратика с галочкой
    res_checked = cv2.matchTemplate(frame_bgr, templ_checked, cv2.TM_CCOEFF_NORMED)
    _, max_val_checked, _, _ = cv2.minMaxLoc(res_checked)

    # Если ни один из шаблонов не превысил threshold → «ничего не найдено»
    LOGGER.debug(f"max_val_empty: {max_val_empty}, max_val_checked: {max_val_checked}")

    # Если оба выше порога, смотрим, у кого коэффициент больший
    if max_val_checked >= max_val_empty:
        return "checked"
    else:
        return "empty"

def detect_image_from_frame(image_names: list[str], scope: tuple[int, int, int, int] = None,
                is_debug: bool = False,
                threshold: float = 0.8) -> str:
   
    frame_bgr = screen(scope)
    
    # Конвертируем скрин в оттенки серого
    gray_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    max_weight = -10000
    check_image = ""
    
    for image_name in image_names:
        templ = cv2.imread(TEMPLATE_DIR / image_name, cv2.IMREAD_GRAYSCALE)
        if templ is None:
            raise FileNotFoundError(f"Не найден шаблон «пустой» по пути {TEMPLATE_DIR / image_name}")
        res = cv2.matchTemplate(gray_frame, templ, cv2.TM_CCOEFF_NORMED)
        _, weight, _, _ = cv2.minMaxLoc(res)
        
        if weight > max_weight:
            check_image = image_name

        return check_image

def find_image(name: str, timeout: float = 8.0, confidence: float = 0.7,
                scope: tuple[int, int, int, int] = None,
                is_debug: bool = False, multiscale: bool = False) -> (tuple[int, int] | None):
    """
    Найти PNG-шаблон на экране.
    """
    path = TEMPLATE_DIR / name
    if not path.exists():
        raise FileNotFoundError(path)

    deadline = time.perf_counter() + timeout
    
    LOGGER.debug("Start locate image")
    
    while time.perf_counter() < deadline:
        
        if not multiscale:
            pos = _locate(path, confidence, scope=scope, is_debug=is_debug)
        else:
            pos = _locate_multiscale(path, confidence, scope=scope, is_debug=is_debug)
            
        LOGGER.debug(f"pos: {pos}")
        if pos:
            LOGGER.debug("return pos image")
            abs_x = MON_X + pos[0]
            abs_y = MON_Y + pos[1]
            return (abs_x, abs_y) 

    return False

def click_image(name: str, timeout: float = 8.0, confidence: float = 0.7,
                scope: tuple[int, int, int, int] = None,
                plus_y: int = 0,
                is_debug: bool = False,
                multiscale: bool = False) -> bool:
    """
    Найти PNG-шаблон на экране (в пределах целевого монитора) и кликнуть его центр.
    Возвращает True, если кликнули, False если не найдено за timeout секунд.
    """
    LOGGER.debug("Start find image")
    result_find = find_image(name, timeout, confidence, scope, is_debug, multiscale)
    if result_find:
        abs_x, abs_y = result_find
        if abs_x is not None and abs_y is not None:
            human_move_and_click(abs_x, abs_y + plus_y)
            time.sleep(0.1)
            return True
        

    return False

def type_text(text: str, interval: Tuple[float, float] = (0.05, 0.12)) -> None:
    """
    Печатать строку с небольшим случайным интервалом между символами.
    """
    for ch in text:
        pag.typewrite(ch)
        time.sleep(random.uniform(*interval))

def take_screenshot() -> Path:
    """
    Сделать PNG скрин целевого MONITOR_INDEX с помощью MSS и вернуть Path.
    """
    import tempfile, datetime as dt

    ts = dt.datetime.utcnow().isoformat().replace(":", "-")
    output_path = Path(tempfile.gettempdir()) / f"scr_{ts}.png"

    with mss.mss() as sct:
        # Снимаем именно ту область, что описывает монитора:
        monitor_region = {"top": MON_Y, "left": MON_X, "width": MON_W, "height": MON_H}
        img_data = sct.grab(monitor_region)
        # Записываем в PNG (MSS возвращает raw-битмап):
        mss.tools.to_png(img_data.rgb, img_data.size, output=str(output_path))

    return output_path

def show_image(img) -> None:
    # Показать изображение через matplotlib
    plt.figure(figsize=(8, 5))
    plt.imshow(img)
    plt.axis('off')
    plt.title("Tesseract Input: Full-Screen Screenshot")
    plt.show()
# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _detect_chrome() -> Path:
    """
    Best-effort поиск chrome.exe / google-chrome в common locations.
    """
    candidates = [
        Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path(r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/google-chrome"),
    ]
    for p in candidates:
        if p.exists():
            return p
    raise RuntimeError("Chrome executable not found; add custom logic in _detect_chrome()")

def scroll(amount: int = 100) -> None:
        pag.scroll(amount) 
        time.sleep(0.01) 

def screen(scope: tuple[int, int, int, int] = None, is_debug: bool = False,
           process_for_read:bool = False):
    with mss.mss() as sct:
        monitor_region = _get_monitor_region(scope)
        img_data = sct.grab(monitor_region)
        # Конвертируем в numpy.ndarray в BGR для OpenCV:
        scr_np = np.array(img_data)
        scr_bgr = cv2.cvtColor(scr_np, cv2.COLOR_BGRA2BGR)
        
        if process_for_read:
            scr_bgr = preprocess_for_ocr(scr_bgr)
        
    if is_debug:
        show_image(scr_bgr)
        time.sleep(0.5)
            
    return scr_bgr

def _locate(template_path: Path, confidence: float,
            scope: tuple[int, int, int, int] = None,
            is_debug: bool = False) -> tuple[int, int] | None:
    """
    Ищет шаблон (template_path) внутри прямоугольника MON_X..MON_W, MON_Y..MON_H.
    Возвращает (x_center_rel, y_center_rel) или None.
    """
    scr_bgr = screen(scope, is_debug = is_debug)
    
    # 2) Загружаем шаблон (PNG) как BGR
    templ = cv2.imread(str(template_path))
    if templ is None:
        raise RuntimeError(f"Cannot read template: {template_path}")
        
    if is_debug:
        show_image(templ)

    # 3) Поиск с помощью matchTemplate
    res = cv2.matchTemplate(scr_bgr, templ, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
    
    LOGGER.debug(f"max_val: {max_val}, confidence: {confidence}")
    
    if max_val < confidence or max_loc is None:
        LOGGER.debug("image not found")
        return None
    
    y_loc, x_loc = max_loc  # top-left внутри локальной (0..MON_W,0..MON_H)
    LOGGER.debug("image found")

    h, w, _ = templ.shape
    center_x_rel = scope[0] + x_loc + w // 2
    center_y_rel = scope[1] + y_loc + h // 2
    return (center_x_rel, center_y_rel)

def _locate_multiscale(
    template_path: Path,
    confidence: float,
    scope: tuple[int, int, int, int] = None,
    is_debug: bool = False
) -> tuple[int, int] | None:
    """
    Multi-scale поиск шаблона template_path внутри области scope на экране.
    Пытаемся разные коэффициенты масштабирования шаблона (или скрина) и выбираем наилучшее совпадение.
    Возвращает (x_center_abs, y_center_abs) или None, если не найдено.
    """
    # 1) Делаем скрин указанной области (или всего экрана, если scope=None)
    scr_bgr = screen(scope, is_debug=is_debug)

    # 2) Загружаем эталонный PNG-шаблон
    templ_orig = cv2.imread(str(template_path))
    if templ_orig is None:
        raise RuntimeError(f"Cannot read template: {template_path}")

    # 3) Подготовим параметры для перебора масштабов
    #    (чем меньше step, тем медленнее, но точнее поиск).
    scales = np.linspace(0.5, 1.5, 21)  # от 50% до 150% с шагом 0.05

    best_val = -1.0
    best_loc = None  # (x_top_left, y_top_left) для лучшего совпадения
    best_scale = 1.0

    # Размеры скрина
    scr_h, scr_w = scr_bgr.shape[:2]

    for scale in scales:
        # 4) Изменяем размер шаблона
        new_w = int(templ_orig.shape[1] * scale)
        new_h = int(templ_orig.shape[0] * scale)
        if new_w < 10 or new_h < 10:
            continue  # шаблон слишком мал, нет смысла
        if new_w > scr_w or new_h > scr_h:
            continue  # шаблон в этом масштабе больше экрана → пропускаем

        templ = cv2.resize(templ_orig, (new_w, new_h), interpolation=cv2.INTER_AREA)
        
        if is_debug:
            show_image(templ)

        # 5) Выполняем matchTemplate
        res = cv2.matchTemplate(scr_bgr, templ, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

        LOGGER.debug(f"[DEBUG] scale={scale:.2f}, max_val={max_val:.3f}")

        # 6) Сохраняем лучшее совпадение по всем масштабам
        if max_val > best_val:
            best_val = max_val
            best_loc = max_loc
            best_scale = scale

    # 7) Проверяем, превысил ли лучший результат наш порог confidence
    if best_val < confidence or best_loc is None:
        if is_debug:
            print(f"[DEBUG] Ни одного совпадения не нашлось выше threshold={confidence:.2f}. "
                  f"Лучшее max_val={best_val:.3f} при scale={best_scale:.2f}")
        return None

    # 8) Если найдено удовлетворительное совпадение, вычисляем центр и возвращаем
    x_top_left, y_top_left = best_loc
    # Итоговый шаблон (в масштабе best_scale):
    templ_w = int(templ_orig.shape[1] * best_scale)
    templ_h = int(templ_orig.shape[0] * best_scale)

    # center внутри «локального» скрина
    x_center_local = x_top_left + templ_w // 2
    y_center_local = y_top_left + templ_h // 2

    # Если у вас есть смещение scope (т.е. скрин делался не всего экрана, а только области),
    # вам нужно прибавить левую/верхнюю границу области:
    if scope is not None:
        scope_left, scope_top, _, _ = scope
    else:
        scope_left, scope_top = 0, 0

    x_center_abs = scope_left + x_center_local
    y_center_abs = scope_top + y_center_local

    if is_debug:
        print(f"[DEBUG] Найдено при scale={best_scale:.2f}, val={best_val:.3f}, "
              f"центр = ({x_center_abs}, {y_center_abs})")

    return (x_center_abs, y_center_abs)

def _human_move(x: int, y: int, duration: Tuple[float, float] = (0.1, 0.2)) -> None:
    """
    Передать абсолютные глобальные координаты (x, y) и выполнить плавное движение
    “по-человечески”. Используется Bezier-кривая + небольшие случайные паузы.
    """
    cx, cy = pag.position()  # текущая абсолютная позиция мыши

    # Точки для кривой Безье: старт → 2 случайные опоры → цель
    anchors = [
        (cx, cy),
        _rand_near(cx, cy, 100),
        _rand_near(x, y, 100),
        (x, y),
    ]
    steps = 10
    for t in np.linspace(0, 1, steps):
        bx, by = _bezier_point(anchors, t)
        pag.moveTo(bx, by, duration=0)
        time.sleep(0.0001)

    pag.moveTo(x, y, duration=random.uniform(*duration))

def human_move_and_click(x: int, y: int, duration: Tuple[float, float] = (0.4, 0.9)) -> None:
    """
    Передать абсолютные глобальные координаты (x, y) и выполнить плавное движение
    “по-человечески” + клик. Используется Bezier-кривая + небольшие случайные паузы.
    """
    _human_move(x, y, duration)
    pag.click()

def human_move(x: int, y: int, duration: Tuple[float, float] = (0.4, 0.9)):
    x = MON_X + x
    _human_move(x, y, duration)
    
def human_move_diff(diff_x: int, diff_y: int, duration: Tuple[float, float] = (0.4, 0.9)):
    x, y = pag.position()
    x = x + diff_x
    y = y + diff_y
    _human_move(x, y, duration)
    
def click(x: int, y: int, duration: Tuple[float, float] = (0.4, 0.9)):
    x = MON_X + x
    human_move_and_click(x, y) 
    
def _bezier_point(pts: list[Tuple[int, int]], t: float) -> Tuple[int, int]:
    """
    Quadratic/ cubic bezier evaluation (De Casteljau) – generic n-degree.
    Вход: pts — список точек (x, y), t от 0.0 до 1.0.
    Выход: координаты точки на кривой Безье.
    """
    pts_arr = np.array(pts, dtype=float)
    while len(pts_arr) > 1:
        pts_arr = (1 - t) * pts_arr[:-1] + t * pts_arr[1:]
    return int(pts_arr[0][0]), int(pts_arr[0][1])

def _rand_near(x: int, y: int, radius: int = 80) -> Tuple[int, int]:
    """
    Вернёт точку в случайном направлении на расстоянии [radius*0.3 .. radius]
    от (x, y). Используется для более «человеческого» движения мыши.
    """
    ang = random.uniform(0, 2 * np.pi)
    r = random.uniform(radius * 0.3, radius)
    return int(x + r * np.cos(ang)), int(y + r * np.sin(ang))

def draw_monitor_region_on_screen(color: tuple[int,int,int] = (0, 0, 255), thickness: int = 4) -> None:
    """
    Нарисовать на рабочем столе (на самой поверхности экрана) полупрозрачный (через XOR)
    или сплошной (через GDI Rectangle) контур области MON_X, MON_Y, MON_W, MON_H.

    Параметры:
    ---------
    color : BGR-цвет рамки, например (0, 0, 255) для красного (как OpenCV).
    thickness : толщина линии рамки в пикселях.

    При запуске этой функции вы увидите чёткую рамку на экране. Она отрисуется поверх всего,
    но исчезнет при следующем обновлении окна или при следующем вызове (в зависимости от режима).
    """
    # 1) Сначала вычислим координаты нужного монитора через MSS:
    with mss.mss() as sct:
        monitors = sct.monitors
        if 1 <= MONITOR_INDEX < len(monitors):
            mon = monitors[MONITOR_INDEX]
        else:
            mon = monitors[1]  # если указан неверный индекс, взять первый
        MON_X, MON_Y, MON_W, MON_H = mon["left"], mon["top"], mon["width"], mon["height"]

    # 2) Получаем контекст устройства (DC) для всего экрана (hwnd=0 → весь экран)
    hdc = ctypes.windll.user32.GetDC(0)

    # 3) Создаём перо нужного цвета и толщины
    #    В GDI цвет задаётся в формате 0x00BBGGRR, поэтому перекладываем:
    b, g, r = color
    gdi_color = (r << 16) | (g << 8) | b

    PS_SOLID = 0          # сплошная линия
    pen = ctypes.windll.gdi32.CreatePen(PS_SOLID, thickness, gdi_color)
    old_pen = ctypes.windll.gdi32.SelectObject(hdc, pen)

    # 4) Получаем «пустую кисть» (NULL_BRUSH), чтобы внутри не заливать
    NULL_BRUSH = 5  # индекс в GDI для «null brush»
    brush = ctypes.windll.gdi32.GetStockObject(NULL_BRUSH)
    old_brush = ctypes.windll.gdi32.SelectObject(hdc, brush)

    # 5) Рисуем прямоугольник. Параметры: hdc, left, top, right, bottom
    left   = MON_X
    top    = MON_Y
    right  = MON_X + MON_W
    bottom = MON_Y + MON_H

    # Rectangle рисует рамку между (left, top) и (right, bottom)
    ctypes.windll.gdi32.Rectangle(hdc, left, top, right, bottom)

    # 6) Возвращаем предыдущее перо/кисть и удаляем созданные объекты
    ctypes.windll.gdi32.SelectObject(hdc, old_pen)
    ctypes.windll.gdi32.SelectObject(hdc, old_brush)
    ctypes.windll.gdi32.DeleteObject(pen)

    # 7) Освобождаем DC
    ctypes.windll.user32.ReleaseDC(0, hdc)

# ---------------------------------------------------------------------------
# Convenience context: launch Chrome + ensure cleanup
# ---------------------------------------------------------------------------
from subprocess import Popen, TimeoutExpired  # noqa: E402
from core.gui_driver import pause

@contextmanager
def chrome_session(user_alias: str, url: str = "https://e-consul.gov.ua/messages") -> Iterator[Popen]:
    """
    Context manager: берет в работу профиль (temp или persistent) через ProfileManager,
    стартует Chrome в нужной области экрана, отдаёт Popen, а по выходу завершает и/или
    убирает профиль.
    """
    with prepare_profile(user_alias) as prof_dir:
        proc = launch_chrome(prof_dir, url)
        time.sleep(3)
        try:
            yield proc
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except TimeoutExpired:
                proc.kill()

def replace_similar_chars(word: str) -> str:
    char_map = {
        'e': 'е',  # англ e → укр е
        'E': 'Е',  # англ E → укр Е
        'i': 'і',  # англ i → укр і (по необходимости)
        'I': 'І',  # англ I → укр І
        'a': 'а',  # англ a → укр а
        'A': 'А',  # англ A → укр А
        'o': 'о',  # англ o → укр о
        'O': 'О',  # англ O → укр О
        'c': 'с',  # англ c → укр с
        'C': 'С',  # англ C → укр С
        'p': 'р',  # англ p → укр р
        'P': 'Р',  # англ P → укр Р
        'x': 'х',  # англ x → укр х
        'X': 'Х',  # англ X → укр Х
    }
    return ''.join(char_map.get(c, c) for c in word)

def read_text(
    lang: str,
    scope: tuple[int, int, int, int] = None,
    is_debug: bool = False
) -> bool:
    """
    OCR-based read text
    
    """
    
    scr_bgr = screen(scope, is_debug = is_debug)
    
    os.environ['TESSDATA_PREFIX'] = os.path.normpath(TESSDATA_PREFIX)
    pytesseract.pytesseract.tesseract_cmd = r"C:/Program Files/Tesseract-OCR/tesseract.exe"

    data = pytesseract.image_to_data(
        scr_bgr, lang=lang, output_type=pytesseract.Output.DICT
    )

    texts = [t.strip().lower() for t in data["text"]]
    LOGGER.debug(f"texts: {texts}")
    return texts

def get_first_date(text_list) -> date:
    date_pattern = r'\b\d{2}\.\d{2}\.\d{4}\b'
    for text in text_list:
        match = re.search(date_pattern, text)
        if match:
            date_str = match.group()
            return _dt.strptime(date_str, '%d.%m.%Y').date()
    return None

def read_first_date(
    lang: str,
    scope: tuple[int, int, int, int] = None,
    is_debug: bool = False
) -> date:
    """
    OCR-based read text
    
    """

    texts = read_text(lang, scope, is_debug)
    dt = get_first_date(texts)
    
    return dt

def click_text(
    query: str|Iterable[str],
    lang: str,
    count_attempt_find: int = 1,
    pause_attempt: int = 2,
    scope: tuple[int, int, int, int] = None,
    plus_y: int = 0,
    is_debug: bool = False
) -> bool:
    """
    OCR-based search: найти текст `query` на экране (в пределах MON_X..MON_W, MON_Y..MON_H)
    и кликнуть его центр.
    Возвращает True, если удалось найти и кликнуть, иначе False по истечении timeout.

    Параметры:
    -----------
    query : str
        Подстрока (без учёта регистра), которую ищем среди распознанных слов.
    timeout : float
        Максимальное время (в секундах) на попытки поиска.
    lang : str
        Язык Tesseract (например, "eng", "rus", "ukr").
    conf_threshold : float
        Минимальный порог доверия (0.0–1.0) для распознанных слов.
    padding : tuple[int, int, int, int], optional
        Смещение (left, bottom, right, top) для сужения области скриншота.
    """
    LOGGER.debug(f"find and click {query}")
    
    pos = None
    
    if isinstance(query, str):
        pos = find_text(query=query, lang=lang, count=count_attempt_find, 
                    pause_attempt = pause_attempt, scope=scope, plus_y = plus_y, is_debug=is_debug)
        
    elif isinstance(query, Iterable):
        pos = find_text_any(queries=query, lang=lang, count=count_attempt_find, 
                    pause_attempt_sec = pause_attempt, scope=scope, is_debug=is_debug)
    else:
        print("click_text error value query")
    
    if pos:
        abs_x, abs_y = pos
        human_move_and_click(abs_x, abs_y + plus_y)
        return True

    time.sleep(0.2)

    return False

def find_text(
    query: str,
    lang: str,
    count: int = 1,
    pause_attempt: int = 2,
    scope: tuple[int, int, int, int] = None,
    plus_y: int = 0,
    is_debug: bool = False
) -> tuple[int, int]:
    """
    OCR-based search: найти текст `query` на экране (в пределах MON_X..MON_W, MON_Y..MON_H)
    
    Возвращает x, y, если удалось найти, иначе None по истечении timeout.

    Параметры:
    -----------
    query : str
        Подстрока (без учёта регистра), которую ищем среди распознанных слов.
    timeout : float
        Максимальное время (в секундах) на попытки поиска.
    lang : str
        Язык Tesseract (например, "eng", "rus", "ukr").
    conf_threshold : float
        Минимальный порог доверия (0.0–1.0) для распознанных слов.
    padding : tuple[int, int, int, int], optional
        Смещение (left, bottom, right, top) для сужения области скриншота.
    """

    # Разбиваем query на слова для поиска последовательности
    query_words = query.lower().split()
    query_words = [replace_similar_chars(w) for w in query_words]
    
    n_words = len(query_words)

    attempts = 0
    while attempts < count:
        attempts += 1
        
        scr_bgr = screen(scope, is_debug = is_debug)
        
        os.environ['TESSDATA_PREFIX'] = os.path.normpath(TESSDATA_PREFIX)
        pytesseract.pytesseract.tesseract_cmd = r"C:/Program Files/Tesseract-OCR/tesseract.exe"

        data = pytesseract.image_to_data(
            scr_bgr, lang=lang, output_type=pytesseract.Output.DICT
        )

        texts = [t.strip().lower() for t in data["text"]]

        n_boxes = len(texts)

        for i in range(n_boxes - n_words + 1):
            window = texts[i:i + n_words]
            
            window = [replace_similar_chars(w) for w in window]
            
            if arrays_fuzzy_equal_as_one_str(window, query_words):
                # Рассчитываем общий прямоугольник для всей последовательности
                x_left = min(int(data["left"][j]) for j in range(i, i + n_words))
                y_top = min(int(data["top"][j]) for j in range(i, i + n_words))
                x_right = max(int(data["left"][j]) + int(data["width"][j]) for j in range(i, i + n_words))
                y_bottom = max(int(data["top"][j]) + int(data["height"][j]) for j in range(i, i + n_words))

                center_x_rel = (x_left + x_right) // 2
                center_y_rel = (y_top + y_bottom) // 2

                abs_x = MON_X + center_x_rel + scope[0]
                abs_y = MON_Y + center_y_rel + scope[1]

                LOGGER.debug(
                    f"Found phrase '{query}' at local ({center_x_rel},{center_y_rel}), clicking global ({abs_x},{abs_y})"
                )
                return abs_x, abs_y + plus_y

        pause(pause_attempt)

    LOGGER.debug(f"Text '{query}' not found within {attempts} attempt")
    return None

def find_text_any(
    queries: Iterable[str],
    lang: str,
    count: int = 1,
    pause_attempt_sec:int = 2,
    scope: tuple[int, int, int, int] = None,
    is_debug: bool = False,
    process_for_read: bool = False
) -> tuple[int, int] | bool:
    """
    Ищет любой из текстов из `queries` на экране. Возвращает координаты (abs_x, abs_y)
    центра первого найденного совпадения, иначе False.

    :param queries:        список строк для поиска
    :param lang:           язык для Tesseract ('ukr+eng' и т.п.)
    :param conf_threshold: порог уверенности (0.0–1.0), ниже которого слова пропускаются
    :param count:          число попыток сканирования экрана (с паузой между ними)
    :param scope:          (x, y, w, h) – область экрана для OCR (если None, весь экран)
    :param is_debug:       флаг детального логирования и вывода отладочных картинок
    :param process_for_read: если True, `screen()` выполнит предварительную обработку для лучшего OCR
    """
    # Подготовка: разбиваем каждый query на список слов в нижнем регистре
    queries_words = [q.lower().split() for q in queries]

    attempts = 0
    while attempts < count:
        attempts += 1

        # 1) Делаем скрин указанной области (screen уже учитывает MON_X/MON_Y внутри)
        scr_bgr = screen(scope=scope, process_for_read=process_for_read, is_debug=is_debug)

        # 2) Настраиваем путь для Tesseract, если необходимо
        os.environ['TESSDATA_PREFIX'] = os.path.normpath(TESSDATA_PREFIX)
        pytesseract.pytesseract.tesseract_cmd = r"C:/Program Files/Tesseract-OCR/tesseract.exe"

        # 3) Запускаем OCR
        data = pytesseract.image_to_data(
            scr_bgr,
            lang=lang,
            output_type=Output.DICT
        )

        # 4) Собираем массив распознанных слов и их конфиденвностей
        texts = []
        confs = []
        n_boxes = len(data["text"])
        for i in range(n_boxes):
            txt = data["text"][i].strip().lower()
            #try:
            #    conf = float(data["conf"][i]) / 100.0
            #except Exception:
            #    conf = 0.0
            #if txt != "":
            texts.append(txt)
            #confs.append(conf)

        #if is_debug:
        LOGGER.debug(f"OCR texts: {[w for w in texts if w != ""]}")
            #LOGGER.debug(f"OCR confs: {confs}")

        # 5) Перебираем каждую последовательность слов из queries_words
        for query_words in queries_words:
            # Нормализуем каждый токен в query
            normalized_query = [replace_similar_chars(w) for w in query_words]
            n_words = len(normalized_query)

            # Сдвиг по всем возможным позициям в тексте
            for i in range(0, n_boxes - n_words + 1):
                window = texts[i : i + n_words]
                #window_confs = confs[i : i + n_words]

                # 5.1) Пропускаем, если хоть одно слово из окна слишком низкой уверенности
                #if any(c < conf_threshold for c in window_confs):
                #    continue

                # 5.2) Нормализуем каждое слово в окне
                normalized_window = [replace_similar_chars(w) for w in window]

                # 5.3) Сравниваем через fuzzy (≥70% или порог внутри arrays_fuzzy_equal)
                if arrays_fuzzy_equal(normalized_window, normalized_query):
                    # 6) Вычисляем bounding box для всей последовательности
                    x_left = min(int(data["left"][j]) for j in range(i, i + n_words))
                    y_top = min(int(data["top"][j]) for j in range(i, i + n_words))
                    x_right = max(int(data["left"][j]) + int(data["width"][j]) for j in range(i, i + n_words))
                    y_bottom = max(int(data["top"][j]) + int(data["height"][j]) for j in range(i, i + n_words))

                    # Центр внутри обрезанного изображения (scope)
                    center_x_rel = (x_left + x_right) // 2
                    center_y_rel = (y_top + y_bottom) // 2

                    # 7) Преобразуем в абсолютные координаты
                    scope_left, scope_top = (scope[0], scope[1]) if scope is not None else (0, 0)
                    abs_x = MON_X + scope_left + center_x_rel
                    abs_y = MON_Y + scope_top  + center_y_rel

                    if is_debug:
                        LOGGER.debug(f"Found '{' '.join(query_words)}' at attempt {attempts}, " +
                                     f"rel=({center_x_rel},{center_y_rel}), abs=({abs_x},{abs_y})")

                    return abs_x, abs_y
                
        pause(pause_attempt_sec)

        # 8) Пауза перед следующей попыткой
        time.sleep(0.2)

    LOGGER.debug(f"None of texts {queries} found after {attempts} attempts")
    return False

def cursor_move_to(
    x: int = 500,
    y: int = 500
) -> None:
   
    x = MON_X + x
    LOGGER.debug("Cursor moved to global (%d,%d)", x, y)                    
    human_move_and_click(x, y)

def contrlScroll(amount:int):
    time.sleep(1)
    # Нажимаем Ctrl и удерживаем
    pag.keyDown('ctrl')
    time.sleep(0.05)

    # Прокручиваем колёсико вверх (положительное значение)
    # Чем больше число, тем сильнее «клик» колёсика
    pag.scroll(amount)

    time.sleep(0.05)
    # Отпускаем Ctrl
    pag.keyUp('ctrl') 
    
def remove_green_background(src_bgr: np.ndarray) -> np.ndarray:
    """
    Превращает зелёные блоки в чисто-белый фон, оставляя текст (и всё остальное) нетронутым.
    Возвращает BGR-изображение, где «зелёное» стало (255,255,255).
    """
    # 1. Переводим в пространство HSV, чтобы легко отфильтровать зелёный
    hsv = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2HSV)

    # 2. Задаём диапазон «зелёного»
    #    Нижний и верхний порог границ H, S, V — можно подкорректировать под ваш оттенок
    lower_green = np.array([40,  40,  40])   # например: H≈60°, но OpenCV: H от 0 до 179
    upper_green = np.array([80, 255, 255])

    # 3. Делаем маску: где пиксели «зеленые» → 255, остальное → 0
    mask_green = cv2.inRange(hsv, lower_green, upper_green)

    # 4. Invert mask: где НЕ зелёное (текст, остальные элементы) → 255, где зелёное → 0
    mask_not_green = cv2.bitwise_not(mask_green)

    # 5. Создаём «фон» полностью белого цвета того же размера
    white_bg = np.full_like(src_bgr, fill_value=255)

    # 6. Накладываем: на исходном изображении всё, что НЕ зелёное, оставляем (AND с mask_not_green),
    #    а в местах «зелёного» будем брать белый фон (AND с mask_green и белый)
    fg = cv2.bitwise_and(src_bgr, src_bgr, mask=mask_not_green)
    bg = cv2.bitwise_and(white_bg, white_bg, mask=mask_green)

    # 7. Склеиваем: получается картинка, где «зелёное» заменено на белое
    result = cv2.add(fg, bg)
    return result

def sharpen_filter(src_bgr: np.ndarray) -> np.ndarray:
    """
    Применяет к BGR-изображению простой фильтр резкости.
    Возвращает «резче» BGR-изображение.
    """
    # Определяем kernel
    kernel = np.array([
        [ 0, -1,  0],
        [-1,  5, -1],
        [ 0, -1,  0]
    ], dtype=np.float32)

    # Применяем фильтр свёртки
    sharpened = cv2.filter2D(src_bgr, ddepth=-1, kernel=kernel)
    return sharpened

def unsharp_mask(src_bgr: np.ndarray, 
                 blur_ksize: tuple[int, int] = (9, 9), 
                 sigma: float = 10.0, 
                 amount: float = 1.5, 
                 threshold: int = 0) -> np.ndarray:
    """
    Параметры:
    - blur_ksize: размер ядра для GaussianBlur (должен быть нечётным, напр. (9,9)).
    - sigma: отклонение по Гауссу (чем больше, тем сильнее сглаживание).
    - amount: во сколько раз усиливается «маска резкости».
    - threshold: минимальная разница яркости, при которой происходит усиление; 0 — без порога.
    """
    # 1) Сглаживаем
    blurred = cv2.GaussianBlur(src_bgr, blur_ksize, sigma)

    # 2) Вычисляем «маску»: оригинал − размытие
    mask = cv2.subtract(src_bgr, blurred)

    # 3) Усиливаем маску и складываем с оригиналом
    sharpened = cv2.addWeighted(src_bgr, 1.0, mask, amount, 0)

    if threshold > 0:
        # Дополнительно: пороговое усиление (Optional)
        # Разница между оригиналом и размытым (по каналам)
        low_contrast_mask = np.absolute(src_bgr - blurred) < threshold
        # В тех местах, где контраст низкий, оставляем оригинал
        np.copyto(sharpened, src_bgr, where=low_contrast_mask)

    return sharpened

def preprocess_for_ocr(src_bgr: np.ndarray) -> np.ndarray:
    """
    1) Удаляет зелёный фон (вызывая remove_green_background)
    2) Конвертирует в серый + CLAHE (локальное выравнивание гистограммы)
    3) Адаптивную бинаризацию (чёрно-белое)
    """
    # 1) Убираем зелёный фон
    no_green = unsharp_mask(remove_green_background(src_bgr))

    # 2) В оттенки серого
    gray = cv2.cvtColor(no_green, cv2.COLOR_BGR2GRAY)

    # 3) CLAHE для повышения контраста
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    equalized = clahe.apply(gray)

    # 4) Адаптивная бинаризация (локальная) — чаще всего лучше, чем просто Otsu
    bw = cv2.adaptiveThreshold(
        equalized,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=11,  # нечётный размер; можно варьировать (11, 15, 21)
        C=2             # константа, вычитаемая из среднего
    )
    return bw

def find_first_free_slot_in_day_week(scope: tuple[int,int,int,int],
                                     is_debug: bool = False
                                    ) -> tuple[int,int] | None:

    # 1) Захват экрана + конверсия BGRA→BGR→HSV
    with mss.mss() as sct:
        mon = _get_monitor_region(scope)
        img = sct.grab(mon)
        bgr = np.array(img)[..., :3]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    if is_debug:
        show_image(bgr)
        show_image(hsv)
        time.sleep(0.5)

    # 2) Маска для голубого (границы берите из отладки HSV)
    lower_blue = np.array([ 90,  30, 150])
    upper_blue = np.array([120, 255, 255])
    mask_blue = cv2.inRange(hsv, lower_blue, upper_blue)
    mask_blue = cv2.GaussianBlur(mask_blue, (5,5), 0)

    # 3) Морфология для очистки
    kernel     = cv2.getStructuringElement(cv2.MORPH_RECT, (5,5))
    mask_clean = cv2.morphologyEx(mask_blue, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask_clean = cv2.morphologyEx(mask_clean, cv2.MORPH_OPEN,  kernel, iterations=1)

    if is_debug:
        show_image(mask_blue)
        show_image(mask_clean)
        time.sleep(0.5)

    # 4) Ищем все контуры и сразу же фильтруем по площади и «насколько голубой» они внутри
    cnts, _ = cv2.findContours(mask_clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blue_rects = []
    for cnt in cnts:
        x, y, w, h = cv2.boundingRect(cnt)
        if w < 30 or h < 15:
            continue

        # посчитаем долю белых пикселей в первичной mask_blue внутри этого прямоугольника
        patch_mask = mask_blue[y:y+h, x:x+w]
        blue_ratio = patch_mask.sum() / 255 / (w*h)

        # дополнительно проверим, что внутри действительно цвет насыщен (чтобы не схватить
        # светло-серый артефакт)
        patch_hsv = hsv[y:y+h, x:x+w]
        mean_s = float(patch_hsv[...,1].mean())

        # берем только те, где хотя бы 30% пикселей попало в маску И средняя насыщенность > 20
        if blue_ratio > 0.3 and mean_s > 20:
            blue_rects.append((x, y, w, h))

    if not blue_rects:
        return None

    # 5) Сортируем «сверху–влево» и возвращаем первую голубую
    blue_rects.sort(key=lambda r: (r[1], r[0]))
    x0, y0, _, _ = blue_rects[0]
    return (x0 + scope[0], y0 + scope[1])

def reload_page():
    LOGGER.debug("reload page")
    click(94,40)
    pause(2)
