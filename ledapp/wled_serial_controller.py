"""
Управление WLED через USB-кабель по протоколу Adalight.
Официально поддерживается WLED (с версии 0.6.3) - используется как "PC ambilight via serial".
Формат пакета (см. https://kno.wled.ge/interfaces/serial/):
    'A' 'd' 'a' [hi] [lo] [checksum] [R G B]*count
    hi/lo = (count-1) как uint16 big-endian
    checksum = hi XOR lo XOR 0x55
"""

import logging
import time
from typing import List, Tuple

log = logging.getLogger(__name__)


class WLEDSerialController:
    """
    Контроллер для WLED-устройств через USB (Adalight протокол).
    Более надёжен, чем WiFi, если сеть нестабильна.
    """

    def __init__(self, port: str = "COM5", baudrate: int = 115200):
        """
        Args:
            port: COM-порт (Windows) или /dev/ttyUSB0 (Linux)
            baudrate: скорость (по умолчанию 115200 - хватает на 50-100 диодов;
                      для большего числа диодов надо поднять в WLED Sync settings
                      и указать то же значение тут)
        """
        self.port = port
        self.baudrate = baudrate
        self.serial_conn = None
        self.led_count = 90
        self.last_send_time = 0
        self.min_send_interval = 0.02  # ~50 FPS максимум
        self.consecutive_failures = 0
        self.last_error = ""

    @property
    def is_connected(self) -> bool:
        return bool(self.serial_conn and getattr(self.serial_conn, "is_open", False))

    def connect(self) -> bool:
        """Открывает последовательный порт."""
        try:
            import serial
            self.serial_conn = serial.Serial(self.port, self.baudrate, timeout=1)
            time.sleep(2)  # ESP нужно время на инициализацию после открытия порта
            self.consecutive_failures = 0
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            log.error("Ошибка при открытии %s: %s", self.port, e)
            return False

    def disconnect(self):
        """Закрывает последовательный порт."""
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except Exception:
                pass
            self.serial_conn = None

    def set_led_count(self, count: int):
        """Устанавливает количество светодиодов ленты."""
        self.led_count = count

    def send_frame(self, pixels: List[Tuple[int, int, int]]) -> bool:
        """
        Отправляет кадр Adalight-пакетом.

        Args:
            pixels: список кортежей (R, G, B) для каждого диода

        Returns:
            True если успешно отправлено
        """
        if not self.serial_conn:
            return False

        now = time.time()
        if now - self.last_send_time < self.min_send_interval:
            return False

        try:
            count = min(len(pixels), self.led_count)
            if count == 0:
                return False

            n = count - 1  # Adalight кодирует (count - 1)
            hi = (n >> 8) & 0xFF
            lo = n & 0xFF
            checksum = hi ^ lo ^ 0x55

            packet = bytearray()
            packet.extend(b'Ada')
            packet.append(hi)
            packet.append(lo)
            packet.append(checksum)

            for i in range(count):
                r, g, b = pixels[i]
                packet.append(int(r) & 0xFF)
                packet.append(int(g) & 0xFF)
                packet.append(int(b) & 0xFF)

            self.serial_conn.write(bytes(packet))
            self.last_send_time = now
            self.consecutive_failures = 0
            return True

        except Exception as e:
            self.consecutive_failures += 1
            self.last_error = str(e)
            if self.consecutive_failures == 1:
                log.warning("Ошибка отправки по USB (%s): %s", self.port, e)
            return False

    def send_single_color(self, r: int, g: int, b: int) -> bool:
        """Отправляет один цвет на все светодиоды."""
        pixels = [(r, g, b)] * self.led_count
        return self.send_frame(pixels)

    @staticmethod
    def list_available_ports() -> list:
        """Возвращает список доступных COM-портов."""
        try:
            import serial.tools.list_ports
            ports = serial.tools.list_ports.comports()
            return [p.device for p in ports]
        except Exception:
            return []
