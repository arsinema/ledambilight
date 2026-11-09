"""
Фоновый поток с собственным asyncio event loop.
Управляется через потокобезопасную очередь команд из GUI-потока,
результаты отдаёт наружу через Qt-сигналы.

Ключевые инварианты, которые здесь соблюдаются:
  * любой сбой в цикле не убивает поток молча - он логируется, сообщается
    в GUI и цикл перезапускается (до MAX_CONSECUTIVE_FAILURES раз подряд);
  * при остановке захвата/выходе лента не остаётся с «замёрзшим» кадром -
    отправляется чёрный кадр и снимается realtime-режим WLED;
  * поэтевиксельная отправка (WLED) проходит тот же цветовой пайплайн
    (HSV-коррекция, яркость, калибровка), что и одиночный цвет для BLE.
"""

import asyncio
import collections
import copy
import logging
import math
import queue
import time

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from .ble_controller import BLEController
from .capture import (
    ScreenCapture,
    detect_profile,
    get_active_process_name,
    hud_rect,
    get_center_area,
    get_quadrant_color,
    get_quadrant_dominant_color,
    get_zone_color,
    get_zone_dominant_color,
    downsize_for_preview,
)
from .color_engine import (
    ColorEngine,
    apply_calibration,
    apply_neighbor_blend,
    estimate_current_ma,
    limit_power,
    process_pixel_frame,
)
from .frame_analysis import FrameAnalyzer
from .audio_engine import AudioCapture
from .wled_controller import WLEDController, MAX_DRGB_LEDS
from .wled_serial_controller import WLEDSerialController
from .strip_mapper import StripMapper
from .now_playing import NowPlayingMonitor
from .weather import WeatherMonitor

log = logging.getLogger(__name__)

PREVIEW_INTERVAL = 0.15      # секунд между кадрами превью (throttle, ~6-7 fps)
AUDIO_LEVEL_INTERVAL = 0.08  # throttle для VU-метра в GUI
VISUALIZER_TICK = 0.03       # пауза между отправками в чистом audio-visualizer режиме

# Зонный цвет считаем на уменьшенной копии кадра: mean по полному разрешению
# четырёх квадрантов стоил ~27 мс на кадр (потолок ~35 FPS), на копии 1/4 -
# около 2 мс. Для одного усреднённого цвета такой точности с запасом хватает.
ANALYSIS_STRIDE = 4

HEALTH_CHECK_INTERVAL = 15.0   # как часто проверять, жива ли плата (HTTP)
TEST_HOLD_SECONDS = 3.0        # сколько держим тестовый цвет, не перекрывая его кадром
MAX_CONSECUTIVE_FAILURES = 5   # столько сбоев подряд - и цикл останавливается

# Цвета оттенка для гибридного аудио-режима: бас подмешивает тёплый (оранжевый),
# высокие частоты - холодный (голубой).
WARM_TINT = (255, 120, 40)
COOL_TINT = (60, 160, 255)


def _clamp255(x):
    return max(0.0, min(255.0, x))


