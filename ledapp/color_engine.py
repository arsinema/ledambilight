"""
Пайплайн обработки цвета перед отправкой на ленту.

Порядок обработки одного кадра:
  1. Извлечение цвета по зонам (среднее, опционально блендованное с
     доминантным цветом каждой зоны — см. worker.py:_get_zone_average)
  2. HSV-коррекция (насыщенность/яркость под тёмные/яркие сцены)
  3. Фильтр мерцания (медиана по короткому окну кадров)
  4. Адаптивное сглаживание (скорость реакции зависит от резкости смены цвета)
  5. Яркость: минимальная яркость / автояркость / общий множитель
  6. Калибровка каналов (RGB gain + offset)
  7. Порог изменения — решаем, отправлять ли новое значение на ленту
"""

import colorsys
from collections import deque

import numpy as np


def improve_color(r, g, b):
    """HSV-коррекция: усиление насыщенности для нормальных сцен,
    приглушение для тёмных, чтобы не было "грязного" цвета в тенях."""

    brightness = max(r, g, b)
    if brightness < 8:
        return (0, 0, 0)

    r_n, g_n, b_n = r / 255.0, g / 255.0, b / 255.0
    h, s, v = colorsys.rgb_to_hsv(r_n, g_n, b_n)

    if v < 0.10:
        s *= 0.35
    elif v < 0.20:
        s *= 0.65
    else:
        s *= 1.12

    s = min(s, 1.0)
    v = min(v * 0.97, 1.0)

    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return (
        int(round(r2 * 255)),
        int(round(g2 * 255)),
        int(round(b2 * 255))
    )


def compute_dominant_color(img, sample_stride=8, quant=32):
    """Быстрый приблизительный доминантный цвет через квантование + подсчёт частот.
    img уже в формате RGB (см. ScreenCapture.start).

    Использует np.bincount вместо np.unique(axis=0) - bincount работает за
    O(n + k), где k - число уникальных квантованных цветов (обычно немного
    сотен при quant>=24), тогда как unique сортирует весь массив (O(n log n)).
    На частых вызовах (до 150 раз за кадр в режиме доминантного цвета на диод)
    разница ощутима."""

    small = img[::sample_stride, ::sample_stride, :3]
    pixels = small.reshape(-1, 3).astype(np.int32)

    if pixels.shape[0] == 0:
        return (0.0, 0.0, 0.0)

    q = pixels // quant
    # Кодируем 3 канала в одно число (каждый канал помещается в свой "разряд")
    bucket_range = 256 // quant + 2  # с запасом на округление
    encoded = (q[:, 0] * bucket_range + q[:, 1]) * bucket_range + q[:, 2]

    counts = np.bincount(encoded)
    best = int(np.argmax(counts))

    b = best % bucket_range
    best //= bucket_range
    g = best % bucket_range
    best //= bucket_range
    r = best

    r = r * quant + quant // 2
    g = g * quant + quant // 2
    b = b * quant + quant // 2
    return (float(r), float(g), float(b))


def _clamp255(x):
    return max(0.0, min(255.0, x))


# Таблица sRGB -> линейный свет (гамма 2.2).
# Усреднять цвет физически правильно в линейном пространстве: в обычном sRGB
# тёмные пиксели «весят» непропорционально много, и средний цвет уходит в грязь
# (особенно на сценах с тенями).
_SRGB_TO_LINEAR = (np.arange(256, dtype=np.float32) / 255.0) ** 2.2


def mean_color_linear(pixels):
    """Средний цвет набора пикселей (N,3 uint8), посчитанный в линейном свете."""
    arr = np.asarray(pixels)
    if arr.size == 0:
        return (0.0, 0.0, 0.0)
    arr = arr.reshape(-1, 3).astype(np.uint8, copy=False)
    lin = _SRGB_TO_LINEAR[arr].mean(axis=0)
    rgb = np.power(np.maximum(lin, 0.0), 1.0 / 2.2) * 255.0
    return (float(rgb[0]), float(rgb[1]), float(rgb[2]))


