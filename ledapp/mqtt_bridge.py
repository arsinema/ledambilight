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
import re

from . import __version__

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except Exception:  # пакет необязательный
    mqtt = None
    HAS_MQTT = False

log = logging.getLogger(__name__)


def discovery_messages(prefix, discovery_prefix="homeassistant"):
    """Топики и payload'ы MQTT Discovery для Home Assistant."""
    prefix = (prefix or "ledambilight").strip("/")
    node_id = re.sub(r"[^a-zA-Z0-9_-]", "_", prefix) or "ledambilight"
    unique_id = f"{node_id}_light"
    topic = f"{discovery_prefix}/light/{unique_id}/config"
    payload = {
        "name": "LED Ambilight",
        "unique_id": unique_id,
        "schema": "json",
        "command_topic": f"{prefix}/set",
        "state_topic": f"{prefix}/ha_state",
        "brightness": True,
        "brightness_scale": 100,
        "supported_color_modes": ["brightness"],
        "availability_topic": f"{prefix}/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
        "icon": "mdi:led-strip-variant",
        "device": {
            "identifiers": [node_id],
            "name": "LED Ambilight",
            "manufacturer": "arsinema",
            "model": "LED Ambilight",
            "sw_version": __version__,
        },
    }
    return {topic: payload}


def ha_state_payload(state):
    """HA-совместимое состояние из внутреннего snapshot."""
    state = state or {}
    try:
        brightness = max(0, min(100, int(state.get("brightness", 0) or 0)))
    except (TypeError, ValueError):
        brightness = 0
    return {
        "state": "ON" if state.get("capturing") else "OFF",
        "brightness": brightness,
        "scene": state.get("scene", "") or "",
        "connected": bool(state.get("connected")),
    }


def is_available() -> bool:
    return HAS_MQTT


class MqttBridge:
    def __init__(self, host, port, prefix, submit, state_snapshot, on_command,
                 discovery=True):
        self.host = host
        self.port = int(port)
        self.prefix = (prefix or "ledambilight").strip("/")
        self._submit = submit
        self._state_snapshot = state_snapshot
        self._on_command = on_command
        self.discovery = bool(discovery)
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
            client.publish(f"{self.prefix}/availability", "offline", retain=True)
            client.loop_stop()
            client.disconnect()
        except Exception:
            log.debug("Ошибка остановки MQTT", exc_info=True)

    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        self._connected = rc == 0
        if self._connected:
            self._last_state = None
            try:
                client.subscribe(f"{self.prefix}/set")
            except Exception:
                log.debug("Не удалось подписаться на топик", exc_info=True)
            try:
                client.publish(
                    f"{self.prefix}/availability", "online", retain=True
                )
                if self.discovery:
                    self._publish_discovery(client)
            except Exception:
                log.debug("Не удалось опубликовать MQTT Discovery", exc_info=True)
            log.info("MQTT подключён")
        else:
            log.warning("MQTT вернул код %s", rc)

    def _publish_discovery(self, client):
        """Публикует конфиг light-сущности для Home Assistant."""
        messages = discovery_messages(self.prefix)
        for topic, payload in messages.items():
            client.publish(
                topic, json.dumps(payload, ensure_ascii=False), retain=True
            )
        log.info("MQTT Discovery: %s", ", ".join(messages))

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
            client.publish(
                f"{self.prefix}/ha_state",
                json.dumps(ha_state_payload(state), ensure_ascii=False),
                retain=True,
            )
        except Exception:
            log.debug("MQTT: не удалось опубликовать состояние", exc_info=True)
