"""
Получение "настроенческого" цвета по текущей погоде через wttr.in -
бесплатный сервис без регистрации и API-ключей.
"""

import logging
import threading
import time
from typing import Optional, Tuple
from urllib.parse import quote

log = logging.getLogger(__name__)

try:
    import requests
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False


# Код погодного явления (WWO/wttr.in) -> приятный цвет-настроение.
# Не претендуем на метеорологическую точность - это декоративный эффект.
_WEATHER_COLOR_MAP = {
    "sunny": (255, 170, 40),
    "clear": (255, 190, 90),
    "partly_cloudy": (200, 190, 160),
    "cloudy": (150, 150, 155),
    "overcast": (120, 120, 130),
    "mist": (170, 175, 180),
    "fog": (160, 165, 170),
    "rain": (70, 110, 160),
    "light_rain": (90, 130, 175),
    "heavy_rain": (50, 85, 130),
    "thunder": (90, 70, 140),
    "snow": (210, 225, 235),
    "sleet": (160, 180, 195),
}


def _classify_condition(desc: str) -> str:
    d = desc.lower()
    if "thunder" in d:
        return "thunder"
    if "snow" in d or "sleet" in d or "ice" in d:
        return "snow" if "snow" in d else "sleet"
    if "heavy rain" in d or "torrential" in d:
        return "heavy_rain"
    if "light rain" in d or "drizzle" in d:
        return "light_rain"
    if "rain" in d or "shower" in d:
        return "rain"
    if "fog" in d:
        return "fog"
    if "mist" in d or "haze" in d:
        return "mist"
    if "overcast" in d:
        return "overcast"
    if "partly cloudy" in d:
        return "partly_cloudy"
    if "cloud" in d:
        return "cloudy"
    if "sunny" in d or "clear" in d:
        return "sunny"
    return "cloudy"


def fetch_weather_color(city: str) -> Optional[Tuple[int, int, int]]:
    """Один запрос погоды для города, возвращает (r,g,b) или None при ошибке."""
    if not HAS_REQUESTS or not city:
        return None
    try:
        # Название города может содержать пробелы и кириллицу - кодируем
        url = f"https://wttr.in/{quote(city)}"
        resp = requests.get(url, params={"format": "j1"}, timeout=6,
                             headers={"User-Agent": "curl/8.0"})
        if resp.status_code != 200:
            return None
        data = resp.json()
        desc = data["current_condition"][0]["weatherDesc"][0]["value"]
        category = _classify_condition(desc)
        return _WEATHER_COLOR_MAP.get(category, (150, 150, 155))
    except Exception:
        log.debug("Не удалось получить погоду для %s", city, exc_info=True)
        return None


class WeatherMonitor:
    """Фоновый поток: раз в N минут обновляет цвет по текущей погоде для
    заданного города. Не бьёт по сети чаще, чем нужно - погода не меняется
    ежесекундно."""

    def __init__(self, city: str = "", refresh_minutes: float = 15.0):
        self.city = city
        self.refresh_minutes = refresh_minutes
        self._lock = threading.Lock()
        self._color: Optional[Tuple[int, int, int]] = None
        self._thread = None
        self._running = False
        self._generation = 0  # защита от «воскрешения» старого потока

    def set_city(self, city: str):
        self.city = city

    def start(self):
        if self._running:
            return
        self._generation += 1
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(self._generation,), daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._generation += 1
        # Ждём недолго: поток спит секундами, поэтому даём ему увидеть флаг
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self._running

    def get_color(self) -> Optional[Tuple[int, int, int]]:
        with self._lock:
            return self._color

    def _loop(self, generation):
        while self._running and generation == self._generation:
            if self.city:
                color = fetch_weather_color(self.city)
                if generation != self._generation:
                    return
                with self._lock:
                    self._color = color
            for _ in range(int(self.refresh_minutes * 60)):
                if not self._running or generation != self._generation:
                    return
                time.sleep(1)
