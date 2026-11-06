"""
Маппер светодиодов на координаты экрана.
Позволяет пользователю нарисовать как наклеена лента (спираль, зигзаг и т.п.)
и автоматически сопоставляет каждый пиксель ленты с куском экрана,
чтобы светодиод светил цветом того места экрана, над которым он находится.
"""

import numpy as np
from typing import List, Tuple
import json

from .color_engine import compute_dominant_color, mean_color


class StripMapper:
    """
    Хранит и использует маппинг: пиксель ленты → координаты экрана.
    """

    def __init__(self, led_count: int = 90, screen_width: int = 1920, screen_height: int = 1080):
        """
        Args:
            led_count: количество светодиодов на ленте
            screen_width: ширина монитора в пикселях
            screen_height: высота монитора в пикселях
        """
        self.led_count = led_count
        self.screen_width = screen_width
        self.screen_height = screen_height

        # Путь ленты - координаты центра каждого светодиода при рисовании
        self.led_path: List[Tuple[float, float]] = []

        # Заполняются в build_mapping(): центры и прямоугольные области экрана
        # для быстрого numpy-среза при вычислении цвета каждого светодиода
        self.led_centers: List[Tuple[int, int]] = []
        self.led_boxes: List[Tuple[int, int, int, int]] = []
        self.last_strip_width = 10
        self.last_exact_positions = False

    def add_path_point(self, x: float, y: float):
        """Добавляет точку к пути ленты (при рисовании пользователем)."""
        self.led_path.append((x, y))

    def set_led_count(self, count: int):
        """Устанавливает количество светодиодов и пересчитывает маппинг"""
        self.led_count = count
        self.led_centers = []
        self.led_boxes = []

    def clear_path(self):
        """Очищает путь ленты."""
        self.led_path = []

    @staticmethod
    def _resample_evenly(points, count):
        """
        Равномерно распределяет count точек ПО РЕАЛЬНОЙ ДЛИНЕ пути,
        а не по числу исходных точек - иначе диоды "толпятся" там, где
        точки пути были близко друг к другу.
        """
        if len(points) < 2 or count <= 0:
            return []

        cum_lengths = [0.0]
        for i in range(1, len(points)):
            x1, y1 = points[i - 1]
            x2, y2 = points[i]
            seg_len = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            cum_lengths.append(cum_lengths[-1] + seg_len)

        total_length = cum_lengths[-1]
        if total_length == 0:
            return [points[0]] * count

        targets = [total_length * i / (count - 1) if count > 1 else 0.0 for i in range(count)]

        result = []
        seg_idx = 0
        for target in targets:
            while seg_idx < len(cum_lengths) - 2 and cum_lengths[seg_idx + 1] < target:
                seg_idx += 1
            seg_start_len = cum_lengths[seg_idx]
            seg_end_len = cum_lengths[seg_idx + 1]
            seg_len = seg_end_len - seg_start_len
            t = 0.0 if seg_len == 0 else (target - seg_start_len) / seg_len
            x1, y1 = points[seg_idx]
            x2, y2 = points[seg_idx + 1]
            x = x1 + t * (x2 - x1)
            y = y1 + t * (y2 - y1)
            result.append((x, y))

        return result

    def build_mapping(self, strip_width: int = 10, exact_positions: bool = False):
        """
        Строит маппинг на основе пути ленты.
        Для каждого светодиода определяет квадратик пикселей экрана,
        которые он будет "анализировать" и влиять на его цвет.

        Args:
            strip_width: ширина ленты в пикселях экрана (сколько пикселей
                         экрана влияет на один светодиод)
            exact_positions: если True и в led_path ровно led_count точек -
                         использует их КАК ЕСТЬ, без пересчёта (для режима
                         ручной расстановки каждого светодиода). Иначе
                         распределяет led_count точек равномерно по РЕАЛЬНОЙ
                         ДЛИНЕ пути (не по количеству исходных точек).
        """
        if len(self.led_path) < 2:
            return False

        if exact_positions and len(self.led_path) == self.led_count:
            interpolated_path = list(self.led_path)
        else:
            interpolated_path = self._resample_evenly(self.led_path, self.led_count)

        # Для каждого светодиода запоминаем прямоугольную область экрана вокруг
        # его позиции - используем numpy-срезы вместо питоновского цикла по
        # отдельным пикселям, это в разы быстрее на каждый кадр
        half_width = max(1, strip_width // 2)
        self.led_centers = []
        self.led_boxes = []  # (y0, y1, x0, x1) - готово для frame[y0:y1, x0:x1]

        for (x, y) in interpolated_path:
            x, y = int(x), int(y)
            x0 = max(0, x - half_width)
            x1 = min(self.screen_width, x + half_width + 1)
            y0 = max(0, y - half_width)
            y1 = min(self.screen_height, y + half_width + 1)
            self.led_centers.append((x, y))
            self.led_boxes.append((y0, y1, x0, x1))

        # Базовая раскладка: к ней возвращаемся, когда умная маска HUD снимается
        self._base_boxes = list(self.led_boxes)
        return True

    def apply_ui_mask(self, mask, frame_width: int, frame_height: int) -> bool:
        """Уводит выборку диодов из статичных областей (HUD игр).

        mask — булева карта (rows x cols). Диод, чей центр попал в статичную
        клетку, начинает смотреть в ближайшую «живую» клетку. Так миникарта
        или полоса здоровья перестают красить ленту.

        Возвращает True, если раскладка изменилась.
        """
        base = getattr(self, "_base_boxes", None) or self.led_boxes
        if not base:
            return False

        if mask is None:
            changed = self.led_boxes != base
            if changed:
                self.led_boxes = list(base)
            return changed

        rows, cols = mask.shape
        if rows == 0 or cols == 0 or frame_width <= 0 or frame_height <= 0:
            return False

        boxes = []
        changed = False
        for (y0, y1, x0, x1) in base:
            cy, cx = (y0 + y1) // 2, (x0 + x1) // 2
            r = min(rows - 1, max(0, int(cy / frame_height * rows)))
            c = min(cols - 1, max(0, int(cx / frame_width * cols)))
            if mask[r, c]:
                target = self._nearest_free_cell(mask, r, c)
                if target is not None:
                    rr, cc = target
                    nx = int((cc + 0.5) * frame_width / cols)
                    ny = int((rr + 0.5) * frame_height / rows)
                    h = y1 - y0
                    w = x1 - x0
                    ny = max(0, min(self.screen_height - h, ny - h // 2))
                    nx = max(0, min(self.screen_width - w, nx - w // 2))
                    y0, y1 = ny, ny + h
                    x0, x1 = nx, nx + w
                    changed = True
            boxes.append((y0, y1, x0, x1))

        if changed or self.led_boxes != boxes:
            self.led_boxes = boxes
            return True
        return False

    @staticmethod
    def _nearest_free_cell(mask, row, col, max_radius: int = 5):
        """Ближайшая незамаскированная клетка (по кольцам вокруг)."""
        rows, cols = mask.shape
        for radius in range(1, max_radius + 1):
            best = None
            best_dist = None
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    if max(abs(dr), abs(dc)) != radius:
                        continue
                    rr, cc = row + dr, col + dc
                    if 0 <= rr < rows and 0 <= cc < cols and not mask[rr, cc]:
                        dist = dr * dr + dc * dc
                        if best_dist is None or dist < best_dist:
                            best, best_dist = (rr, cc), dist
            if best is not None:
                return best
        return None

    def get_led_color(self, frame: np.ndarray, led_index: int, mode: str = "average",
                      clip_rect=None, linear: bool = True) -> Tuple[int, int, int]:
        """
        Вычисляет цвет для одного светодиода на основе куска экрана,
        который он "видит".

        Args:
            frame: кадр экрана (H, W, 3) в RGB
            led_index: индекс светодиода (0..led_count-1)
            mode: "average" - среднее по области (плавнее, немного смазывает),
                  "dominant" - самый частый цвет в области (контрастнее,
                  лучше подходит для чётких цветных объектов на экране)
            clip_rect: (top, bottom, left, right) - разрешённая область экрана
                  (HUD-маска). Область выборки диода прижимается внутрь неё,
                  чтобы диод не подхватывал цвет таскбара/HUD, который
                  пользователь исключил из анализа.

        Returns:
            Кортеж (R, G, B)
        """
        if led_index < 0 or led_index >= len(getattr(self, "led_boxes", [])):
            return (0, 0, 0)

        y0, y1, x0, x1 = self.led_boxes[led_index]

        if clip_rect is not None:
            ct, cb, cl, cr = clip_rect
            box_h = y1 - y0
            box_w = x1 - x0
            if y0 < ct:
                y0, y1 = ct, min(ct + box_h, cb)
            elif y1 > cb:
                y1, y0 = cb, max(cb - box_h, ct)
            if x0 < cl:
                x0, x1 = cl, min(cl + box_w, cr)
            elif x1 > cr:
                x1, x0 = cr, max(cr - box_w, cl)

        if y1 <= y0 or x1 <= x0:
            return (0, 0, 0)
        if y1 > frame.shape[0] or x1 > frame.shape[1]:
            y1 = min(y1, frame.shape[0])
            x1 = min(x1, frame.shape[1])
            if y1 <= y0 or x1 <= x0:
                return (0, 0, 0)

        patch = frame[y0:y1, x0:x1]
        if patch.size == 0:
            return (0, 0, 0)

        if mode == "dominant":
            # Область уже маленькая (обычно ~10x10 пикселей) - stride=1,
            # чтобы не терять точность на и без того небольшом кусочке
            r, g, b = compute_dominant_color(patch, sample_stride=1, quant=24)
            return (int(r), int(g), int(b))

        r, g, b = mean_color(patch, linear=linear)
        return (int(round(r)), int(round(g)), int(round(b)))

    def get_all_leds_colors(self, frame: np.ndarray, mode: str = "average",
                            clip_rect=None, linear: bool = True) -> List[Tuple[int, int, int]]:
        """
        Вычисляет цвета для всех светодиодов.

        Args:
            frame: кадр экрана (H, W, 3) в RGB
            mode: "average" или "dominant" (см. get_led_color)
            clip_rect: (top, bottom, left, right) - разрешённая область (HUD-маска)
            linear: усреднять в линейном свете (физически корректно)

        Returns:
            Список кортежей (R, G, B) для каждого светодиода
        """
        get_color = self.get_led_color
        return [get_color(frame, i, mode=mode, clip_rect=clip_rect, linear=linear)
                for i in range(self.led_count)]

    def save_to_file(self, filepath: str, strip_width: int = 10, exact_positions: bool = False):
        """Сохраняет маппинг в JSON."""
        data = {
            "led_count": self.led_count,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "led_path": self.led_path,
            "strip_width": strip_width,
            "exact_positions": exact_positions,
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def load_from_file(self, filepath: str):
        """Загружает маппинг из JSON и СРАЗУ пересобирает рабочие данные
        (led_centers/led_boxes) через build_mapping - раньше загружался только
        сырой путь без пересборки, из-за чего лента после загрузки конфига
        оставалась чёрной (get_led_color опирается на led_boxes, не на путь).

        Файл проверяется: понятная ошибка лучше, чем KeyError из недр."""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("в файле должен быть объект с маппингом")

        raw_path = data.get("led_path")
        if not isinstance(raw_path, list) or len(raw_path) < 2:
            raise ValueError("в файле нет корректного пути ленты (led_path: минимум 2 точки)")

        try:
            led_count = int(data.get("led_count") or len(raw_path))
        except (TypeError, ValueError):
            led_count = len(raw_path)
        if led_count < 1:
            raise ValueError("led_count должен быть положительным")

        try:
            points = [(float(point[0]), float(point[1])) for point in raw_path]
        except (TypeError, ValueError, IndexError):
            raise ValueError("led_path должен быть списком пар чисел [x, y]")

        screen_width = int(data.get("screen_width") or 1920)
        screen_height = int(data.get("screen_height") or 1080)
        if screen_width <= 0 or screen_height <= 0:
            raise ValueError("screen_width/screen_height должны быть положительными")

        self.led_count = led_count
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.led_path = points
        self.last_strip_width = data.get("strip_width", 10)
        self.last_exact_positions = bool(data.get("exact_positions", False))
        return self.build_mapping(strip_width=self.last_strip_width,
                                  exact_positions=self.last_exact_positions)
