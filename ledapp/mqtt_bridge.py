"""
Мост в MQTT / Home Assistant (опционально).

Если paho-mqtt установлен и в настройках включён MQTT, приложение публикует
своё состояние и принимает команды:

    <prefix>/state   — JSON: подключено, идёт ли подсветка, яркость, сцена
    <prefix>/set     — команда: {"capture": true} | {"brightness": 80}
                       | {"scene": "Кино"} | {"blackout": true}

Команды выполняются в потоке GUI через submit(), чтобы не трогать виджеты
из сетевого потока.
"""

import json
import logging

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except Exception:  # пакет необязательный
    mqtt = None
    HAS_MQTT = False

log = logging.getLogger(__name__)


def is_available() -> bool:
    return HAS_MQTT


class MqttBridge:
    def __init__(self, host, port, prefix, submit, state_snapshot, on_command):
        self.host = host
        self.port = int(port)
        self.prefix = (prefix or "ledambilight").strip("/")
        self._submit = submit
        self._state_snapshot = state_snapshot
        self._on_command = on_command
        self._client = None
        self._last_state = None
        self._connected = False

    # ------------------------------------------------------------------

    @property
    def is_running(self):
        return self._client is not None

    def start(self):
        if not HAS_MQTT:
            log.warning("paho-mqtt не установлен — MQTT выключен")
            return False
        if self._client is not None:
            return True
        try:
            client = mqtt.Client()
            client.on_connect = self._on_connect
            client.on_message = self._on_message
            client.connect_async(self.host, self.port, keepalive=30)
            client.loop_start()
            self._client = client
            log.info("MQTT: %s:%s, топик %s", self.host, self.port, self.prefix)
            return True
        except Exception:
            log.exception("Не удалось подключиться к MQTT %s:%s", self.host, self.port)
            self._client = None
            return False

    def stop(self):
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            log.debug("Ошибка остановки MQTT", exc_info=True)

    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        self._connected = rc == 0
        if self._connected:
            try:
                client.subscribe(f"{self.prefix}/set")
            except Exception:
                log.debug("Не удалось подписаться на топик", exc_info=True)
            log.info("MQTT подключён")
        else:
            log.warning("MQTT вернул код %s", rc)

    def _on_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8") or "{}")
        except ValueError:
            log.debug("MQTT: некорректный JSON в команде")
            return
        if not isinstance(payload, dict):
            return
        try:
            self._submit(lambda: self._on_command(payload))
        except Exception:
            log.debug("MQTT: не удалось передать команду", exc_info=True)

    # ------------------------------------------------------------------

    def publish_state(self):
        """Публикует состояние, если оно изменилось."""
        client = self._client
        if client is None or not self._connected:
            return
        try:
            state = self._state_snapshot()
        except Exception:
            return
        if state == self._last_state:
            return
        self._last_state = state
        try:
            client.publish(f"{self.prefix}/state",
                           json.dumps(state, ensure_ascii=False), retain=True)
        except Exception:
            log.debug("MQTT: не удалось опубликовать состояние", exc_info=True)
