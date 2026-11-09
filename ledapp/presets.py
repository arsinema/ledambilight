"""Экспорт и импорт пресетов LED Ambilight.

Пресет — это JSON с настройками цвета/фильтров/сцен и (опционально) текущим
маппингом ленты. Сетевые адреса, MQTT, автозапуск и прочие машинно-зависимые
вещи в пресет не попадают: его можно передать другому человеку.
"""

import copy
import datetime as _dt

from . import __version__
from .config import _sanitize

PRESET_FORMAT = "ledambilight-preset"
PRESET_VERSION = 1

SECTION_KEYS = (
    "calibration", "brightness", "filters", "power", "content",
    "audio", "profiles", "lamp", "standby", "schedule", "sunrise",
    "hud_mask", "app_scenes",
)
TOP_KEYS = (
    "analysis_area_percent", "led_sampling_mode", "sample_width",
    "strip_meters", "strip_density", "wled_led_count", "led_start_offset",
    "monitor_diagonal_inches",
)


def export_preset(config, mapping=None, name=""):
    settings = {}
    for key in TOP_KEYS:
        if key in config:
            settings[key] = copy.deepcopy(config[key])
    for key in SECTION_KEYS:
        value = config.get(key)
        if isinstance(value, dict):
            settings[key] = copy.deepcopy(value)
    return {
        "format": PRESET_FORMAT,
        "version": PRESET_VERSION,
        "app_version": __version__,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "name": str(name or "").strip(),
        "settings": settings,
        "mapping": copy.deepcopy(mapping) if mapping else None,
    }


def _validate_mapping(mapping):
    if mapping is None:
        return None
    if not isinstance(mapping, dict):
        raise ValueError("mapping должен быть объектом")
    try:
        led_count = int(mapping.get("led_count"))
        screen_w = int(mapping.get("screen_width"))
        screen_h = int(mapping.get("screen_height"))
    except (TypeError, ValueError):
        raise ValueError("в маппинге нет корректных led_count/screen_width/screen_height")
    if led_count < 1 or screen_w < 1 or screen_h < 1:
        raise ValueError("размеры маппинга должны быть положительными")
    raw_path = mapping.get("led_path")
    if not isinstance(raw_path, list) or len(raw_path) < 2:
        raise ValueError("в маппинге должен быть путь минимум из 2 точек")
    path = []
    for point in raw_path:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            raise ValueError("точки маппинга должны быть парами [x, y]")
        try:
            path.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            raise ValueError("координаты маппинга должны быть числами")
    return {
        "led_count": led_count,
        "screen_width": screen_w,
        "screen_height": screen_h,
        "strip_width": int(mapping.get("strip_width", 10) or 10),
        "exact_positions": bool(mapping.get("exact_positions", False)),
        "led_path": path,
    }


def validate_preset(data):
    """Проверяет файл пресета и возвращает нормализованную копию."""
    if not isinstance(data, dict):
        raise ValueError("корень пресета должен быть объектом")
    if data.get("format") != PRESET_FORMAT:
        raise ValueError("это не файл пресета LED Ambilight")
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        version = 0
    if version < 1 or version > PRESET_VERSION:
        raise ValueError(f"неподдерживаемая версия пресета: {data.get('version')!r}")

    raw_settings = data.get("settings")
    if not isinstance(raw_settings, dict):
        raise ValueError("в пресете нет секции settings")
    settings = {}
    for key in TOP_KEYS:
        if key in raw_settings:
            value = raw_settings[key]
            if isinstance(value, (dict, list)):
                continue
            settings[key] = copy.deepcopy(value)
    for key in SECTION_KEYS:
        value = raw_settings.get(key)
        if isinstance(value, dict):
            settings[key] = copy.deepcopy(value)
    if not settings:
        raise ValueError("в пресете нет поддерживаемых настроек")

    mapping = _validate_mapping(data.get("mapping"))
    return {
        "format": PRESET_FORMAT,
        "version": version,
        "app_version": str(data.get("app_version", "")),
        "created": str(data.get("created", "")),
        "name": str(data.get("name", "")),
        "settings": settings,
        "mapping": mapping,
    }


def apply_preset(config, data):
    """Возвращает новый конфиг с настройками из пресета."""
    preset = validate_preset(data)
    new_config = copy.deepcopy(config)
    for key, value in preset["settings"].items():
        new_config[key] = copy.deepcopy(value)
    return _sanitize(new_config)


def mapping_payload(data):
    """(led_path, screen_w, screen_h, exact_positions, led_count) или None."""
    mapping = _validate_mapping((data or {}).get("mapping"))
    if mapping is None:
        return None
    return (
        mapping["led_path"],
        mapping["screen_width"],
        mapping["screen_height"],
        mapping["exact_positions"],
        mapping["led_count"],
    )