def mean_color(pixels, linear=True):
    """Средний цвет набора пикселей: линейно или «как есть»."""
    if linear:
        return mean_color_linear(pixels)
    arr = np.asarray(pixels).reshape(-1, 3)
    if arr.size == 0:
        return (0.0, 0.0, 0.0)
    red, green, blue = arr.mean(axis=0)
    return (float(red), float(green), float(blue))


def _color_distance(c1, c2):
    return sum(abs(a - b) for a, b in zip(c1, c2))


def apply_calibration(r, g, b, calib):
    """Применяет калибровку (gain + offset) к цвету и клэмпит в 0-255.
    Используется и в основном пайплайне, и для быстрых тестовых команд."""
    r = r * calib.get("r_gain", 1.0) + calib.get("r_offset", 0)
    g = g * calib.get("g_gain", 1.0) + calib.get("g_offset", 0)
    b = b * calib.get("b_gain", 1.0) + calib.get("b_offset", 0)
    return (
        int(round(_clamp255(r))),
        int(round(_clamp255(g))),
        int(round(_clamp255(b))),
    )


# ----------------------------------------------------------------------
# Векторные версии того же пайплайна для поэтевиксельной отправки (WLED).
# Раньше в режиме WLED с маппингом калибровка, яркость и HSV-коррекция
# вообще не применялись: на ленту уходил "сырой" цвет с экрана, а в GUI
# при этом показывался уже обработанный цвет. Теперь оба пути используют
# одну и ту же математику (см. тест в selftest.py - скалярный и векторный
# варианты обязаны совпадать).
# ----------------------------------------------------------------------


def improve_color_array(colors):
    """Векторный аналог improve_color для массива (N, 3).

    Сохраняет оттенок каждого пикселя и меняет только насыщенность/яркость -
    ровно так же, как colorsys-версия выше:
      * очень тёмные (v < 0.10)   -> насыщенность x0.35
      * тёмные      (v < 0.20)    -> насыщенность x0.65
      * остальные                 -> насыщенность x1.12
      * яркость всегда x0.97
      * пиксели темнее 8/255      -> чёрный
    """
    arr = np.asarray(colors, dtype=np.float32).reshape(-1, 3)
    if arr.size == 0:
        return arr

    mx = arr.max(axis=1)
    mn = arr.min(axis=1)
    chroma = mx - mn
    v = mx / 255.0

    # Насыщенность в HSV = chroma / max (при max > 0)
    sat = np.where(mx > 0, chroma / np.maximum(mx, 1e-6), 0.0)
    mult = np.where(v < 0.10, 0.35, np.where(v < 0.20, 0.65, 1.12))
    new_sat = np.minimum(1.0, sat * mult)

    new_max = np.minimum(v * 0.97, 1.0) * 255.0
    new_chroma = np.minimum(new_max, new_sat * new_max)
    new_min = new_max - new_chroma

    ratio = np.where(chroma > 1e-6, new_chroma / np.maximum(chroma, 1e-6), 0.0)
    out = new_min[:, None] + (arr - mn[:, None]) * ratio[:, None]

    out[mx < 8] = 0.0
    return np.clip(out, 0.0, 255.0)


def apply_calibration_array(colors, calib):
    """Векторный аналог apply_calibration для массива (N, 3)."""
    arr = np.asarray(colors, dtype=np.float32).reshape(-1, 3)
    if arr.size == 0:
        return arr
    gains = np.array([
        calib.get("r_gain", 1.0),
        calib.get("g_gain", 1.0),
        calib.get("b_gain", 1.0),
    ], dtype=np.float32)
    offsets = np.array([
        calib.get("r_offset", 0),
        calib.get("g_offset", 0),
        calib.get("b_offset", 0),
    ], dtype=np.float32)
    return np.clip(arr * gains + offsets, 0.0, 255.0)


