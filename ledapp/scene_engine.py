"""Рендерер слоистых композиций 2.0."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np

from .effects import _color, render_effect
from .scene_model import Composition, normalize_compositions


@dataclass
class NotificationEvent:
    text: str = ""
    color: tuple = (80, 200, 255)
    source: str = ""
    duration: float = 2.0
    created: float = field(default_factory=time.monotonic)
    pattern: str = "pulse"

    def active(self, now=None):
        now = time.monotonic() if now is None else now
        return now - self.created <= max(0.2, float(self.duration))


class NotificationBus:
    """Потокобезопасная очередь коротких цветовых уведомлений."""

    def __init__(self, max_items=12):
        self._items = []
        self._lock = threading.Lock()
        self._max = max(1, int(max_items))

    def push(self, text="", color=(80, 200, 255), source="", duration=2.0,
             pattern="pulse"):
        event = NotificationEvent(
            text=str(text)[:200],
            color=_color(color, (80, 200, 255)).astype(int).tolist(),
            source=str(source)[:80],
            duration=max(0.2, min(30.0, float(duration))),
            pattern=str(pattern or "pulse"),
        )
        with self._lock:
            self._items.append(event)
            del self._items[:-self._max]
        return event

    def active(self, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            self._items = [item for item in self._items if item.active(now)]
            return list(self._items)

    def clear(self):
        with self._lock:
            self._items.clear()


def _mask_weights(mask, n):
    """1.0 — слой действует на диод, 0.0 — диод остаётся базовым."""
    mask = mask if isinstance(mask, dict) else {}
    mode = mask.get("mode", "all")
    if mode == "all":
        return np.ones(n, dtype=np.float32)
    weights = np.zeros(n, dtype=np.float32)
    if mode in ("range", "invert_range"):
        try:
            start = max(0, min(n - 1, int(mask.get("start", 0))))
            end = max(start + 1, min(n, int(mask.get("end", n))))
        except (TypeError, ValueError):
            start, end = 0, n
        if mode == "range":
            weights[start:end] = 1.0
        else:
            weights[:] = 1.0
            weights[start:end] = 0.0
    elif mode == "indices":
        for index in mask.get("indices") or []:
            try:
                value = int(index)
            except (TypeError, ValueError):
                continue
            if 0 <= value < n:
                weights[value] = 1.0
    return weights


def blend(base, top, mode="normal", opacity=1.0):
    base = np.asarray(base, dtype=np.float32)
    top = np.asarray(top, dtype=np.float32)
    if top.shape != base.shape:
        return base
    opacity_arr = np.asarray(opacity, dtype=np.float32)
    if opacity_arr.ndim == 1:
        opacity_arr = opacity_arr[:, None]
    opacity_arr = np.clip(opacity_arr, 0.0, 1.0)
    if mode == "add":
        mixed = base + top
    elif mode == "screen":
        mixed = 255.0 - (255.0 - base) * (255.0 - top) / 255.0
    elif mode == "multiply":
        mixed = base * top / 255.0
    elif mode == "overlay":
        low = 2.0 * base * top / 255.0
        high = 255.0 - 2.0 * (255.0 - base) * (255.0 - top) / 255.0
        mixed = np.where(base < 128.0, low, high)
    else:
        mixed = top
    return np.clip(base * (1.0 - opacity_arr) + mixed * opacity_arr, 0.0, 255.0)


class SceneRenderer:
    """Собирает кадр из слоёв; хранит состояние перехода между сценами."""

    def __init__(self, notifications=None):
        self.notifications = notifications or NotificationBus()
        self._last = None
        self._last_comp_id = None
        self._switch_at = 0.0
        self._last_time = None

    def render(self, composition, screen_pixels=None, audio=None,
               palette=None, now=None, led_count=None):
        if not isinstance(composition, Composition):
            composition = Composition.from_dict(composition)
        composition.normalized()
        now = time.monotonic() if now is None else float(now)
        n = int(led_count or 0)
        if n <= 0 and screen_pixels is not None:
            n = int(np.asarray(screen_pixels).shape[0])
        n = max(1, min(1000, n))
        base = np.repeat(
            _color(composition.background)[None, :], n, axis=0
        ).astype(np.float32)
        active_notes = self.notifications.active(now)

        layers = sorted(
            [layer for layer in composition.layers if layer.enabled],
            key=lambda layer: layer.z,
        )
        for layer in layers:
            if layer.type == "notification":
                if not active_notes:
                    continue
                note = active_notes[-1]
                params = dict(layer.params or {})
                params.setdefault("color", note.color)
                params.setdefault("speed", 1.6)
                values = render_effect(
                    "notification", n, now, params, audio, palette, screen_pixels,
                )
            elif layer.type == "screen":
                values = render_effect(
                    "screen", n, now, layer.params, audio, palette, screen_pixels,
                )
            elif layer.type == "spotify":
                local = dict(layer.params or {})
                if palette is not None and "colors" not in local:
                    local["colors"] = np.asarray(palette).tolist()
                values = render_effect(
                    layer.effect or "spotify_palette", n, now, local, audio,
                    palette, screen_pixels,
                )
            else:
                values = render_effect(
                    layer.effect, n, now, layer.params, audio, palette,
                    screen_pixels,
                )
            weights = _mask_weights(layer.mask, n)
            base = blend(base, values, layer.blend, layer.opacity * weights)

        if self._last is not None and self._last_comp_id == composition.id:
            # Мягкое сглаживание только на уровне композиции, без лишней
            # инерции у эффектов с собственным временем.
            base = self._last * 0.15 + base * 0.85
        elif self._last is not None and composition.transition_ms > 0:
            elapsed = max(0.0, (now - self._switch_at) * 1000.0)
            alpha = min(1.0, elapsed / max(1, composition.transition_ms))
            base = self._last * (1.0 - alpha) + base * alpha

        if self._last_comp_id != composition.id:
            self._last_comp_id = composition.id
            self._switch_at = now
        self._last = base.copy()
        self._last_time = now
        return np.clip(base, 0.0, 255.0)


def compositions_from_config(config):
    section = (config or {}).get("scene_composer", {})
    return normalize_compositions(section.get("compositions") or [])


def active_composition(config):
    section = (config or {}).get("scene_composer", {})
    if not section.get("enabled", False):
        return None
    active = section.get("active", "")
    for comp in compositions_from_config(config):
        if comp.id == active:
            return comp
    comps = compositions_from_config(config)
    return comps[0] if comps else None
