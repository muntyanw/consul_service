"""gui_driver.py
~~~~~~~~~~~~~~~~

Low‑level wrapper around **PyAutoGUI** and **OpenCV** that gives the rest of the
project a *clean*, high‑level API:

* открыть Chrome с нужным профилем;
* искать элементы на экране по PNG‑шаблону (учитывая текущий zoom 90–100 %);
* перемещать мышь «по‑человечески» (Bezier‑кривая + случайные паузы);
* клики, ввод текста, горячие клавиши;
* базовые утилиты (fullscreen, zoom_to_fit, take_screenshot).

Это **не** бизнес‑логика (SlotFinder), а тонкий "водитель" GUI.
"""
from __future__ import annotations

import random
import subprocess
import time
from pathlib import Path
from typing import Final, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore
import pyautogui as pag  # PyAutoGUI

from screeninfo import get_monitors

from utils.logger import setup_logger
from utils.profile_manager import prepare as prepare_profile

LOGGER = setup_logger(__name__)

# Disable PyAutoGUI failsafe?  Better keep it true and document "move mouse to top‑left".
pag.FAILSAFE = True

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Try to find a monitor with the exact 1920×1080 resolution
TARGET_RES: Final[Tuple[int, int]] = (1920, 1080)
monitors = get_monitors()
match = next((m for m in monitors if (m.width, m.height) == TARGET_RES), None)
if match:
    SCREEN_W, SCREEN_H = TARGET_RES
else:
    SCREEN_W, SCREEN_H = pag.size()
    LOGGER.warning(
        "Detected virtual screen %sx%s, but using actual %sx%s for templates",
        pag.size()[0], pag.size()[1], SCREEN_W, SCREEN_H
    )

TEMPLATE_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "assets"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def launch_chrome(profile_dir: Path, url: str = "https://e-consul.gov.ua/") -> subprocess.Popen:
    """Launch Chrome at 1920×1080 on the monitor matching TARGET_RES or primary."""
    chrome_path = _detect_chrome()

    # Detect monitors
    try:
        from screeninfo import get_monitors
        mons = get_monitors()
        # Ищем монитор нужного разрешения или берём первый
        mon = next((m for m in mons if (m.width, m.height) == TARGET_RES), mons[0])
        offset_x, offset_y = mon.x, mon.y
    except Exception:
        offset_x, offset_y = 0, 0

    width, height = TARGET_RES
    cmd = [
        str(chrome_path),
        f"--user-data-dir={profile_dir}",
        "--new-window",
        f"--window-size={width},{height}",
        f"--window-position={offset_x},{offset_y}",
        url,
    ]
    LOGGER.debug(f"Run Chrome at {width}x{height}+{offset_x}+{offset_y}: {cmd}")
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)




def click_image(name: str, timeout: float = 8.0, confidence: float = 0.9) -> bool:
    """Найти PNG‑шаблон на экране и кликнуть центр. Возврат True/False."""
    path = TEMPLATE_DIR / name
    if not path.exists():
        raise FileNotFoundError(path)

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        pos = _locate(path, confidence)
        if pos:
            _human_move_and_click(*pos)
            return True
    return False


def type_text(text: str, interval: tuple[float, float] = (0.05, 0.12)) -> None:
    """Печатать строку с небольшим случайным интервалом между символами."""
    for ch in text:
        pag.typewrite(ch)
        time.sleep(random.uniform(*interval))


def take_screenshot() -> Path:
    """Сохранить PNG скрин в tmp‑dir, вернуть Path."""
    import tempfile, datetime as dt

    ts = dt.datetime.utcnow().isoformat().replace(":", "-")
    path = Path(tempfile.gettempdir()) / f"scr_{ts}.png"
    pag.screenshot(str(path))
    return path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_chrome() -> Path:
    """Best‑effort поиск chrome.exe / google‑chrome в common locations."""
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


def _locate(template_path: Path, confidence: float) -> tuple[int, int] | None:
    """Return (x, y) центра совпадения или None."""
    screenshot = pag.screenshot()
    scr_np = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
    templ = cv2.imread(str(template_path))
    res = cv2.matchTemplate(scr_np, templ, cv2.TM_CCOEFF_NORMED)
    loc = np.where(res >= confidence)
    try:
        y, x = next(zip(*loc[::-1]))  # first match
    except StopIteration:
        return None
    h, w, _ = templ.shape
    return (x + w // 2, y + h // 2)


def _human_move_and_click(x: int, y: int, duration: tuple[float, float] = (0.4, 0.9)) -> None:
    """Движение по кривой Безье + click."""
    cx, cy = pag.position()

    # Control points: start – two random anchors – end
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
    pag.click()


def _bezier_point(pts: list[tuple[int, int]], t: float) -> tuple[int, int]:
    """Quadratic/ cubic bezier evaluation (De Casteljau) – generic n‑degree."""
    pts_arr = np.array(pts, dtype=float)
    while len(pts_arr) > 1:
        pts_arr = (1 - t) * pts_arr[:-1] + t * pts_arr[1:]
    return int(pts_arr[0][0]), int(pts_arr[0][1])


def _rand_near(x: int, y: int, radius: int = 80) -> tuple[int, int]:
    ang = random.uniform(0, 2 * np.pi)
    r = random.uniform(radius * 0.3, radius)
    return int(x + r * np.cos(ang)), int(y + r * np.sin(ang))


# ---------------------------------------------------------------------------
# Convenience context: launch Chrome + ensure cleanup
# ---------------------------------------------------------------------------
from contextlib import contextmanager
from subprocess import Popen, TimeoutExpired


@contextmanager
def chrome_session(user_alias: str, url: str = "https://e-consul.gov.ua/") -> Iterator[Popen]:
    """Context manager: copy profile → launch chrome → yield Popen → kill & cleanup."""
    with prepare_profile(user_alias) as prof_dir:
        proc = launch_chrome(prof_dir, url)
        try:
            yield proc
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except TimeoutExpired:
                proc.kill()
