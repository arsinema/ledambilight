"""
Управление BLE-лентой ELK-BLEDOM: поиск, подключение, отправка цвета.
"""

import logging

from bleak import BleakScanner, BleakClient

log = logging.getLogger(__name__)

# Сколько ждём подключения, прежде чем сдаться. Без явного таймаута bleak
# может висеть десятки секунд, а всё это время фоновый поток не отправляет
# кадры на ленту (и интерфейс выглядит «зависшим»).
CONNECT_TIMEOUT = 12.0


def color_command(r, g, b):
    r = max(0, min(255, int(round(r))))
    g = max(0, min(255, int(round(g))))
    b = max(0, min(255, int(round(b))))
    return bytes([0x7E, 0x07, 0x05, 0x03, r, g, b, 0x0A, 0xEF])


def power_command(on: bool):
    # 0x04 = вкл, 0x00 = выкл (стандартная команда для ELK-BLEDOM)
    state = 0x04 if on else 0x00
    return bytes([0x7E, 0x04, 0x04, state, 0x00, 0x00, 0x00, 0x00, 0xEF])


class BLEController:
    def __init__(self, write_uuid):
        self.write_uuid = write_uuid
        self.client: BleakClient | None = None
        self.address: str | None = None
        self.name: str | None = None

    @property
    def is_connected(self):
        return self.client is not None and self.client.is_connected

    async def scan(self, timeout=5):
        """Возвращает список найденных устройств [(name, address), ...]."""
        devices = await BleakScanner.discover(timeout=timeout)
        result = []
        for d in devices:
            name = d.name or "Unknown"
            result.append((name, d.address))
        return result

    async def scan_for_elk(self, timeout=5):
        """Ищет конкретно ELK-устройство и возвращает (name, address) или None."""
        devices = await BleakScanner.discover(timeout=timeout)
        for d in devices:
            if "ELK" in (d.name or "").upper():
                return (d.name, d.address)
        return None

    async def connect(self, address):
        if self.client is not None:
            await self.disconnect()

        client = BleakClient(address)
        self.client = client
        try:
            await client.connect(timeout=CONNECT_TIMEOUT)
        except Exception:
            log.exception("Не удалось подключиться к %s", address)
            if self.client is client:
                self.client = None
            raise

        # Пока шло подключение, пользователь мог нажать «Отключиться» или
        # сменить режим - тогда клиент уже сброшен, и обращаться к нему нельзя
        if self.client is not client:
            try:
                await client.disconnect()
            except Exception:
                pass
            return False

        self.address = address
        return client.is_connected

    async def disconnect(self):
        client = self.client
        self.client = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                log.debug("Ошибка отключения BLE", exc_info=True)

    async def send_color(self, r, g, b):
        if not self.is_connected:
            return False
        try:
            await self.client.write_gatt_char(
                self.write_uuid,
                color_command(r, g, b),
                response=False
            )
            return True
        except Exception:
            log.debug("Ошибка отправки цвета по BLE", exc_info=True)
            return False

    async def send_power(self, on: bool):
        if not self.is_connected:
            return False
        try:
            await self.client.write_gatt_char(
                self.write_uuid,
                power_command(on),
                response=False
            )
            return True
        except Exception:
            log.debug("Ошибка отправки команды питания по BLE", exc_info=True)
            return False
