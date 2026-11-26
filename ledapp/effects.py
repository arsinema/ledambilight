"""Векторизованные эффекты для Scene Composer 2.0.

Каждый эффект получает количество диодов и нормализованное время и
возвращает массив (N, 3) float32 в диапазоне 0..255. Эффекты не знают о
WLED/захвате — это позволяет тестировать их без железа.
"""

from __future__ import annotations

import colorsys

import numpy as np

from .audio_engine import SPECTRUM_BANDS

EFFECT_LABELS = {
    "solid": "Однотонный",
    "gradient": "Градиент",
    "breathing": "Дыхание",
    "wave": "Волна",
    "plasma": "Плазма",
    "flame": "Огонь",
    "meteor": "Метеор",
    "rainbow": "Радуга",
    "audio_bars": "Спектр по диодам",
    "vu_meter": "VU-метр",
    "pulse": "Пульс",
    "sparkle": "Искры",
    "spotify_palette": "Палитра Spotify",
    "notification": "Уведомление",
    "screen": "Экран",
}


def _color(value, fallback=(255, 255, 255)):
    try:
        return np.array([max(0, min(255, int(v))) for v in value[:3]], dtype=np.float32)
    except (TypeError, ValueError, IndexError):
        return np.array(fallback, dtype=np.float32)


def _palette(params, fallback=((30, 80, 255), (255, 40, 180))):
    raw = params.get("colors") if isinstance(params, dict) else None
    if not isinstance(raw, (list, tuple)) or not raw:
        raw = fallback
    return np.array([_color(item) for item in raw], dtype=np.float32)


def _n(value, default, low=0.0, high=10.0):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _gradient(n, colors, offset=0.0):
    if len(colors) == 1:
        return np.repeat(colors[:1], n, axis=0)
    positions = np.linspace(0.0, 1.0, n, dtype=np.float32) + offset
    positions = np.mod(positions, 1.0)
    scaled = positions * (len(colors) - 1)
    index = np.floor(scaled).astype(np.int32)
    index = np.clip(index, 0, len(colors) - 2)
    frac = (scaled - index)[:, None]
    return colors[index] * (1.0 - frac) + colors[index + 1] * frac


def _hsv(n, hue, saturation=1.0, value=1.0):
    hues = np.mod(hue if np.ndim(hue) else np.full(n, hue), 1.0)
    sat = np.full(n, saturation, dtype=np.float32)
    val = np.full(n, value, dtype=np.float32)
    out = np.empty((n, 3), dtype=np.float32)
    for i in range(n):
        r, g, b = colorsys.hsv_to_rgb(float(hues[i]), float(sat[i]), float(val[i]))
        out[i] = (r * 255, g * 255, b * 255)
    return out


def _audio_spectrum(audio):
    spectrum = getattr(audio, "spectrum", None) if audio is not None else None
    if spectrum is None:
        return np.zeros(SPECTRUM_BANDS, dtype=np.float32)
    return np.asarray(spectrum, dtype=np.float32)[:SPECTRUM_BANDS]


def solid(n, t, params, audio, palette, screen=None):
    return np.repeat(_color(params.get("color", (255, 255, 255)))[None, :], n, axis=0)


def gradient(n, t, params, audio, palette, screen=None):
    speed = _n(params.get("speed", 0.05), 0.05, -2.0, 2.0)
    return _gradient(n, _palette(params), offset=speed * t)


def breathing(n, t, params, audio, palette, screen=None):
    rate = _n(params.get("rate", 0.15), 0.15, 0.01, 2.0)
    level = 0.15 + 0.85 * (0.5 + 0.5 * np.sin(2 * np.pi * rate * t))
    color = _color(params.get("color", palette[0] if len(palette) else (80, 120, 255)))
    return np.repeat((color * level)[None, :], n, axis=0)


