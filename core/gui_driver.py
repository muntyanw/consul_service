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

from utils.logger import setup_logger
from utils.profile_manager import prepare_profile
from project_config import LOG_LEVEL, TEMPLATE_DIR, MONITOR_WIDTH, MONITOR_HEIGHT,MONITOR_INDEX,TESSERCAT_CMD,TESSDATA_PREFIX

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
        LOGGER.info("Using MSS monitor #%d: offset (%d,%d), size %dx%d",
                    MONITOR_INDEX, MON_X, MON_Y, MON_W, MON_H)
    else:
        # fallback: если указанный индекс вне диапазона — берем первый монитор
        mon = monitors[1]
        MON_X, MON_Y, MON_W, MON_H = mon["left"], mon["top"], mon["width"], mon["height"]
        LOGGER.warning("monitor_index=%d is invalid, using primary monitor #%d", MONITOR_INDEX, 1)
    
# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def launch_chrome(profile_dir: Path, url: str = "https://e-consul.gov.ua/") -> subprocess.Popen:
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
        f"--window-size={width},{height}",
        f"--window-position={offset_x},{offset_y}",
        url,
    ]
    LOGGER.debug("Run Chrome at %dx%d+%d+%d: %s", width, height, offset_x, offset_y, cmd)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def click_image(name: str, timeout: float = 8.0, confidence: float = 0.9) -> bool:
    """
    Найти PNG-шаблон на экране (в пределах целевого монитора) и кликнуть его центр.
    Возвращает True, если кликнули, False если не найдено за timeout секунд.
    """
    path = TEMPLATE_DIR / name
    if not path.exists():
        raise FileNotFoundError(path)

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        pos = _locate(path, confidence)
        if pos:
            # pos возвращается как (x_center_rel, y_center_rel) внутри области полу-монитора,
            # но мы сразу сконвертируем его в глобальные координаты:
            abs_x = MON_X + pos[0]
            abs_y = MON_Y + pos[1]
            _human_move_and_click(abs_x, abs_y)
            return True
        # Короткая пауза, чтобы не грузить CPU
        time.sleep(0.1)

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
     for _ in range(amount): 
        pag.scroll(100) 
        time.sleep(0.01) 

def _locate(template_path: Path, confidence: float) -> tuple[int, int] | None:
    """
    Ищет шаблон (template_path) внутри прямоугольника MON_X..MON_W, MON_Y..MON_H.
    Возвращает (x_center_rel, y_center_rel) или None.
    """
    # 1) Снимаем область MON_X..MON_H с помощью MSS
    with mss.mss() as sct:
        monitor_region = {"top": MON_Y, "left": MON_X, "width": MON_W, "height": MON_H}
        img_data = sct.grab(monitor_region)
        # Конвертируем в numpy.ndarray в BGR для OpenCV:
        scr_np = np.array(img_data.rgb, dtype=np.uint8)
        scr_np = scr_np.reshape((img_data.height, img_data.width, 3))
        scr_bgr = cv2.cvtColor(scr_np, cv2.COLOR_RGB2BGR)

    # 2) Загружаем шаблон (PNG) как BGR
    templ = cv2.imread(str(template_path))
    if templ is None:
        raise RuntimeError(f"Cannot read template: {template_path}")

    # 3) Поиск с помощью matchTemplate
    res = cv2.matchTemplate(scr_bgr, templ, cv2.TM_CCOEFF_NORMED)
    loc = np.where(res >= confidence)
    try:
        y_loc, x_loc = next(zip(*loc[::-1]))  # top-left внутри локальной (0..MON_W,0..MON_H)
    except StopIteration:
        return None

    h, w, _ = templ.shape
    center_x_rel = x_loc + w // 2
    center_y_rel = y_loc + h // 2
    return (center_x_rel, center_y_rel)

def _human_move(x: int, y: int, duration: Tuple[float, float] = (0.4, 0.9)) -> None:
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
    steps = 30
    for t in np.linspace(0, 1, steps):
        bx, by = _bezier_point(anchors, t)
        pag.moveTo(bx, by, duration=0)
        time.sleep(0.003)

    pag.moveTo(x, y, duration=random.uniform(*duration))


def _human_move_and_click(x: int, y: int, duration: Tuple[float, float] = (0.4, 0.9)) -> None:
    """
    Передать абсолютные глобальные координаты (x, y) и выполнить плавное движение
    “по-человечески” + клик. Используется Bezier-кривая + небольшие случайные паузы.
    """
    _human_move(x, y, duration)
    pag.click()


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

