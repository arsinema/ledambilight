"""Модели сцен 2.0: композиции, слои и их валидация.

Композиция — это стек слоёв. Слой не знает, как устроен захват или WLED:
он получает нормализованный контекст и возвращает цвета диодов.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field

LAYER_TYPES = (
    "screen", "audio", "effect", "notification", "spotify", "solid", "gradient",
)
BLEND_MODES = ("normal", "add", "screen", "multiply", "overlay")
MASK_MODES = ("all", "range", "indices", "invert_range")


def _clean_id(value):
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip())
    return text.strip("-")[:48] or uuid.uuid4().hex[:12]


@dataclass
class Layer:
    id: str
    type: str = "effect"
    enabled: bool = True
    opacity: float = 1.0
    blend: str = "normal"
    effect: str = ""
    params: dict = field(default_factory=dict)
    mask: dict = field(default_factory=dict)
    z: int = 0

    def normalized(self):
        self.id = _clean_id(self.id)
        if self.type not in LAYER_TYPES:
            self.type = "effect"
        if self.blend not in BLEND_MODES:
            self.blend = "normal"
        try:
            self.opacity = max(0.0, min(1.0, float(self.opacity)))
        except (TypeError, ValueError):
            self.opacity = 1.0
        self.params = copy.deepcopy(self.params) if isinstance(self.params, dict) else {}
        self.mask = copy.deepcopy(self.mask) if isinstance(self.mask, dict) else {}
        mode = self.mask.get("mode", "all")
        if mode not in MASK_MODES:
            self.mask["mode"] = "all"
        try:
            self.z = int(self.z)
        except (TypeError, ValueError):
            self.z = 0
        if not self.effect:
            self.effect = {
                "screen": "screen",
                "audio": "audio_bars",
                "notification": "notification",
                "spotify": "spotify_palette",
                "solid": "solid",
                "gradient": "gradient",
            }.get(self.type, "solid")
        return self

    def to_dict(self):
        self.normalized()
        return {
            "id": self.id, "type": self.type, "enabled": self.enabled,
            "opacity": self.opacity, "blend": self.blend,
            "effect": self.effect, "params": copy.deepcopy(self.params),
            "mask": copy.deepcopy(self.mask), "z": self.z,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            data = {}
        return cls(
            id=data.get("id") or uuid.uuid4().hex[:12],
            type=data.get("type", "effect"),
            enabled=bool(data.get("enabled", True)),
            opacity=data.get("opacity", 1.0),
            blend=data.get("blend", "normal"),
            effect=str(data.get("effect") or ""),
            params=data.get("params") if isinstance(data.get("params"), dict) else {},
            mask=data.get("mask") if isinstance(data.get("mask"), dict) else {},
            z=data.get("z", 0),
        ).normalized()


@dataclass
class Composition:
    id: str
    name: str = "Новая композиция"
    layers: list = field(default_factory=list)
    transition_ms: int = 300
    background: tuple = (0, 0, 0)
    hotkey: str = ""

    def normalized(self):
        self.id = _clean_id(self.id)
        self.name = str(self.name or "Композиция")[:80]
        try:
            self.transition_ms = max(0, min(5000, int(self.transition_ms)))
        except (TypeError, ValueError):
            self.transition_ms = 300
        self.hotkey = str(self.hotkey or "")[:32]
        clean = []
        for index, layer in enumerate(self.layers or []):
            if isinstance(layer, Layer):
                item = layer.normalized()
            else:
                item = Layer.from_dict(layer)
            item.z = item.z if item.z else index
            # Звуковой слой — это добавка к базе, а не замена: в тишине
            # audio_bars возвращает чёрный и при normal полностью гасил Spotify.
            if item.type == "audio" and item.blend == "normal":
                item.blend = "add"
            clean.append(item)
        self.layers = clean
        self.background = _color(self.background, (0, 0, 0))
        return self

    def to_dict(self):
        self.normalized()
        return {
            "id": self.id, "name": self.name,
            "layers": [layer.to_dict() for layer in self.layers],
            "transition_ms": self.transition_ms,
            "background": list(self.background),
            "hotkey": self.hotkey,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            data = {}
        layers = [Layer.from_dict(item) for item in data.get("layers") or []]
        return cls(
            id=data.get("id") or uuid.uuid4().hex[:12],
            name=data.get("name", "Композиция"),
            layers=layers,
            transition_ms=data.get("transition_ms", 300),
            background=data.get("background", (0, 0, 0)),
            hotkey=data.get("hotkey", ""),
        ).normalized()


def _color(value, fallback):
    try:
        r, g, b = (int(max(0, min(255, int(v)))) for v in value[:3])
        return (r, g, b)
    except (TypeError, ValueError, IndexError):
        return tuple(fallback)


def default_compositions():
    """Готовые композиции, которые не ломают привычный Ambilight."""
    return [
        Composition(
            id="classic", name="Классический Ambilight", transition_ms=250,
            layers=[
                Layer(id="screen", type="screen", effect="screen", z=0),
            ],
        ).to_dict(),
        Composition(
            id="game-2", name="Игра 2.0", transition_ms=180,
            layers=[
                Layer(id="screen", type="screen", effect="screen", z=0),
                Layer(
                    id="audio", type="audio", effect="pulse", z=1, opacity=0.35,
                    params={"color": [80, 160, 255], "min": 0.0, "max": 0.7},
                ),
                Layer(
                    id="notify", type="notification", z=10, opacity=0.9,
                    effect="notification", params={"duration": 2.2},
                ),
            ],
        ).to_dict(),
        Composition(
            id="music-2", name="Музыка 2.0", transition_ms=300,
            layers=[
                Layer(
                    id="spotify", type="spotify", z=0, opacity=0.85,
                    effect="spotify_palette",
                ),
                Layer(
                    id="bars", type="audio", effect="audio_bars", z=1, opacity=0.7,
                    params={"mirror": True, "height": 1.0},
                ),
                Layer(
                    id="notify", type="notification", z=10, opacity=0.9,
                    effect="notification",
                ),
            ],
        ).to_dict(),
        Composition(
            id="calm", name="Спокойный вечер", transition_ms=1200,
            layers=[
                Layer(
                    id="base", type="gradient", effect="gradient", z=0,
                    params={"colors": [[20, 35, 90], [90, 40, 110], [10, 20, 60]],
                            "speed": 0.03},
                ),
                Layer(
                    id="breath", type="effect", effect="breathing", z=1,
                    opacity=0.35, params={"color": [80, 120, 255], "rate": 0.12},
                ),
            ],
        ).to_dict(),
    ]


def normalize_compositions(items):
    result = []
    seen = set()
    for raw in items or []:
        comp = Composition.from_dict(raw)
        if comp.id in seen:
            comp.id = f"{comp.id}-{uuid.uuid4().hex[:4]}"
        seen.add(comp.id)
        result.append(comp)
    return result