def wave(n, t, params, audio, palette, screen=None):
    speed = _n(params.get("speed", 0.4), 0.4, -4.0, 4.0)
    wavelength = _n(params.get("wavelength", 1.5), 1.5, 0.1, 20.0)
    colors = _palette(params)
    phase = (np.arange(n, dtype=np.float32) / max(1, n - 1) * wavelength
             - speed * t)
    level = 0.25 + 0.75 * (0.5 + 0.5 * np.sin(phase * 2 * np.pi))
    base = _gradient(n, colors, offset=0.1 * t)
    return base * level[:, None]


def plasma(n, t, params, audio, palette, screen=None):
    speed = _n(params.get("speed", 0.25), 0.25, -2.0, 2.0)
    scale = _n(params.get("scale", 3.0), 3.0, 0.2, 20.0)
    x = np.linspace(0, 1, n, dtype=np.float32)
    v = (np.sin((x * scale + t * speed) * 2 * np.pi)
         + np.sin((x * scale * 1.7 - t * speed * 0.8) * 2 * np.pi)
         + np.sin((x * 5.0 + t * speed * 1.3) * 2 * np.pi)) / 3.0
    level = 0.15 + 0.85 * (0.5 + 0.5 * v)
    base = _gradient(n, _palette(params), offset=0.2 * t)
    return base * level[:, None]


def flame(n, t, params, audio, palette, screen=None):
    speed = _n(params.get("speed", 1.0), 1.0, 0.05, 8.0)
    intensity = _n(params.get("intensity", 1.0), 1.0, 0.1, 2.0)
    x = np.linspace(0, 1, n, dtype=np.float32)
    v = (0.55 + 0.25 * np.sin((x * 9 + t * speed * 3) * 2 * np.pi)
         + 0.12 * np.sin((x * 23 - t * speed * 5) * 2 * np.pi)
         + 0.08 * np.sin((x * 41 + t * speed * 7) * 2 * np.pi))
    v = np.clip(v * intensity, 0.0, 1.0)
    # горячий центр: жёлтый -> оранжевый -> красный
    r = np.clip(140 + 115 * v, 0, 255)
    g = np.clip(30 + 150 * v * v, 0, 255)
    b = np.clip(5 + 40 * v * v * v, 0, 255)
    return np.stack([r, g, b], axis=-1).astype(np.float32)


def meteor(n, t, params, audio, palette, screen=None):
    speed = _n(params.get("speed", 0.8), 0.8, 0.02, 5.0)
    tail = _n(params.get("tail", 0.25), 0.25, 0.02, 1.0)
    color = _color(params.get("color", (255, 180, 70)))
    head = (t * speed) % 1.0
    x = np.linspace(0, 1, n, dtype=np.float32)
    dist = np.mod(head - x, 1.0)
    level = np.clip(1.0 - dist / max(0.01, tail), 0.0, 1.0) ** 1.6
    return color[None, :] * level[:, None]


def rainbow(n, t, params, audio, palette, screen=None):
    speed = _n(params.get("speed", 0.1), 0.1, -2.0, 2.0)
    spread = _n(params.get("spread", 1.0), 1.0, 0.05, 10.0)
    hue = np.linspace(0, spread, n, dtype=np.float32) + speed * t
    return _hsv(n, hue)


