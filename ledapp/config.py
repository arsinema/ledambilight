"""
Конфигурация приложения: дефолтные значения, загрузка и сохранение в JSON.

Файл конфига по умолчанию лежит рядом с приложением (config.json).
Если писать туда нельзя (приложение установлено в Program Files и т.п.),
конфиг автоматически переезжает в %APPDATA%\\LedAmbilight\\config.json.

Запись атомарная: сначала пишется .tmp, потом os.replace + копия .bak.
Так падение/выключение питания посреди записи не оставит битый JSON.
"""

import copy
import json
import logging
import os
import shutil
import sys

log = logging.getLogger(__name__)


def _app_dir():
    """Папка приложения.

    В собранной версии (.exe через PyInstaller) `__file__` указывает во
    временную папку распаковки — если писать конфиг туда, он исчезнет при
    выходе. Поэтому рядом с exe, а не с распакованными модулями.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_DIR = _app_dir()

DEFAULT_CONFIG = {
    # BLE
    "write_uuid": "0000fff3-0000-1000-8000-00805f9b34fb",
    "device_address": None,
    "device_name": None,
    "auto_connect": True,        # автоподключение к последнему устройству при запуске
    "autostart_windows": False,  # запускать приложение при старте Windows
    "strip_mode": "ble",         # "ble" (ELK-BLEDOM) | "wled" (ESP32 по Wi-Fi) | "wled_serial" (USB)
    "wled_host": "192.168.1.100",  # IP-адрес WLED-устройства
    "wled_port": 21324,          # UDP порт WLED
    "wled_led_count": 90,        # количество светодиодов на ленте
    "wled_serial_port": "COM5",  # COM-порт для подключения по USB (Adalight)
    "wled_serial_baud": 115200,  # скорость USB-соединения (115200 хватает на 50-100 диодов)
    "strip_meters": 5,           # метраж ленты (1-10м)
    "strip_density": 30,         # плотность светодиодов на метр (18/30/60/144)
    "screen_width": 1920,        # ширина экрана для маппинга (обновляется автоматически)
    "screen_height": 1080,       # высота экрана для маппинга (обновляется автоматически)
    "monitor_diagonal_inches": 24.0,  # диагональ монитора - для точного расчёта реального периметра
    "led_sampling_mode": "average",  # "average" (среднее по области) или "dominant" (самый частый цвет)
    "sample_width": 10,          # размер области выборки одного диода, px (5-40)
    "capture_backend": "dxgi",   # "dxgi" (быстрый) или "wgc" (работает в HDR/эксклюзивном полноэкране)

    "minimize_to_tray": True,     # закрытие окна сворачивает в трей вместо выхода
    "auto_reconnect": True,       # пытаться само-восстанавливать связь при обрыве
    "first_run_done": False,      # мастер первого запуска уже показывали

    "standby": {
        "enabled": False,             # "дыхание" выбранным цветом, когда Ambilight не запущен
        "source": "fixed",            # "fixed" | "weather" | "spotify"
        "fixed_color": [80, 60, 180], # цвет дыхания для источника "fixed"
        "speed": 0.35,                # скорость дыхания (циклов в ~10 секунд)
        "weather_city": "",           # город для источника "weather" (латиницей, напр. Minsk)
    },

    # Калибровка цвета (применяется последним, перед отправкой на ленту).
    # gain — множитель канала, offset — сдвиг (компенсирует паразитную засветку
    # других каналов, из-за которой "красный" на ленте выглядит розовым/фиолетовым).
    "calibration": {
        "r_gain": 1.0,
        "g_gain": 1.0,
        "b_gain": 1.0,
        "r_offset": 0,
        "g_offset": 0,
        "b_offset": 0
    },

    # Яркость
    "brightness": {
        "min_brightness": 0,       # минимальная яркость (0-255), 0 = выключено
        "auto_brightness": False,  # автоконтроль яркости
        "target_brightness": 180,  # целевая средняя яркость для авто-режима
        "max_scale": 1.0           # общий множитель яркости (0.1 - 1.5)
    },

    # HUD-маска: сколько % от каждого края игнорировать при анализе
    "hud_mask": {
        "top": 0,
        "bottom": 0,
        "left": 0,
        "right": 0
    },

    # Область анализа для режима center_quadrants, % от экрана (50-80 рекомендуется)
    "analysis_area_percent": 60,

    # Фильтры и сглаживание
    "filters": {
        "change_threshold": 4,       # минимальная дельта цвета, чтобы отправить обновление
        "flicker_filter": True,      # игнорировать мелкие быстрые колебания
        "flicker_window": 3,         # сколько кадров держим в буфере для медианного фильтра
        "adaptive_smoothing": True,  # скорость сглаживания зависит от резкости смены цвета
        "smoothing_min": 0.12,       # мин. коэффициент сглаживания (плавно)
        "smoothing_max": 0.55,       # макс. коэффициент сглаживания (резко/быстро)
        "adaptive_sensitivity": 90,  # порог дельты, после которого сглаживание = max
        "dominant_color_priority": True,
        "dominant_weight": 0.65,     # 0 = только среднее (вымывается в серо-белый
                                      # на пёстрых сценах), 1 = только доминирующий цвет
        # Пространственное сглаживание ВДОЛЬ ленты: каждый диод смешивается с
        # соседями, чтобы не было резких цветных «ступенек» на границах
        "neighbor_blend": 0.35,      # 0 = выключено, 0.5 = сильное размытие
        # Усреднять цвет в линейном свете (гамма 2.2) - физически корректно
        "linear_average": True,
        "gamma": 1.0                 # 0.7 = светлее, 1.6 = темнее (для HDR/блёклых сцен)
    },

    # Адаптивная частота кадров: на статичной картинке захват реже
    "adaptive_fps": {
        "enabled": True,
        "min_fps": 10
    },

    # Ограничение по току: страховка от перегрузки блока питания на белых сценах
    "power": {
        "limit_ma": 0,      # 0 = не ограничивать
        "led_ma": 55        # сколько миллиампер на один диод (паспорт ленты)
    },

    # Пауза при блокировке экрана или простое
    "auto_pause": {
        "enabled": True,
        "idle_minutes": 5,
        "pause_on_lock": True
    },

    # Анализ содержимого экрана
    "content": {
        "letterbox_crop": True,   # обрезать чёрные полосы фильмов
        "auto_cinema": True,      # включать кино-профиль, если на экране фильм
        "smart_hud": True,        # сам находить статичный интерфейс игр
        "fade_on_pause": True,    # приглушать, когда картинка остановилась
        "fade_level": 0.15        # до какой доли яркости гасить на паузе
    },

    "hotkeys": {
        "enabled": True
    },

    # Пульт с телефона (встроенный HTTP-сервер).
    # По умолчанию выключен: это открытый порт в локальной сети, включать
    # должен сам пользователь, осознанно.
    "remote": {
        "enabled": False,
        "port": 8765
    },

    # Привязка приложений к сценам: {"cs2.exe": "Игра", "chrome.exe": "Кино"}
    "app_scenes": {},

    # Режим «Лампа»: лента как светильник, когда Ambilight не нужен
    "lamp": {
        "enabled": False,
        "temperature": 0.35,   # 0 = тёплый, 1 = холодный белый
        "brightness": 0.7
    },

    # Расписание: днём одна сцена, ночью другая
    "schedule": {
        "enabled": False,
        "night_start": "23:00",
        "night_end": "07:00",
        "night_scene": "Ночь",
        "day_scene": ""
    },

    # Рассвет-будильник: плавно разгорается к указанному времени
    "sunrise": {
        "enabled": False,
        "time": "07:00",
        "duration_minutes": 20
    },

    # MQTT / Home Assistant (нужен paho-mqtt и брокер)
    "mqtt": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 1883,
        "prefix": "ledambilight"
    },

    # Проверка обновлений на GitHub Releases
    "updates": {
        "check_on_start": True
    },

    # Профили захвата
    "profiles": {
        "desktop": {"mode": "quadrants", "smoothing": 0.25, "delay": 0.15},
        "movie": {"mode": "edges", "smoothing": 0.20, "delay": 0.12},
        "game": {"mode": "center_quadrants", "smoothing": 0.30, "delay": 0.03}
    },

    # Соответствие процесс -> профиль (не игра, спец-случаи типа плеера)
    "process_profile_map": {
        "opera.exe": "movie",
        "chrome.exe": "movie",
        "firefox.exe": "movie",
        "msedge.exe": "movie",
        "vlc.exe": "movie",
        "mpc-hc64.exe": "movie",
        "mpc-hc.exe": "movie",
        "mpc-be64.exe": "movie",
        "mpc-be.exe": "movie",
        "potplayermini64.exe": "movie",
        "potplayermini.exe": "movie",
        "mpv.exe": "movie",
        "kmplayer.exe": "movie",
        "gomplayer.exe": "movie",
        "wmplayer.exe": "movie",
        "plex.exe": "movie",
        "plexmediaplayer.exe": "movie",
        "kodi.exe": "movie",
        "kinopoisk.exe": "movie",
        "ivi.exe": "movie",
        "wink.exe": "movie",
        "okko.exe": "movie",
    },

    # Список процессов, которые считаются играми -> профиль "game"
    "game_processes": [
        "cs2.exe", "valorant.exe", "r5apex.exe", "eldenring.exe",
        "gta5.exe", "gta5_enhanced.exe", "witcher3.exe",
        "cyberpunk2077.exe", "minecraft.exe", "javaw.exe",
        "dota2.exe", "leagueclient.exe", "league of legends.exe",
        "fortniteclient-win64-shipping.exe", "rocketleague.exe",
        "deadbydaylight-win64-shipping.exe", "hogwartslegacy.exe",
        "starfield.exe", "fallguys_client_game.exe", "robloxplayerbeta.exe"
    ],

    # Профиль, применяемый к неизвестным процессам (desktop/movie/game).
    # "desktop" - разумный дефолт: рабочий стол и браузеры не должны внезапно
    # гонять захват в игровом режиме.
    "unknown_process_profile": "desktop",

    "target_fps": 30,

    # Аудио-реактивная подсветка
    "audio": {
        "enabled": False,
        "mode": "hybrid",       # hybrid | flash | visualizer
        "device_name": None,    # None = устройство вывода по умолчанию
        "sensitivity": 1.0,
        "bass_gain": 1.0,
        "mid_gain": 1.0,
        "treble_gain": 1.0,
        "envelope_attack": 0.5,
        "envelope_release": 0.15,
        "peak_threshold": 1.8,
        "hybrid_min_brightness": 0.25,  # (устарело, оставлено для совместимости старых config.json)
        "tint_strength": 0.35,   # сила оттенка от баса/высоких в гибрид-режиме (0=не видно, 1=сильно)
        "flash_duration": 0.15,         # сек, время затухания вспышки на пик
        "flash_intensity": 1.0
    }
}


def _fallback_config_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    directory = os.path.join(base, "LedAmbilight")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        directory = APP_DIR
    return os.path.join(directory, "config.json")


def _resolve_config_path():
    """Конфиг рядом с приложением, если он там есть или туда можно писать."""
    candidate = os.path.join(APP_DIR, "config.json")
    if os.path.exists(candidate):
        return candidate
    try:
        probe = os.path.join(APP_DIR, ".write_probe")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        return candidate
    except OSError:
        path = _fallback_config_path()
        log.warning("Папка приложения недоступна для записи, конфиг будет в %s", path)
        return path


CONFIG_PATH = _resolve_config_path()


def load_config():
    """Загружает конфиг из файла, дополняя отсутствующие ключи дефолтами."""
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return copy.deepcopy(DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError("корень конфига должен быть объектом")
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        # Битый конфиг не выбрасываем молча - сохраняем рядом для разбора
        broken = CONFIG_PATH + ".broken"
        try:
            shutil.copy2(CONFIG_PATH, broken)
        except OSError:
            broken = "(не удалось сохранить копию)"
        log.error("Конфиг повреждён (%s), беру значения по умолчанию. Копия: %s", exc, broken)
        defaults = copy.deepcopy(DEFAULT_CONFIG)
        save_config(defaults)
        return defaults

    return _sanitize(_deep_merge(copy.deepcopy(DEFAULT_CONFIG), loaded))


def save_config(config):
    """Атомарно сохраняет конфиг: tmp -> replace, со страховочной копией .bak."""
    directory = os.path.dirname(CONFIG_PATH) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass

    tmp_path = CONFIG_PATH + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(CONFIG_PATH):
            try:
                shutil.copy2(CONFIG_PATH, CONFIG_PATH + ".bak")
            except OSError:
                pass  # не критично
        os.replace(tmp_path, CONFIG_PATH)
    except OSError:
        log.exception("Не удалось сохранить конфиг в %s", CONFIG_PATH)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def _deep_merge(base, override):
    """Рекурсивно дополняет base значениями из override, сохраняя недостающие дефолты."""
    for key, value in override.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, dict)
        ):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# ----------------------------------------------------------------------
# Приведение конфига к рабочему виду.
#
# config.json можно править руками (и он может оказаться повреждён
# наполовину). Раньше, например, "brightness": null или строка вместо
# числа роняли приложение при старте: GUI делал setValue("abc") и падал
# с TypeError ещё до появления окна.
# ----------------------------------------------------------------------

_DICT_SECTIONS = (
    "standby", "calibration", "brightness", "hud_mask",
    "filters", "audio", "process_profile_map",
    "adaptive_fps", "power", "auto_pause", "hotkeys", "content", "remote",
    "lamp", "schedule", "sunrise", "app_scenes", "mqtt", "updates",
)

_INT_RANGES = {
    "wled_port": (1, 65535, 21324),
    "wled_led_count": (1, 1000, 90),
    "wled_serial_baud": (9600, 2000000, 115200),
    "screen_width": (320, 16384, 1920),
    "screen_height": (240, 16384, 1080),
    "target_fps": (10, 60, 60),
    "analysis_area_percent": (30, 100, 60),
    # плотность держим целой: она сравнивается с пресетами в выпадающем списке
    "strip_density": (1, 300, 30),
    "sample_width": (4, 64, 10),
}

_FLOAT_RANGES = {
    "strip_meters": (0.1, 10.0, 5.0),
    "monitor_diagonal_inches": (10.0, 100.0, 24.0),
}

# (секция, ключ) -> (минимум, максимум, значение по умолчанию)
_NESTED_RANGES = {
    ("calibration", "r_gain"): (0.5, 1.5, 1.0),
    ("calibration", "g_gain"): (0.5, 1.5, 1.0),
    ("calibration", "b_gain"): (0.5, 1.5, 1.0),
    ("calibration", "r_offset"): (-50, 50, 0),
    ("calibration", "g_offset"): (-50, 50, 0),
    ("calibration", "b_offset"): (-50, 50, 0),
    ("brightness", "min_brightness"): (0, 255, 0),
    ("brightness", "target_brightness"): (20, 255, 180),
    ("brightness", "max_scale"): (0.1, 1.5, 1.0),
    ("hud_mask", "top"): (0, 40, 0),
    ("hud_mask", "bottom"): (0, 40, 0),
    ("hud_mask", "left"): (0, 40, 0),
    ("hud_mask", "right"): (0, 40, 0),
    ("filters", "change_threshold"): (0, 255, 4),
    ("filters", "flicker_window"): (1, 8, 3),
    ("filters", "smoothing_min"): (0.01, 1.0, 0.12),
    ("filters", "smoothing_max"): (0.01, 1.0, 0.55),
    ("filters", "adaptive_sensitivity"): (1, 765, 90),
    ("filters", "dominant_weight"): (0.0, 1.0, 0.65),
    ("filters", "neighbor_blend"): (0.0, 0.49, 0.35),
    ("filters", "gamma"): (0.5, 2.0, 1.0),
    ("standby", "speed"): (0.05, 1.0, 0.35),
    ("adaptive_fps", "min_fps"): (1, 60, 10),
    ("power", "limit_ma"): (0, 50000, 0),
    ("power", "led_ma"): (1, 200, 55),
    ("auto_pause", "idle_minutes"): (1, 120, 5),
    ("content", "fade_level"): (0.0, 1.0, 0.15),
    ("remote", "port"): (1024, 65535, 8765),
    ("lamp", "temperature"): (0.0, 1.0, 0.35),
    ("lamp", "brightness"): (0.05, 1.0, 0.7),
    ("sunrise", "duration_minutes"): (1, 120, 20),
    ("mqtt", "port"): (1, 65535, 1883),
}


def _to_number(value, default, as_int):
    """Аккуратно приводит значение к числу нужного типа."""
    if isinstance(value, bool):
        return default
    try:
        return int(float(value)) if as_int else float(value)
    except (TypeError, ValueError):
        return default


def _sanitize(config):
    """Проверяет типы и диапазоны всех настроек, чиня их при необходимости."""
    for section in _DICT_SECTIONS:
        if not isinstance(config.get(section), dict):
            log.warning("Секция %r в конфиге повреждена, беру значения по умолчанию", section)
            config[section] = copy.deepcopy(DEFAULT_CONFIG[section])

    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        config["profiles"] = copy.deepcopy(DEFAULT_CONFIG["profiles"])
    else:
        for name, default_profile in DEFAULT_CONFIG["profiles"].items():
            if not isinstance(profiles.get(name), dict):
                profiles[name] = copy.deepcopy(default_profile)

    if not isinstance(config.get("game_processes"), list):
        config["game_processes"] = list(DEFAULT_CONFIG["game_processes"])
    else:
        config["game_processes"] = [
            str(item).strip() for item in config["game_processes"] if str(item).strip()
        ]

    for key, (low, high, default) in _INT_RANGES.items():
        value = _to_number(config.get(key), default, as_int=True)
        config[key] = max(low, min(high, value))

    for key, (low, high, default) in _FLOAT_RANGES.items():
        value = _to_number(config.get(key), default, as_int=False)
        config[key] = max(low, min(high, value))

    for (section, key), (low, high, default) in _NESTED_RANGES.items():
        value = _to_number(config[section].get(key), default, as_int=False)
        value = max(low, min(high, value))
        config[section][key] = int(value) if isinstance(default, int) else value

    for key, allowed, fallback in (
        ("strip_mode", ("ble", "wled", "wled_serial"), "ble"),
        ("unknown_process_profile", ("desktop", "movie", "game"), "desktop"),
        ("led_sampling_mode", ("average", "dominant"), "average"),
        ("capture_backend", ("dxgi", "wgc"), "dxgi"),
    ):
        if config.get(key) not in allowed:
            log.warning("Недопустимое значение %r=%r, ставлю %r", key, config.get(key), fallback)
            config[key] = fallback

    return config
