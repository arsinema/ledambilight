"""
Управление WLED (ESP32/ESP8266 с прошивкой WLED).

Два канала:
  * UDP realtime (протокол DRGB) — покадровая передача цвета каждого диода;
  * HTTP JSON API — проверка того, что плата реально в сети, включение/выключение,
    выход из realtime-режима и получение информации об устройстве.

Важно: сам по себе UDP-сокет создать можно всегда, даже если плата выключена —
UDP не умеет сообщать о недоступности адреса. Поэтому "подключён" здесь значит
"плата ответила на HTTP-запрос /json/info", а не просто "открылся сокет".
"""

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Optional, Sequence, Tuple

log = logging.getLogger(__name__)

PROTOCOL_DRGB = 2
MAX_DRGB_LEDS = 490          # ограничение протокола DRGB
REALTIME_TIMEOUT = 8         # сек, сколько WLED держит последний кадр без новых пакетов
DEFAULT_UDP_PORT = 21324


def _http_json(host: str, path: str, payload=None, timeout: float = 3.0):
    """GET/POST JSON к WLED. Возвращает распарсенный ответ или None."""
    url = f"http://{host}{path}"
    try:
        if payload is None:
            request = urllib.request.Request(url, method="GET")
        else:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        return json.loads(body) if body.strip() else {}
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        log.debug("WLED HTTP %s %s: %s", host, path, exc)
        return None


class WLEDController:
    """Контроллер WLED: UDP realtime + HTTP-управление."""

    def __init__(self, host: str = "192.168.1.100", port: int = DEFAULT_UDP_PORT,
                 led_count: int = 90):
        self.host = host
        self.port = int(port)
        self.socket: Optional[socket.socket] = None
        self.led_count = int(led_count)
        self.last_send_time = 0.0
        self.min_send_interval = 0.02  # ~50 FPS максимум, чтобы не спамить сеть
        self.consecutive_failures = 0
        self.last_error = ""
        self.device_info: Optional[dict] = None

    # ------------------------------------------------------------------
    # Соединение
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self.socket is not None

    @staticmethod
    def fetch_info(host: str, timeout: float = 3.0) -> Optional[dict]:
        """Запрашивает /json/info. None = плата недоступна.

        Это единственный надёжный способ узнать, что WLED вообще есть в сети:
        UDP-сокет создаётся мгновенно и для выключенной платы."""
        info = _http_json(host, "/json/info", timeout=timeout)
        if isinstance(info, dict) and "leds" in info:
            return info
        return None

    @classmethod
    def probe(cls, host: str, timeout: float = 3.0) -> Optional[dict]:
        """Проверка доступности + краткая сводка об устройстве."""
        return cls.fetch_info(host, timeout=timeout)

    def connect(self) -> bool:
        """Готовит UDP-сокет. Доступность платы проверяется отдельно
        (probe/fetch_info) — здесь намеренно нет блокирующего HTTP-запроса,
        потому что метод вызывается из фонового asyncio-потока."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.settimeout(1.0)
            self.consecutive_failures = 0
            self.last_error = ""
            return True
        except OSError as exc:
            self.last_error = str(exc)
            log.error("Не удалось создать UDP-сокет: %s", exc)
            return False

    def disconnect(self):
        """Закрывает UDP-сокет."""
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

    # ------------------------------------------------------------------
    # HTTP-управление
    # ------------------------------------------------------------------

    def set_power(self, on: bool, timeout: float = 2.0) -> bool:
        """Включает/выключает выход на ленту через HTTP API."""
        result = _http_json(self.host, "/json/state", {"on": bool(on)}, timeout=timeout)
        return result is not None

    def release_live(self, timeout: float = 2.0) -> bool:
        """Завершает realtime-режим, чтобы WLED вернулся к своему эффекту."""
        result = _http_json(self.host, "/json/state", {"live": False}, timeout=timeout)
        return result is not None

    # ------------------------------------------------------------------
    # Отправка кадров
    # ------------------------------------------------------------------

    def send_frame(self, pixels: Sequence[Tuple[int, int, int]]) -> bool:
        """
        Отправляет кадр с цветами для всех светодиодов (протокол DRGB).

        Формат (https://kno.wled.ge/interfaces/udp-realtime/):
            Байт 0: 2 (код протокола DRGB - именно ЧИСЛО 2, не буква)
            Байт 1: таймаут в секундах (сколько держать кадр без новых пакетов)
            Далее: R,G,B для каждого диода подряд (до 490 диодов)

        Args:
            pixels: список кортежей (R, G, B) для каждого диода

        Returns:
            True если пакет отправлен
        """
        if self.socket is None:
            return False

        now = time.time()
        if now - self.last_send_time < self.min_send_interval:
            return False

        try:
            frame = bytearray([PROTOCOL_DRGB, REALTIME_TIMEOUT])
            count = min(len(pixels), self.led_count, MAX_DRGB_LEDS)
            for i in range(count):
                r, g, b = pixels[i]
                frame.append(int(r) & 0xFF)
                frame.append(int(g) & 0xFF)
                frame.append(int(b) & 0xFF)

            self.socket.sendto(bytes(frame), (self.host, self.port))
            self.last_send_time = now
            self.consecutive_failures = 0
            return True

        except OSError as exc:
            self.consecutive_failures += 1
            self.last_error = str(exc)
            # В лог - только первая ошибка серии, чтобы не залить файл
            if self.consecutive_failures == 1:
                log.warning("Ошибка отправки на WLED %s:%s: %s", self.host, self.port, exc)
            return False

    def send_single_color(self, r: int, g: int, b: int) -> bool:
        """Один цвет на все диоды (fallback, когда нет поэтевиксельного маппинга)."""
        return self.send_frame([(r, g, b)] * max(1, self.led_count))

    def send_black(self) -> bool:
        """Гасит ленту кадром чёрного (быстро, без HTTP)."""
        return self.send_frame([(0, 0, 0)] * max(1, self.led_count))

    def set_led_count(self, count: int):
        self.led_count = max(1, int(count))

    # ------------------------------------------------------------------
    # Прочее
    # ------------------------------------------------------------------

    @staticmethod
    def discover() -> Optional[str]:
        """Пытается найти WLED по mDNS-имени. Возвращает IP или None."""
        for name in ("wled.local", "wled-931208.local"):
            try:
                return socket.gethostbyname(name)
            except OSError:
                continue
        return None
