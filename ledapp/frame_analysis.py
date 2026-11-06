"""
Анализ кадра: чёрные полосы (letterbox), статичный интерфейс (HUD),
статичность сцены и тип контента.

Всё считается на уже уменьшенной копии кадра (обычно 480x270), поэтому
стоит доли миллисекунды и не влияет на FPS.
"""

import logging
import time

import numpy as np

log = logging.getLogger(__name__)


class FrameAnalyzer:
    """Накопительная статистика по кадрам.

    Что умеет:
      * letterbox()      — границы реального изображения без чёрных полос;
      * is_cinema        — похоже на фильм (широкие полосы сверху/снизу);
      * is_static        — картинка не меняется (пауза, статичное меню);
      * ui_mask()        — карта статичных областей (HUD игр, панели задач).
    """

    # Разрешение карты HUD: 32x18 клеток достаточно, чтобы грубо очертить
    # миникарту или полосу здоровья, и при этом считается мгновенно
    CELLS_X = 32
    CELLS_Y = 18

    def __init__(self):
        self._prev_gray = None
        self._still_frames = 0
        self._moving_frames = 0

        # «Было движение, потом остановилось» — это пауза видео, а не статичный
        # рабочий стол с открытым окном
        self._motion_streak = 0
        self._motion_was_live = False
        self._last_motion_time = 0.0

        # letterbox: доли от размера маленького кадра (0..1) для каждой стороны
        self._lb_candidate = (0.0, 0.0, 0.0, 0.0)
        self._lb_streak = 0
        self.letterbox = (0.0, 0.0, 0.0, 0.0)

        # карта статичных клеток
        rows, cols = self.CELLS_Y, self.CELLS_X
        self._hits = np.zeros((rows, cols), dtype=np.float32)
        self._mask = np.zeros((rows, cols), dtype=bool)
        self._cell_shape = None

        self.is_cinema = False
        self.is_static = False

    # ------------------------------------------------------------------

    def update(self, small: np.ndarray):
        """Обновляет статистику по уменьшенному кадру (H, W, 3). Возвращает self."""
        if small is None or small.size == 0 or small.ndim != 3:
            return self

        gray = small.mean(axis=2)

        # --- статичность всей картинки ---
        if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
            diff = float(np.abs(gray - self._prev_gray).mean())
            if diff < 0.8:
                self._still_frames += 1
                self._moving_frames = 0
            else:
                self._still_frames = 0
                self._moving_frames += 1
        self._prev_gray = gray

        moving = self._moving_frames > 2
        self.is_static = self._still_frames > 12

        if moving:
            self._motion_streak += 1
            self._last_motion_time = time.monotonic()
            if self._motion_streak > 25:
                self._motion_was_live = True
        else:
            self._motion_streak = 0

        self._update_letterbox(gray)
        self._update_ui_mask(gray, cell_shape=gray.shape, moving=moving)
        return self

    # ------------------------------------------------------------------

    def _update_letterbox(self, gray):
        """Ищет чёрные полосы сверху/снизу и слева/справа."""
        h, w = gray.shape
        rows = gray.mean(axis=1)
        cols = gray.mean(axis=0)

        dark = 10.0
        top = 0
        while top < h // 3 and rows[top] < dark:
            top += 1
        bottom = h
        while bottom > h - h // 3 and rows[bottom - 1] < dark:
            bottom -= 1
        left = 0
        while left < w // 4 and cols[left] < dark:
            left += 1
        right = w
        while right > w - w // 4 and cols[right - 1] < dark:
            right -= 1

        candidate = (top / h, 1.0 - bottom / h, left / w, 1.0 - right / w)

        # Слишком большая «полоса» означает тёмную сцену, а не кино:
        # не даём обрезать больше трети кадра с каждой стороны.
        if sum(candidate) > 1.2:
            return

        # Гистерезис: принимаем новую геометрию только после 4 одинаковых кадров
        if self._is_close(candidate, self._lb_candidate, 0.004):
            self._lb_streak += 1
            if self._lb_streak >= 4:
                self.letterbox = candidate
        else:
            self._lb_candidate = candidate
            self._lb_streak = 0

        top_frac, bottom_frac = self.letterbox[0], self.letterbox[1]
        self.is_cinema = (top_frac + bottom_frac) > 0.10

    @staticmethod
    def _is_close(a, b, eps):
        return all(abs(x - y) <= eps for x, y in zip(a, b))

    # ------------------------------------------------------------------

    def _update_ui_mask(self, gray, cell_shape, moving):
        """Накапливает карту областей, которые не меняются, пока сцена движется.

        Именно так выглядят HUD в играх: миникарта, полоса здоровья, счётчик
        патронов стоят на месте, а картинка за ними живёт.
        """
        h, w = cell_shape
        rows, cols = self.CELLS_Y, self.CELLS_X

        # сжимаем кадр до сетки клеток
        blocked = gray[: h - h % rows, : w - w % cols]
        cell_h = blocked.shape[0] // rows
        cell_w = blocked.shape[1] // cols
        cells = blocked.reshape(rows, cell_h, cols, cell_w).mean(axis=(1, 3))

        if self._cell_shape is None or self._cell_shape != cells.shape:
            self._cell_shape = cells.shape
            self._prev_cells = cells
            self._hits[:] = 0
            return

        cell_diff = np.abs(cells - self._prev_cells)
        self._prev_cells = cells

        if moving:
            # Клетка «замерла», пока сцена вокруг двигается - кандидат в HUD
            static_cells = cell_diff < 0.6
            self._hits = np.where(static_cells, self._hits + 1.0, self._hits - 2.5)
        else:
            # Вся картинка стоит (пауза, меню) - ничего не накапливаем,
            # иначе весь экран объявится интерфейсом
            self._hits *= 0.98

        np.clip(self._hits, 0.0, 60.0, out=self._hits)

        mask = self._hits > 18.0
        # Предохранитель: не маскируем больше трети площади
        if mask.mean() > 0.33:
            mask = np.zeros_like(mask)
        self._mask = mask

    # ------------------------------------------------------------------

    def ui_mask(self):
        """Булева карта статичных областей (rows x cols) или None."""
        if self._mask is None or not self._mask.any():
            return None
        return self._mask

    def paused_content(self) -> bool:
        """Картинка остановилась ПОСЛЕ движения — похоже на паузу видео.

        Статичный рабочий стол (окно браузера, документ) сюда не попадает:
        там движения не было вовсе, и гасить подсветку не нужно."""
        if not self.is_static or not self._motion_was_live:
            return False
        return (time.monotonic() - self._last_motion_time) < 30.0

    def reset_pause_state(self):
        self._motion_was_live = False
        self._last_motion_time = 0.0

    def ui_mask_coverage(self) -> float:
        return float(self._mask.mean()) if self._mask is not None else 0.0

    def content_rect(self, width, height, user_rect=None):
        """Итоговый прямоугольник анализа: (top, bottom, left, right) в пикселях.

        Учитывает и чёрные полосы, и пользовательскую HUD-маску.
        """
        top_f, bottom_f, left_f, right_f = self.letterbox
        top = int(top_f * height)
        bottom = height - int(bottom_f * height)
        left = int(left_f * width)
        right = width - int(right_f * width)

        if user_rect is not None:
            u_top, u_bottom, u_left, u_right = user_rect
            top = max(top, u_top)
            bottom = min(bottom, u_bottom)
            left = max(left, u_left)
            right = min(right, u_right)

        if bottom <= top or right <= left:
            return (0, height, 0, width)
        return (top, bottom, left, right)

    def reset(self):
        self._prev_gray = None
        self._still_frames = 0
        self._moving_frames = 0
        self._motion_streak = 0
        self._motion_was_live = False
        self._last_motion_time = 0.0
        self._lb_streak = 0
        self._hits[:] = 0
        self._mask = np.zeros_like(self._mask)
        self.is_cinema = False
        self.is_static = False