def apply_neighbor_blend(colors, strength=0.35):
    """Пространственное сглаживание ВДОЛЬ ленты.

    Каждый диод смешивается с двумя соседями: (1-2k)*self + k*left + k*right.
    Без этого соседние диоды, попавшие на разные части картинки, дают резкие
    цветные «ступеньки» - лента выглядит как гирлянда, а не как подсветка.
    Края обрабатываются отдельно (у ленты есть начало и конец).
    """
    arr = np.asarray(colors, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] < 3:
        return colors
    k = max(0.0, min(0.49, float(strength)))
    if k <= 0:
        return colors

    out = arr.copy()
    out[1:-1] = arr[1:-1] * (1.0 - 2.0 * k) + (arr[:-2] + arr[2:]) * k
    out[0] = arr[0] * (1.0 - k) + arr[1] * k
    out[-1] = arr[-1] * (1.0 - k) + arr[-2] * k
    out = np.clip(out, 0.0, 255.0)
    return [(int(round(r)), int(round(g)), int(round(b))) for r, g, b in out]


def apply_gamma(colors, gamma):
    """Гамма-кривая выходного цвета.

    gamma < 1 - светлее, > 1 - темнее. Нужна для HDR-мониторов и «блёклых»
    сцен, где цвета выглядят выцветшими.
    """
    if abs(float(gamma) - 1.0) < 1e-3:
        return colors
    arr = np.asarray(colors, dtype=np.float32)
    if arr.size == 0:
        return colors
    arr = np.clip(arr / 255.0, 0.0, 1.0) ** float(gamma) * 255.0
    return [(int(round(r)), int(round(g)), int(round(b))) for r, g, b in arr]


def limit_power(colors, config):
    """Мягко ограничивает суммарную яркость, если она превышает лимит по току.

    Оценка: каждый канал диода в максимуме - led_ma миллиампер, значит
    сумма (r+g+b)/255 по всем диодам даёт оценку тока в миллиамперах.
    На белой вспышке 90 диодов легко упираются в возможности блока питания.
    """
    power_cfg = config.get("power", {})
    limit = power_cfg.get("limit_ma", 0)
    if not limit or limit <= 0:
        return colors
    arr = np.asarray(colors, dtype=np.float32)
    if arr.size == 0:
        return colors
    led_ma = power_cfg.get("led_ma", 55)
    estimate = float(arr.sum()) / 255.0 * led_ma
    if estimate > limit:
        arr = arr * (float(limit) / estimate)
        return [(int(round(r)), int(round(g)), int(round(b))) for r, g, b in arr]
    return colors


def estimate_current_ma(colors, config):
    """Оценка потребляемого тока для диагностики, мА."""
    arr = np.asarray(colors, dtype=np.float32)
    if arr.size == 0:
        return 0.0
    return float(arr.sum()) / 255.0 * config.get("power", {}).get("led_ma", 55)


def process_pixel_frame(colors, config, apply_power=True):
    """Полный пайплайн цвета для массива пикселей ленты (шаги 2, 5, 6 из
    docstring модуля): HSV-коррекция -> яркость -> калибровка -> гамма.

    Яркость (минимальная/авто/множитель) считается ОДНИМ коэффициентом на
    весь кадр: иначе, например, "минимальная яркость" поднимала бы каждый
    чёрный пиксель до порога и лента светилась бы равномерной засветкой
    вместо картинки с экрана."""
    arr = improve_color_array(colors)
    if arr.size == 0:
        return []

    brightness_cfg = config.get("brightness", {})
    gain = 1.0

    min_brightness = brightness_cfg.get("min_brightness", 0)
    if min_brightness > 0:
        current_max = float(arr.max())
        if 0 < current_max < min_brightness:
            gain *= min_brightness / current_max

    if brightness_cfg.get("auto_brightness", False):
        target = brightness_cfg.get("target_brightness", 180)
        current_avg = float(arr.mean())
        if current_avg > 1:
            gain *= max(0.3, min(2.0, target / current_avg))

    gain *= brightness_cfg.get("max_scale", 1.0)
    if gain != 1.0:
        arr = arr * gain

    arr = apply_calibration_array(arr, config.get("calibration", {}))
    result = [(int(round(r)), int(round(g)), int(round(b))) for r, g, b in arr]
    result = apply_gamma(result, config.get("filters", {}).get("gamma", 1.0))
    if apply_power:
        result = limit_power(result, config)
    return result