def audio_bars(n, t, params, audio, palette, screen=None):
    spectrum = _audio_spectrum(audio)
    if spectrum.size == 0:
        return np.zeros((n, 3), dtype=np.float32)
    mirror = bool(params.get("mirror", False))
    height = _n(params.get("height", 1.0), 1.0, 0.1, 2.0)
    colors = _palette(params)
    count = max(1, n // 2 if mirror else n)
    bins = np.minimum(spectrum.size - 1,
                      (np.arange(count) * spectrum.size // count)).astype(np.int32)
    values = np.clip(spectrum[bins] * height, 0.0, 1.0)
    base = _gradient(count, colors)
    bars = base * values[:, None]
    if mirror:
        return np.concatenate([bars[::-1], bars], axis=0)[:n]
    return bars[:n]


def vu_meter(n, t, params, audio, palette, screen=None):
    volume = float(getattr(audio, "volume", 0.0) or 0.0) if audio is not None else 0.0
    level = max(0.0, min(1.0, volume))
    color = _color(params.get("color", palette[0] if len(palette) else (50, 255, 120)))
    lit = int(round(level * n))
    out = np.zeros((n, 3), dtype=np.float32)
    if lit:
        ramp = np.linspace(0.25, 1.0, lit, dtype=np.float32)[:, None]
        out[:lit] = color[None, :] * ramp
    return out


def pulse(n, t, params, audio, palette, screen=None):
    beat = float(getattr(audio, "beat", 0.0) or 0.0) if audio is not None else 0.0
    volume = float(getattr(audio, "volume", 0.0) or 0.0) if audio is not None else 0.0
    level = max(beat, volume * 0.8)
    lo = _n(params.get("min", 0.0), 0.0, 0.0, 1.0)
    hi = _n(params.get("max", 0.8), 0.8, 0.0, 1.0)
    color = _color(params.get("color", palette[-1] if len(palette) else (255, 60, 150)))
    return np.repeat((color * (lo + (hi - lo) * level))[None, :], n, axis=0)


def sparkle(n, t, params, audio, palette, screen=None):
    density = _n(params.get("density", 0.08), 0.08, 0.0, 1.0)
    speed = _n(params.get("speed", 0.5), 0.5, 0.01, 5.0)
    color = _color(params.get("color", (255, 255, 255)))
    out = np.zeros((n, 3), dtype=np.float32)
    count = max(1, int(n * density))
    for k in range(count):
        seed = (k * 2654435761) % 4294967296
        pos = ((seed / 4294967296.0) + t * speed * (0.5 + (k % 5) * 0.18)) % 1.0
        idx = int(pos * n) % n
        out[idx] = color
    return out


def spotify_palette(n, t, params, audio, palette, screen=None):
    speed = _n(params.get("speed", 0.04), 0.04, -1.0, 1.0)
    energy = float(getattr(audio, "volume", 0.0) or 0.0) if audio is not None else 0.0
    base = _gradient(n, palette, offset=speed * t)
    breathe = 0.55 + 0.45 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.15 * t))
    level = breathe * (0.7 + 0.3 * max(0.0, min(1.0, energy)))
    return np.clip(base * level, 0, 255)


def notification(n, t, params, audio, palette, screen=None):
    color = _color(params.get("color", (80, 200, 255)))
    speed = _n(params.get("speed", 1.6), 1.6, 0.1, 6.0)
    level = 0.5 + 0.5 * np.sin(2 * np.pi * speed * t)
    return np.repeat((color * level)[None, :], n, axis=0)


def screen(n, t, params, audio, palette, screen_pixels=None):
    if screen_pixels is None:
        return np.zeros((n, 3), dtype=np.float32)
    arr = np.asarray(screen_pixels, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] != n:
        return np.zeros((n, 3), dtype=np.float32)
    return arr[:, :3]


EFFECTS = {
    "solid": solid,
    "gradient": gradient,
    "breathing": breathing,
    "wave": wave,
    "plasma": plasma,
    "flame": flame,
    "meteor": meteor,
    "rainbow": rainbow,
    "audio_bars": audio_bars,
    "vu_meter": vu_meter,
    "pulse": pulse,
    "sparkle": sparkle,
    "spotify_palette": spotify_palette,
    "notification": notification,
    "screen": screen,
}


def render_effect(name, n, t, params=None, audio=None, palette=None, screen_pixels=None):
    func = EFFECTS.get(name) or EFFECTS["solid"]
    params = params if isinstance(params, dict) else {}
    if palette is None:
        palette = _palette(params)
    try:
        out = func(int(n), float(t), params, audio, palette, screen_pixels)
        out = np.asarray(out, dtype=np.float32)
        if out.shape != (n, 3):
            out = np.zeros((n, 3), dtype=np.float32)
        return np.clip(out, 0.0, 255.0)
    except Exception:
        return np.zeros((n, 3), dtype=np.float32)