class LedWorker(QThread):
    status_changed = pyqtSignal(str)
    connected_changed = pyqtSignal(bool)
    capture_state_changed = pyqtSignal(bool)  # реально ли идёт захват (а не «кнопку нажали»)
    color_updated = pyqtSignal(int, int, int)
    profile_changed = pyqtSignal(str)
    devices_found = pyqtSignal(list)
    error_occurred = pyqtSignal(str)
    frame_preview = pyqtSignal(object)          # маленький numpy-массив RGB (H, W, 3)
    audio_level_update = pyqtSignal(float, float, float, float)  # volume, bass, mid, treble
    mapping_loaded = pyqtSignal(object)         # dict для перерисовки холста после загрузки
    strip_pixels_updated = pyqtSignal(list)     # текущий массив (R,G,B) на ленте
    now_playing_changed = pyqtSignal(str)       # название трека для дежурного режима
    stats_update = pyqtSignal(float, float, int, float)  # fps, задержка мс, пакетов, ток мА
    mapping_boxes = pyqtSignal(object)          # области выборки диодов для превью
    diagnostics_ready = pyqtSignal(str)         # текстовый отчёт диагностики
    process_changed = pyqtSignal(str)           # имя активного процесса (для сцен приложений)
    beat_update = pyqtSignal(float)             # текущий темп, BPM

    def __init__(self, config):
        super().__init__()
        self.config = copy.deepcopy(config)
        self._queue = queue.Queue()
        self._running = True
        self.capture_enabled = False
        self._led_start_offset = max(0, int(config.get("led_start_offset", 0)))

        self.ble = BLEController(config["write_uuid"])
        self.wled = WLEDController(
            config.get("wled_host", "192.168.1.100"),
            config.get("wled_port", 21324),
            config.get("wled_led_count", 90),
        )
        self.wled_serial = WLEDSerialController(
            config.get("wled_serial_port", "COM5"),
            config.get("wled_serial_baud", 115200),
        )
        self.wled_serial.set_led_count(config.get("wled_led_count", 90))
        self.strip_mapper = StripMapper(
            config.get("wled_led_count", 90),
            config.get("screen_width", 1920),
            config.get("screen_height", 1080),
        )
        self.color_engine = ColorEngine(self.config)
        self._capture = None
        self._last_profile = None
        self._last_process_name = None
        self._last_preview_time = 0.0
        self._strip_mode = config.get("strip_mode", "ble")  # "ble" | "wled" | "wled_serial"
        self._last_mapper_strip_width = int(config.get("sample_width", 10))
        self._last_mapper_exact_positions = False

        # -- Адаптивная частота кадров --
        self._static_frames = 0
        self._last_signature = None

        # -- Анализ содержимого кадра (полосы, HUD, статика) --
        self._analyzer = FrameAnalyzer()
        self._fade = 1.0
        self._last_ui_apply = 0.0
        self._wake_ramp = 0.0
        # Режим раскладки: Ambilight не трогает ленту, пока подсвечиваем диоды
        self._mapping_mode = False
        # Меняется ли картинка на экране (нужно автопаузе: фильм - не простой)
        self._screen_moving = False

        # -- Ambient standby ("дыхание" в простое) --
        self.now_playing = NowPlayingMonitor()
        self.weather = WeatherMonitor(config.get("standby", {}).get("weather_city", ""))
        self._standby_phase = 0.0
        self._last_now_playing_text = ""
        self._smoothed_pixels = None

        # -- Живое мини-превью ленты в GUI --
        self._last_strip_preview_emit = 0.0
        self._strip_preview_interval = 0.1

        # -- Статистика для строки состояния --
        self._sent_packets = 0
        self._frame_stamps = collections.deque(maxlen=90)
        self._last_stats_emit = 0.0
        self._last_latency_ms = 0.0
        self._current_ma = 0.0
        self._last_pixels = None
        self._diag_task = None

        # -- Автопереподключение --
        self._last_health_check = 0.0
        self._health_check_task = None
        self._should_be_connected = False
        self._wled_reachable = None
        self._wled_fail_streak = 0
        self._connect_task = None
        self._scan_task = None
        self._last_known_ble_address = config.get("device_address")
        self._last_known_wled_host = config.get("wled_host")
        self._last_known_wled_port = config.get("wled_port", 21324)
        self._last_known_serial_port = config.get("wled_serial_port")
        self._last_known_serial_baud = config.get("wled_serial_baud", 115200)
        self._warned_led_limit = False

        # -- Тестовый цвет / развёртка --
        self._hold_until = 0.0
        self._last_test_color = None
        self._last_test_calibrated = False
        self._sweep_task = None
        self._sweep_cancel = False
        self._sweep_active = False

        # -- Приостановка (таймер сна / ручное выключение) --
        # Пока True, дежурное «дыхание» не включается: иначе лента гасла бы
        # и тут же загоралась снова следующим тиком standby.
        self._suspended = False

        self.audio = AudioCapture()
        self._last_audio_level_emit = 0.0

        self._preview_active = False

    # -------- Публичные методы (вызываются из GUI-потока) --------

    def update_config(self, config):
        # Копия, а не ссылка: GUI продолжает менять свой dict, пока воркер
        # читает этот (иначе - гонка при итерации по game_processes и т.п.)
        self._queue.put(("update_config", copy.deepcopy(config)))

    def scan_devices(self):
        self._queue.put(("scan", None))

    def connect_device(self, address):
        self._queue.put(("connect", address))

    def disconnect_device(self):
        self._queue.put(("disconnect", None))

    def start_capture(self):
        self._queue.put(("start_capture", None))

    def stop_capture(self):
        self._queue.put(("stop_capture", None))

    def set_preview_active(self, active: bool):
        """Включает/выключает генерацию кадров превью — не тратим CPU/память
        на даунскейл и передачу картинки в GUI, если вкладка 'Просмотр' не открыта."""
        self._queue.put(("set_preview_active", bool(active)))

    def start_audio(self):
        self._queue.put(("start_audio", None))

    def stop_audio(self):
        self._queue.put(("stop_audio", None))

    def set_strip_mode(self, mode: str):
        """Переключает режим: 'ble' | 'wled' | 'wled_serial'"""
        self._queue.put(("set_strip_mode", mode))

    def connect_wled(self, host: str, port: int = 21324):
        """Подключается к WLED-устройству по Wi-Fi (с реальной проверкой связи)"""
        self._queue.put(("connect_wled", (host, port)))

    def connect_wled_serial(self, com_port: str, baud: int = 115200):
        """Подключается к WLED-устройству по USB (Adalight)"""
        self._queue.put(("connect_wled_serial", (com_port, baud)))

    def run_test_sweep(self, r: int, g: int, b: int, delay_ms: int = 150):
        """Пробегает выбранным цветом по одному светодиоду за раз."""
        self._queue.put(("test_sweep", (r, g, b, delay_ms)))

    def cancel_test_sweep(self):
        """Отменяет текущую тест-развёртку (не блокирует остальной цикл)."""
        self._queue.put(("cancel_sweep", None))

    def set_strip_mapper(self, led_path: list, screen_width: int, screen_height: int,
                         exact_positions: bool = False, led_count: int = None):
        """Устанавливает маппинг ленты на основе пути и размеров экрана."""
        self._queue.put(("set_strip_mapper",
                         (led_path, screen_width, screen_height, exact_positions, led_count)))

    def clear_strip_mapping(self):
        """Сбрасывает маппинг: после смены монитора старые координаты неверны."""
        self._queue.put(("clear_strip_mapping", None))

    def mapping_snapshot(self):
        """Копия текущего маппинга для отчёта поддержки (без изменения состояния)."""
        mapper = self.strip_mapper
        if mapper is None or not getattr(mapper, "led_path", None):
            return None
        return {
            "led_count": int(mapper.led_count),
            "screen_width": int(mapper.screen_width),
            "screen_height": int(mapper.screen_height),
            "strip_width": int(getattr(mapper, "last_strip_width", 10)),
            "exact_positions": bool(getattr(mapper, "last_exact_positions", False)),
            "led_path": [[float(x), float(y)] for x, y in mapper.led_path],
        }

    def save_strip_mapping(self, filepath: str):
        self._queue.put(("save_strip_mapping", filepath))

    def load_strip_mapping(self, filepath: str):
        self._queue.put(("load_strip_mapping", filepath))

    def send_test_color(self, r, g, b):
        """Отправляет цвет как есть, без калибровки (для сравнения)."""
        self._queue.put(("test_color", (r, g, b)))

    def send_test_calibrated(self, r, g, b):
        """Отправляет цвет с текущей калибровкой - удобно крутить слайдеры."""
        self._queue.put(("test_calibrated", (r, g, b)))

    def blackout(self):
        """Погасить ленту (таймер сна, ручное выключение)."""
        self._queue.put(("blackout", None))

    def run_diagnostics(self):
        """Собрать отчёт о состоянии (связь, плата, захват, маппинг)."""
        self._queue.put(("diagnose", None))

    def set_wake_ramp(self, level: float):
        """Уровень «рассвета» 0..1 (0 — выключено, 1 — полная яркость)."""
        self._queue.put(("wake_ramp", float(level)))

    def highlight_led(self, index, r=255, g=255, b=255):
        """Подсветить один диод (для раскладки по развёртке)."""
        self._queue.put(("highlight", (int(index), int(r), int(g), int(b))))

    def set_mapping_mode(self, active: bool):
        """Пока включено, Ambilight не перекрывает подсвеченный диод."""
        self._queue.put(("mapping_mode", bool(active)))

    def stop_worker(self):
        self._queue.put(("quit", None))

    # -------- Внутренний цикл --------

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main_loop())
        except Exception:
            log.exception("Фоновый поток завершился с ошибкой")
        finally:
            # Даём отменённым задачам (развёртка, подключение, проверка связи)
            # корректно завершиться, иначе asyncio ругается при закрытии цикла
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                log.debug("Не удалось корректно остановить задачи", exc_info=True)
            finally:
                loop.close()

    async def _main_loop(self):
        self._running = True
        failures = 0
        while self._running:
            try:
                await self._tick_once()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                log.exception("Сбой в основном цикле воркера (%s/%s)",
                              failures, MAX_CONSECUTIVE_FAILURES)
                self.error_occurred.emit(f"Сбой фонового потока: {exc}")
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    self.status_changed.emit("Фоновый поток остановлен из-за повторяющихся ошибок")
                    self.capture_state_changed.emit(False)
                    self._running = False
                    break
                # Даём шанс восстановиться: сбрасываем захват, он чаще всего и падает
                self.capture_enabled = False
                self._stop_capture_object()
                self.capture_state_changed.emit(False)
                await asyncio.sleep(1.0)
                continue

    async def _tick_once(self):
        await self._drain_commands()

        delays = []
        did_active_work = False

        if self.capture_enabled and self._capture is not None:
            delays.append(await self._screen_tick())
            did_active_work = True

        if self.audio.is_running:
            audio_delay = await self._audio_tick()
            if audio_delay is not None:
                delays.append(audio_delay)
            did_active_work = True

        if not did_active_work:
            if self._mapping_mode:
                delays.append(0.1)
            else:
                wake_delay = await self._wake_tick()
                if wake_delay is not None:
                    delays.append(wake_delay)
                elif self.config.get("lamp", {}).get("enabled", False):
                    lamp_delay = await self._lamp_tick()
                    if lamp_delay is not None:
                        delays.append(lamp_delay)
                else:
                    standby_delay = await self._standby_tick()
                    if standby_delay is not None:
                        delays.append(standby_delay)

        self._maybe_check_connection_health()

        # Один sleep за итерацию, на минимально нужное время
        await asyncio.sleep(min(delays) if delays else 0.05)

    async def _drain_commands(self):
        try:
            while True:
                cmd, payload = self._queue.get_nowait()
                await self._handle_command(cmd, payload)
        except queue.Empty:
            pass

    async def _handle_command(self, cmd, payload):
        if cmd == "update_config":
            self.config = payload
            self._led_start_offset = max(0, int(payload.get("led_start_offset", 0)))
            self.color_engine.update_config(payload)
            self.ble.write_uuid = payload.get("write_uuid", self.ble.write_uuid)
            self.audio.update_settings(payload.get("audio", {}))

            try:
                led_count = max(1, int(payload.get("wled_led_count", 90)))
            except (TypeError, ValueError):
                led_count = 90
            self.wled.set_led_count(led_count)
            self.wled_serial.set_led_count(led_count)
            self.wled.host = payload.get("wled_host", self.wled.host)
            try:
                self.wled.port = int(payload.get("wled_port", self.wled.port))
            except (TypeError, ValueError):
                pass

            self._warn_led_limit_if_needed()
            await self._refresh_held_test_color()
            self._apply_target_fps(payload)
            self._apply_sample_width(payload)

        elif cmd == "clear_strip_mapping":
            self._clear_mapping()

        elif cmd == "scan":
            # Сканирование BLE занимает до 5 секунд - выносим в задачу,
            # чтобы кадры на ленту продолжали уходить
            if self._scan_task is not None and not self._scan_task.done():
                self.status_changed.emit("Сканирование уже идёт...")
            else:
                self._scan_task = asyncio.create_task(self._scan_ble())

        elif cmd == "connect":
            # Подключение по BLE может занять до CONNECT_TIMEOUT секунд -
            # выносим в задачу, чтобы не замораживать отправку кадров
            if self._connect_task is not None and not self._connect_task.done():
                self.status_changed.emit("Подключение уже выполняется...")
            else:
                self._connect_task = asyncio.create_task(self._connect_ble(payload))

        elif cmd == "disconnect":
            await self._blackout(power_off=True)
            await self._disconnect_all()
            self.connected_changed.emit(False)
            self.status_changed.emit("Отключено")
            self._should_be_connected = False
            self._wled_reachable = None

        elif cmd == "start_capture":
            try:
                if self._capture is None:
                    self._capture = ScreenCapture(
                        target_fps=self.config.get("target_fps", 30),
                        backend=self.config.get("capture_backend", "dxgi"),
                        region=self.config.get("capture_region"),
                        monitor_index=self.config.get("monitor_index", 0),
                    )
                    self._capture.start()
                self.capture_enabled = True
                self._suspended = False
                self.color_engine.reset()
                self._smoothed_pixels = None
                self._last_profile = None
                self._fade = 1.0
                self._analyzer.reset()
                self._align_mapping_to_frame()
                await self._set_strip_power(True)
                self.capture_state_changed.emit(True)
                if self._is_strip_available():
                    self.status_changed.emit("Захват запущен")
                else:
                    self.status_changed.emit(
                        "Захват запущен, но лента не подключена - нажми «Подключить»"
                    )
                log.info("Захват запущен (режим %s)", self._strip_mode)
            except Exception as e:
                self.capture_enabled = False
                self._stop_capture_object()
                self.capture_state_changed.emit(False)
                self.error_occurred.emit(f"Ошибка захвата экрана: {e}")
                log.exception("Не удалось запустить захват")

        elif cmd == "stop_capture":
            self.capture_enabled = False
            self._stop_capture_object()
            self._cancel_sweep()
            await self._blackout(power_off=False)
            self.capture_state_changed.emit(False)
            self.status_changed.emit("Захват остановлен")

        elif cmd == "start_audio":
            try:
                self.audio.update_settings(self.config.get("audio", {}))
                self.audio.start()
                self._suspended = False
                self.status_changed.emit("Аудио-реакция запущена")
            except Exception as e:
                self.error_occurred.emit(f"Ошибка захвата звука: {e}")

        elif cmd == "stop_audio":
            self.audio.stop()
            self.status_changed.emit("Аудио-реакция остановлена")

        elif cmd == "set_preview_active":
            self._preview_active = bool(payload)

        elif cmd == "test_color":
            r, g, b = payload
            self._remember_test_color((r, g, b), calibrated=False)
            self.color_updated.emit(r, g, b)
            await self._send_to_current_strip(r, g, b)

        elif cmd == "test_calibrated":
            r, g, b = payload
            self._remember_test_color((r, g, b), calibrated=True)
            calib = self.config.get("calibration", {})
            cr, cg, cb = apply_calibration(r, g, b, calib)
            self.color_updated.emit(cr, cg, cb)
            await self._send_to_current_strip(cr, cg, cb)

        elif cmd == "blackout":
            self._suspended = True
            await self._blackout(power_off=True)
            self.status_changed.emit("Подсветка выключена")

        elif cmd == "wake_ramp":
            self._wake_ramp = max(0.0, min(1.0, float(payload)))

        elif cmd == "mapping_mode":
            self._mapping_mode = bool(payload)

        elif cmd == "highlight":
            index, r, g, b = payload
            await self._highlight_led(index, r, g, b)

        elif cmd == "set_strip_mode":
            if payload != self._strip_mode:
                await self._blackout(power_off=True)
                await self._disconnect_all()
                self.connected_changed.emit(False)
            self._strip_mode = payload
            self.config["strip_mode"] = payload
            self._should_be_connected = False  # новый режим - подключаемся заново вручную
            if payload == "wled":
                self.status_changed.emit("Режим: WLED (Wi-Fi)")
            elif payload == "wled_serial":
                self.status_changed.emit("Режим: WLED (USB-кабель)")
            else:
                self.status_changed.emit("Режим: ELK-BLEDOM (BLE)")

        elif cmd == "connect_wled":
            # Подключение может занять несколько секунд (HTTP-проба), поэтому
            # выполняем его отдельной задачей, а не внутри обработчика команд
            if self._connect_task is not None and not self._connect_task.done():
                self.status_changed.emit("Подключение уже выполняется...")
            else:
                self._connect_task = asyncio.create_task(self._connect_wled(payload))

        elif cmd == "connect_wled_serial":
            com_port, baud = payload
            self.wled_serial.disconnect()
            self.wled_serial = WLEDSerialController(com_port, baud)
            self.wled_serial.set_led_count(self.config.get("wled_led_count", 90))
            if self.wled_serial.connect():
                self._strip_mode = "wled_serial"
                self.config["strip_mode"] = "wled_serial"
                self.status_changed.emit(f"WLED подключён по USB: {com_port} @ {baud}")
                self.connected_changed.emit(True)
                self._should_be_connected = True
                self._last_known_serial_port = com_port
                self._last_known_serial_baud = baud
            else:
                self.error_occurred.emit(
                    f"Не удалось открыть порт {com_port}: {self.wled_serial.last_error}"
                )
                self.connected_changed.emit(False)

        elif cmd == "test_sweep":
            self._start_sweep(payload)

        elif cmd == "cancel_sweep":
            self._cancel_sweep()

        elif cmd == "set_strip_mapper":
            await self._apply_strip_mapper(payload)

        elif cmd == "save_strip_mapping":
            try:
                self.strip_mapper.save_to_file(
                    payload,
                    strip_width=self._last_mapper_strip_width,
                    exact_positions=self._last_mapper_exact_positions,
                )
                self.status_changed.emit(f"Маппинг сохранён в {payload}")
            except Exception as e:
                self.error_occurred.emit(f"Ошибка сохранения маппинга: {e}")

        elif cmd == "load_strip_mapping":
            await self._load_strip_mapping(payload)

        elif cmd == "quit":
            await self._shutdown()

        elif cmd == "diagnose":
            if self._diag_task is not None and not self._diag_task.done():
                self.status_changed.emit("Диагностика уже идёт...")
            else:
                self.status_changed.emit("Проверяю связь...")
                self._diag_task = asyncio.create_task(self._run_diagnostics())

    # ------------------------------------------------------------------
    # Подключение / отключение
    # ------------------------------------------------------------------

    async def _run_diagnostics(self):
        """Собирает отчёт «почему не работает» и отдаёт его в GUI."""
        lines = []
        mode_names = {
            "ble": "ELK-BLEDOM (BLE)",
            "wled": "WLED (Wi-Fi)",
            "wled_serial": "WLED (USB)",
        }
        lines.append(f"Режим ленты: {mode_names.get(self._strip_mode, self._strip_mode)}")

        if self._strip_mode == "wled":
            host = self.wled.host
            lines.append(f"Адрес: {host}:{self.wled.port}")
            started = time.perf_counter()
            info = await self._run_blocking(WLEDController.fetch_info, host, 5.0)
            elapsed = (time.perf_counter() - started) * 1000.0
            if info is None:
                lines.append("Плата: НЕ ОТВЕЧАЕТ на /json/info")
                lines.append("  → проверь питание, IP-адрес и что компьютер в той же Wi-Fi сети")
            else:
                wifi = info.get("wifi", {}) or {}
                leds = info.get("leds", {}) or {}
                lines.append(
                    f"Плата: {info.get('name')} {info.get('ver')} ({info.get('arch')}), "
                    f"ответ за {elapsed:.0f} мс"
                )
                lines.append(
                    f"Диодов на плате: {leds.get('count')} "
                    f"(в приложении {self.config.get('wled_led_count')})"
                )
                rssi = wifi.get("rssi")
                if rssi is not None:
                    lines.append(f"Wi-Fi: RSSI {rssi} dBm, сигнал {wifi.get('signal')}%")
                    if rssi < -75:
                        lines.append("  → сигнал слабый: возможны подвисания HTTP и рывки картинки")
                if leds.get("count") and leds.get("count") != self.config.get("wled_led_count"):
                    lines.append("  → число диодов не совпадает: поправь «Конфигурация ленты»")
            lines.append(f"UDP-сокет: {'открыт' if self.wled.socket is not None else 'закрыт'}")
            if elapsed > 800:
                lines.append("  → плата отвечает медленно, это нормально для слабого Wi-Fi")

        elif self._strip_mode == "ble":
            lines.append(f"Адрес: {self._last_known_ble_address or 'не задан'}")
            lines.append(f"BLE: {'подключено' if self.ble.is_connected else 'нет соединения'}")

        else:
            lines.append(f"Порт: {self.wled_serial.port} @ {self.wled_serial.baudrate}")
            lines.append(f"USB: {'открыт' if self.wled_serial.is_connected else 'закрыт'}")

        lines.append("")
        lines.append(f"Захват: {'включён' if self.capture_enabled else 'выключен'}")
        if self._capture is not None:
            try:
                frame = self._capture.get_frame()
                if frame is not None:
                    lines.append(f"Кадр: {frame.shape[1]}x{frame.shape[0]}")
                else:
                    lines.append("Кадр: камера пока не отдала ни одного кадра")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"Кадр: ошибка {exc}")
        else:
            lines.append("Кадр: захват не запущен (кнопка «Запустить Ambilight»)")

        boxes = len(self.strip_mapper.led_boxes)
        lines.append(
            f"Маппинг: {boxes} диодов, область выборки {self._last_mapper_strip_width} px"
        )
        if not boxes:
            lines.append("  → маппинг не построен: вся лента светит одним цветом экрана")

        filters = self.config.get("filters", {})
        lines.append("")
        lines.append(f"Пространственное сглаживание: {filters.get('neighbor_blend', 0):.2f}")
        lines.append(f"Усреднение в линейном свете: {'да' if filters.get('linear_average', True) else 'нет'}")
        lines.append(f"Гамма: {filters.get('gamma', 1.0):.2f}")
        lines.append(f"Целевой FPS: {self.config.get('target_fps')}")
        fps_adapt = self.config.get("adaptive_fps", {})
        if fps_adapt.get("enabled", True):
            lines.append(f"Адаптивный FPS: да (минимум {fps_adapt.get('min_fps', 10)} на статике)")

        lines.append("")
        lines.append(f"Отправлено кадров: {self._sent_packets}")
        lines.append(f"Оценка тока: {self._current_ma:.0f} мА")
        limit_ma = self.config.get("power", {}).get("limit_ma", 0)
        if limit_ma:
            lines.append(f"Ограничение тока: {limit_ma} мА")

        self.status_changed.emit("Диагностика завершена")
        self.diagnostics_ready.emit("\n".join(lines))

    async def _scan_ble(self):
        """Поиск BLE-устройств в отдельной задаче (не блокирует отправку кадров)."""
        self.status_changed.emit("Сканирование...")
        try:
            devices = await self.ble.scan(timeout=5)
            self.devices_found.emit(devices)
            if devices:
                self.status_changed.emit(f"Найдено устройств: {len(devices)}")
            else:
                self.status_changed.emit("Устройства не найдены")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Ошибка сканирования BLE")
            self.status_changed.emit("Сканирование не удалось")
            self.error_occurred.emit(f"Ошибка сканирования: {e}")

    async def _connect_ble(self, address):
        """Подключение к ELK-BLEDOM в отдельной задаче."""
        self.status_changed.emit("Подключение...")
        try:
            ok = await self.ble.connect(address)
            self.connected_changed.emit(ok)
            if ok:
                self._strip_mode = "ble"
                self._should_be_connected = True
                self._last_known_ble_address = address
                self.status_changed.emit("Подключено")
                log.info("BLE подключён: %s", address)
            else:
                self._should_be_connected = False
                self.status_changed.emit("Не удалось подключиться")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Ошибка подключения BLE")
            self._should_be_connected = False
            self.connected_changed.emit(False)
            self.status_changed.emit("Не удалось подключиться")
            self.error_occurred.emit(f"Ошибка подключения: {e}")

    async def _connect_wled(self, payload):
        host, port = payload
        port = int(port)
        try:
            self.status_changed.emit(f"Проверяю {host}...")
            # У ESP32 со слабым Wi-Fi HTTP-сервер иногда «залипает» на 2-3 секунды
            # (потеря TCP-пакета и ретрансмиссия), поэтому одна неудачная попытка
            # ещё ничего не значит - пробуем дважды с увеличенным таймаутом.
            info = None
            for attempt in range(2):
                info = await self._run_blocking(WLEDController.fetch_info, host, 4.0)
                if info is not None:
                    break
                if attempt == 0:
                    self.status_changed.emit(f"{host} не ответил, пробую ещё раз...")

            if info is None:
                self.connected_changed.emit(False)
                self.status_changed.emit("WLED не отвечает")
                self.error_occurred.emit(
                    f"WLED не отвечает по адресу {host}. Проверь IP-адрес, питание платы "
                    f"и что компьютер с ней в одной Wi-Fi сети."
                )
                log.warning("WLED %s не ответил на /json/info", host)
                return

            self.wled.disconnect()
            self.wled = WLEDController(host, port, self.config.get("wled_led_count", 90))
            self.wled.device_info = info
            self._strip_mode = "wled"
            self.config["strip_mode"] = "wled"

            if not self.wled.connect():
                self.connected_changed.emit(False)
                self.error_occurred.emit("Не удалось создать UDP-сокет")
                return

            name = info.get("name", "WLED")
            version = info.get("ver", "")
            device_leds = int(info.get("leds", {}).get("count", 0) or 0)
            app_leds = self.wled.led_count
            if device_leds and device_leds != app_leds:
                self.status_changed.emit(
                    f"Внимание: у платы {device_leds} диодов, в приложении {app_leds}. "
                    f"Поправь «Конфигурация ленты»."
                )
                log.warning("Расхождение числа диодов: плата=%s, приложение=%s",
                            device_leds, app_leds)
            self._warn_led_limit_if_needed()

            self.connected_changed.emit(True)
            self._should_be_connected = True
            self._wled_reachable = True
            self._wled_fail_streak = 0
            self._last_known_wled_host = host
            self._last_known_wled_port = port
            self.status_changed.emit(
                f"WLED подключён: {host}:{port} ({name} {version}, диодов {device_leds or app_leds})"
            )
            log.info("WLED подключён: %s:%s (%s %s, диодов %s)",
                     host, port, name, version, device_leds or app_leds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка подключения к WLED")
            self.connected_changed.emit(False)
            self.error_occurred.emit("Ошибка подключения к WLED (подробности в журнале)")

    def _apply_sample_width(self, payload):
        """Перестраивает области выборки, если пользователь поменял их размер."""
        if not self.strip_mapper.led_boxes or not self.strip_mapper.led_path:
            return
        try:
            new_width = int(payload.get("sample_width", 10))
        except (TypeError, ValueError):
            return
        if new_width == self._last_mapper_strip_width:
            return
        self.strip_mapper.build_mapping(
            strip_width=new_width,
            exact_positions=self._last_mapper_exact_positions,
        )
        self._last_mapper_strip_width = new_width
        self._smoothed_pixels = None
        self._emit_mapping_boxes()
        log.info("Размер области выборки: %s px", new_width)

    def _apply_target_fps(self, payload):
        """Перезапускает захват, если пользователь поменял целевой FPS:
        иначе новое значение применилось бы только после перезапуска."""
        if self._capture is None or not self.capture_enabled:
            return
        try:
            new_fps = int(payload.get("target_fps", 30))
        except (TypeError, ValueError):
            return
        old_fps = int(getattr(self._capture, "target_fps", new_fps) or new_fps)
        if new_fps == old_fps:
            return
        try:
            self._capture.stop()
            self._capture = ScreenCapture(
                target_fps=new_fps,
                backend=self.config.get("capture_backend", "dxgi"),
                region=self.config.get("capture_region"),
                monitor_index=self.config.get("monitor_index", 0),
            )
            self._capture.start()
            self._smoothed_pixels = None
            log.info("Захват перезапущен с %s FPS", new_fps)
        except Exception:
            log.exception("Не удалось применить новый целевой FPS")
            self.error_occurred.emit("Не удалось изменить частоту захвата - перезапусти Ambilight")

    def _warn_led_limit_if_needed(self):
        count = self.config.get("wled_led_count", 90)
        if count > MAX_DRGB_LEDS and not self._warned_led_limit:
            self._warned_led_limit = True
            self.error_occurred.emit(
                f"Диодов {count}, а протокол DRGB передаёт максимум {MAX_DRGB_LEDS} за пакет. "
                f"Дальше этого числа лента светиться не будет."
            )

    async def _disconnect_all(self):
        try:
            await self.ble.disconnect()
        except Exception:
            log.debug("Ошибка отключения BLE", exc_info=True)
        try:
            self.wled.disconnect()
        except Exception:
            log.debug("Ошибка отключения WLED", exc_info=True)
        try:
            self.wled_serial.disconnect()
        except Exception:
            log.debug("Ошибка отключения USB", exc_info=True)

    def is_screen_moving(self) -> bool:
        """Меняется ли картинка на экране (используется автопаузой)."""
        return bool(getattr(self, "_screen_moving", False))

    def _is_strip_available(self) -> bool:
        """Есть ли сейчас живой канал до ленты (в текущем режиме)."""
        if self._strip_mode == "ble":
            return self.ble.is_connected
        if self._strip_mode == "wled":
            return self.wled.socket is not None
        return self.wled_serial.is_connected

    async def _set_strip_power(self, on: bool):
        """Явное включение/выключение ленты (WLED - по HTTP, BLE - командой питания)."""
        try:
            if self._strip_mode == "wled":
                # Если пользователь ещё не подключался - не тратим 1.5 секунды
                # на HTTP-запрос к плате, соединения всё равно нет
                if self.wled.host and self._should_be_connected:
                    await self._run_blocking(self.wled.set_power, on, 1.5)
            elif self._strip_mode == "ble" and self.ble.is_connected:
                await self.ble.send_power(on)
        except Exception:
            log.debug("Не удалось переключить питание ленты", exc_info=True)

    async def _blackout(self, power_off: bool = False, http_timeout: float = 1.5):
        """Гасит ленту, чтобы она не осталась с «замёрзшим» последним кадром.

        power_off=False - чёрный кадр + выход из realtime (WLED вернётся к своему
        эффекту). power_off=True - ещё и выключить выход на ленту.

        http_timeout ограничивает ожидание ответа платы: при выходе из
        приложения нельзя зависать на медленной сети - иначе поток не успевает
        остановиться и процесс остаётся висеть в памяти.
        """
        try:
            if self._strip_mode == "ble":
                if self.ble.is_connected:
                    await self.ble.send_color(0, 0, 0)
                    if power_off:
                        await self.ble.send_power(False)
            elif self._strip_mode == "wled":
                if self.wled.socket is not None:
                    old_interval = self.wled.min_send_interval
                    self.wled.min_send_interval = 0.0
                    try:
                        self.wled.send_black()
                    finally:
                        self.wled.min_send_interval = old_interval
                # HTTP-команды имеют смысл только если мы реально подключались
                if self.wled.host and self._should_be_connected:
                    if power_off:
                        # Сначала выключаем выход, потом снимаем realtime-флаг:
                        # в обратном порядке WLED успевает вернуть свой эффект
                        # и снова включает ленту
                        await self._run_blocking(self.wled.set_power, False, http_timeout)
                        await self._run_blocking(self.wled.release_live, http_timeout)
                    else:
                        # Возвращаем плату к её собственному эффекту
                        await self._run_blocking(self.wled.release_live, http_timeout)
            elif self._strip_mode == "wled_serial":
                if self.wled_serial.is_connected:
                    old_interval = self.wled_serial.min_send_interval
                    self.wled_serial.min_send_interval = 0.0
                    try:
                        self.wled_serial.send_frame(
                            [(0, 0, 0)] * max(1, self.wled_serial.led_count)
                        )
                    finally:
                        self.wled_serial.min_send_interval = old_interval
        except Exception:
            log.debug("Ошибка при гашении ленты", exc_info=True)

    # ------------------------------------------------------------------
    # Тестовый цвет и развёртка
    # ------------------------------------------------------------------

    def _remember_test_color(self, color, calibrated: bool):
        """Запоминает тестовый цвет и держит его несколько секунд: иначе
        следующий кадр захвата (через 30 мс) мгновенно затирает тест и
        калибровать по нему невозможно."""
        self._last_test_color = color
        self._last_test_calibrated = calibrated
        self._hold_until = time.monotonic() + TEST_HOLD_SECONDS

    async def _refresh_held_test_color(self):
        """Пока держится тестовый цвет, пересчитываем и отправляем его заново
        при каждом изменении настроек - так слайдеры калибровки работают «живьём»."""
        if self._last_test_color is None or time.monotonic() >= self._hold_until:
            return
        r, g, b = self._last_test_color
        if self._last_test_calibrated:
            r, g, b = apply_calibration(r, g, b, self.config.get("calibration", {}))
        await self._send_to_current_strip(r, g, b)

    def _start_sweep(self, payload):
        r, g, b, delay_ms = payload
        if self._sweep_task is not None and not self._sweep_task.done():
            self.error_occurred.emit("Развёртка уже запущена")
            return
        self._sweep_cancel = False
        self._sweep_task = asyncio.create_task(self._run_sweep(r, g, b, delay_ms))

    def _cancel_sweep(self):
        self._sweep_cancel = True

    async def _run_sweep(self, r, g, b, delay_ms):
        target = self.wled if self._strip_mode == "wled" else (
            self.wled_serial if self._strip_mode == "wled_serial" else None
        )
        if target is None:
            self.error_occurred.emit("Тест-развёртка доступна только в режиме WLED (Wi-Fi или USB)")
            return

        offset = max(0, int(self.config.get("led_start_offset", 0)))
        count = self.strip_mapper.led_count if self.strip_mapper.led_boxes \
            else max(1, int(self.config.get("wled_led_count", 90)) - offset)
        if count <= 0:
            self.error_occurred.emit("Нет диодов для развёртки - сначала построй маппинг")
            return

        self._sweep_active = True
        old_interval = target.min_send_interval
        target.min_send_interval = 0.0  # иначе пакеты будут отбрасываться троттлингом
        self.status_changed.emit(f"Развёртка запущена: {offset + 1} / {count + offset}")
        try:
            for i in range(count):
                if self._sweep_cancel:
                    self.status_changed.emit(
                        f"Развёртка отменена на диоде {i + 1 + offset} / {count + offset}"
                    )
                    break
                pixels = [(0, 0, 0)] * count
                pixels[i] = (r, g, b)
                target.send_frame(self._with_led_offset(pixels))
                self.status_changed.emit(
                    f"Развёртка: диод {i + 1 + offset} / {count + offset}"
                )
                await asyncio.sleep(delay_ms / 1000.0)
            else:
                self.status_changed.emit("Развёртка завершена")
        finally:
            target.min_send_interval = old_interval
            target.send_frame([(0, 0, 0)] * (count + offset))
            self._sweep_active = False

    # ------------------------------------------------------------------
    # Маппинг ленты
    # ------------------------------------------------------------------

    def _clear_mapping(self):
        """Пустой маппинг: старые координаты после смены монитора не используем."""
        try:
            width = int(self.config.get("screen_width", 1920))
            height = int(self.config.get("screen_height", 1080))
        except (TypeError, ValueError):
            width, height = 1920, 1080
        self.strip_mapper = StripMapper(1, max(1, width), max(1, height))
        self._smoothed_pixels = None
        self._emit_mapping_boxes()
        self.status_changed.emit("Маппинг сброшен после смены монитора — построй заново")

    async def _apply_strip_mapper(self, payload):
        led_path, screen_width, screen_height, exact_positions, led_count = payload

        # Область захвата задана в координатах полного экрана. Маппинг с
        # холста тоже приходит в полных координатах, поэтому сначала
        # сдвигаем его внутрь области, а потом подгоняем под реальный кадр.
        region = None
        if self._capture is not None:
            region = getattr(self._capture, "region", None)
        if region is None:
            region = self.config.get("capture_region")
        if region:
            try:
                left, top, region_w, region_h = (int(v) for v in region)
                if region_w > 1 and region_h > 1:
                    led_path = [
                        (
                            min(max(x - left, 0.0), region_w - 1.0),
                            min(max(y - top, 0.0), region_h - 1.0),
                        )
                        for x, y in led_path
                    ]
                    screen_width, screen_height = region_w, region_h
            except (TypeError, ValueError):
                log.debug("Некорректная область захвата при маппинге: %r", region)

        # Если захват уже запущен - берём РЕАЛЬНОЕ разрешение кадра,
        # чтобы маппинг точно совпадал с тем, что видит камера захвата
        if self._capture is not None:
            try:
                test_frame = self._capture.get_frame()
                if test_frame is not None:
                    real_h, real_w = test_frame.shape[0], test_frame.shape[1]
                    led_path = [
                        (x * real_w / screen_width, y * real_h / screen_height)
                        for x, y in led_path
                    ]
                    screen_width, screen_height = real_w, real_h
            except Exception:
                log.debug("Не удалось получить кадр для подгонки маппинга", exc_info=True)

        offset = max(0, int(self.config.get("led_start_offset", 0)))
        if led_count is None:
            led_count = max(1, int(self.config.get("wled_led_count", 90)) - offset)
        led_count = max(1, int(led_count))
        strip_width = int(self.config.get("sample_width", 10))

        self.strip_mapper = StripMapper(led_count, screen_width, screen_height)
        self.strip_mapper.led_path = led_path
        self._last_mapper_strip_width = strip_width
        self._last_mapper_exact_positions = exact_positions
        if self.strip_mapper.build_mapping(strip_width=strip_width, exact_positions=exact_positions):
            self._smoothed_pixels = None
            self._led_start_offset = offset
            total_leds = led_count + offset
            self.wled.set_led_count(total_leds)
            self.wled_serial.set_led_count(total_leds)
            mode_txt = "точные позиции" if exact_positions else "равномерно по длине"
            region_txt = f", область {region}" if region else ", весь экран"
            self.status_changed.emit(
                f"Маппинг ленты настроен ({len(led_path)} точек, {mode_txt}, "
                f"экран {screen_width}x{screen_height}{region_txt}, "
                f"активных диодов {led_count}, пропущено первых {offset})"
            )
            self._emit_mapping_boxes()
            log.info("Маппинг построен: точек=%s, активных=%s, offset=%s, экран=%sx%s, exact=%s",
                     len(led_path), led_count, offset, screen_width, screen_height, exact_positions)
        else:
            self.error_occurred.emit("Ошибка построения маппинга")

    async def _load_strip_mapping(self, path):
        try:
            ok = self.strip_mapper.load_from_file(path)
            if not ok:
                self.error_occurred.emit("Файл маппинга загружен, но построить карту не удалось")
                return

            # Если включена область захвата, а файл маппинга сделан для
            # полного экрана — сдвигаем точки в координаты области.
            region = None
            if self._capture is not None:
                region = getattr(self._capture, "region", None)
            if region is None:
                region = self.config.get("capture_region")
            if region:
                try:
                    left, top, region_w, region_h = (int(v) for v in region)
                    sw, sh = self.strip_mapper.screen_width, self.strip_mapper.screen_height
                    if sw > region_w or sh > region_h:
                        self.strip_mapper.led_path = [
                            (
                                min(max(x - left, 0.0), region_w - 1.0),
                                min(max(y - top, 0.0), region_h - 1.0),
                            )
                            for x, y in self.strip_mapper.led_path
                        ]
                        self.strip_mapper.screen_width = region_w
                        self.strip_mapper.screen_height = region_h
                        self.strip_mapper.build_mapping(
                            strip_width=self.strip_mapper.last_strip_width,
                            exact_positions=self.strip_mapper.last_exact_positions,
                        )
                except (TypeError, ValueError):
                    log.debug("Некорректная область при загрузке маппинга: %r", region)

            # Подгонка под реальное разрешение захвата (как и при обычном построении)
            if self._capture is not None:
                try:
                    test_frame = self._capture.get_frame()
                    if test_frame is not None:
                        real_h, real_w = test_frame.shape[0], test_frame.shape[1]
                        sw, sh = self.strip_mapper.screen_width, self.strip_mapper.screen_height
                        if (real_w, real_h) != (sw, sh) and sw and sh:
                            self.strip_mapper.led_path = [
                                (x * real_w / sw, y * real_h / sh)
                                for x, y in self.strip_mapper.led_path
                            ]
                            self.strip_mapper.screen_width = real_w
                            self.strip_mapper.screen_height = real_h
                            self.strip_mapper.build_mapping(
                                strip_width=self.strip_mapper.last_strip_width,
                                exact_positions=self.strip_mapper.last_exact_positions,
                            )
                except Exception:
                    log.debug("Не удалось подогнать загруженный маппинг", exc_info=True)

            self._last_mapper_strip_width = self.strip_mapper.last_strip_width
            self._last_mapper_exact_positions = self.strip_mapper.last_exact_positions
            offset = max(0, int(self.config.get("led_start_offset", 0)))
            total_leds = self.strip_mapper.led_count + offset
            self.config["wled_led_count"] = total_leds
            self.wled.set_led_count(total_leds)
            self.wled_serial.set_led_count(total_leds)
            self._smoothed_pixels = None

            self.status_changed.emit(
                f"Маппинг загружен из {path} ({self.strip_mapper.led_count} активных диодов, "
                f"первые {offset} пропущены)"
            )
            self._emit_mapping_boxes()
            self.mapping_loaded.emit({
                "led_path": list(self.strip_mapper.led_path),
                "screen_width": self.strip_mapper.screen_width,
                "screen_height": self.strip_mapper.screen_height,
                "led_count": self.strip_mapper.led_count,
                "exact_positions": self.strip_mapper.last_exact_positions,
            })
        except Exception as e:
            log.exception("Ошибка загрузки маппинга")
            self.error_occurred.emit(f"Ошибка загрузки маппинга: {e}")

    def _align_mapping_to_frame(self):
        """Если разрешение захвата отличается от того, под которое построен
        маппинг - пропорционально пересчитываем путь, иначе диоды «съезжают»."""
        if self._capture is None or not self.strip_mapper.led_boxes:
            return
        try:
            frame = self._capture.get_frame()
        except Exception:
            return
        if frame is None:
            return
        real_h, real_w = frame.shape[0], frame.shape[1]
        sw, sh = self.strip_mapper.screen_width, self.strip_mapper.screen_height
        if not sw or not sh or (real_w, real_h) == (sw, sh):
            return
        self.strip_mapper.led_path = [
            (x * real_w / sw, y * real_h / sh) for x, y in self.strip_mapper.led_path
        ]
        self.strip_mapper.screen_width = real_w
        self.strip_mapper.screen_height = real_h
        self.strip_mapper.build_mapping(
            strip_width=self.strip_mapper.last_strip_width,
            exact_positions=self.strip_mapper.last_exact_positions,
        )
        self._smoothed_pixels = None
        log.info("Маппинг подогнан под реальное разрешение захвата %sx%s", real_w, real_h)
        self._emit_mapping_boxes()
        self.mapping_loaded.emit({
            "led_path": list(self.strip_mapper.led_path),
            "screen_width": real_w,
            "screen_height": real_h,
            "led_count": self.strip_mapper.led_count,
            "exact_positions": self.strip_mapper.last_exact_positions,
        })

    # ------------------------------------------------------------------
    # Служебное
    # ------------------------------------------------------------------

    def _stop_capture_object(self):
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception:
                log.debug("Ошибка остановки захвата", exc_info=True)
            self._capture = None

    async def _shutdown(self):
        log.info("Останавливаю фоновый поток")
        self.capture_enabled = False
        self._cancel_sweep()
        for task in (self._sweep_task, self._connect_task, self._scan_task,
                     self._health_check_task):
            if task is not None and not task.done():
                task.cancel()
        self._stop_capture_object()
        self.audio.stop()
        self.now_playing.stop()
        self.weather.stop()
        await self._blackout(power_off=False, http_timeout=0.8)
        await self._disconnect_all()
        self.connected_changed.emit(False)
        self._running = False

    async def _run_blocking(self, func, *args):
        """Выполняет блокирующую функцию (HTTP-запрос) в пуле потоков, чтобы
        не вешать asyncio-цикл и не останавливать передачу кадров."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args)

    # ------------------------------------------------------------------
    # Проверка связи
    # ------------------------------------------------------------------

    def _maybe_check_connection_health(self):
        """Раз в несколько секунд проверяет, жива ли связь с устройством, и
        если пользователь явно подключался - пытается тихо восстановить
        соединение при обрыве.

        Сама проверка запускается как ОТДЕЛЬНАЯ фоновая задача, а не через
        await прямо здесь: пока приложение ждало бы ответа по HTTP, новые
        кадры на ленту не уходили бы, и WLED успевал бы откатиться на свой
        эффект."""
        if not self.config.get("auto_reconnect", True):
            return
        if not self._should_be_connected:
            return

        now = time.monotonic()
        if now - self._last_health_check < HEALTH_CHECK_INTERVAL:
            return
        if self._health_check_task is not None and not self._health_check_task.done():
            return
        self._last_health_check = now
        self._health_check_task = asyncio.create_task(self._run_connection_health_check())

    async def _run_connection_health_check(self):
        try:
            if self._strip_mode == "ble":
                if not self.ble.is_connected and self._last_known_ble_address:
                    self.status_changed.emit("Связь потеряна, пробую переподключиться...")
                    try:
                        ok = await self.ble.connect(self._last_known_ble_address)
                        self.connected_changed.emit(ok)
                        self.status_changed.emit("Переподключено" if ok else "Не удалось переподключиться")
                    except Exception:
                        self.connected_changed.emit(False)

            elif self._strip_mode == "wled":
                host = self.wled.host or self._last_known_wled_host
                if not host:
                    return
                info = await self._run_blocking(WLEDController.fetch_info, host, 4.0)
                if info is not None:
                    self._wled_fail_streak = 0
                    if self._wled_reachable is not True:
                        self._wled_reachable = True
                        self.connected_changed.emit(True)
                        self.status_changed.emit(f"WLED на связи: {host}")
                    if self.wled.socket is None:
                        self.wled.connect()
                else:
                    self._wled_fail_streak += 1
                    # Один медленный ответ - ещё не обрыв: у ESP32 со слабым
                    # Wi-Fi HTTP иногда «залипает» на 2-3 секунды. Сообщаем
                    # только после двух провалов подряд, иначе статус мигает.
                    if self._wled_fail_streak >= 2 and self._wled_reachable is not False:
                        self._wled_reachable = False
                        self.connected_changed.emit(False)
                        self.status_changed.emit(f"WLED не отвечает: {host}")
                        log.warning("WLED %s перестал отвечать", host)

            elif self._strip_mode == "wled_serial":
                if not self.wled_serial.is_connected and self._last_known_serial_port:
                    self.status_changed.emit("USB-связь потеряна, пробую переподключиться...")
                    try:
                        self.wled_serial.disconnect()
                        self.wled_serial = WLEDSerialController(
                            self._last_known_serial_port, self._last_known_serial_baud
                        )
                        self.wled_serial.set_led_count(self.config.get("wled_led_count", 90))
                        ok = self.wled_serial.connect()
                        self.connected_changed.emit(ok)
                        self.status_changed.emit("USB переподключено" if ok else "USB недоступен")
                    except Exception:
                        self.connected_changed.emit(False)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("Ошибка проверки связи", exc_info=True)

    # ------------------------------------------------------------------
    # Дежурный режим
    # ------------------------------------------------------------------

    async def _highlight_led(self, index, r, g, b):
        """Подсвечивает ровно один диод и гасит остальные (раскладка)."""
        if self._strip_mode not in ("wled", "wled_serial"):
            return
        target = self.wled if self._strip_mode == "wled" else self.wled_serial
        offset = max(0, int(self.config.get("led_start_offset", 0)))
        count = max(1, int(getattr(target, "led_count", 90) or 90) - offset)
        pixels = [(0, 0, 0)] * count
        if 0 <= index < count:
            pixels[index] = (r, g, b)

        old_interval = target.min_send_interval
        target.min_send_interval = 0.0
        try:
            sent = target.send_frame(self._with_led_offset(pixels))
            self._note_frame(sent)
        finally:
            target.min_send_interval = old_interval

    async def _wake_tick(self):
        """Рассвет-будильник: от тёплого оранжевого к мягкому белому."""
        if self._wake_ramp <= 0.01:
            return None
        if self._suspended or not self._is_strip_available():
            return None

        level = max(0.0, min(1.0, self._wake_ramp))
        warm = (255, 120, 40)
        white = (255, 240, 215)
        color = tuple(int(warm[i] + (white[i] - warm[i]) * level) for i in range(3))
        color = tuple(int(c * (0.04 + 0.8 * level)) for c in color)

        self.color_updated.emit(*color)
        await self._send_to_current_strip(*color)
        return 0.5

    async def _lamp_tick(self):
        """Режим «Лампа»: ровный белый свет выбранной температуры."""
        lamp = self.config.get("lamp", {})
        if not lamp.get("enabled", False):
            return None
        if self._suspended or not self._is_strip_available():
            return None

        r, g, b = self._lamp_color(lamp)
        self.color_updated.emit(r, g, b)
        await self._send_to_current_strip(r, g, b)
        return 0.25

    @staticmethod
    def _lamp_color(lamp):
        """Цвет лампы: 0 — тёплый (2700К), 1 — холодный (6500К)."""
        temperature = max(0.0, min(1.0, float(lamp.get("temperature", 0.35))))
        warm = (255, 176, 104)
        cool = (196, 220, 255)
        base = tuple(int(warm[i] + (cool[i] - warm[i]) * temperature) for i in range(3))
        level = max(0.02, min(1.0, float(lamp.get("brightness", 0.7))))
        return tuple(int(c * level) for c in base)

    async def _standby_tick(self):
        """"Дыхание" выбранным цветом, когда Ambilight и аудио-визуализация не
        активны. Источник цвета: фиксированный / погода / Spotify."""
        standby_cfg = self.config.get("standby", {})
        if not standby_cfg.get("enabled", False):
            return None
        # После таймера сна / ручного выключения подсветка не должна
        # «просыпаться» сама собой
        if self._suspended:
            return None
        if not self._is_strip_available():
            return None

        source = standby_cfg.get("source", "fixed")
        base_color = None

        # Лишние мониторы не должны молотить в фоне
        if source != "weather" and self.weather.is_running:
            self.weather.stop()
        if source != "spotify" and self.now_playing.is_running:
            self.now_playing.stop()

        if source == "weather":
            if not self.weather.is_running:
                self.weather.set_city(standby_cfg.get("weather_city", ""))
                self.weather.start()
            base_color = self.weather.get_color()
        elif source == "spotify":
            if not self.now_playing.is_running:
                self.now_playing.start()
            base_color = self.now_playing.get_color()
            track_name = self.now_playing.get_track_name()
            if track_name != self._last_now_playing_text:
                self._last_now_playing_text = track_name
                self.now_playing_changed.emit(track_name)

        if base_color is None:
            base_color = tuple(standby_cfg.get("fixed_color", [80, 60, 180]))

        speed = max(0.05, standby_cfg.get("speed", 0.35))
        self._standby_phase += speed * 0.1  # шаг фазы за один тик (~100мс)
        brightness = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(self._standby_phase))

        r = int(base_color[0] * brightness)
        g = int(base_color[1] * brightness)
        b = int(base_color[2] * brightness)

        self.color_updated.emit(r, g, b)
        await self._send_to_current_strip(r, g, b)
        return 0.1

    # ------------------------------------------------------------------
    # Поэтевиксельное сглаживание
    # ------------------------------------------------------------------

    def _smooth_pixel_array(self, raw_pixels):
        """Плавно подтягивает цвет каждого диода к только что посчитанному,
        вместо мгновенного скачка - та же адаптивная логика, что и для одного
        общего цвета, только векторизованная сразу для всех диодов."""
        filters = self.config.get("filters", {})
        target = np.array(raw_pixels, dtype=np.float32)

        if self._smoothed_pixels is None or self._smoothed_pixels.shape != target.shape:
            self._smoothed_pixels = target.copy()
            return raw_pixels

        if not filters.get("adaptive_smoothing", True):
            self._smoothed_pixels = target.copy()
            return raw_pixels

        prev = self._smoothed_pixels
        delta = np.abs(target - prev).sum(axis=1)
        sensitivity = max(1, filters.get("adaptive_sensitivity", 90))
        ratio = np.clip(delta / sensitivity, 0.0, 1.0)

        s_min = filters.get("smoothing_min", 0.12)
        s_max = filters.get("smoothing_max", 0.55)
        factor = (s_min + (s_max - s_min) * ratio)[:, None]

        smoothed = prev + (target - prev) * factor
        self._smoothed_pixels = smoothed

        clipped = np.clip(smoothed, 0, 255)
        return [(int(r), int(g), int(b)) for r, g, b in clipped]

    async def _send_to_current_strip(self, r, g, b):
        """Отправляет один цвет на всю ленту в ТЕКУЩЕМ активном режиме."""
        if self._strip_mode == "ble":
            if self.ble.is_connected:
                sent = await self.ble.send_color(r, g, b)
                self._note_frame(sent)
        elif self._strip_mode == "wled":
            if self.wled.socket is not None:
                # Троттлинг выключаем только на этот кадр: иначе одиночный
                # тестовый цвет или чёрный кадр может быть отброшен
                old_interval = self.wled.min_send_interval
                self.wled.min_send_interval = 0.0
                try:
                    started = time.perf_counter()
                    offset = max(0, int(self.config.get("led_start_offset", 0)))
                    active = max(1, int(self.wled.led_count) - offset)
                    pixels = self._with_led_offset([(r, g, b)] * active)
                    sent = self.wled.send_frame(pixels)
                    self._note_frame(sent, (time.perf_counter() - started) * 1000.0)
                finally:
                    self.wled.min_send_interval = old_interval
        elif self._strip_mode == "wled_serial":
            offset = max(0, int(self.config.get("led_start_offset", 0)))
            active = max(1, int(self.wled_serial.led_count) - offset)
            pixels = self._with_led_offset([(r, g, b)] * active)
            sent = self.wled_serial.send_frame(pixels)
            self._note_frame(sent)

    def _emit_mapping_boxes(self):
        """Отдаёт в GUI области выборки диодов (для наложения на превью)."""
        mapper = self.strip_mapper
        if not mapper.led_boxes:
            return
        self.mapping_boxes.emit({
            "boxes": list(mapper.led_boxes),
            "screen_width": mapper.screen_width,
            "screen_height": mapper.screen_height,
        })

    # ------------------------------------------------------------------
    # Основной кадр
    # ------------------------------------------------------------------

    async def _screen_tick(self):
        frame = self._capture.get_frame()
        if frame is None:
            return 0.005
        frame_h, frame_w = frame.shape[0], frame.shape[1]
        if frame_h < 2 or frame_w < 2:
            return 0.005

        small = frame[::ANALYSIS_STRIDE, ::ANALYSIS_STRIDE]
        if small.size == 0:
            return 0.005

        content = self.config.get("content", {})
        info = self._analyzer.update(small)
        idle_frame = self._update_static_state(frame)
        # Автопауза смотрит на это: если на экране идёт фильм, пользователь
        # «не активен» только для Windows, а подсветку гасить нельзя
        self._screen_moving = not info.is_static

        # --- имя активного процесса (для сцен приложений) ---
        process_name = get_active_process_name()
        if process_name != self._last_process_name:
            self._last_process_name = process_name
            self.process_changed.emit(process_name or "")

        # --- профиль: активный процесс + авто-определение кино по полосам ---
        profile_name = detect_profile(self.config)
        if (content.get("auto_cinema", True) and info.is_cinema
                and profile_name in ("desktop", "movie")):
            profile_name = "movie"
        if profile_name != self._last_profile:
            self._last_profile = profile_name
            self.color_engine.set_profile(profile_name)
            self.color_engine.reset()
            self.profile_changed.emit(profile_name)

        profiles = self.config.get("profiles", {})
        profile = profiles.get(profile_name, profiles.get("desktop", {
            "mode": "quadrants", "delay": 0.1
        }))

        now = time.monotonic()
        if self._preview_active and now - self._last_preview_time > PREVIEW_INTERVAL:
            self._last_preview_time = now
            try:
                preview = downsize_for_preview(frame, max_width=320)
                self.frame_preview.emit(preview)
            except Exception:
                log.debug("Не удалось отправить кадр превью", exc_info=True)

        # Пока держим тестовый цвет, идёт развёртка или раскладка -
        # не перекрываем их кадром
        if now < self._hold_until or self._sweep_active or self._mapping_mode:
            return 0.03

        audio_cfg = self.config.get("audio", {})
        audio_mode = audio_cfg.get("mode", "hybrid")
        audio_active = self.audio.is_running and audio_cfg.get("enabled", False)
        visualizer_owns_strip = audio_active and audio_mode in ("visualizer", "spectrum")

        # В режиме «визуализатор» лентой управляет _audio_tick: считать зоны
        # экрана и гонять цветовой пайплайн незачем
        if visualizer_owns_strip:
            return self._profile_delay(profile)

        # --- область анализа: пользовательская HUD-маска + чёрные полосы ---
        hud = self.config.get("hud_mask", {})
        user_rect = hud_rect(frame_h, frame_w, hud)
        if content.get("letterbox_crop", True):
            rect = info.content_rect(frame_h, frame_w, user_rect)
        else:
            rect = user_rect

        # --- умная маска статичного интерфейса (HUD игр) ---
        if content.get("smart_hud", True) and self.strip_mapper.led_boxes:
            if now - self._last_ui_apply > 1.0:
                self._last_ui_apply = now
                try:
                    self.strip_mapper.apply_ui_mask(info.ui_mask(), frame_w, frame_h)
                except Exception:
                    log.debug("Не удалось применить маску статичного HUD", exc_info=True)

        top, bottom, left, right = rect
        sy0 = min(small.shape[0] - 1, max(0, top // ANALYSIS_STRIDE))
        sy1 = max(sy0 + 1, min(small.shape[0], bottom // ANALYSIS_STRIDE))
        sx0 = min(small.shape[1] - 1, max(0, left // ANALYSIS_STRIDE))
        sx1 = max(sx0 + 1, min(small.shape[1], right // ANALYSIS_STRIDE))
        img = small[sy0:sy1, sx0:sx1]

        mode = profile.get("mode", "quadrants")
        if mode == "center_quadrants":
            img_for_zones = get_center_area(img, self.config.get("analysis_area_percent", 60))
        else:
            img_for_zones = img

        raw = self._get_zone_average(img_for_zones, mode)
        final_color, should_send = self.color_engine.process(raw)

        # --- плавное затухание, когда картинка остановилась (пауза) ---
        # Только для кино: на рабочем столе картинка тоже часто статична,
        # и гасить там подсветку до 15% было бы неправильно.
        target_fade = 1.0
        cinema_context = info.is_cinema or profile_name == "movie"
        if content.get("fade_on_pause", True) and info.paused_content() and cinema_context:
            target_fade = max(0.0, min(1.0, float(content.get("fade_level", 0.15))))
        self._fade += (target_fade - self._fade) * 0.08

        state = None
        send_color = final_color
        if audio_active and audio_mode in ("hybrid", "flash"):
            state = self.audio.get_state()
            send_color = self._apply_audio_modulation(final_color, audio_cfg, audio_mode, state)
            should_send = True

        if self._fade < 0.995:
            send_color = tuple(int(c * self._fade) for c in send_color)

        # Для WLED (Wi-Fi или USB) всегда отправляем свежий кадр - порог смены
        # цвета придуман для медленного BLE, а тут нужна реалтайм передача
        if self._strip_mode in ("wled", "wled_serial"):
            should_send = True

        self.color_updated.emit(*send_color)
        if should_send:
            r, g, b = send_color
            if self._strip_mode == "ble" and self.ble.is_connected:
                await self.ble.send_color(r, g, b)
            elif self._strip_mode in ("wled", "wled_serial"):
                await self._send_pixel_frame(frame, rect, send_color,
                                             audio_active, audio_cfg, audio_mode, state)

        delay = self._profile_delay(profile)
        if idle_frame:
            # Картинка не меняется - незачем гонять 60 кадров/с и грузить Wi-Fi
            min_fps = max(1, int(self.config.get("adaptive_fps", {}).get("min_fps", 10)))
            delay = max(delay, 1.0 / min_fps)
        return delay

    def _update_static_state(self, frame):
        """Проверяет, меняется ли картинка, чтобы снизить частоту кадров на статике."""
        cfg = self.config.get("adaptive_fps", {})
        if not cfg.get("enabled", True):
            self._static_frames = 0
            return False
        try:
            signature = frame[::32, ::32].astype(np.int16)
        except Exception:
            return False
        if self._last_signature is not None and self._last_signature.shape == signature.shape:
            diff = float(np.abs(signature - self._last_signature).mean())
            self._static_frames = self._static_frames + 1 if diff < 1.5 else 0
        else:
            self._static_frames = 0
        self._last_signature = signature
        return self._static_frames > 25

    def _profile_delay(self, profile):
        """Пауза между кадрами с учётом возможностей транспорта.

        Задержка профиля подбиралась под медленный BLE. WLED по сети спокойно
        принимает 60 кадров/с, а USB-линк ограничен скоростью порта, поэтому
        для него считаем максимальный FPS из бодов и размера кадра.
        """
        delay = profile.get("delay", 0.1)
        if self._strip_mode == "wled":
            target_fps = max(10, int(self.config.get("target_fps", 60)))
            delay = min(delay, 1.0 / target_fps)
        elif self._strip_mode == "wled_serial":
            count = max(1, int(self.wled_serial.led_count))
            baud = max(9600, int(getattr(self.wled_serial, "baudrate", 115200) or 115200))
            # 10 бит на байт (старт + 8 данных + стоп), кадр = 6 байт заголовка + 3*N
            max_fps = (baud / 10.0) / (6 + 3 * count)
            if max_fps > 0:
                delay = max(delay, 1.0 / min(60.0, max_fps))
        return max(0.001, float(delay))

    def _with_led_offset(self, pixels):
        """Добавляет в начало кадра чёрные диоды, которые пользователь пропустил."""
        offset = max(0, int(self.config.get("led_start_offset", 0)))
        if offset <= 0:
            return list(pixels)
        return [(0, 0, 0)] * offset + list(pixels)

    async def _send_pixel_frame(self, frame, clip, fallback_color,
                                audio_active, audio_cfg, audio_mode, state):
        """Считает и отправляет цвет каждого диода отдельно (если есть маппинг).

        clip — (top, bottom, left, right) разрешённой области: пользовательский
        HUD плюс автоматически найденные чёрные полосы.
        """
        target = self.wled if self._strip_mode == "wled" else self.wled_serial
        if not self.strip_mapper.led_boxes:
            target.send_single_color(*fallback_color)
            return

        sampling_mode = self.config.get("led_sampling_mode", "average")
        linear = self.config.get("filters", {}).get("linear_average", True)
        pixels = self.strip_mapper.get_all_leds_colors(
            frame, mode=sampling_mode, clip_rect=clip, linear=linear
        )
        pixels = self._smooth_pixel_array(pixels)
        # Тот же цветовой пайплайн, что и для одиночного цвета (HSV, яркость,
        # калибровка, гамма) - раньше здесь уходил «сырой» цвет экрана.
        # Ограничение по току применяем в самом конце, после всех смешиваний.
        pixels = process_pixel_frame(pixels, self.config, apply_power=False)

        if audio_active and audio_mode in ("hybrid", "flash"):
            pixels = self._apply_audio_modulation_to_pixels(pixels, audio_cfg, audio_mode, state)

        # Пространственное сглаживание вдоль ленты: убирает резкие цветные
        # «ступеньки» между соседними диодами
        blend = self.config.get("filters", {}).get("neighbor_blend", 0.35)
        if blend:
            pixels = apply_neighbor_blend(pixels, blend)

        pixels = limit_power(pixels, self.config)

        # Затухание на паузе
        if self._fade < 0.995:
            pixels = [(int(r * self._fade), int(g * self._fade), int(b * self._fade))
                      for r, g, b in pixels]

        started = time.perf_counter()
        send_pixels = self._with_led_offset(pixels)
        sent = target.send_frame(send_pixels)
        latency = (time.perf_counter() - started) * 1000.0
        self._last_pixels = pixels
        self._current_ma = estimate_current_ma(send_pixels, self.config)
        self._note_frame(sent, latency)

        now_p = time.monotonic()
        if now_p - self._last_strip_preview_emit > self._strip_preview_interval:
            self._last_strip_preview_emit = now_p
            self.strip_pixels_updated.emit(pixels)

    def _note_frame(self, sent, latency_ms=0.0):
        """Учитывает отправленный кадр для статистики в строке состояния."""
        if sent:
            self._sent_packets += 1
            self._frame_stamps.append(time.monotonic())
            self._last_latency_ms = latency_ms
        now = time.monotonic()
        if now - self._last_stats_emit < 0.5:
            return
        self._last_stats_emit = now
        fps = 0.0
        if len(self._frame_stamps) >= 2:
            span = self._frame_stamps[-1] - self._frame_stamps[0]
            if span > 0:
                fps = (len(self._frame_stamps) - 1) / span
        self.stats_update.emit(float(fps), float(self._last_latency_ms),
                               int(self._sent_packets), float(self._current_ma))

    async def _audio_tick(self):
        audio_cfg = self.config.get("audio", {})
        if not audio_cfg.get("enabled", False):
            return 0.05

        state = self.audio.get_state()

        now = time.monotonic()
        if now - self._last_audio_level_emit > AUDIO_LEVEL_INTERVAL:
            self._last_audio_level_emit = now
            self.audio_level_update.emit(state.volume, state.bass, state.mid, state.treble)
            self.beat_update.emit(float(getattr(state, "bpm", 0.0)))

        mode = audio_cfg.get("mode", "hybrid")
        if mode == "visualizer":
            color = self._compute_visualizer_color(state, audio_cfg)
            self.color_updated.emit(*color)
            r, g, b = color
            await self._send_to_current_strip(r, g, b)
            # BLE не выдержит 33 записи в секунду - для него реже
            return 0.06 if self._strip_mode == "ble" else VISUALIZER_TICK

        if mode == "spectrum":
            await self._send_spectrum(state, audio_cfg)
            return 0.06 if self._strip_mode == "ble" else VISUALIZER_TICK

        # hybrid/flash обрабатываются внутри _screen_tick - тут только уровни
        return None if self.capture_enabled else 0.05

    def _compute_tint_amounts(self, audio_cfg, state):
        """Насколько сильно подмешать тёплый (бас) и холодный (высокие) оттенок."""
        bass_gain = audio_cfg.get("bass_gain", 1.0)
        treble_gain = audio_cfg.get("treble_gain", 1.0)
        tint_strength = max(0.0, min(1.0, audio_cfg.get("tint_strength", 0.35)))

        bass_e = max(0.0, min(1.0, state.bass * bass_gain))
        treble_e = max(0.0, min(1.0, state.treble * treble_gain))

        warm = bass_e * tint_strength
        cool = treble_e * tint_strength

        total = warm + cool
        if total > 0.85:
            scale = 0.85 / total
            warm *= scale
            cool *= scale

        return warm, cool

    def _apply_audio_modulation_to_pixels(self, pixels, audio_cfg, mode, state):
        """То же, что _apply_audio_modulation, но сразу для всего массива пикселей."""
        if not pixels or state is None:
            return pixels

        arr = np.array(pixels, dtype=np.float32)

        if mode == "hybrid":
            warm, cool = self._compute_tint_amounts(audio_cfg, state)
            if warm > 0 or cool > 0:
                keep = 1.0 - warm - cool
                warm_tint = np.array(WARM_TINT, dtype=np.float32)
                cool_tint = np.array(COOL_TINT, dtype=np.float32)
                arr = arr * keep + warm_tint * warm + cool_tint * cool

        elif mode == "flash":
            flash_duration = max(0.02, audio_cfg.get("flash_duration", 0.15))
            elapsed = time.monotonic() - state.last_peak_time
            if elapsed < flash_duration and state.last_peak_strength > 0:
                fade = 1.0 - (elapsed / flash_duration)
                intensity = audio_cfg.get("flash_intensity", 1.0)
                boost = min(1.0, state.last_peak_strength * fade * intensity)
                arr = arr + (255.0 - arr) * boost

        arr = np.clip(arr, 0, 255).astype(np.int32)
        return [(int(r), int(g), int(b)) for r, g, b in arr]

    def _apply_audio_modulation(self, base_color, audio_cfg, mode, state):
        """Модулирует уже посчитанный экранный цвет звуком."""
        r, g, b = base_color

        if mode == "hybrid":
            warm, cool = self._compute_tint_amounts(audio_cfg, state)
            if warm > 0 or cool > 0:
                keep = 1.0 - warm - cool
                r = r * keep + WARM_TINT[0] * warm + COOL_TINT[0] * cool
                g = g * keep + WARM_TINT[1] * warm + COOL_TINT[1] * cool
                b = b * keep + WARM_TINT[2] * warm + COOL_TINT[2] * cool

        elif mode == "flash":
            flash_duration = max(0.02, audio_cfg.get("flash_duration", 0.15))
            elapsed = time.monotonic() - state.last_peak_time
            if elapsed < flash_duration and state.last_peak_strength > 0:
                fade = 1.0 - (elapsed / flash_duration)
                intensity = audio_cfg.get("flash_intensity", 1.0)
                boost = min(1.0, state.last_peak_strength * fade * intensity)
                r = r + (255 - r) * boost
                g = g + (255 - g) * boost
                b = b + (255 - b) * boost

        return (int(round(_clamp255(r))), int(round(_clamp255(g))), int(round(_clamp255(b))))

    async def _send_spectrum(self, state, audio_cfg):
        """Режим «спектроанализатор»: полосы частот раскладываются по диодам."""
        pixels = self._compute_spectrum_pixels(state, audio_cfg)
        if not pixels:
            return
        count = len(pixels)
        avg = tuple(int(sum(p[i] for p in pixels) / count) for i in range(3))
        self.color_updated.emit(*avg)

        if self._strip_mode in ("wled", "wled_serial") and self.strip_mapper.led_boxes:
            target = self.wled if self._strip_mode == "wled" else self.wled_serial
            started = time.perf_counter()
            send_pixels = self._with_led_offset(pixels)
            sent = target.send_frame(send_pixels)
            self._note_frame(sent, (time.perf_counter() - started) * 1000.0)
            self._current_ma = estimate_current_ma(send_pixels, self.config)
            self.strip_pixels_updated.emit(pixels)
        else:
            # BLE умеет только один цвет на всю ленту
            await self._send_to_current_strip(*avg)

    def _compute_spectrum_pixels(self, state, audio_cfg):
        """Раскладывает 32 полосы спектра по диодам ленты."""
        bands = state.spectrum
        if not bands:
            return []
        count = len(self.strip_mapper.led_boxes) or int(self.config.get("wled_led_count", 90))
        count = max(1, count)
        n_bands = len(bands)

        sensitivity = audio_cfg.get("sensitivity", 1.0)
        bass_gain = audio_cfg.get("bass_gain", 1.0)
        mid_gain = audio_cfg.get("mid_gain", 1.0)
        treble_gain = audio_cfg.get("treble_gain", 1.0)

        pixels = []
        for i in range(count):
            position = i / max(1, count - 1)
            band_pos = position * (n_bands - 1)
            lo = int(band_pos)
            hi = min(n_bands - 1, lo + 1)
            frac = band_pos - lo
            value = bands[lo] * (1.0 - frac) + bands[hi] * frac

            if position < 0.5:
                gain = bass_gain + (mid_gain - bass_gain) * (position / 0.5)
            else:
                gain = mid_gain + (treble_gain - mid_gain) * ((position - 0.5) / 0.5)
            value = max(0.0, min(1.0, value * sensitivity * gain))
            pixels.append(self._spectrum_color(position, value))

        # Вспышка на удар: короткий белый подмес поверх всего спектра
        beat = float(getattr(state, "beat", 0.0))
        if beat > 0.05:
            boost = min(0.85, beat * 0.9)
            pixels = [
                (min(255, int(r + (255 - r) * boost)),
                 min(255, int(g + (255 - g) * boost)),
                 min(255, int(b + (255 - b) * boost)))
                for r, g, b in pixels
            ]
        return pixels

    @staticmethod
    def _spectrum_color(position, value):
        """Палитра спектра: низ — красный, середина — зелёный, верх — синий."""
        p = max(0.0, min(1.0, position))
        r = g = b = 0.0
        if p < 0.25:
            t = p / 0.25
            r, g = 255.0, 60.0 + 195.0 * t
        elif p < 0.5:
            t = (p - 0.25) / 0.25
            r, g = 255.0 - 195.0 * t, 255.0
        elif p < 0.75:
            t = (p - 0.5) / 0.25
            r, g, b = 60.0 * (1.0 - t), 255.0, 120.0 * t
        else:
            t = (p - 0.75) / 0.25
            r, g, b = 0.0, 255.0 - 155.0 * t, 120.0 + 135.0 * t

        v = max(0.04, min(1.0, value))
        return (int(r * v), int(g * v), int(b * v))

    def _compute_visualizer_color(self, state, audio_cfg):
        """Полный аудио-визуализатор: цвет целиком из звука, экран не участвует."""
        r = state.bass * 255.0
        g = state.mid * 255.0
        b = state.treble * 255.0

        brightness = 0.3 + 0.7 * state.volume
        r, g, b = r * brightness, g * brightness, b * brightness

        flash_duration = max(0.02, audio_cfg.get("flash_duration", 0.15))
        elapsed = time.monotonic() - state.last_peak_time
        if elapsed < flash_duration and state.last_peak_strength > 0:
            fade = 1.0 - (elapsed / flash_duration)
            boost = min(1.0, state.last_peak_strength * fade)
            r = r + (255 - r) * boost
            g = g + (255 - g) * boost
            b = b + (255 - b) * boost

        calib = self.config.get("calibration", {})
        return apply_calibration(r, g, b, calib)

    def _get_zone_average(self, img, mode):
        """Считает итоговый цвет по зонам режима, с блендингом доминантного цвета."""
        filters = self.config.get("filters", {})
        weight = 0.0
        if filters.get("dominant_color_priority"):
            weight = max(0.0, min(1.0, filters.get("dominant_weight", 0.5)))

        if mode == "edges":
            zone_names = ("top", "bottom", "left", "right")
            avg_fn, dom_fn = get_zone_color, get_zone_dominant_color
        else:
            zone_names = ("top_left", "top_right", "bottom_left", "bottom_right")
            avg_fn, dom_fn = get_quadrant_color, get_quadrant_dominant_color

        colors = []
        for zone in zone_names:
            color = avg_fn(img, zone)
            if weight > 0:
                dominant = dom_fn(img, zone)
                color = tuple(
                    color[i] * (1 - weight) + dominant[i] * weight
                    for i in range(3)
                )
            colors.append(color)

        r, g, b = np.mean(np.array(colors), axis=0)
        return (float(r), float(g), float(b))
