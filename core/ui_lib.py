"""ui_lib.py
~~~~~~~~~~~~~~~~

Utility functions that wrap *OpenCV template‑matching* for GUI automation
keeping performance acceptable on a 1920×1080 desktop.  It is a stand‑alone
helper used by **core.gui_driver** but also available to future modules if more
advanced image processing is required (e.g. OCR cropping).

Key features
------------
* **Template cache** – PNG files are loaded and converted to BGR numpy arrays
  once; repeated calls reuse cached data.
* **Region‑of‑interest (ROI)** – optional bounding box to speed up matching when
  prior knowledge of element location is available.
* **Multi‑scale matching** – if `scales=[1.0, 0.9]` the function tries original
  template size first and then a down‑scaled copy, useful when browser zoom or
  DPR is not exactly 100 %.
* Single dependency: `opencv‑python`; PIL is not needed because screenshots come
  from **PyAutoGUI** as RGB numpy arrays already.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Iterable, Optional

import cv2  # type: ignore
import numpy as np  # type: ignore
import pyautogui as pag
import pytesseract

from utils.logger import setup_logger

LOGGER = setup_logger(__name__)

# Root folder for PNG templates (injected from settings or default "assets")
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets"

__all__ = [
    "locate_on_screen",
    "load_template",
]

# ---------------------------------------------------------------------------
# Internal cache helpers
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=128)
def _read_png(path: Path) -> np.ndarray:
    """Read PNG file as BGR numpy array and cache the result."""
    if not path.exists():
        raise FileNotFoundError(path)
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"Unable to read {path}")
    return img


def load_template(name: str, scale: float = 1.0) -> np.ndarray:
    """Return cached template by filename inside *assets* dir, optionally scaled."""
    path = TEMPLATE_DIR / name
    img = _read_png(path)
    if scale != 1.0:
        h, w = img.shape[:2]
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LINEAR)
    return img


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def locate_on_screen(
    template_name: str,
    confidence: float = 0.9,
    roi: tuple[int, int, int, int] | None = None,
    scales: Iterable[float] | None = None,
) -> tuple[int, int] | None:
    """Locate *template_name* on current screen.

    Parameters
    ----------
    template_name : str
        File name inside the *assets* directory (PNG).
    confidence : float, default 0.9
        Min correlation value for `cv2.matchTemplate`.
    roi : (x, y, w, h) or *None*
        If provided, restrict search area (top‑left corner + width/height).
    scales : Iterable[float] | None
        Try multiple scales of the template (e.g. `[1.0, 0.9]`).  First match
        that satisfies *confidence* wins.

    Returns
    -------
    (x, y) of the **center** of the match in screen coordinates, or *None*.
    """
    scales = scales or (1.0,)

    screenshot = pag.screenshot()
    screen_np = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
    if roi:
        x, y, w, h = roi
        screen_roi = screen_np[y : y + h, x : x + w]
    else:
        screen_roi = screen_np

    for sc in scales:
        tmpl = load_template(template_name, sc)
        res = cv2.matchTemplate(screen_roi, tmpl, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= confidence)
        try:
            y_t, x_t = next(zip(*loc))  # first match
        except StopIteration:
            continue
        h_t, w_t = tmpl.shape[:2]
        cx = x_t + w_t // 2 + (roi[0] if roi else 0)
        cy = y_t + h_t // 2 + (roi[1] if roi else 0)
        LOGGER.debug("Found %s at (%d,%d) scale %.2f", template_name, cx, cy, sc)
        return int(cx), int(cy)

    LOGGER.debug("Template %s not found", template_name)
    return None

def locate_text_on_screen(
    query: str,
    lang: str = "eng",
    threshold: float = 0.6
) -> Optional[Tuple[int, int]]:
    """
    OCR-based search: take a screenshot of the entire screen,
    run Tesseract to get bounding boxes, then find the first occurrence
    of `query` (exact substring match) and return the (center_x, center_y).

    Parameters
    ----------
    query : str
        Exact substring to search for in recognized words/lines.
    lang : str
        Language for Tesseract (e.g. "eng", "rus", "ukr").   
    threshold : float
        Minimal confidence (0.0–1.0) for recognized words to be considered.

    Returns
    -------
    (x_center, y_center) in screen-coordinates, or None if not found.
    """
    # 1) Получаем скриншот всего экрана через pyautogui
    screenshot = pag.screenshot()
    img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    # 2) Запускаем Tesseract, запрашиваем детальную информацию (level=WORD)
    #    image_to_data вернёт табличку с колонками: ['level','page_num','block_num',...,'left','top','width','height','conf','text']
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)

    n_boxes = len(data["text"])
    for i in range(n_boxes):
        text = data["text"][i].strip()
        conf = float(data["conf"][i]) / 100.0  # преобразуем в 0.0–1.0
        if conf < threshold or text == "":
            continue

        # Проверяем, есть ли в распознанном слове нужная подстрока
        if query.lower() in text.lower():
            x, y = int(data["left"][i]), int(data["top"][i])
            w, h = int(data["width"][i]), int(data["height"][i])
            center_x = x + w // 2
            center_y = y + h // 2
            return (center_x, center_y)

    return None