def flip_focus_rect_on_screen(scope):
    """
    Нарисовать/стереть XOR-рамку вокруг left, top, width, height.
    При первом вызове – появится рамка, при втором – исчезнет.
    """

    # 2) получаем HDC для экрана
    hdc = ctypes.windll.user32.GetDC(0)
    # 3) DrawFocusRect рисует рамку в режиме XOR: повторный вызов с теми же координатами удаляет её
    rect = wintypes.RECT(scope["left"], scope["top"], scope["left"] + scope["width"], scope["top"] + scope["height"])
    ctypes.windll.user32.DrawFocusRect(hdc, ctypes.byref(rect))
    # 4) освобождаем HDC
    ctypes.windll.user32.ReleaseDC(0, hdc)

# ---------------------------------------------------------------------------
# Convenience context: launch Chrome + ensure cleanup
# ---------------------------------------------------------------------------
from subprocess import Popen, TimeoutExpired  # noqa: E402

@contextmanager
def chrome_session(user_alias: str, url: str = "https://e-consul.gov.ua/") -> Iterator[Popen]:
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

def click_text(
    query: str,
    timeout: float,
    lang: str,
    conf_threshold: float,
    scope: tuple[int, int, int, int] = None
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
    deadline = time.perf_counter() + timeout

    # Разбиваем query на слова для поиска последовательности
    query_words = query.lower().split()
    n_words = len(query_words)

    while time.perf_counter() < deadline:
        left, bottom, right, top = scope
        with mss.mss() as sct:
           
            if scope != None:
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
                
            img_data = sct.grab(monitor_region)
            scr_np = np.array(img_data)
            scr_bgr = cv2.cvtColor(scr_np, cv2.COLOR_BGRA2BGR)

        if LOG_LEVEL == "DEBUG":
            flip_focus_rect_on_screen(monitor_region)
            show_image(scr_bgr)
            time.sleep(0.5)

        os.environ['TESSDATA_PREFIX'] = os.path.normpath(TESSDATA_PREFIX)
        pytesseract.pytesseract.tesseract_cmd = r"C:/Program Files/Tesseract-OCR/tesseract.exe"

        data = pytesseract.image_to_data(
            scr_bgr, lang=lang, output_type=pytesseract.Output.DICT
        )

        texts = [t.strip().lower() for t in data["text"]]
        confs = []
        for c in data.get("conf", []):
            try:
                confs.append(float(c) / 100.0)
            except Exception:
                confs.append(0.0)

        n_boxes = len(texts)

        for i in range(n_boxes - n_words + 1):
            window = texts[i:i + n_words]
            window_confs = confs[i:i + n_words]

            if any(not w for w in window):
                continue
            if any(conf < conf_threshold for conf in window_confs):
                continue

            if window == query_words:
                # Рассчитываем общий прямоугольник для всей последовательности
                x_left = min(int(data["left"][j]) for j in range(i, i + n_words))
                y_top = min(int(data["top"][j]) for j in range(i, i + n_words))
                x_right = max(int(data["left"][j]) + int(data["width"][j]) for j in range(i, i + n_words))
                y_bottom = max(int(data["top"][j]) + int(data["height"][j]) for j in range(i, i + n_words))

                center_x_rel = (x_left + x_right) // 2
                center_y_rel = (y_top + y_bottom) // 2

                abs_x = MON_X + center_x_rel + left
                abs_y = MON_Y + center_y_rel + bottom

                LOGGER.debug(
                    "Found phrase '%s' at local (%d,%d), clicking global (%d,%d)",
                    query, center_x_rel, center_y_rel, abs_x, abs_y
                )
                _human_move_and_click(abs_x, abs_y)
                return True

        time.sleep(0.2)

    LOGGER.debug("Text '%s' not found within %.2f seconds", query, timeout)
    return False

def find_text(
    query: str,
    timeout: float,
    lang: str,
    conf_threshold: float,
    scope: tuple[int, int, int, int] = None
) -> bool:
    """
    OCR-based search: найти текст `query` на экране (в пределах MON_X..MON_W, MON_Y..MON_H).
    Возвращает True, если удалось найти, иначе False по истечении timeout.

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
    scope : tuple[int, int, int, int], optional
        Область поиска (left, bottom, right, top) для сужения области скриншота.
    """
    deadline = time.perf_counter() + timeout

    query_words = query.lower().split()
    n_words = len(query_words)

    while time.perf_counter() < deadline:
        if scope is not None:
            left, bottom, right, top = scope
            monitor_region = {
                "top": bottom,
                "left": MON_X + left,
                "width": right - left,
                "height": top - bottom
            }
        else:
            monitor_region = {
                "top": MON_Y,
                "left": MON_X,
                "width": MON_W,
                "height": MON_H
            }

        with mss.mss() as sct:
            img_data = sct.grab(monitor_region)
            scr_np = np.array(img_data)
            scr_bgr = cv2.cvtColor(scr_np, cv2.COLOR_BGRA2BGR)

        if LOG_LEVEL == "DEBUG":
            flip_focus_rect_on_screen(monitor_region)
            show_image(scr_bgr)
            time.sleep(0.5)

        os.environ['TESSDATA_PREFIX'] = os.path.normpath(TESSDATA_PREFIX)
        pytesseract.pytesseract.tesseract_cmd = r"C:/Program Files/Tesseract-OCR/tesseract.exe"

        data = pytesseract.image_to_data(
            scr_bgr, lang=lang, output_type=pytesseract.Output.DICT
        )

        texts = [t.strip().lower() for t in data["text"]]
        confs = []
        for c in data.get("conf", []):
            try:
                confs.append(float(c) / 100.0)
            except Exception:
                confs.append(0.0)

        n_boxes = len(texts)

        for i in range(n_boxes - n_words + 1):
            window = texts[i:i + n_words]
            window_confs = confs[i:i + n_words]

            if any(not w for w in window):
                continue
            if any(conf < conf_threshold for conf in window_confs):
                continue

            if query_words in window:
                LOGGER.debug(
                    "Found phrase '%s' within timeout %.2f seconds", query, timeout
                )
                return True

        time.sleep(0.2)

    LOGGER.debug("Text '%s' not found within %.2f seconds", query, timeout)
    return False

from typing import Iterable

def find_text_any(
    queries: Iterable[str],
    timeout: float,
    lang: str,
    conf_threshold: float,
    scope: tuple[int, int, int, int] = None
) -> bool:
    """
    Ищет любой из текстов из `queries` на экране.
    Возвращает True, если найден хотя бы один, иначе False.

    queries : список или кортеж строк для поиска.
    Остальные параметры как в find_text.
    """
    deadline = time.perf_counter() + timeout
    # Для оптимизации разобьём все query на списки слов заранее
    queries_words = [q.lower().split() for q in queries]

    while time.perf_counter() < deadline:
        if scope is not None:
            left, bottom, right, top = scope
            monitor_region = {
                "top": bottom,
                "left": MON_X + left,
                "width": right - left,
                "height": top - bottom
            }
        else:
            left = bottom = 0
            monitor_region = {
                "top": MON_Y,
                "left": MON_X,
                "width": MON_W,
                "height": MON_H
            }

        with mss.mss() as sct:
            img_data = sct.grab(monitor_region)
            scr_np = np.array(img_data)
            scr_bgr = cv2.cvtColor(scr_np, cv2.COLOR_BGRA2BGR)

        if LOG_LEVEL == "DEBUG":
            flip_focus_rect_on_screen(monitor_region)
            show_image(scr_bgr)
            time.sleep(0.5)

        os.environ['TESSDATA_PREFIX'] = os.path.normpath(TESSDATA_PREFIX)
        pytesseract.pytesseract.tesseract_cmd = r"C:/Program Files/Tesseract-OCR/tesseract.exe"

        data = pytesseract.image_to_data(
            scr_bgr, lang=lang, output_type=pytesseract.Output.DICT
        )

        texts = [t.strip().lower() for t in data["text"]]
        confs = []
        for c in data.get("conf", []):
            try:
                confs.append(float(c) / 100.0)
            except Exception:
                confs.append(0.0)

        n_boxes = len(texts)

        for query_words in queries_words:
            n_words = len(query_words)
            for i in range(n_boxes - n_words + 1):
                window = texts[i:i + n_words]
                window_confs = confs[i:i + n_words]

                if any(not w for w in window):
                    continue
                if any(conf < conf_threshold for conf in window_confs):
                    continue

                if window == query_words:
                    LOGGER.debug(
                        "Found phrase '%s' within timeout %.2f seconds", ' '.join(query_words), timeout
                    )
                    return True

        time.sleep(0.2)

    LOGGER.debug("None of texts '%s' found within %.2f seconds", queries, timeout)
    return False