class ColorEngine:
    def __init__(self, config):
        self.config = config
        self.profile_name = "desktop"
        self.prev_smoothed = None
        self.prev_sent = None
        self._flicker_buffer = deque(maxlen=8)

    def update_config(self, config):
        self.config = config

    def set_profile(self, name):
        """Запоминает активный профиль: у него своя скорость сглаживания,
        когда адаптивное сглаживание выключено (раньше всегда брался desktop)."""
        self.profile_name = name or "desktop"

    def reset(self):
        """Сбрасывает состояние сглаживания — вызывать при смене профиля."""
        self.prev_smoothed = None
        self._flicker_buffer.clear()

    def process(self, raw_color):
        """
        raw_color: (r, g, b) — цвет по зонам текущего режима (уже с учётом
        блендинга среднего и доминантного цвета внутри каждой зоны, см. worker.py).
        Возвращает: (final_color, should_send)
        """
        filters = self.config.get("filters", {})
        r, g, b = raw_color

        # --- 2. HSV-коррекция ---
        r, g, b = improve_color(r, g, b)

        # --- 3. Фильтр мерцания (медиана по окну) ---
        if filters.get("flicker_filter", True):
            window = max(1, min(8, filters.get("flicker_window", 3)))
            self._flicker_buffer.append((r, g, b))
            while len(self._flicker_buffer) > window:
                self._flicker_buffer.popleft()

            arr = np.array(self._flicker_buffer)
            r, g, b = np.median(arr, axis=0)

        target = (float(r), float(g), float(b))

        # --- 4. Адаптивное сглаживание ---
        if self.prev_smoothed is None:
            smoothed = target
        elif filters.get("adaptive_smoothing", True):
            delta = _color_distance(target, self.prev_smoothed)
            sensitivity = max(1, filters.get("adaptive_sensitivity", 90))
            ratio = min(1.0, delta / sensitivity)

            s_min = filters.get("smoothing_min", 0.12)
            s_max = filters.get("smoothing_max", 0.55)
            factor = s_min + (s_max - s_min) * ratio

            smoothed = tuple(
                self.prev_smoothed[i] + (target[i] - self.prev_smoothed[i]) * factor
                for i in range(3)
            )
        else:
            fixed_factor = self.config.get("profiles", {}).get(
                self.profile_name, {}
            ).get("smoothing", 0.25)
            smoothed = tuple(
                self.prev_smoothed[i] + (target[i] - self.prev_smoothed[i]) * fixed_factor
                for i in range(3)
            )

        self.prev_smoothed = smoothed
        r, g, b = smoothed

        # --- 5. Яркость ---
        brightness_cfg = self.config.get("brightness", {})

        min_brightness = brightness_cfg.get("min_brightness", 0)
        if min_brightness > 0:
            current_max = max(r, g, b)
            if 0 < current_max < min_brightness:
                scale = min_brightness / current_max
                r, g, b = r * scale, g * scale, b * scale

        if brightness_cfg.get("auto_brightness", False):
            target_brightness = brightness_cfg.get("target_brightness", 180)
            current_avg = (r + g + b) / 3.0
            if current_avg > 1:
                gain = target_brightness / current_avg
                gain = max(0.3, min(2.0, gain))
                r, g, b = r * gain, g * gain, b * gain

        max_scale = brightness_cfg.get("max_scale", 1.0)
        r, g, b = r * max_scale, g * max_scale, b * max_scale

        # --- 6. Калибровка каналов (gain + offset) ---
        calib = self.config.get("calibration", {})
        final_color = apply_calibration(r, g, b, calib)

        # --- 6b. Гамма (HDR / блёклые сцены) ---
        gamma = filters.get("gamma", 1.0)
        if abs(float(gamma) - 1.0) > 1e-3:
            final_color = apply_gamma([final_color], gamma)[0]

        # --- 7. Порог изменения ---
        threshold = filters.get("change_threshold", 4)
        should_send = True
        if self.prev_sent is not None:
            if _color_distance(final_color, self.prev_sent) < threshold:
                should_send = False

        if should_send:
            self.prev_sent = final_color

        return final_color, should_send
