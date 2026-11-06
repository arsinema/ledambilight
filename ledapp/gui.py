"""
Главное окно приложения.
"""

import logging
import queue
import threading
import time

from PyQt5.QtCore import Qt, QTimer, QRectF, QTime, QUrl, pyqtSignal
from PyQt5.QtGui import (
    QImage, QPixmap, QPainter, QColor, QPen, QIcon, QKeySequence,
    QDesktopServices,
)
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTabWidget, QLabel, QPushButton, QListWidget, QListWidgetItem,
    QSlider, QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox,
    QLineEdit, QPlainTextEdit, QMessageBox, QGroupBox, QProgressBar,
    QApplication, QScrollArea, QSystemTrayIcon, QMenu, QAction, QDialog,
    QTimeEdit, QShortcut
)

from .config import load_config, save_config
from .worker import LedWorker
from .audio_engine import list_output_devices, is_available as audio_is_available
from .updates import check_for_update
from . import autostart, __version__

log = logging.getLogger(__name__)


# Готовые сцены. Применяются к виджетам, поэтому интерфейс сразу показывает
# новые значения, а обработчики сами обновляют конфиг.
SCENE_PRESETS = {
    "Кино": {
        "max_scale": 0.65, "min_brightness": 0,
        "smoothing_min": 0.10, "smoothing_max": 0.40,
        "dominant_weight": 0.55, "audio": "hybrid",
    },
    "Игра": {
        "max_scale": 0.95, "min_brightness": 10,
        "smoothing_min": 0.25, "smoothing_max": 0.70,
        "dominant_weight": 0.65, "audio": "flash",
    },
    "Ночь": {
        "max_scale": 0.30, "min_brightness": 0,
        "smoothing_min": 0.08, "smoothing_max": 0.30,
        "dominant_weight": 0.50, "audio": "hybrid",
    },
    "Яркий день": {
        "max_scale": 1.30, "min_brightness": 40,
        "smoothing_min": 0.20, "smoothing_max": 0.60,
        "dominant_weight": 0.70, "audio": "hybrid",
    },
}


def _slider_row(form, label_text, minv, maxv, initial, on_change, suffix=""):
    """Добавляет в QFormLayout строку 'слайдер + значение', возвращает сам слайдер."""
    row = QHBoxLayout()
    slider = QSlider(Qt.Horizontal)
    slider.setMinimum(minv)
    slider.setMaximum(maxv)
    slider.setValue(initial)

    value_label = QLabel(f"{initial}{suffix}")
    value_label.setFixedWidth(50)

    def _changed(v):
        value_label.setText(f"{v}{suffix}")
        on_change(v)

    slider.valueChanged.connect(_changed)

    row.addWidget(slider)
    row.addWidget(value_label)
    container = QWidget()
    container.setLayout(row)
    form.addRow(label_text, container)
    return slider


class TestPatternWindow(QWidget):
    """Полноэкранная схема раскладки: на мониторе видны позиции всех диодов.

    Запускается вместе с тест-развёрткой: загорелся диод №37 — смотришь, где
    на схеме нарисована точка 37. Так сразу видно, совпадает ли маппинг с
    тем, как лента наклеена физически.
    """

    def __init__(self, data, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        self._data = data or {}
        self.setStyleSheet("background-color: black;")
        self.setCursor(Qt.BlankCursor)
        self.setWindowTitle("Схема раскладки ленты")

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Space, Qt.Key_Return):
            self.close()

    def mousePressEvent(self, event):
        self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        painter.setRenderHint(QPainter.Antialiasing)

        boxes = self._data.get("boxes") or []
        if not boxes:
            painter.setPen(QColor(220, 220, 220))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Маппинг не построен — нарисуй путь ленты и нажми «Построить маппинг»")
            painter.end()
            return

        width, height = self.width(), self.height()
        screen_w = max(1, self._data.get("screen_width") or width)
        screen_h = max(1, self._data.get("screen_height") or height)

        # рамка экрана
        painter.setPen(QPen(QColor(60, 60, 70), 2))
        painter.drawRect(self.rect().adjusted(2, 2, -2, -2))

        for index, (y0, y1, x0, x1) in enumerate(boxes):
            cx = ((x0 + x1) / 2.0) / screen_w * width
            cy = ((y0 + y1) / 2.0) / screen_h * height
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 200, 110, 150))
            painter.drawEllipse(QRectF(cx - 4, cy - 4, 8, 8))
            if index % 10 == 0:
                painter.setPen(QColor(255, 220, 0))
                painter.setBrush(Qt.NoBrush)
                painter.drawText(QRectF(cx + 8, cy - 16, 80, 22),
                                 Qt.AlignLeft | Qt.AlignVCenter, str(index))

        painter.setPen(QColor(150, 150, 160))
        painter.drawText(20, height - 24,
                         "Esc — закрыть. Запущена развёртка: сверяй номер горящего диода с точкой на схеме.")
        painter.end()


def build_loop_path(width, height, loops=2, start_corner="tl", clockwise=True,
                    inset_pct=3.0, gap_pct=12.0):
    """Готовая раскладка «витками» — для ленты на задней крышке монитора.

    Возвращает полилинию в координатах экрана. Каждый следующий виток
    сдвинут внутрь на gap_pct, а между витками остаётся короткий переход —
    ровно так лента и ложится, когда её клеят кольцами.

    Args:
        loops: сколько витков (1 — просто по периметру).
        start_corner: "tl" | "tr" | "br" | "bl" — откуда начинать.
        clockwise: направление обхода.
        inset_pct: отступ первого витка от края, % экрана.
        gap_pct: расстояние между витками, % экрана.
    """
    points = []
    order_base = ["tl", "tr", "br", "bl"]
    for index in range(max(1, int(loops))):
        margin = (float(inset_pct) + float(gap_pct) * index) / 100.0
        left = width * margin
        right = width * (1.0 - margin)
        top = height * margin
        bottom = height * (1.0 - margin)
        if right - left < 20 or bottom - top < 20:
            break

        corners = {
            "tl": (left, top),
            "tr": (right, top),
            "br": (right, bottom),
            "bl": (left, bottom),
        }
        order = list(order_base) if clockwise else ["tl", "bl", "br", "tr"]
        start_index = order.index(start_corner) if start_corner in order else 0
        order = order[start_index:] + order[:start_index]

        points.extend(corners[name] for name in order)
        points.append(corners[order[0]])  # замыкаем виток

    return points


class MappingWizard(QDialog):
    """Раскладка «по развёртке» — самый простой способ настроить маппинг.

    Приложение по очереди зажигает диоды (0, 10, 20, ...), пользователь щёлкает
    по схеме монитора там, где видит этот свет. По отмеченным точкам строится
    путь ленты: рисовать ничего не нужно, направление и начало определяются
    автоматически — первый же зажжённый диод задаёт старт.
    """

    def __init__(self, parent, worker, led_count, screen_w, screen_h, background=None):
        super().__init__(parent)
        self.setWindowTitle("Раскладка по развёртке")
        self.setModal(True)
        self._worker = worker
        self._led_count = max(2, int(led_count))
        self._screen_w = max(1, int(screen_w))
        self._screen_h = max(1, int(screen_h))
        self._background = background

        self._canvas_w = 640
        self._canvas_h = max(180, int(self._canvas_w * self._screen_h / self._screen_w))

        # Шаг между опорными точками: около десяти кликов на всю ленту
        step = max(5, round(self._led_count / 9))
        indices = list(range(0, self._led_count, step))
        if indices[-1] != self._led_count - 1:
            indices.append(self._led_count - 1)
        self._indices = indices

        self._points = []     # (index, x_экран, y_экран)
        self._pos = 0
        self.result_points = []

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Приложение будет зажигать диоды по одному. Щёлкай по схеме там,\n"
            "где видишь этот свет — этого достаточно, путь построится сам."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #ffd75e; font-weight: 600;")
        layout.addWidget(self._hint)

        self._canvas = QLabel()
        self._canvas.setFixedSize(self._canvas_w, self._canvas_h)
        self._canvas.setStyleSheet("background-color: #111; border: 2px solid #444;")
        self._canvas.setCursor(Qt.CrossCursor)
        self._canvas.mousePressEvent = self._on_click
        layout.addWidget(self._canvas, 0, Qt.AlignHCenter)

        buttons = QHBoxLayout()
        self._back_btn = QPushButton("Назад")
        self._back_btn.clicked.connect(self._on_back)
        self._skip_btn = QPushButton("Пропустить")
        self._skip_btn.clicked.connect(self._on_skip)
        buttons.addWidget(self._back_btn)
        buttons.addWidget(self._skip_btn)
        buttons.addStretch()
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        self._finish_btn = QPushButton("Готово")
        self._finish_btn.setObjectName("primaryAction")
        self._finish_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(self._finish_btn)
        layout.addLayout(buttons)

        self._worker.set_mapping_mode(True)
        self._highlight_current()

    # ------------------------------------------------------------------

    def _highlight_current(self):
        if self._pos < len(self._indices):
            self._worker.highlight_led(self._indices[self._pos])
        else:
            self._worker.highlight_led(-1)
        self._update_ui()

    def _on_click(self, event):
        if self._pos >= len(self._indices):
            return
        x = event.x() / self._canvas_w * self._screen_w
        y = event.y() / self._canvas_h * self._screen_h
        self._points.append((self._indices[self._pos], x, y))
        self._pos += 1
        self._highlight_current()

    def _on_skip(self):
        if self._pos < len(self._indices):
            self._pos += 1
            self._highlight_current()

    def _on_back(self):
        if self._pos > 0:
            self._pos -= 1
            if self._points and self._points[-1][0] == self._indices[self._pos]:
                self._points.pop()
            self._highlight_current()

    def _update_ui(self):
        total = len(self._indices)
        if self._pos < total:
            self._hint.setText(
                f"Шаг {self._pos + 1} из {total}: зажёг диод №{self._indices[self._pos]}. "
                "Щёлкни по схеме там, где видишь свет."
            )
        else:
            self._hint.setText(
                "Все точки отмечены. Нажми «Готово» — путь и диоды построятся сами."
            )
        self._finish_btn.setEnabled(self._pos >= total or len(self._points) >= 3)
        self._back_btn.setEnabled(self._pos > 0)
        self._skip_btn.setEnabled(self._pos < total)
        self._paint()

    def _paint(self):
        pixmap = QPixmap(self._canvas_w, self._canvas_h)
        pixmap.fill(QColor(14, 14, 16))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        if self._background is not None:
            painter.drawPixmap(0, 0, self._background.scaled(
                self._canvas_w, self._canvas_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            painter.fillRect(0, 0, self._canvas_w, self._canvas_h, QColor(0, 0, 0, 120))

        painter.setPen(QPen(QColor(90, 90, 100), 1))
        painter.drawRect(0, 0, self._canvas_w - 1, self._canvas_h - 1)

        def to_canvas(x, y):
            return (x / self._screen_w * self._canvas_w,
                    y / self._screen_h * self._canvas_h)

        if len(self._points) > 1:
            painter.setPen(QPen(QColor(60, 170, 255, 180), 2))
            for i in range(len(self._points) - 1):
                x1, y1 = to_canvas(self._points[i][1], self._points[i][2])
                x2, y2 = to_canvas(self._points[i + 1][1], self._points[i + 1][2])
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        for index, x, y in self._points:
            cx, cy = to_canvas(x, y)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(60, 220, 120))
            painter.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
            painter.setPen(QColor(255, 220, 0))
            painter.drawText(QRectF(cx + 7, cy - 16, 60, 20),
                             Qt.AlignLeft | Qt.AlignVCenter, str(index))

        painter.end()
        self._canvas.setPixmap(pixmap)

    # ------------------------------------------------------------------

    def accept(self):
        self._worker.set_mapping_mode(False)
        ordered = [point for point in self._points]
        if len(ordered) >= 2:
            self.result_points = [(x, y) for _, x, y in ordered]
        else:
            self.result_points = []
        super().accept()

    def reject(self):
        self._worker.set_mapping_mode(False)
        self._worker.highlight_led(-1)
        self.result_points = []
        super().reject()


class MainWindow(QMainWindow):
    update_checked = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("LED Ambilight")
        self.resize(780, 640)
        self.setMinimumSize(480, 360)  # разумный минимум - меньше уже неюзабельно,
                                        # но при этом окно свободно уменьшается/растягивается

        self.config = load_config()

        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_and_push_config)

        self._sleep_countdown_timer = QTimer(self)
        self._sleep_countdown_timer.setInterval(1000)
        self._sleep_countdown_timer.timeout.connect(self._on_sleep_timer_tick)
        self._sleep_remaining_seconds = 0

        # Требует уже созданного _save_timer (может захотеть сохранить конфиг)
        self._detect_screen_size()

        self.worker = LedWorker(self.config)
        self.worker.status_changed.connect(self.on_status)
        self.worker.connected_changed.connect(self.on_connected)
        self.worker.capture_state_changed.connect(self.on_capture_state_changed)
        self.worker.color_updated.connect(self.on_color_updated)
        self.worker.profile_changed.connect(self.on_profile_changed)
        self.worker.devices_found.connect(self.on_devices_found)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.frame_preview.connect(self.on_frame_preview)
        self.worker.mapping_loaded.connect(self._on_mapping_loaded)
        self.worker.audio_level_update.connect(self.on_audio_level_update)
        self.worker.strip_pixels_updated.connect(self.on_strip_pixels_updated)
        self.worker.now_playing_changed.connect(self.on_now_playing_changed)
        self.worker.stats_update.connect(self.on_stats_update)
        self.worker.mapping_boxes.connect(self.on_mapping_boxes)
        self.worker.diagnostics_ready.connect(self.on_diagnostics)
        self.worker.process_changed.connect(self.on_process_changed)
        self.worker.beat_update.connect(self.on_beat_update)
        self.worker.start()

        self._update_check_running = False
        self.update_checked.connect(self._on_update_checked)

        self.current_profile = "desktop"
        self._really_quit = False
        self._capture_running = False
        self._last_error_text = ""
        self._last_error_time = 0.0
        self._scene_sliders = {}

        self._build_ui()
        self._build_tray_icon()

        # Ctrl+Q — привычный способ закрыть приложение из окна, когда
        # крестик настроен на сворачивание в трей.
        self._quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._quit_shortcut.activated.connect(self._quit_from_tray)

        self._auto_paused = False
        self._auto_pause_timer = QTimer(self)
        self._auto_pause_timer.setInterval(3000)
        self._auto_pause_timer.timeout.connect(self._check_auto_pause)
        self._auto_pause_timer.start()

        self.hotkey_filter = None
        self._setup_hotkeys()

        self._remote = None
        self._remote_queue = queue.Queue()
        self._remote_state = {}
        self._connected_flag = False
        self._last_scene_name = ""
        self._mqtt = None
        self._start_remote()
        self._start_mqtt()
        self._remote_timer = QTimer(self)
        self._remote_timer.setInterval(100)
        self._remote_timer.timeout.connect(self._drain_remote_queue)
        self._remote_timer.start()
        self._remote_state_timer = QTimer(self)
        self._remote_state_timer.setInterval(1000)
        self._remote_state_timer.timeout.connect(self._refresh_remote_state)
        self._remote_state_timer.start()

        self._schedule_timer = QTimer(self)
        self._schedule_timer.setInterval(30000)
        self._schedule_timer.timeout.connect(self._check_schedule)
        self._schedule_timer.start()
        QTimer.singleShot(3000, self._check_schedule)

        QTimer.singleShot(900, self._maybe_show_welcome)

        if self.config.get("updates", {}).get("check_on_start", True):
            # Тихо, через 5 секунд после старта: не задерживаем запуск и не
            # показываем модальное окно, если обновления нет.
            QTimer.singleShot(5000, lambda: self._on_check_updates(silent=True))

        if self.config.get("auto_connect", True):
            # небольшая задержка, чтобы фоновый поток успел стартовать
            QTimer.singleShot(800, self._auto_connect_on_startup)

    def _setup_hotkeys(self):
        """Включает глобальные горячие клавиши, если они разрешены в настройках."""
        if not self.config.get("hotkeys", {}).get("enabled", True):
            return
        try:
            from .hotkeys import HotkeyFilter
            self.hotkey_filter = HotkeyFilter(self)
            self.hotkey_filter.triggered.connect(self._on_hotkey)
            QApplication.instance().installNativeEventFilter(self.hotkey_filter)
            self.hotkey_filter.set_enabled(True)
        except Exception:
            log.exception("Не удалось включить горячие клавиши")
            self.hotkey_filter = None

    def _on_hotkey(self, action):
        """Реакция на глобальную горячую клавишу."""
        try:
            if action == "toggle_capture":
                self._on_tray_toggle_capture()
            elif action == "brightness_up":
                self._nudge_brightness(0.05)
            elif action == "brightness_down":
                self._nudge_brightness(-0.05)
            elif action == "blackout":
                self.worker.blackout()
            elif action == "diagnostics":
                self.worker.run_diagnostics()
        except Exception:
            log.exception("Ошибка обработки горячей клавиши %s", action)

    def _nudge_brightness(self, delta):
        """Меняет общий множитель яркости на небольшую величину."""
        current = float(self.config.get("brightness", {}).get("max_scale", 1.0))
        value = max(0.1, min(1.5, round(current + delta, 2)))
        if abs(value - current) < 1e-3:
            return
        self._set_nested("brightness", "max_scale", value)
        slider = getattr(self, "brightness_slider", None)
        if slider is not None:
            slider.blockSignals(True)
            slider.setValue(int(round(value * 100)))
            slider.blockSignals(False)
        self.status_label.setText(f"Яркость: {int(round(value * 100))}%")

    def _check_auto_pause(self):
        """Пауза подсветки, когда экран заблокирован или пользователь отошёл."""
        try:
            self._auto_pause_tick()
        except Exception:
            log.debug("Ошибка автопаузы", exc_info=True)

    def _auto_pause_tick(self):
        cfg = self.config.get("auto_pause", {})
        if not cfg.get("enabled", True):
            if self._auto_paused:
                self._resume_after_pause()
            return

        from .win_utils import idle_seconds, is_session_locked

        reason = None
        if cfg.get("pause_on_lock", True) and is_session_locked():
            reason = "экран заблокирован"
        else:
            minutes = int(cfg.get("idle_minutes", 5) or 0)
            if minutes > 0 and idle_seconds() >= minutes * 60:
                # Кино смотрят, не трогая мышь: Windows считает это простоем,
                # но подсветку гасить нельзя, пока картинка меняется
                if not self.worker.is_screen_moving():
                    reason = "нет активности"

        if reason and self._capture_running and not self._auto_paused:
            self._auto_paused = True
            self._on_stop_capture_clicked()
            self.status_label.setText(f"Пауза: {reason}")
            log.info("Автопауза: %s", reason)
        elif not reason and self._auto_paused:
            self._resume_after_pause()

    def _resume_after_pause(self):
        self._auto_paused = False
        if not self._capture_running:
            self._on_start_capture_clicked()
        self.status_label.setText("Подсветка возобновлена")

    def _detect_screen_size(self):
        """Подхватывает реальное разрешение основного монитора.

        Раньше screen_width/screen_height были жёстко 1920x1080 из конфига:
        на другом мониторе холст разметки ленты имел неверные пропорции, а
        диоды вставали не туда."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        ratio = screen.devicePixelRatio() or 1.0
        size = screen.size()
        width = int(round(size.width() * ratio))
        height = int(round(size.height() * ratio))
        if width <= 0 or height <= 0:
            return
        old = (self.config.get("screen_width"), self.config.get("screen_height"))
        if old != (width, height):
            log.info("Разрешение экрана: %sx%s (было %sx%s)", width, height, old[0], old[1])
            self.config["screen_width"] = width
            self.config["screen_height"] = height
            self._queue_save()

    def _auto_connect_on_startup(self):
        """Автоподключение к последнему устройству - для всех трёх режимов."""
        mode = self.config.get("strip_mode", "ble")
        if mode == "wled":
            host = self.config.get("wled_host")
            if host:
                self.status_label.setText("Автоподключение к WLED...")
                self.worker.connect_wled(host, int(self.config.get("wled_port", 21324)))
            return
        if mode == "wled_serial":
            port = self.config.get("wled_serial_port")
            if port:
                self.status_label.setText("Автоподключение по USB...")
                self.worker.connect_wled_serial(
                    port, int(self.config.get("wled_serial_baud", 115200))
                )
            return
        address = self.config.get("device_address")
        if address:
            self.status_label.setText("Автоподключение...")
            self.worker.connect_device(address)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _wrap_scroll(self, widget):
        """Оборачивает содержимое вкладки в QScrollArea. Если окно уменьшили
        сильнее, чем нужно виджетам с фиксированным размером (холст конфигурации
        ленты, превью захвата) - появится скролл вместо того, чтобы ломать
        макет или мешать окну нормально сжиматься."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        scroll.setFrameShape(QScrollArea.NoFrame)
        return scroll

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        top_row = QHBoxLayout()

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #6b6c72; font-size: 14px;")
        top_row.addWidget(self.status_dot)

        self.status_label = QLabel("Не подключено")
        self.status_label.setObjectName("statusHeader")
        top_row.addWidget(self.status_label)

        top_row.addSpacing(16)
        top_row.addWidget(QLabel("Устройство:"))
        _device_labels = {
            "ble": "ELK-BLEDOM (BLE)",
            "wled": "WLED (Wi-Fi)",
            "wled_serial": "WLED (USB-кабель)",
        }
        self.device_mode_label = QLabel(_device_labels.get(self.config.get("strip_mode", "ble"), "ELK-BLEDOM (BLE)"))
        self.device_mode_label.setStyleSheet("color: #8fb8ff;")
        top_row.addWidget(self.device_mode_label)

        top_row.addStretch()

        top_row.addWidget(QLabel("Профиль:"))
        self.profile_label = QLabel("-")
        top_row.addWidget(self.profile_label)
        top_row.addSpacing(20)

        top_row.addWidget(QLabel("Цвет:"))
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(36, 22)
        self.color_swatch.setStyleSheet("background-color: black; border: 1px solid #666; border-radius: 4px;")
        top_row.addWidget(self.color_swatch)

        top_row.addSpacing(16)
        self.stats_label = QLabel("—")
        self.stats_label.setStyleSheet("color: #8a8b93;")
        self.stats_label.setToolTip(
            "Частота кадров · задержка отправки · всего пакетов · оценка потребления"
        )
        top_row.addWidget(self.stats_label)

        root.addLayout(top_row)

        self.mini_strip_preview = QLabel()
        self.mini_strip_preview.setFixedHeight(18)
        self.mini_strip_preview.setStyleSheet("background-color: #000; border-radius: 3px;")
        root.addWidget(self.mini_strip_preview)
        self._mini_preview_pixels = None
        self._mini_preview_flat_color = (0, 0, 0)
        self._mini_preview_mode = "flat"
        self._last_pixels_update_time = 0.0

        tabs = QTabWidget()
        root.addWidget(tabs)

        tabs.addTab(self._wrap_scroll(self._build_connection_tab()), "Подключение")
        preview_tab_index = tabs.addTab(self._wrap_scroll(self._build_preview_tab()), "Просмотр захвата")
        tabs.addTab(self._wrap_scroll(self._build_calibration_tab()), "Калибровка")
        tabs.addTab(self._wrap_scroll(self._build_hud_tab()), "Зоны / HUD")
        tabs.addTab(self._wrap_scroll(self._build_filters_tab()), "Фильтры")
        tabs.addTab(self._wrap_scroll(self._build_audio_tab()), "Звук")
        tabs.addTab(self._wrap_scroll(self._build_strip_config_tab()), "Конфигурация ленты")
        tabs.addTab(self._wrap_scroll(self._build_standby_tab()), "Дежурный режим")
        tabs.addTab(self._wrap_scroll(self._build_wifi_setup_tab()), "Настройка WiFi (ESP)")
        tabs.addTab(self._wrap_scroll(self._build_profiles_tab()), "Профили")
        tabs.addTab(self._wrap_scroll(self._build_system_tab()), "Система")

        tabs.currentChanged.connect(
            lambda idx: self.worker.set_preview_active(idx == preview_tab_index)
        )
        # по умолчанию открыта вкладка "Подключение" (индекс 0) — превью не активно,
        # пока пользователь сам не откроет вкладку "Просмотр захвата"
        self.worker.set_preview_active(tabs.currentIndex() == preview_tab_index)

        # Нижняя строка: версия слева, явный «Выход» справа. Крестик окна
        # по умолчанию только прячет приложение в трей, поэтому нужна
        # кнопка, которая завершает программу по-настоящему.
        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        self.version_label = QLabel(f"LED Ambilight {__version__}")
        self.version_label.setStyleSheet("color: #6b6c72; font-size: 11px;")
        self.version_label.setToolTip("Версия приложения")
        footer.addWidget(self.version_label)
        footer.addStretch()

        self.exit_btn = QPushButton("Выход")
        self.exit_btn.setObjectName("exitAction")
        self.exit_btn.setToolTip(
            "Завершить приложение. Крестик в углу окна только сворачивает его в трей."
        )
        self.exit_btn.clicked.connect(self._quit_from_tray)
        footer.addWidget(self.exit_btn)
        root.addLayout(footer)

    # -- Вкладка дежурного режима (дыхание в простое) --

    def _build_standby_tab(self):
        from .now_playing import NowPlayingMonitor
        from .weather import HAS_REQUESTS

        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel(
            "Когда Ambilight не запущен (и звук не активен), лента не гаснет совсем,\n"
            "а медленно и мягко «дышит» выбранным цветом - как ночник."
        ))

        standby = self.config.get("standby", {})

        enable_cb = QCheckBox("Включить дежурное дыхание")
        enable_cb.setChecked(standby.get("enabled", False))
        enable_cb.stateChanged.connect(lambda s: self._set_nested("standby", "enabled", s == Qt.Checked))
        layout.addWidget(enable_cb)

        source_box = QGroupBox("Источник цвета")
        source_form = QFormLayout(source_box)

        source_combo = QComboBox()
        source_combo.addItem("Постоянный цвет", "fixed")
        source_combo.addItem("Погода за окном", "weather")
        source_combo.addItem("Обложка трека (Spotify и др.)", "spotify")
        idx = source_combo.findData(standby.get("source", "fixed"))
        source_combo.setCurrentIndex(max(0, idx))
        source_combo.currentIndexChanged.connect(
            lambda i: self._on_standby_source_changed(source_combo.itemData(i))
        )
        self.standby_source_combo = source_combo
        source_form.addRow("Источник", source_combo)

        # -- Постоянный цвет --
        fixed_row = QHBoxLayout()
        fc = standby.get("fixed_color", [80, 60, 180])
        self._standby_fixed_color = QColor(fc[0], fc[1], fc[2])
        fixed_color_btn = QPushButton()
        fixed_color_btn.setFixedWidth(60)
        fixed_color_btn.setStyleSheet(f"background-color: rgb({fc[0]},{fc[1]},{fc[2]});")
        fixed_color_btn.clicked.connect(lambda: self._on_pick_standby_color(fixed_color_btn))
        self.standby_fixed_color_btn = fixed_color_btn
        fixed_row.addWidget(fixed_color_btn)
        fixed_row.addStretch()
        source_form.addRow("Цвет", fixed_row)

        # -- Погода --
        city_input = QLineEdit(standby.get("weather_city", ""))
        city_input.setPlaceholderText("Например: Minsk (латиницей)")
        city_input.editingFinished.connect(
            lambda: self._set_nested("standby", "weather_city", city_input.text().strip())
        )
        self.standby_city_input = city_input
        source_form.addRow("Город", city_input)
        if not HAS_REQUESTS:
            source_form.addRow(QLabel("⚠ Библиотека 'requests' недоступна - погода не будет работать"))

        # -- Spotify / now playing --
        self.standby_now_playing_label = QLabel("Сейчас играет: -")
        source_form.addRow(self.standby_now_playing_label)
        if not NowPlayingMonitor.is_available():
            source_form.addRow(QLabel(
                "⚠ Для этого источника нужны пакеты 'winsdk' и 'Pillow'\n"
                "(pip install winsdk Pillow). Работает только на Windows 10/11\n"
                "и только если плеер (например Spotify) поддерживает\n"
                "системное отображение 'сейчас играет'."
            ))

        layout.addWidget(source_box)

        # --- режим лампы ---
        lamp_box = QGroupBox("Режим лампы (лента как светильник)")
        lamp_form = QFormLayout(lamp_box)
        lamp_cfg = self.config.get("lamp", {})
        lamp_cb = QCheckBox("Включить: ровный белый свет вместо подсветки экрана")
        lamp_cb.setChecked(lamp_cfg.get("enabled", False))
        lamp_cb.stateChanged.connect(
            lambda s: self._set_nested("lamp", "enabled", s == Qt.Checked)
        )
        lamp_form.addRow(lamp_cb)
        _slider_row(
            lamp_form, "Температура (тёплый → холодный)", 0, 100,
            int(float(lamp_cfg.get("temperature", 0.35)) * 100),
            lambda v: self._set_nested("lamp", "temperature", v / 100.0), "%"
        )
        _slider_row(
            lamp_form, "Яркость лампы", 5, 100,
            int(float(lamp_cfg.get("brightness", 0.7)) * 100),
            lambda v: self._set_nested("lamp", "brightness", v / 100.0), "%"
        )
        layout.addWidget(lamp_box)

        speed_box = QGroupBox("Скорость дыхания")
        speed_form = QFormLayout(speed_box)
        _slider_row(
            speed_form, "Скорость", 5, 100, int(standby.get("speed", 0.35) * 100),
            lambda v: self._set_nested("standby", "speed", v / 100.0), "%"
        )
        layout.addWidget(speed_box)

        layout.addStretch()
        self._on_standby_source_changed(standby.get("source", "fixed"), _init=True)
        return w

    def _on_standby_source_changed(self, source, _init=False):
        if not _init:
            self._set_nested("standby", "source", source)
        # Показываем только то, что реально относится к выбранному источнику -
        # та же идея, что и с регуляторами на вкладке "Звук"
        self.standby_fixed_color_btn.setEnabled(source == "fixed")
        self.standby_city_input.setEnabled(source == "weather")

    def _on_pick_standby_color(self, btn):
        from PyQt5.QtWidgets import QColorDialog
        color = QColorDialog.getColor(self._standby_fixed_color, self, "Цвет дежурного дыхания")
        if color.isValid():
            self._standby_fixed_color = color
            btn.setStyleSheet(f"background-color: rgb({color.red()},{color.green()},{color.blue()});")
            self._set_nested("standby", "fixed_color", [color.red(), color.green(), color.blue()])

    # -- Вкладка настройки WiFi для ESP --

    def _build_wifi_setup_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel(
            "Первое подключение платы к твоей домашней Wi-Fi.\n"
            "WLED намеренно не позволяет передать логин/пароль через API,\n"
            "поэтому настройка идёт через собственную точку доступа платы."
        ))

        steps_box = QGroupBox("Шаги")
        steps_layout = QVBoxLayout(steps_box)
        for i, step in enumerate((
            "Прошей плату через install.wled.me (Chrome/Edge), если ещё не делал",
            "На телефоне/ПК подключись к Wi-Fi «WLED-XXXX» (без пароля)",
            "Нажми кнопку ниже — откроется страница настроек платы",
            "Открой Settings → WiFi Setup, введи имя и пароль домашней сети, сохрани",
            "Подожди 10-20 секунд, пока плата перезагрузится и подключится",
            "Вернись на вкладку «Подключение», проверь IP и нажми «Подключить WLED»",
        ), 1):
            steps_layout.addWidget(QLabel(f"{i}. {step}"))
        layout.addWidget(steps_box)

        open_box = QGroupBox("Открыть веб-интерфейс платы")
        open_form = QFormLayout(open_box)

        host_row = QHBoxLayout()
        host_row.addWidget(QLabel("IP платы:"))
        self.wifi_setup_host_input = QLineEdit("4.3.2.1")
        self.wifi_setup_host_input.setToolTip(
            "4.3.2.1 — адрес точки доступа WLED по умолчанию. "
            "Если плата уже в домашней сети — впиши её обычный IP."
        )
        host_row.addWidget(self.wifi_setup_host_input)
        open_form.addRow(host_row)

        open_btn = QPushButton("Открыть настройки WiFi")
        open_btn.setObjectName("primaryAction")
        open_btn.clicked.connect(self._on_open_wled_settings)
        open_form.addRow("", open_btn)

        open_form.addRow(QLabel(
            "Там же можно посмотреть версию прошивки, число диодов\n"
            "и настройки сегментов — полезно сверить с вкладкой «Конфигурация ленты»."
        ))

        layout.addWidget(open_box)
        layout.addStretch()
        return w

    def _on_open_wled_settings(self):
        """Открывает страницу настроек WLED в системном браузере."""
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices

        host = self.wifi_setup_host_input.text().strip() or "4.3.2.1"
        url = QUrl(f"http://{host}/settings/wifi")
        if not QDesktopServices.openUrl(url):
            QMessageBox.warning(self, "Браузер", f"Не удалось открыть {url.toString()}")

    # -- Вкладка подключения --

    def _build_connection_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        scan_row = QHBoxLayout()
        self.scan_btn = QPushButton("Сканировать устройства")
        self.scan_btn.clicked.connect(self.worker.scan_devices)
        scan_row.addWidget(self.scan_btn)
        layout.addLayout(scan_row)

        self.device_list = QListWidget()
        layout.addWidget(self.device_list)

        conn_row = QHBoxLayout()
        self.connect_btn = QPushButton("Подключиться")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.disconnect_btn = QPushButton("Отключиться")
        self.disconnect_btn.clicked.connect(self.worker.disconnect_device)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.disconnect_btn)
        layout.addLayout(conn_row)

        manual_row = QHBoxLayout()
        manual_row.addWidget(QLabel("MAC-адрес вручную:"))
        self.manual_address = QLineEdit(self.config.get("device_address") or "")
        manual_row.addWidget(self.manual_address)
        layout.addLayout(manual_row)

        layout.addWidget(QLabel(""))

        strip_box = QGroupBox("Режим ленты")
        strip_form = QFormLayout(strip_box)

        strip_mode_combo = QComboBox()
        strip_mode_combo.addItem("ELK-BLEDOM (BLE)", "ble")
        strip_mode_combo.addItem("WLED (Wi-Fi ESP32/ESP8266)", "wled")
        strip_mode_combo.addItem("WLED (USB-кабель, Adalight)", "wled_serial")
        current_mode = self.config.get("strip_mode", "ble")
        idx = strip_mode_combo.findData(current_mode)
        strip_mode_combo.setCurrentIndex(max(0, idx))
        strip_mode_combo.currentIndexChanged.connect(
            lambda i: self._on_strip_mode_changed(strip_mode_combo.itemData(i))
        )
        self.strip_mode_combo = strip_mode_combo
        strip_form.addRow("Выбор устройства:", strip_mode_combo)

        wled_host_row = QHBoxLayout()
        wled_host_row.addWidget(QLabel("IP WLED:"))
        self.wled_host_input = QLineEdit(self.config.get("wled_host", "192.168.1.100"))
        wled_host_row.addWidget(self.wled_host_input)
        wled_port_row = QHBoxLayout()
        wled_port_row.addWidget(QLabel("Порт:"))
        self.wled_port_input = QLineEdit(str(self.config.get("wled_port", 21324)))
        self.wled_port_input.setMaximumWidth(80)
        wled_port_row.addWidget(self.wled_port_input)
        wled_port_row.addStretch()

        wled_connect_btn = QPushButton("Подключить WLED (Wi-Fi)")
        wled_connect_btn.clicked.connect(self._on_wled_connect_clicked)

        strip_form.addRow("WLED параметры (Wi-Fi):", wled_host_row)
        strip_form.addRow(wled_port_row)
        strip_form.addRow("", wled_connect_btn)

        strip_form.addRow(QLabel(""))

        # -- Раздел для USB (Adalight) подключения --
        com_port_row = QHBoxLayout()
        com_port_row.addWidget(QLabel("COM-порт:"))
        self.wled_serial_port_input = QLineEdit(self.config.get("wled_serial_port", "COM5"))
        com_port_row.addWidget(self.wled_serial_port_input)

        refresh_ports_btn = QPushButton("Обновить список")
        refresh_ports_btn.clicked.connect(self._on_refresh_com_ports)
        com_port_row.addWidget(refresh_ports_btn)

        baud_row = QHBoxLayout()
        baud_row.addWidget(QLabel("Baud rate:"))
        self.wled_serial_baud_input = QLineEdit(str(self.config.get("wled_serial_baud", 115200)))
        self.wled_serial_baud_input.setMaximumWidth(100)
        baud_row.addWidget(self.wled_serial_baud_input)
        baud_row.addStretch()

        wled_serial_connect_btn = QPushButton("Подключить WLED (USB)")
        wled_serial_connect_btn.clicked.connect(self._on_wled_serial_connect_clicked)

        strip_form.addRow("USB параметры:", com_port_row)
        strip_form.addRow(baud_row)
        strip_form.addRow("", wled_serial_connect_btn)

        layout.addWidget(strip_box)

        diag_row = QHBoxLayout()
        diag_btn = QPushButton("Диагностика")
        diag_btn.setToolTip("Проверить связь с платой, захват экрана и маппинг")
        diag_btn.clicked.connect(self.worker.run_diagnostics)
        diag_row.addWidget(diag_btn)
        diag_row.addStretch()
        layout.addLayout(diag_row)

        layout.addWidget(QLabel(
            "💡 Первый раз настраиваешь WLED по Wi-Fi? Смотри вкладку "
            "«Настройка WiFi (ESP)» — там пошагово."
        ))

        layout.addWidget(QLabel(""))

        startup_box = QGroupBox("Запуск")
        startup_form = QFormLayout(startup_box)

        auto_connect_cb = QCheckBox("Автоподключение к последнему устройству при запуске")
        auto_connect_cb.setChecked(self.config.get("auto_connect", True))
        auto_connect_cb.stateChanged.connect(
            lambda s: self._set_top("auto_connect", s == Qt.Checked)
        )
        startup_form.addRow(auto_connect_cb)

        autostart_cb = QCheckBox("Запускать приложение при старте Windows")
        autostart_cb.setChecked(self.config.get("autostart_windows", False))
        if not autostart.is_supported():
            autostart_cb.setEnabled(False)
            autostart_cb.setText(autostart_cb.text() + " (недоступно на этой ОС)")
        autostart_cb.stateChanged.connect(self._on_autostart_toggled)
        startup_form.addRow(autostart_cb)

        tray_cb = QCheckBox("Сворачивать в трей при закрытии окна (не завершать программу)")
        tray_cb.setChecked(self.config.get("minimize_to_tray", True))
        tray_cb.stateChanged.connect(lambda s: self._set_top("minimize_to_tray", s == Qt.Checked))
        startup_form.addRow(tray_cb)

        reconnect_cb = QCheckBox("Автоматически переподключаться при обрыве связи")
        reconnect_cb.setChecked(self.config.get("auto_reconnect", True))
        reconnect_cb.stateChanged.connect(lambda s: self._set_top("auto_reconnect", s == Qt.Checked))
        startup_form.addRow(reconnect_cb)

        layout.addWidget(startup_box)

        # -- Таймер сна --
        sleep_box = QGroupBox("Таймер сна")
        sleep_form = QFormLayout(sleep_box)

        sleep_row = QHBoxLayout()
        self.sleep_timer_spin = QSpinBox()
        self.sleep_timer_spin.setRange(1, 480)
        self.sleep_timer_spin.setValue(30)
        self.sleep_timer_spin.setSuffix(" мин")
        sleep_row.addWidget(self.sleep_timer_spin)

        self.sleep_timer_btn = QPushButton("Запустить таймер")
        self.sleep_timer_btn.clicked.connect(self._on_sleep_timer_toggle)
        sleep_row.addWidget(self.sleep_timer_btn)

        self.sleep_timer_label = QLabel("")
        sleep_row.addWidget(self.sleep_timer_label)
        sleep_row.addStretch()
        sleep_form.addRow(sleep_row)

        layout.addWidget(sleep_box)

        # -- Экспорт / импорт всех настроек --
        backup_box = QGroupBox("Резервная копия настроек")
        backup_row = QHBoxLayout(backup_box)
        export_btn = QPushButton("Экспортировать все настройки")
        export_btn.clicked.connect(self._on_export_settings)
        import_btn = QPushButton("Импортировать настройки")
        import_btn.clicked.connect(self._on_import_settings)
        backup_row.addWidget(export_btn)
        backup_row.addWidget(import_btn)
        layout.addWidget(backup_box)

        layout.addWidget(QLabel(""))

        capture_row = QHBoxLayout()
        self.start_capture_btn = QPushButton("▶ Запустить Ambilight")
        self.start_capture_btn.setObjectName("primaryAction")
        self.start_capture_btn.clicked.connect(self._on_start_capture_clicked)
        self.stop_capture_btn = QPushButton("⏸ Остановить")
        self.stop_capture_btn.setObjectName("dangerAction")
        self.stop_capture_btn.setEnabled(False)  # пока захват не запущен - нечего останавливать
        self.stop_capture_btn.clicked.connect(self._on_stop_capture_clicked)
        capture_row.addWidget(self.start_capture_btn)
        capture_row.addWidget(self.stop_capture_btn)
        layout.addLayout(capture_row)

        test_row = QHBoxLayout()
        test_white_btn = QPushButton("Тест: белый")
        test_white_btn.clicked.connect(lambda: self.worker.send_test_color(255, 255, 255))
        test_off_btn = QPushButton("Тест: выключить (чёрный)")
        test_off_btn.clicked.connect(lambda: self.worker.send_test_color(0, 0, 0))
        test_row.addWidget(test_white_btn)
        test_row.addWidget(test_off_btn)
        layout.addLayout(test_row)

        layout.addStretch()
        return w

    def _on_autostart_toggled(self, state):
        enabled = state == Qt.Checked
        try:
            if enabled:
                autostart.enable()
            else:
                autostart.disable()
        except Exception as e:
            # Возвращаем галочку обратно, чтобы она не врала о состоянии системы
            self.config["autostart_windows"] = not enabled
            sender = self.sender()
            if sender is not None:
                sender.blockSignals(True)
                sender.setChecked(not enabled)
                sender.blockSignals(False)
            QMessageBox.warning(self, "Автозапуск", f"Не удалось изменить автозапуск: {e}")
            return
        self._set_top("autostart_windows", enabled)

    def _on_sleep_timer_toggle(self):
        """Запускает или отменяет обратный отсчёт до автоостановки Ambilight"""
        if self._sleep_countdown_timer.isActive():
            self._sleep_countdown_timer.stop()
            self._sleep_remaining_seconds = 0
            self.sleep_timer_label.setText("")
            self.sleep_timer_btn.setText("Запустить таймер")
        else:
            minutes = self.sleep_timer_spin.value()
            self._sleep_remaining_seconds = minutes * 60
            self._sleep_countdown_timer.start()
            self.sleep_timer_btn.setText("Отменить")
            self._update_sleep_timer_label()

    def _on_sleep_timer_tick(self):
        self._sleep_remaining_seconds -= 1
        if self._sleep_remaining_seconds <= 0:
            self._sleep_countdown_timer.stop()
            self.sleep_timer_label.setText("")
            self.sleep_timer_btn.setText("Запустить таймер")
            if self._capture_running:
                self._on_stop_capture_clicked()
            self.worker.stop_audio()
            # Реально выключаем ленту: раньше «подсветка выключена» означало
            # только «остановили захват», а лента оставалась светиться
            self.worker.blackout()
            if getattr(self, "tray_icon", None) is not None:
                self.tray_icon.showMessage(
                    "LED Ambilight", "Таймер сна сработал — подсветка выключена",
                    QSystemTrayIcon.Information, 3000,
                )
        else:
            self._update_sleep_timer_label()

    def _update_sleep_timer_label(self):
        m, s = divmod(self._sleep_remaining_seconds, 60)
        self.sleep_timer_label.setText(f"Осталось: {m:02d}:{s:02d}")

    def _on_export_settings(self):
        """Сохраняет ВСЕ настройки приложения (включая маппинг ленты) в один файл"""
        from PyQt5.QtWidgets import QFileDialog
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Экспортировать настройки", "ledapp_settings.json", "JSON (*.json)"
        )
        if not filepath:
            return
        try:
            import json
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Экспорт", f"Настройки сохранены в {filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить: {e}")

    def _on_import_settings(self):
        """Загружает настройки из файла экспорта и применяет их ко всему приложению"""
        from PyQt5.QtWidgets import QFileDialog
        filepath, _ = QFileDialog.getOpenFileName(self, "Импортировать настройки", "", "JSON (*.json)")
        if not filepath:
            return
        try:
            import json
            with open(filepath, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать файл: {e}")
            return

        if not isinstance(loaded, dict) or not loaded:
            QMessageBox.critical(self, "Ошибка", "В файле нет настроек (ожидался JSON-объект)")
            return

        reply = QMessageBox.question(
            self, "Импорт настроек",
            "Это заменит ВСЕ текущие настройки на настройки из файла.\n"
            "Понадобится перезапустить приложение, чтобы всё применилось корректно.\n"
            "Продолжить?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            from .config import save_config
            save_config(loaded)
            # Подхватываем импорт в память: иначе первое же изменение любого
            # слайдера перезаписало бы файл старыми настройками
            self.config = load_config()
            QMessageBox.information(
                self, "Импорт", "Настройки импортированы. Перезапусти приложение, чтобы применить их."
            )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить настройки: {e}")

    def _on_strip_mode_changed(self, mode):
        """Переключает режим между BLE и WLED"""
        self._set_top("strip_mode", mode)
        self.worker.set_strip_mode(mode)
        labels = {
            "ble": "ELK-BLEDOM (BLE)",
            "wled": "WLED (Wi-Fi)",
            "wled_serial": "WLED (USB-кабель)",
        }
        self.device_mode_label.setText(labels.get(mode, mode))
        # Смена устройства - предыдущее "подключено" уже не актуально
        self.status_label.setText("Не подключено")
        self.status_dot.setStyleSheet("color: #6b6c72; font-size: 14px;")

    def _ensure_strip_mode(self, mode):
        """Приводит режим ленты в соответствие с кнопкой, которую нажал пользователь.

        Раньше «Подключить WLED» в режиме BLE создавало соединение, но кадры
        продолжали идти в BLE (или никуда) - режим не переключался."""
        if self.config.get("strip_mode") == mode:
            return
        self._set_top("strip_mode", mode)
        self._on_strip_mode_changed(mode)
        combo = getattr(self, "strip_mode_combo", None)
        if combo is not None:
            index = combo.findData(mode)
            if index >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)

    def _on_wled_connect_clicked(self):
        """Подключается к WLED-устройству."""
        host = self.wled_host_input.text().strip()
        port_str = self.wled_port_input.text().strip()

        if not host:
            QMessageBox.warning(self, "WLED", "Введите IP-адрес устройства")
            return

        try:
            port = int(port_str)
        except ValueError:
            QMessageBox.warning(self, "WLED", "Порт должен быть числом")
            return
        if not (1 <= port <= 65535):
            QMessageBox.warning(self, "WLED", "Порт должен быть в диапазоне 1-65535")
            return

        self._set_top("wled_host", host)
        self._set_top("wled_port", port)
        # Кнопка должна не только подключиться, но и переключить режим ленты -
        # иначе в режиме BLE соединение есть, а кадры никуда не уходят
        self._ensure_strip_mode("wled")
        self.worker.connect_wled(host, port)

    def _on_refresh_com_ports(self):
        """Обновляет список доступных COM-портов"""
        try:
            from ledapp.wled_serial_controller import WLEDSerialController
            ports = WLEDSerialController.list_available_ports()
            if ports:
                QMessageBox.information(self, "COM-порты", "Найдены порты:\n" + "\n".join(ports))
            else:
                QMessageBox.warning(self, "COM-порты", "Порты не найдены. Проверь, подключена ли плата по USB.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка поиска портов: {e}")

    def _on_wled_serial_connect_clicked(self):
        """Подключается к WLED по USB (Adalight)"""
        com_port = self.wled_serial_port_input.text().strip()
        baud_str = self.wled_serial_baud_input.text().strip()

        if not com_port:
            QMessageBox.warning(self, "USB", "Введите COM-порт (например COM5)")
            return

        try:
            baud = int(baud_str)
        except ValueError:
            QMessageBox.warning(self, "USB", "Baud rate должен быть числом")
            return
        if not (9600 <= baud <= 2000000):
            QMessageBox.warning(self, "USB", "Baud rate должен быть в диапазоне 9600-2000000")
            return

        self._set_top("wled_serial_port", com_port)
        self._set_top("wled_serial_baud", baud)
        self._ensure_strip_mode("wled_serial")
        self.worker.connect_wled_serial(com_port, baud)

    def _on_connect_clicked(self):
        address = self.manual_address.text().strip()
        if not address:
            item = self.device_list.currentItem()
            if item is not None:
                address = item.data(Qt.UserRole)
        if not address:
            QMessageBox.warning(self, "Нет адреса", "Выберите устройство из списка или введите MAC-адрес.")
            return
        self.config["device_address"] = address
        self._queue_save()
        self._ensure_strip_mode("ble")
        self.worker.connect_device(address)

    # -- Вкладка превью захвата --

    def _build_preview_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        info = QLabel(
            "Показывает, что реально видит захват экрана (обновляется ~6 раз/сек).\n"
            "Красным подсвечена область, обрезанная HUD-маской (влияет и на общий цвет,\n"
            "и на цвет каждого диода). Жёлтой рамкой — область анализа для режима\n"
            "center_quadrants / профиль game.\n"
            "Кликни по картинке, чтобы взять цвет пипеткой (удобно для теста без ручного ввода RGB)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.preview_label = QLabel("Нет данных — запустите Ambilight на вкладке «Подключение»")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedSize(480, 270)  # ФИКСИРОВАННЫЙ размер — не даём лейблу расти
        self.preview_label.setStyleSheet("background-color: #111; color: #888; border: 1px solid #444;")
        self.preview_label.mousePressEvent = self._on_preview_clicked
        layout.addWidget(self.preview_label)

        self._last_preview_frame = None
        self._preview_pixmap_size = (0, 0)
        self._picked_color = None
        self._mapping_boxes = None

        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("Цвет с пипетки:"))
        self.preview_picked_swatch = QLabel()
        self.preview_picked_swatch.setFixedSize(30, 20)
        self.preview_picked_swatch.setStyleSheet("background-color: #000; border: 1px solid #666; border-radius: 4px;")
        pick_row.addWidget(self.preview_picked_swatch)
        self.preview_picked_label = QLabel("—")
        pick_row.addWidget(self.preview_picked_label)
        send_picked_btn = QPushButton("Отправить как тест")
        send_picked_btn.clicked.connect(self._on_send_picked_color)
        pick_row.addWidget(send_picked_btn)

        self.show_zones_cb = QCheckBox("Зоны диодов")
        self.show_zones_cb.setToolTip(
            "Показать области экрана, из которых каждый диод берёт цвет.\n"
            "Помогает увидеть, что диод на углу смотрит не туда."
        )
        self.show_zones_cb.stateChanged.connect(lambda _: self._redraw_preview())
        pick_row.addWidget(self.show_zones_cb)

        pick_row.addStretch()
        layout.addLayout(pick_row)

        layout.addStretch()
        return w

    def _on_preview_clicked(self, event):
        """Пипетка: по клику на превью берёт цвет реального пикселя экрана
        в этой точке (с поправкой на то, что картинка вписана по центру
        фиксированного лейбла с сохранением пропорций)."""
        if self._last_preview_frame is None:
            return
        arr = self._last_preview_frame
        pw, ph = self._preview_pixmap_size
        if pw == 0 or ph == 0:
            return

        label_w, label_h = self.preview_label.width(), self.preview_label.height()
        offset_x = (label_w - pw) / 2
        offset_y = (label_h - ph) / 2
        x = event.x() - offset_x
        y = event.y() - offset_y
        if not (0 <= x < pw and 0 <= y < ph):
            return

        arr_h, arr_w = arr.shape[0], arr.shape[1]
        ax = min(max(int(x / pw * arr_w), 0), arr_w - 1)
        ay = min(max(int(y / ph * arr_h), 0), arr_h - 1)
        r, g, b = (int(v) for v in arr[ay, ax][:3])

        self._picked_color = (r, g, b)
        self.preview_picked_swatch.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); border: 1px solid #666; border-radius: 4px;"
        )
        self.preview_picked_label.setText(f"RGB: {r}, {g}, {b}")

    def _on_send_picked_color(self):
        if self._picked_color is None:
            QMessageBox.information(self, "Пипетка", "Сначала кликни по картинке превью, чтобы выбрать цвет")
            return
        r, g, b = self._picked_color
        self.worker.send_test_color(r, g, b)

    # -- Вкладка калибровки --

    def _build_calibration_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        calib_box = QGroupBox(
            "Калибровка каналов — подгоняем так, чтобы красный на ленте был красным, "
            "белый — белым, а не с посторонним оттенком"
        )
        calib_form = QFormLayout(calib_box)

        calib = self.config["calibration"]

        calib_form.addRow(QLabel(
            "Gain — множитель канала (общая сила канала). "
            "Offset — сдвиг (компенсирует паразитную засветку соседних каналов)."
        ))

        _slider_row(calib_form, "Gain — Красный (R)", 50, 150, int(calib["r_gain"] * 100),
                    lambda v: self._set_nested("calibration", "r_gain", v / 100.0), "%")
        _slider_row(calib_form, "Gain — Зелёный (G)", 50, 150, int(calib["g_gain"] * 100),
                    lambda v: self._set_nested("calibration", "g_gain", v / 100.0), "%")
        _slider_row(calib_form, "Gain — Синий (B)", 50, 150, int(calib["b_gain"] * 100),
                    lambda v: self._set_nested("calibration", "b_gain", v / 100.0), "%")

        _slider_row(calib_form, "Offset — Красный (R)", -50, 50, calib["r_offset"],
                    lambda v: self._set_nested("calibration", "r_offset", v))
        _slider_row(calib_form, "Offset — Зелёный (G)", -50, 50, calib["g_offset"],
                    lambda v: self._set_nested("calibration", "g_offset", v))
        _slider_row(calib_form, "Offset — Синий (B)", -50, 50, calib["b_offset"],
                    lambda v: self._set_nested("calibration", "b_offset", v))

        layout.addWidget(calib_box)

        test_box = QGroupBox(
            "Быстрый тест калибровки — шлёт эталонный цвет С УЧЁТОМ калибровки выше. "
            "Крутите слайдеры и смотрите на ленту, пока цвет не станет верным."
        )
        test_row = QHBoxLayout(test_box)

        test_colors = [
            ("Красный", (255, 0, 0)),
            ("Зелёный", (0, 255, 0)),
            ("Синий", (0, 0, 255)),
            ("Белый", (255, 255, 255)),
            ("Выкл", (0, 0, 0)),
        ]
        for label, (r, g, b) in test_colors:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, rr=r, gg=g, bb=b: self.worker.send_test_calibrated(rr, gg, bb))
            test_row.addWidget(btn)

        layout.addWidget(test_box)

        bright_box = QGroupBox("Яркость")
        bright_form = QFormLayout(bright_box)

        bright = self.config["brightness"]

        self._scene_sliders["min_brightness"] = _slider_row(
            bright_form, "Минимальная яркость", 0, 255, bright["min_brightness"],
            lambda v: self._set_nested("brightness", "min_brightness", v))
        auto_cb = QCheckBox("Автоконтроль яркости")
        auto_cb.setChecked(bright["auto_brightness"])
        auto_cb.stateChanged.connect(
            lambda s: self._set_nested("brightness", "auto_brightness", s == Qt.Checked)
        )
        bright_form.addRow(auto_cb)

        _slider_row(bright_form, "Целевая яркость (авто)", 20, 255, bright["target_brightness"],
                    lambda v: self._set_nested("brightness", "target_brightness", v))

        self.brightness_slider = _slider_row(bright_form, "Общий множитель яркости", 10, 150,
                    int(bright["max_scale"] * 100),
                    lambda v: self._set_nested("brightness", "max_scale", v / 100.0), "%")
        self._scene_sliders["max_scale"] = self.brightness_slider

        layout.addWidget(bright_box)
        layout.addStretch()
        return w

    # -- Вкладка зон/HUD --

    def _build_hud_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        hud_box = QGroupBox("HUD-маска: игнорировать край экрана при анализе, %")
        hud_form = QFormLayout(hud_box)

        hud = self.config["hud_mask"]

        smart_box = QGroupBox("Умные зоны (авто)")
        smart_form = QFormLayout(smart_box)
        smart_form.addRow(QLabel(
            "Приложение само смотрит на картинку и подстраивается:\n"
            "полосы фильма, статичный интерфейс игры, пауза."
        ))
        content = self.config.get("content", {})

        letterbox_cb = QCheckBox("Обрезать чёрные полосы фильмов (letterbox)")
        letterbox_cb.setChecked(content.get("letterbox_crop", True))
        letterbox_cb.stateChanged.connect(
            lambda s: self._set_nested("content", "letterbox_crop", s == Qt.Checked)
        )
        smart_form.addRow(letterbox_cb)

        cinema_cb = QCheckBox("Включать кино-профиль, если на экране фильм")
        cinema_cb.setChecked(content.get("auto_cinema", True))
        cinema_cb.stateChanged.connect(
            lambda s: self._set_nested("content", "auto_cinema", s == Qt.Checked)
        )
        smart_form.addRow(cinema_cb)

        hud_auto_cb = QCheckBox("Сам находить статичный интерфейс игр (миникарта, полоса здоровья)")
        hud_auto_cb.setChecked(content.get("smart_hud", True))
        hud_auto_cb.stateChanged.connect(
            lambda s: self._set_nested("content", "smart_hud", s == Qt.Checked)
        )
        smart_form.addRow(hud_auto_cb)

        fade_cb = QCheckBox("Приглушать подсветку, когда видео стоит на паузе")
        fade_cb.setChecked(content.get("fade_on_pause", True))
        fade_cb.stateChanged.connect(
            lambda s: self._set_nested("content", "fade_on_pause", s == Qt.Checked)
        )
        smart_form.addRow(fade_cb)

        _slider_row(
            smart_form, "Яркость на паузе", 0, 50,
            int(float(content.get("fade_level", 0.15)) * 100),
            lambda v: self._set_nested("content", "fade_level", v / 100.0), "%"
        )
        layout.addWidget(smart_box)

        _slider_row(hud_form, "Сверху", 0, 40, hud["top"],
                    lambda v: self._set_nested("hud_mask", "top", v), "%")
        _slider_row(hud_form, "Снизу", 0, 40, hud["bottom"],
                    lambda v: self._set_nested("hud_mask", "bottom", v), "%")
        _slider_row(hud_form, "Слева", 0, 40, hud["left"],
                    lambda v: self._set_nested("hud_mask", "left", v), "%")
        _slider_row(hud_form, "Справа", 0, 40, hud["right"],
                    lambda v: self._set_nested("hud_mask", "right", v), "%")

        layout.addWidget(hud_box)

        area_box = QGroupBox("Область анализа для режима 'center_quadrants' (рекомендуется 50-80%)")
        area_form = QFormLayout(area_box)

        _slider_row(
            area_form, "Область анализа", 30, 100,
            self.config.get("analysis_area_percent", 60),
            lambda v: self._set_top("analysis_area_percent", v), "%"
        )

        layout.addWidget(area_box)
        layout.addStretch()
        return w

    # -- Вкладка фильтров --

    def _build_filters_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        filters = self.config["filters"]

        scenes_box = QGroupBox("Пресеты сцен (ставят яркость, сглаживание и режим звука)")
        scenes_row = QHBoxLayout(scenes_box)
        for scene_name in SCENE_PRESETS:
            scene_btn = QPushButton(scene_name)
            scene_btn.clicked.connect(lambda _=False, n=scene_name: self._apply_scene(n))
            scenes_row.addWidget(scene_btn)
        scenes_row.addStretch()
        layout.addWidget(scenes_box)

        thresh_box = QGroupBox("Порог изменения цвета")
        thresh_form = QFormLayout(thresh_box)
        _slider_row(thresh_form, "Игнорировать изменения меньше", 0, 60,
                    filters["change_threshold"],
                    lambda v: self._set_nested("filters", "change_threshold", v))
        layout.addWidget(thresh_box)

        flicker_box = QGroupBox("Фильтр мерцания")
        flicker_form = QFormLayout(flicker_box)
        flicker_cb = QCheckBox("Включить фильтр мерцания (медиана по кадрам)")
        flicker_cb.setChecked(filters["flicker_filter"])
        flicker_cb.stateChanged.connect(
            lambda s: self._set_nested("filters", "flicker_filter", s == Qt.Checked)
        )
        flicker_form.addRow(flicker_cb)
        _slider_row(flicker_form, "Размер окна (кадров)", 1, 8, filters["flicker_window"],
                    lambda v: self._set_nested("filters", "flicker_window", v))
        layout.addWidget(flicker_box)

        smooth_box = QGroupBox("Адаптивное сглаживание")
        smooth_form = QFormLayout(smooth_box)
        smooth_form.addRow(QLabel(
            "Управляет плавностью перехода цвета - и для одного общего цвета\n"
            "(режим BLE), и для каждого диода отдельно (поэтевиксельный WLED)."
        ))
        adaptive_cb = QCheckBox("Включить адаптивное сглаживание")
        adaptive_cb.setChecked(filters["adaptive_smoothing"])
        adaptive_cb.stateChanged.connect(
            lambda s: self._set_nested("filters", "adaptive_smoothing", s == Qt.Checked)
        )
        smooth_form.addRow(adaptive_cb)

        self._scene_sliders["smoothing_min"] = _slider_row(
            smooth_form, "Мин. скорость (плавные сцены)", 5, 100,
            int(filters["smoothing_min"] * 100),
            lambda v: self._set_nested("filters", "smoothing_min", v / 100.0), "%")
        self._scene_sliders["smoothing_max"] = _slider_row(
            smooth_form, "Макс. скорость (резкие сцены)", 5, 100,
            int(filters["smoothing_max"] * 100),
            lambda v: self._set_nested("filters", "smoothing_max", v / 100.0), "%")
        _slider_row(smooth_form, "Чувствительность (порог 'резкости')", 10, 300,
                    filters["adaptive_sensitivity"],
                    lambda v: self._set_nested("filters", "adaptive_sensitivity", v))

        layout.addWidget(smooth_box)

        spatial_box = QGroupBox("Пространственное сглаживание вдоль ленты")
        spatial_form = QFormLayout(spatial_box)
        spatial_form.addRow(QLabel(
            "Каждый диод смешивается с двумя соседями, поэтому на резких границах\n"
            "нет цветных «ступенек». 0% — выключено, 50% — сильное размытие."
        ))
        _slider_row(
            spatial_form, "Сила смешивания", 0, 49,
            int(filters.get("neighbor_blend", 0.35) * 100),
            lambda v: self._set_nested("filters", "neighbor_blend", v / 100.0), "%"
        )
        linear_cb = QCheckBox("Усреднять цвет в линейном свете (точнее тени и переходы)")
        linear_cb.setChecked(filters.get("linear_average", True))
        linear_cb.stateChanged.connect(
            lambda s: self._set_nested("filters", "linear_average", s == Qt.Checked)
        )
        spatial_form.addRow(linear_cb)
        _slider_row(
            spatial_form, "Гамма (для HDR и блёклых сцен)", 70, 160,
            int(filters.get("gamma", 1.0) * 100),
            lambda v: self._set_nested("filters", "gamma", v / 100.0), "%"
        )
        layout.addWidget(spatial_box)

        dom_box = QGroupBox("Приоритет доминирующего цвета (лечит 'вымывание' в серо-белый)")
        dom_form = QFormLayout(dom_box)
        dom_cb = QCheckBox("Учитывать доминирующий цвет в каждой зоне (не только среднее)")
        dom_cb.setChecked(filters["dominant_color_priority"])
        dom_cb.stateChanged.connect(
            lambda s: self._set_nested("filters", "dominant_color_priority", s == Qt.Checked)
        )
        dom_form.addRow(dom_cb)
        dom_form.addRow(QLabel(
            "0% = чистое среднее (может казаться бело-серым на пёстрых сценах).\n"
            "100% = только самый частый цвет в зоне, без примеси среднего."
        ))
        self._scene_sliders["dominant_weight"] = _slider_row(
            dom_form, "Вес доминирующего цвета", 0, 100,
            int(filters["dominant_weight"] * 100),
            lambda v: self._set_nested("filters", "dominant_weight", v / 100.0), "%")
        layout.addWidget(dom_box)

        layout.addStretch()
        return w

    # -- Вкладка звука --

    def _build_audio_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        if not audio_is_available():
            layout.addWidget(QLabel(
                "⚠ Библиотека 'soundcard' недоступна в этом окружении.\n"
                "Установите: pip install soundcard — и перезапустите приложение."
            ))

        audio = self.config["audio"]

        control_box = QGroupBox("Режим и управление")
        control_form = QFormLayout(control_box)

        mode_combo = QComboBox()
        mode_combo.addItem("Гибрид - лёгкий оттенок от баса/высоких поверх экрана", "hybrid")
        mode_combo.addItem("Вспышки на пиках (поверх обычного Ambilight)", "flash")
        mode_combo.addItem("Полный визуализатор (звук вместо экрана)", "visualizer")
        mode_combo.addItem("Спектроанализатор (полосы частот по диодам)", "spectrum")
        idx = mode_combo.findData(audio.get("mode", "hybrid"))
        mode_combo.setCurrentIndex(max(0, idx))
        mode_combo.currentIndexChanged.connect(
            lambda i: self._on_audio_mode_changed(mode_combo.itemData(i))
        )
        self.audio_mode_combo = mode_combo
        control_form.addRow("Режим", mode_combo)

        self.audio_mode_hint = QLabel("")
        self.audio_mode_hint.setWordWrap(True)
        self.audio_mode_hint.setStyleSheet("color: #9a9ba3;")
        control_form.addRow(self.audio_mode_hint)

        device_combo = QComboBox()
        device_combo.addItem("Устройство вывода по умолчанию", None)
        for name in list_output_devices():
            device_combo.addItem(name, name)
        saved_device = audio.get("device_name")
        if saved_device:
            di = device_combo.findData(saved_device)
            if di >= 0:
                device_combo.setCurrentIndex(di)
        device_combo.currentIndexChanged.connect(
            lambda i: self._set_nested("audio", "device_name", device_combo.itemData(i))
        )
        control_form.addRow("Устройство", device_combo)

        layout.addWidget(control_box)

        btn_row = QHBoxLayout()
        start_audio_btn = QPushButton("▶ Запустить аудио-реакцию")
        start_audio_btn.setObjectName("primaryAction")

        def _start_audio():
            self.config["audio"]["enabled"] = True
            self._save_and_push_config()
            self.worker.start_audio()

        def _stop_audio():
            self.config["audio"]["enabled"] = False
            self._save_and_push_config()
            self.worker.stop_audio()

        start_audio_btn.clicked.connect(_start_audio)
        stop_audio_btn = QPushButton("⏸ Остановить")
        stop_audio_btn.setObjectName("dangerAction")
        stop_audio_btn.clicked.connect(_stop_audio)
        btn_row.addWidget(start_audio_btn)
        btn_row.addWidget(stop_audio_btn)
        layout.addLayout(btn_row)

        vu_box = QGroupBox("Живой уровень звука")
        vu_layout = QFormLayout(vu_box)
        self.vu_volume = QProgressBar()
        self.vu_bass = QProgressBar()
        self.vu_mid = QProgressBar()
        self.vu_treble = QProgressBar()
        for bar in (self.vu_volume, self.vu_bass, self.vu_mid, self.vu_treble):
            bar.setRange(0, 100)
            bar.setTextVisible(False)
        vu_layout.addRow("Громкость", self.vu_volume)
        vu_layout.addRow("Бас", self.vu_bass)
        vu_layout.addRow("Средние", self.vu_mid)
        vu_layout.addRow("Высокие", self.vu_treble)
        layout.addWidget(vu_box)

        self.bpm_label = QLabel("Темп: —")
        self.bpm_label.setStyleSheet("color: #8fb8ff;")
        layout.addWidget(self.bpm_label)

        # -- Гибридный режим: сила оттенка --
        hybrid_box = QGroupBox("Гибрид: оттенок от баса/высоких")
        hybrid_form = QFormLayout(hybrid_box)
        hybrid_form.addRow(QLabel(
            "Обычный цвет с экрана остаётся основным - бас лишь слегка\n"
            "подмешивает тёплый оттенок, высокие частоты - холодный."
        ))
        self.slider_tint_strength = _slider_row(
            hybrid_form, "Сила оттенка", 0, 100,
            int(audio.get("tint_strength", 0.35) * 100),
            lambda v: self._set_nested("audio", "tint_strength", v / 100.0), "%"
        )
        layout.addWidget(hybrid_box)

        # -- Общие регуляторы полос (используются гибридом и визуализатором) --
        sens_box = QGroupBox("Чувствительность по полосам")
        sens_form = QFormLayout(sens_box)
        self.slider_sensitivity = _slider_row(
            sens_form, "Общая чувствительность (визуализатор)", 10, 300,
            int(audio["sensitivity"] * 100),
            lambda v: self._set_nested("audio", "sensitivity", v / 100.0), "%"
        )
        self.slider_bass_gain = _slider_row(
            sens_form, "Усиление баса (гибрид + визуализатор)", 10, 300, int(audio["bass_gain"] * 100),
            lambda v: self._set_nested("audio", "bass_gain", v / 100.0), "%"
        )
        self.slider_mid_gain = _slider_row(
            sens_form, "Усиление средних (только визуализатор)", 10, 300, int(audio["mid_gain"] * 100),
            lambda v: self._set_nested("audio", "mid_gain", v / 100.0), "%"
        )
        self.slider_treble_gain = _slider_row(
            sens_form, "Усиление высоких (гибрид + визуализатор)", 10, 300, int(audio["treble_gain"] * 100),
            lambda v: self._set_nested("audio", "treble_gain", v / 100.0), "%"
        )
        layout.addWidget(sens_box)

        env_box = QGroupBox("Отклик (огибающая) - действует всегда")
        env_form = QFormLayout(env_box)
        _slider_row(env_form, "Скорость роста (атака)", 5, 100,
                    int(audio["envelope_attack"] * 100),
                    lambda v: self._set_nested("audio", "envelope_attack", v / 100.0), "%")
        _slider_row(env_form, "Скорость спада (релиз)", 5, 100,
                    int(audio["envelope_release"] * 100),
                    lambda v: self._set_nested("audio", "envelope_release", v / 100.0), "%")
        layout.addWidget(env_box)

        peak_box = QGroupBox("Детекция пиков (вспышки, визуализатор)")
        peak_form = QFormLayout(peak_box)
        self.slider_peak_threshold = _slider_row(
            peak_form, "Порог срабатывания", 110, 400,
            int(audio["peak_threshold"] * 100),
            lambda v: self._set_nested("audio", "peak_threshold", v / 100.0), "%"
        )
        self.slider_flash_duration = _slider_row(
            peak_form, "Длительность вспышки", 50, 500,
            int(audio["flash_duration"] * 1000),
            lambda v: self._set_nested("audio", "flash_duration", v / 1000.0), "мс"
        )
        self.slider_flash_intensity = _slider_row(
            peak_form, "Интенсивность вспышки (только вспышки)", 10, 200,
            int(audio["flash_intensity"] * 100),
            lambda v: self._set_nested("audio", "flash_intensity", v / 100.0), "%"
        )
        layout.addWidget(peak_box)

        layout.addStretch()

        self._on_audio_mode_changed(audio.get("mode", "hybrid"), save=False)
        return w

    def _on_audio_mode_changed(self, mode, save=True):
        """Переключает режим звука и включает/выключает только те регуляторы,
        которые реально влияют на выбранный режим - раньше все слайдеры были
        видны одновременно независимо от режима, и было неясно, что из них
        вообще на что-то влияет."""
        if save:
            self._set_nested("audio", "mode", mode)

        hints = {
            "hybrid": "Бас и высокие подмешивают лёгкий оттенок к цвету с экрана. "
                      "Средние частоты и детекция пиков в этом режиме не используются.",
            "flash": "Обычный Ambilight с экрана + короткая белая вспышка на резких "
                     "пиках громкости. Полосы баса/средних/высоких не используются.",
            "visualizer": "Цвет полностью из звука (экран не участвует): бас → красный, "
                          "средние → зелёный, высокие → синий, громкость → яркость.",
            "spectrum": "32 полосы частот раскладываются по ленте: левый край — бас, "
                        "правый — высокие. Чем громче полоса, тем ярче её диоды.",
        }
        self.audio_mode_hint.setText(hints.get(mode, ""))

        enabled_map = {
            "hybrid": {
                self.slider_bass_gain: True,
                self.slider_treble_gain: True,
                self.slider_tint_strength: True,
                self.slider_mid_gain: False,
                self.slider_sensitivity: False,
                self.slider_peak_threshold: False,
                self.slider_flash_duration: False,
                self.slider_flash_intensity: False,
            },
            "flash": {
                self.slider_bass_gain: False,
                self.slider_treble_gain: False,
                self.slider_tint_strength: False,
                self.slider_mid_gain: False,
                self.slider_sensitivity: False,
                self.slider_peak_threshold: True,
                self.slider_flash_duration: True,
                self.slider_flash_intensity: True,
            },
            "visualizer": {
                self.slider_bass_gain: True,
                self.slider_treble_gain: True,
                self.slider_tint_strength: False,
                self.slider_mid_gain: True,
                self.slider_sensitivity: True,
                self.slider_peak_threshold: True,
                self.slider_flash_duration: True,
                self.slider_flash_intensity: False,
            },
            "spectrum": {
                self.slider_bass_gain: True,
                self.slider_treble_gain: True,
                self.slider_tint_strength: False,
                self.slider_mid_gain: True,
                self.slider_sensitivity: True,
                self.slider_peak_threshold: False,
                self.slider_flash_duration: False,
                self.slider_flash_intensity: False,
            },
        }
        for slider, enabled in enabled_map.get(mode, {}).items():
            slider.setEnabled(enabled)

    @staticmethod
    def _resample_path_evenly(points, count):
        """
        Возвращает count точек, равномерно распределённых по РЕАЛЬНОЙ ДЛИНЕ пути
        (а не по количеству исходных точек мыши). Так диоды не "толпятся" там,
        где мышь двигалась медленно, и не "разбегаются" там, где двигалась быстро.
        """
        if len(points) < 2 or count <= 0:
            return []

        # Считаем длину каждого отрезка и накопленную длину до каждой точки
        cum_lengths = [0.0]
        for i in range(1, len(points)):
            x1, y1 = points[i - 1]
            x2, y2 = points[i]
            seg_len = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            cum_lengths.append(cum_lengths[-1] + seg_len)

        total_length = cum_lengths[-1]
        if total_length == 0:
            return [points[0]] * count

        # Целевые позиции вдоль пути - равномерно от 0 до total_length
        targets = [total_length * i / (count - 1) if count > 1 else 0.0 for i in range(count)]

        result = []
        seg_idx = 0
        for target in targets:
            # Продвигаем указатель сегмента, пока не найдём подходящий отрезок
            while seg_idx < len(cum_lengths) - 2 and cum_lengths[seg_idx + 1] < target:
                seg_idx += 1
            seg_start_len = cum_lengths[seg_idx]
            seg_end_len = cum_lengths[seg_idx + 1]
            seg_len = seg_end_len - seg_start_len
            t = 0.0 if seg_len == 0 else (target - seg_start_len) / seg_len
            x1, y1 = points[seg_idx]
            x2, y2 = points[seg_idx + 1]
            x = x1 + t * (x2 - x1)
            y = y1 + t * (y2 - y1)
            result.append((x, y))

        return result

    @staticmethod
    def _path_length(points):
        """Суммарная длина пути (в тех же единицах, что и координаты точек)."""
        total = 0.0
        for i in range(1, len(points)):
            x1, y1 = points[i - 1]
            x2, y2 = points[i]
            total += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        return total

    def _build_strip_config_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel("Конфигурация расположения ленты на мониторе"))

        # Параметры ленты
        params_box = QGroupBox("Параметры ленты")
        params_form = QFormLayout(params_box)

        # Метраж ленты
        meters_spin = QDoubleSpinBox()
        meters_spin.setRange(0.1, 10.0)
        meters_spin.setSingleStep(0.1)
        meters_spin.setValue(self.config.get("strip_meters", 5.0))
        meters_spin.setSuffix(" м")
        meters_spin.setDecimals(1)
        meters_spin.valueChanged.connect(lambda v: self._on_strip_params_changed())
        self.strip_meters_spin = meters_spin
        params_form.addRow("Метров ленты:", meters_spin)

        # Плотность диодов
        density_combo = QComboBox()
        density_combo.setEditable(True)
        for d in (18, 30, 60, 144):
            density_combo.addItem(f"{d} LED/м", d)
        current_density = self.config.get("strip_density", 30)
        idx = density_combo.findData(current_density)
        if idx >= 0:
            density_combo.setCurrentIndex(idx)
        else:
            # Плотность не из стандартного списка (например 18/м, как у многих
            # реальных лент с Ozon/WB) - просто вписываем как текст, поле
            # редактируемое и это ок
            density_combo.setCurrentText(str(current_density))
        density_combo.currentIndexChanged.connect(lambda i: self._on_strip_params_changed())
        density_combo.lineEdit().editingFinished.connect(self._on_strip_params_changed)
        self.strip_density_combo = density_combo
        params_form.addRow("Плотность светодиодов:", density_combo)

        # Итоговое количество
        total_leds_label = QLabel("0")
        self.strip_total_leds_label = total_leds_label
        params_form.addRow("Всего светодиодов:", total_leds_label)

        # Диагональ монитора - нужна для точного расчёта реального периметра
        diagonal_spin = QDoubleSpinBox()
        diagonal_spin.setRange(10.0, 100.0)
        diagonal_spin.setSingleStep(0.5)
        diagonal_spin.setValue(self.config.get("monitor_diagonal_inches", 24.0))
        diagonal_spin.setSuffix(" дюймов")
        diagonal_spin.setDecimals(1)
        diagonal_spin.valueChanged.connect(lambda v: self._on_strip_params_changed())
        self.strip_diagonal_spin = diagonal_spin
        params_form.addRow("Диагональ монитора:", diagonal_spin)

        monitor_size_label = QLabel("")
        self.strip_monitor_size_label = monitor_size_label
        params_form.addRow("Реальный размер:", monitor_size_label)

        # Режим сэмплинга цвета для каждого светодиода
        sampling_combo = QComboBox()
        sampling_combo.addItem("Средний цвет области", "average")
        sampling_combo.addItem("Доминантный цвет области", "dominant")
        current_sampling = self.config.get("led_sampling_mode", "average")
        idx = sampling_combo.findData(current_sampling)
        sampling_combo.setCurrentIndex(max(0, idx))
        sampling_combo.currentIndexChanged.connect(
            lambda i: self._set_top("led_sampling_mode", sampling_combo.itemData(i))
        )
        self.strip_sampling_combo = sampling_combo
        params_form.addRow("Как определять цвет:", sampling_combo)

        # Размер области, из которой диод берёт цвет
        sample_row = QHBoxLayout()
        self.sample_width_slider = QSlider(Qt.Horizontal)
        self.sample_width_slider.setRange(4, 40)
        self.sample_width_slider.setValue(int(self.config.get("sample_width", 10)))
        self.sample_width_label = QLabel(f"{self.sample_width_slider.value()} px")
        self.sample_width_label.setFixedWidth(56)

        def _on_sample_width(value):
            self.sample_width_label.setText(f"{value} px")
            self._set_top("sample_width", value)

        self.sample_width_slider.valueChanged.connect(_on_sample_width)
        self.sample_width_slider.setToolTip(
            "Сколько пикселей экрана усредняет один диод.\n"
            "Мало — цвет «дёргается», много — смазывается картинка."
        )
        sample_row.addWidget(self.sample_width_slider)
        sample_row.addWidget(self.sample_width_label)
        params_form.addRow("Область выборки диода:", sample_row)

        # Размеры экрана
        screen_w_label = QLabel(str(self.config.get("screen_width", 1920)))
        screen_h_label = QLabel(str(self.config.get("screen_height", 1080)))
        params_form.addRow("Размер экрана:", QLabel(f"{screen_w_label.text()}×{screen_h_label.text()} px"))

        layout.addWidget(params_box)

        # --- готовые шаблоны раскладки ---
        preset_box = QGroupBox("Готовая раскладка (без рисования)")
        preset_form = QFormLayout(preset_box)
        preset_form.addRow(QLabel(
            "Лента на задней крышке? Выбери витки — путь построится сам,\n"
            "а кнопкой «Раскладка по развёртке» потом можно уточнить по факту."
        ))

        self.loop_count_combo = QComboBox()
        self.loop_count_combo.addItem("По периметру (1 виток)", 1)
        self.loop_count_combo.addItem("Два витка", 2)
        self.loop_count_combo.addItem("Три витка", 3)
        self.loop_count_combo.addItem("Четыре витка", 4)
        self.loop_count_combo.setCurrentIndex(1)
        preset_form.addRow("Раскладка:", self.loop_count_combo)

        corner_row = QHBoxLayout()
        self.start_corner_combo = QComboBox()
        self.start_corner_combo.addItem("Левый верх", "tl")
        self.start_corner_combo.addItem("Правый верх", "tr")
        self.start_corner_combo.addItem("Правый низ", "br")
        self.start_corner_combo.addItem("Левый низ", "bl")
        self.start_corner_combo.setCurrentIndex(3)
        corner_row.addWidget(QLabel("Начало:"))
        corner_row.addWidget(self.start_corner_combo)

        self.direction_combo = QComboBox()
        self.direction_combo.addItem("По часовой", True)
        self.direction_combo.addItem("Против часовой", False)
        corner_row.addWidget(self.direction_combo)
        corner_row.addStretch()
        preset_form.addRow(corner_row)

        self.inset_slider = _slider_row(preset_form, "Отступ от края", 0, 30, 3,
                                        lambda v: None, "%")
        self.gap_slider = _slider_row(preset_form, "Шаг между витками", 2, 25, 12,
                                      lambda v: None, "%")

        preset_btn = QPushButton("Создать раскладку")
        preset_btn.setObjectName("primaryAction")
        preset_btn.clicked.connect(self._on_apply_layout_preset)
        preset_form.addRow("", preset_btn)
        layout.addWidget(preset_box)

        layout.addWidget(QLabel(""))

        layout.addWidget(QLabel(
            "Инструкция: нарисуйте путь ленты мышкой ВНУТРИ прямоугольника экрана.\n"
            "Кружочки = светодиоды. Начните с левого края, обведите монитор спиралью.\n"
            "Рисовать можно несколькими штрихами, «Очистить» начинает заново."
        ))

        self.strip_length_label = QLabel("Нарисовано: 0.0 м / 0.0 м")
        self.strip_length_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.strip_length_label)

        # -- Управление режимом рисования и фоном --
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Режим разметки:"))
        self.strip_draw_mode_combo = QComboBox()
        self.strip_draw_mode_combo.addItem("Прямые линии (клик, авто-распределение)", "straight")
        self.strip_draw_mode_combo.addItem("Свободное рисование (авто-распределение)", "freehand")
        self.strip_draw_mode_combo.addItem("Вручную - каждый светодиод отдельно", "manual")
        self.strip_draw_mode_combo.currentIndexChanged.connect(self._on_strip_draw_mode_changed)
        mode_row.addWidget(self.strip_draw_mode_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        self.strip_manual_progress_label = QLabel("")
        self.strip_manual_progress_label.setStyleSheet("font-weight: bold; color: #ffc800;")
        layout.addWidget(self.strip_manual_progress_label)

        draw_controls_row = QHBoxLayout()

        screenshot_btn = QPushButton("Снимок экрана (фон)")
        screenshot_btn.clicked.connect(self._on_capture_screen_background)
        draw_controls_row.addWidget(screenshot_btn)

        undo_point_btn = QPushButton("Отменить точку")
        undo_point_btn.clicked.connect(self._on_undo_strip_point)
        draw_controls_row.addWidget(undo_point_btn)

        draw_controls_row.addStretch()
        layout.addLayout(draw_controls_row)

        # Холст для рисования с пропорциями экрана
        self.strip_canvas = QLabel()
        screen_w = self.config.get("screen_width", 1920)
        screen_h = self.config.get("screen_height", 1080)
        aspect_ratio = screen_w / screen_h
        
        # Устанавливаем размер холста с учётом пропорций
        canvas_width = 600
        canvas_height = int(canvas_width / aspect_ratio)
        self.strip_canvas.setMinimumSize(canvas_width, canvas_height)
        self.strip_canvas.setMaximumSize(canvas_width, canvas_height)
        self.strip_canvas.setStyleSheet("background-color: #111; border: 2px solid #444;")
        self.strip_canvas.setAlignment(Qt.AlignCenter)
        self.strip_canvas.setMouseTracking(True)
        
        # Сохраняем размеры холста
        self.strip_canvas_width = canvas_width
        self.strip_canvas_height = canvas_height
        self.strip_screen_aspect = aspect_ratio
        
        # Включаем обработку мыши
        self.strip_canvas.mouseMoveEvent = self._on_strip_canvas_mouse_move
        self.strip_canvas.mouseReleaseEvent = self._on_strip_canvas_mouse_release
        self.strip_canvas.mousePressEvent = self._on_strip_canvas_mouse_press

        self.strip_drawing = True
        self.strip_path_points = []
        self.strip_draw_mode = "straight"  # "straight" / "freehand" / "manual"
        self.strip_manual_points = []  # позиции светодиодов в режиме ручной расстановки
        self.strip_background_pixmap = None
        self.strip_preview_point = None  # текущая позиция мыши для предпросмотра линии
        
        layout.addWidget(QLabel("Рисунок ленты (в пропорциях экрана):"))
        layout.addWidget(self.strip_canvas)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Очистить")
        clear_btn.clicked.connect(self._on_clear_strip_path)
        wizard_btn = QPushButton("Раскладка по развёртке")
        wizard_btn.setObjectName("primaryAction")
        wizard_btn.setToolTip(
            "Самый простой способ: приложение само зажигает диоды по одному,\n"
            "ты щёлкаешь по схеме, где видишь свет. Рисовать ничего не нужно."
        )
        wizard_btn.clicked.connect(self._on_mapping_wizard)
        reverse_btn = QPushButton("Реверс пути")
        reverse_btn.setToolTip(
            "Развернуть путь задом наперёд.\n"
            "Пригодится, если лента наклеена в обратную сторону и тест-развёртка "
            "бежит не в том направлении."
        )
        reverse_btn.clicked.connect(self._on_reverse_strip_path)
        build_btn = QPushButton("Построить маппинг")
        build_btn.clicked.connect(self._on_build_strip_mapping)
        save_btn = QPushButton("Сохранить")
        save_btn.clicked.connect(self._on_save_strip_config)
        load_btn = QPushButton("Загрузить")
        load_btn.clicked.connect(self._on_load_strip_config)

        btn_row.addWidget(clear_btn)
        btn_row.addWidget(wizard_btn)
        btn_row.addWidget(reverse_btn)
        btn_row.addWidget(build_btn)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(load_btn)
        layout.addLayout(btn_row)

        # -- Тест-развёртка: проверка совпадения физического порядка ленты --
        sweep_box = QGroupBox("Тест-развёртка (проверка порядка диодов)")
        sweep_layout = QHBoxLayout(sweep_box)

        self.strip_sweep_color = QColor(255, 0, 0)  # по умолчанию красный

        sweep_color_btn = QPushButton()
        sweep_color_btn.setFixedWidth(60)
        self._update_sweep_color_button(sweep_color_btn)
        sweep_color_btn.clicked.connect(lambda: self._on_pick_sweep_color(sweep_color_btn))
        self.strip_sweep_color_btn = sweep_color_btn
        sweep_layout.addWidget(QLabel("Цвет:"))
        sweep_layout.addWidget(sweep_color_btn)

        sweep_delay_spin = QSpinBox()
        sweep_delay_spin.setRange(20, 2000)
        sweep_delay_spin.setValue(150)
        sweep_delay_spin.setSuffix(" мс/диод")
        self.strip_sweep_delay_spin = sweep_delay_spin
        sweep_layout.addWidget(sweep_delay_spin)

        sweep_run_btn = QPushButton("▶ Запустить развёртку")
        sweep_run_btn.clicked.connect(self._on_run_sweep)
        sweep_layout.addWidget(sweep_run_btn)

        pattern_btn = QPushButton("Схема на весь экран")
        pattern_btn.setToolTip(
            "Развернуть схему раскладки на весь монитор и одновременно\n"
            "запустить развёртку — так проще всего сверить порядок диодов"
        )
        pattern_btn.clicked.connect(self._on_show_test_pattern)
        sweep_layout.addWidget(pattern_btn)

        sweep_layout.addStretch()
        layout.addWidget(sweep_box)
        layout.addWidget(QLabel(
            "Развёртка зажигает диоды по одному, в порядке 1, 2, 3... - если на\n"
            "реальной ленте цвет 'бежит' не в том направлении или не по порядку,\n"
            "значит лента физически приклеена в обратную сторону от нарисованного пути."
        ))

        layout.addStretch()
        
        self._on_strip_params_changed()
        return w

    def _update_sweep_color_button(self, btn):
        """Красит кнопку выбора цвета тест-развёртки в текущий выбранный цвет"""
        c = self.strip_sweep_color
        btn.setStyleSheet(f"background-color: rgb({c.red()},{c.green()},{c.blue()});")

    def _on_pick_sweep_color(self, btn):
        """Открывает диалог выбора цвета для тест-развёртки"""
        from PyQt5.QtWidgets import QColorDialog
        color = QColorDialog.getColor(self.strip_sweep_color, self, "Цвет для тест-развёртки")
        if color.isValid():
            self.strip_sweep_color = color
            self._update_sweep_color_button(btn)

    def _on_show_test_pattern(self):
        """Схема раскладки на весь экран + развёртка для сверки порядка."""
        data = getattr(self, "_mapping_boxes", None)
        if not data or not data.get("boxes"):
            QMessageBox.information(
                self, "Тест-паттерн",
                "Сначала нарисуй путь ленты и нажми «Построить маппинг»",
            )
            return
        self._pattern_window = TestPatternWindow(data, self)
        screen = QApplication.primaryScreen()
        if screen is not None:
            self._pattern_window.setGeometry(screen.geometry())
        self._pattern_window.showFullScreen()
        self.worker.run_test_sweep(255, 255, 255, 300)

    def _on_run_sweep(self):
        """Запускает тест-развёртку - зажигает диоды по одному по порядку"""
        c = self.strip_sweep_color
        delay_ms = self.strip_sweep_delay_spin.value()
        self.worker.run_test_sweep(c.red(), c.green(), c.blue(), delay_ms)

    def _get_strip_density(self):
        """Читает плотность LED/м - либо выбранный пресет, либо число,
        вписанное пользователем вручную (поле редактируемое, чтобы
        поддержать любую реальную ленту, не только стандартные 30/60/144)."""
        data = self.strip_density_combo.currentData()
        text = self.strip_density_combo.currentText().strip()
        # Если currentData соответствует тому, что реально написано в поле -
        # это выбранный пресет, используем его напрямую
        if data is not None and text.startswith(str(data)):
            return data
        # Иначе пользователь вписал своё значение - вытаскиваем число из текста
        import re
        match = re.search(r'\d+', text)
        return int(match.group()) if match else 30

    def _on_strip_params_changed(self):
        """Обновляет параметры ленты (метраж, плотность, всего диодов)"""
        meters = self.strip_meters_spin.value()
        density = self._get_strip_density()
        total_leds = int(round(meters * density))  # Округляем до целого числа
        
        self.strip_total_leds_label.setText(str(total_leds))
        self._set_top("strip_meters", meters)
        self._set_top("strip_density", density)
        self._set_top("wled_led_count", total_leds)

        diagonal_inches = self.strip_diagonal_spin.value()
        self._set_top("monitor_diagonal_inches", diagonal_inches)

        # Точный расчёт реального периметра монитора по диагонали и пропорциям
        # (aspect_ratio уже посчитан из screen_width/screen_height, обычно 16:9)
        diagonal_cm = diagonal_inches * 2.54
        aspect = self.strip_screen_aspect
        height_cm = diagonal_cm / (aspect ** 2 + 1) ** 0.5
        width_cm = height_cm * aspect
        real_perimeter_m = 2 * (width_cm + height_cm) / 100

        self.strip_monitor_size_label.setText(f"≈ {width_cm:.1f} × {height_cm:.1f} см (периметр {real_perimeter_m:.2f} м)")

        # Масштаб холста: периметр холста (в пикселях) соответствует реальному
        # периметру монитора (в метрах), посчитанному выше - точнее, чем усреднённое
        # допущение "обычно 2 метра", раз мы знаем настоящую диагональ
        canvas_perimeter = 2 * (self.strip_canvas_width + self.strip_canvas_height)
        self.strip_pixels_per_meter = canvas_perimeter / real_perimeter_m
        self.strip_max_path_pixels = meters * self.strip_pixels_per_meter

        self._update_strip_length_label()
        self._update_manual_progress_label()
        
        # Обновление реального маппера произойдёт при нажатии "Построить маппинг" -
        # тут просто обновляем локальные параметры интерфейса и превью-холст
        self._draw_strip_canvas()

    def _update_strip_length_label(self):
        """Обновляет надпись с текущей нарисованной длиной пути"""
        drawn_px = self._path_length(self.strip_path_points) if len(self.strip_path_points) > 1 else 0.0
        drawn_m = drawn_px / self.strip_pixels_per_meter if self.strip_pixels_per_meter else 0.0
        max_m = self.strip_meters_spin.value()
        self.strip_length_label.setText(f"Нарисовано: {drawn_m:.2f} м / {max_m:.1f} м")
        if drawn_m >= max_m:
            self.strip_length_label.setStyleSheet("font-weight: bold; color: #e05050;")
        else:
            self.strip_length_label.setStyleSheet("font-weight: bold;")

    def _draw_strip_canvas(self):
        """Перерисовывает холст с сеткой и путём ленты (кружочками диодов)"""
        from PyQt5.QtGui import QPixmap, QPainter, QPen, QColor, QBrush
        
        w = self.strip_canvas_width
        h = self.strip_canvas_height
        
        pixmap = QPixmap(w, h)
        pixmap.fill(QColor(17, 17, 17))
        
        painter = QPainter(pixmap)

        # Если есть снимок экрана - рисуем его как затемнённый фон,
        # чтобы было видно реальное содержимое монитора под сеткой и диодами
        if self.strip_background_pixmap is not None:
            painter.drawPixmap(0, 0, self.strip_background_pixmap)
            painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 140))  # затемнение для контраста

        # Рисуем рамку экрана (белая граница)
        pen_border = QPen(QColor(255, 255, 255))
        pen_border.setWidth(2)
        painter.setPen(pen_border)
        painter.drawRect(0, 0, w - 1, h - 1)
        
        # Рисуем сетку (имитация монитора) - только если нет фона-скриншота,
        # чтобы не загромождать реальную картинку лишними линиями
        if self.strip_background_pixmap is None:
            pen_grid = QPen(QColor(60, 60, 60))
            pen_grid.setWidth(1)
            painter.setPen(pen_grid)

            grid_step = 50
            for x in range(0, w, grid_step):
                painter.drawLine(x, 0, x, h)
            for y in range(0, h, grid_step):
                painter.drawLine(0, y, w, y)
        
        # Рисуем путь ленты (прямые линии между точками) - для straight/freehand
        if self.strip_draw_mode != "manual" and len(self.strip_path_points) > 1:
            pen_path = QPen(QColor(255, 255, 0))
            pen_path.setWidth(2)
            painter.setPen(pen_path)
            
            for i in range(len(self.strip_path_points) - 1):
                x1, y1 = self.strip_path_points[i]
                x2, y2 = self.strip_path_points[i + 1]
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # В режиме "straight" - показываем пунктирную превью-линию от последней точки
        # к текущей позиции мыши, чтобы видеть куда ляжет следующий отрезок
        if self.strip_draw_mode == "straight" and self.strip_path_points and self.strip_preview_point:
            pen_preview = QPen(QColor(255, 255, 0, 140))
            pen_preview.setWidth(1)
            pen_preview.setStyle(Qt.DashLine)
            painter.setPen(pen_preview)
            last_x, last_y = self.strip_path_points[-1]
            px, py = self.strip_preview_point
            painter.drawLine(int(last_x), int(last_y), int(px), int(py))

        # Отмечаем поставленные точки-вершины (видно в режиме "straight")
        if self.strip_draw_mode == "straight" and self.strip_path_points:
            pen_vertex = QPen(QColor(255, 200, 0))
            pen_vertex.setWidth(1)
            painter.setPen(pen_vertex)
            painter.setBrush(QBrush(QColor(255, 200, 0)))
            for x, y in self.strip_path_points:
                painter.drawEllipse(int(x) - 3, int(y) - 3, 6, 6)

        # Режим "manual" - рисуем светодиоды РОВНО в тех местах, куда кликнул
        # пользователь, без какого-либо авто-распределения. Соединяем тонкой
        # линией по порядку (важно для физического порядка на реальной ленте)
        # и подписываем номер у первого, последнего и каждого 10го диода.
        if self.strip_draw_mode == "manual" and self.strip_manual_points:
            if len(self.strip_manual_points) > 1:
                pen_seq = QPen(QColor(150, 150, 0))
                pen_seq.setWidth(1)
                painter.setPen(pen_seq)
                for i in range(len(self.strip_manual_points) - 1):
                    x1, y1 = self.strip_manual_points[i]
                    x2, y2 = self.strip_manual_points[i + 1]
                    painter.drawLine(int(x1), int(y1), int(x2), int(y2))

            brush = QBrush(QColor(0, 255, 0))
            pen_led = QPen(QColor(0, 200, 0))
            pen_led.setWidth(1)
            painter.setPen(pen_led)
            painter.setBrush(brush)
            led_radius = 5
            for i, (x, y) in enumerate(self.strip_manual_points):
                painter.drawEllipse(int(x) - led_radius, int(y) - led_radius, led_radius * 2, led_radius * 2)
                if i == 0 or i == len(self.strip_manual_points) - 1 or (i + 1) % 10 == 0:
                    painter.setPen(QColor(255, 255, 255))
                    painter.drawText(int(x) + 6, int(y) - 6, str(i + 1))
                    painter.setPen(pen_led)
        
        # Рисуем светодиоды (кружочки) равномерно вдоль пути - только для
        # straight/freehand режимов (manual уже отрисован выше как есть)
        total_leds = self.strip_total_leds_label.text()
        try:
            led_count = int(total_leds)
        except ValueError:
            led_count = 90
        
        if self.strip_draw_mode != "manual" and len(self.strip_path_points) > 1 and led_count > 0:
            # Равномерно распределяем диоды ПО ДЛИНЕ пути (не по числу точек мыши) -
            # так скорость рисования не влияет на расстояние между диодами
            led_positions = self._resample_path_evenly(self.strip_path_points, led_count)
            
            # Рисуем кружочки
            brush = QBrush(QColor(0, 255, 0))
            pen_led = QPen(QColor(0, 200, 0))
            pen_led.setWidth(1)
            painter.setPen(pen_led)
            painter.setBrush(brush)
            
            led_radius = 4
            for x, y in led_positions:
                painter.drawEllipse(int(x) - led_radius, int(y) - led_radius, led_radius * 2, led_radius * 2)
        
        painter.end()
        self.strip_canvas.setPixmap(pixmap)

    def _on_strip_canvas_mouse_press(self, event):
        """Добавление точки/светодиода в зависимости от выбранного режима"""
        if event.button() != Qt.LeftButton:
            return

        x, y = event.x(), event.y()
        if not (0 <= x < self.strip_canvas_width and 0 <= y < self.strip_canvas_height):
            return

        if self.strip_draw_mode == "manual":
            self._try_add_manual_led(x, y)
        elif self.strip_draw_mode == "straight":
            # Режим точек: каждый клик добавляет ровно одну точку,
            # линия между соседними точками рисуется идеально прямой
            self._try_add_strip_point(x, y)
        else:
            # Свободное рисование: продолжаем путь с этой точки, а не начинаем
            # заново - иначе второй штрих стирал первый и спираль приходилось
            # рисовать одним движением мыши. Начать заново - кнопка «Очистить».
            self.strip_path_points.append((x, y))
            self._update_strip_length_label()
            self._draw_strip_canvas()

    def _try_add_manual_led(self, x, y):
        """Добавляет позицию одного светодиода в режиме ручной расстановки"""
        total_leds_str = self.strip_total_leds_label.text()
        try:
            total_leds = int(total_leds_str)
        except ValueError:
            total_leds = 90

        if len(self.strip_manual_points) >= total_leds:
            QMessageBox.information(
                self, "Готово",
                f"Все {total_leds} светодиодов уже расставлены!\n"
                f"Нажми 'Отменить точку' чтобы поправить последний, или 'Построить маппинг'."
            )
            return

        self.strip_manual_points.append((x, y))
        self._update_manual_progress_label()
        self._draw_strip_canvas()

    def _try_add_strip_point(self, x, y):
        """Добавляет точку с проверкой лимита длины (используется в режиме точек)"""
        if not self.strip_path_points:
            self.strip_path_points = [(x, y)]
            self._update_strip_length_label()
            self._draw_strip_canvas()
            return

        last_x, last_y = self.strip_path_points[-1]
        seg_len = ((x - last_x) ** 2 + (y - last_y) ** 2) ** 0.5
        current_len = self._path_length(self.strip_path_points)

        max_len = getattr(self, "strip_max_path_pixels", None)
        if max_len is not None and current_len + seg_len > max_len:
            remaining = max_len - current_len
            if remaining > 0 and seg_len > 0:
                t = remaining / seg_len
                clipped_x = last_x + t * (x - last_x)
                clipped_y = last_y + t * (y - last_y)
                self.strip_path_points.append((clipped_x, clipped_y))
            self._update_strip_length_label()
            self._draw_strip_canvas()
            return

        self.strip_path_points.append((x, y))
        self._update_strip_length_label()
        self._draw_strip_canvas()

    def _on_strip_canvas_mouse_move(self, event):
        """В режиме 'straight' - показывает превью следующей прямой линии.
        В режиме 'freehand' - рисует при зажатой кнопке мыши.
        В режиме 'manual' - ничего (позиции ставятся только кликом)."""
        x, y = event.x(), event.y()
        in_bounds = 0 <= x < self.strip_canvas_width and 0 <= y < self.strip_canvas_height

        if self.strip_draw_mode == "manual":
            return

        if self.strip_draw_mode == "straight":
            if in_bounds and self.strip_path_points:
                self.strip_preview_point = (x, y)
                self._draw_strip_canvas()
            return

        # Свободное рисование - только при зажатой левой кнопке
        if not (event.buttons() & Qt.LeftButton):
            return
        if not self.strip_path_points:
            return
        if not in_bounds:
            return

        last_x, last_y = self.strip_path_points[-1]
        seg_len = ((x - last_x) ** 2 + (y - last_y) ** 2) ** 0.5
        current_len = self._path_length(self.strip_path_points)

        max_len = getattr(self, "strip_max_path_pixels", None)
        if max_len is not None and current_len + seg_len > max_len:
            remaining = max_len - current_len
            if remaining > 0 and seg_len > 0:
                t = remaining / seg_len
                clipped_x = last_x + t * (x - last_x)
                clipped_y = last_y + t * (y - last_y)
                self.strip_path_points.append((clipped_x, clipped_y))
            self._update_strip_length_label()
            self._draw_strip_canvas()
            return

        self.strip_path_points.append((x, y))
        self._update_strip_length_label()
        self._draw_strip_canvas()

    def _on_strip_canvas_mouse_release(self, event):
        """Конец рисования"""
        pass

    def _on_strip_draw_mode_changed(self, index):
        """Переключает между тремя режимами разметки"""
        self.strip_draw_mode = self.strip_draw_mode_combo.itemData(index)
        self.strip_preview_point = None
        self._update_manual_progress_label()
        self._draw_strip_canvas()

    def _update_manual_progress_label(self):
        """Обновляет надпись прогресса в режиме ручной расстановки"""
        if self.strip_draw_mode == "manual":
            total = self.strip_total_leds_label.text()
            placed = len(self.strip_manual_points)
            self.strip_manual_progress_label.setText(
                f"Установлено светодиодов: {placed} / {total}"
                + ("  ✅ Готово, можно строить маппинг!" if str(placed) == total else "")
            )
        else:
            self.strip_manual_progress_label.setText("")

    def _on_undo_strip_point(self):
        """Убирает последнюю добавленную точку"""
        if self.strip_draw_mode == "manual":
            if self.strip_manual_points:
                self.strip_manual_points.pop()
                self._update_manual_progress_label()
                self._draw_strip_canvas()
        else:
            if self.strip_path_points:
                self.strip_path_points.pop()
                self._update_strip_length_label()
                self._draw_strip_canvas()

    def _on_reverse_strip_path(self):
        """Разворачивает путь задом наперёд.

        Физическое направление ленты по фото не определить, а от него зависит,
        в какую сторону «бежит» тест-развёртка и совпадает ли порядок цветов.
        Проще перевернуть путь, чем рисовать заново."""
        if self.strip_draw_mode == "manual":
            if len(self.strip_manual_points) < 2:
                self.status_label.setText("Сначала расставь хотя бы 2 светодиода")
                return
            self.strip_manual_points.reverse()
        else:
            if len(self.strip_path_points) < 2:
                self.status_label.setText("Сначала нарисуй путь ленты")
                return
            self.strip_path_points.reverse()

        self.strip_preview_point = None
        self._update_strip_length_label()
        self._update_manual_progress_label()
        self._draw_strip_canvas()
        self.status_label.setText("Путь развёрнут — нажми «Построить маппинг»")

    def _on_capture_screen_background(self):
        """Делает снимок экрана и использует его как фон холста для точного рисования"""
        try:
            screen = QApplication.primaryScreen()
            if screen is None:
                QMessageBox.warning(self, "Снимок экрана", "Не удалось получить доступ к экрану")
                return
            pixmap = screen.grabWindow(0)
            scaled = pixmap.scaled(
                self.strip_canvas_width, self.strip_canvas_height,
                Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            )
            self.strip_background_pixmap = scaled
            self._draw_strip_canvas()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сделать снимок экрана: {e}")

    def _on_clear_strip_path(self):
        """Очищает путь ленты (обоих режимов)"""
        self.strip_path_points = []
        self.strip_manual_points = []
        self.strip_preview_point = None
        self._update_strip_length_label()
        self._update_manual_progress_label()
        self._draw_strip_canvas()

    def _total_leds(self):
        """Текущее число диодов из интерфейса (с запасным вариантом)."""
        try:
            return max(1, int(self.strip_total_leds_label.text()))
        except (TypeError, ValueError):
            return max(1, int(self.config.get("wled_led_count", 90)))

    def _on_apply_layout_preset(self):
        """Строит путь по шаблону «витки» — без рисования мышкой."""
        canvas_w = self.strip_canvas_width
        canvas_h = self.strip_canvas_height
        if not canvas_w or not canvas_h:
            return

        points = build_loop_path(
            canvas_w, canvas_h,
            loops=int(self.loop_count_combo.currentData() or 2),
            start_corner=self.start_corner_combo.currentData(),
            clockwise=bool(self.direction_combo.currentData()),
            inset_pct=float(self.inset_slider.value()),
            gap_pct=float(self.gap_slider.value()),
        )
        if len(points) < 4:
            QMessageBox.warning(
                self, "Раскладка",
                "Слишком большой отступ — путь не помещается в экран",
            )
            return

        # переключаемся в режим прямых отрезков, чтобы холст рисовал как надо
        index = self.strip_draw_mode_combo.findData("straight")
        if index >= 0:
            self.strip_draw_mode_combo.blockSignals(True)
            self.strip_draw_mode_combo.setCurrentIndex(index)
            self.strip_draw_mode_combo.blockSignals(False)
        self.strip_draw_mode = "straight"

        self.strip_path_points = [(float(x), float(y)) for x, y in points]
        self.strip_manual_points = []
        self.strip_preview_point = None
        self._update_strip_length_label()
        self._draw_strip_canvas()
        self._on_build_strip_mapping()
        self.status_label.setText("Раскладка по шаблону применена")

    def _on_mapping_wizard(self):
        """Раскладка по развёртке — самый простой способ настроить маппинг."""
        if self.config.get("strip_mode") not in ("wled", "wled_serial"):
            QMessageBox.information(
                self, "Раскладка по развёртке",
                "Этот способ работает в режиме WLED (Wi-Fi или USB-кабель):\n"
                "BLE-лента умеет светить только одним цветом на всю длину,\n"
                "поэтому раскладывать по диодам нечего."
            )
            return

        count = self._total_leds()
        wizard = MappingWizard(
            self, self.worker, count,
            self.config.get("screen_width", 1920),
            self.config.get("screen_height", 1080),
            getattr(self, "strip_background_pixmap", None),
        )
        accepted = wizard.exec_() == QDialog.Accepted
        points = list(wizard.result_points or [])
        if not accepted or len(points) < 2:
            self.status_label.setText("Раскладка отменена")
            return

        screen_w = int(self.config.get("screen_width", 1920))
        screen_h = int(self.config.get("screen_height", 1080))
        self._set_top("wled_led_count", count)
        self.worker.set_strip_mapper(points, screen_w, screen_h,
                                     exact_positions=False, led_count=count)

        # показываем результат на холсте вкладки
        self.strip_path_points = [
            (x * self.strip_canvas_width / screen_w,
             y * self.strip_canvas_height / screen_h)
            for x, y in points
        ]
        self.strip_manual_points = []
        self._update_strip_length_label()
        self._draw_strip_canvas()
        self.status_label.setText(
            f"Раскладка готова: {len(points)} опорных точек, {count} диодов"
        )
        log.info("Раскладка по развёртке: %s точек, %s диодов", len(points), count)

    def _on_build_strip_mapping(self):
        """Строит маппинг на основе нарисованного пути или ручной расстановки"""
        screen_w = self.config.get("screen_width", 1920)
        screen_h = self.config.get("screen_height", 1080)
        canvas_w = self.strip_canvas_width
        canvas_h = self.strip_canvas_height

        if canvas_w == 0 or canvas_h == 0:
            return

        if self.strip_draw_mode == "manual":
            if len(self.strip_manual_points) < 2:
                QMessageBox.warning(self, "Ошибка", "Расставь хотя бы 2 светодиода")
                return

            total_leds_str = self.strip_total_leds_label.text()
            try:
                total_leds = int(total_leds_str)
            except ValueError:
                total_leds = len(self.strip_manual_points)

            if len(self.strip_manual_points) != total_leds:
                reply = QMessageBox.question(
                    self, "Не все диоды расставлены",
                    f"Расставлено {len(self.strip_manual_points)} из {total_leds} светодиодов.\n"
                    f"Построить маппинг только для расставленных (остальные не будут светиться),\n"
                    f"или сначала доставить оставшиеся?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply == QMessageBox.No:
                    return

            scaled_path = [
                (int(x * screen_w / canvas_w), int(y * screen_h / canvas_h))
                for x, y in self.strip_manual_points
            ]
            self._set_top("wled_led_count", len(scaled_path))
            self.worker.set_strip_mapper(
                scaled_path, screen_w, screen_h,
                exact_positions=True, led_count=len(scaled_path),
            )
            QMessageBox.information(self, "Успех", f"Маппинг построен вручную для {len(scaled_path)} светодиодов - точно там, где кликнул!")
            return

        # Режимы straight / freehand - авто-распределение по длине пути
        if len(self.strip_path_points) < 2:
            QMessageBox.warning(self, "Ошибка", "Нарисуйте путь ленты (минимум 2 точки)")
            return

        scaled_path = [
            (int(x * screen_w / canvas_w), int(y * screen_h / canvas_h))
            for x, y in self.strip_path_points
        ]

        led_count = self._total_leds()
        self._set_top("wled_led_count", led_count)
        self.worker.set_strip_mapper(
            scaled_path, screen_w, screen_h,
            exact_positions=False, led_count=led_count,
        )
        QMessageBox.information(self, "Успех", f"Маппинг построен для {len(scaled_path)} точек пути,\n{led_count} светодиодов")

    def _on_save_strip_config(self):
        """Сохраняет конфигурацию ленты"""
        from PyQt5.QtWidgets import QFileDialog
        filepath, _ = QFileDialog.getSaveFileName(self, "Сохранить конфигурацию", "", "JSON (*.json)")
        if filepath:
            self.worker.save_strip_mapping(filepath)

    def _on_load_strip_config(self):
        """Загружает конфигурацию ленты"""
        from PyQt5.QtWidgets import QFileDialog
        filepath, _ = QFileDialog.getOpenFileName(self, "Загрузить конфигурацию", "", "JSON (*.json)")
        if filepath:
            self.worker.load_strip_mapping(filepath)

    def _on_mapping_loaded(self, data):
        """Вызывается, когда worker реально загрузил и построил маппинг из файла -
        переводим координаты обратно в пространство холста и перерисовываем,
        иначе после загрузки на экране визуально ничего не менялось."""
        led_path = data.get("led_path") or []
        screen_w = data.get("screen_width") or 0
        screen_h = data.get("screen_height") or 0
        led_count = data.get("led_count", 0)
        exact_positions = data.get("exact_positions", False)

        if not led_path or not screen_w or not screen_h:
            QMessageBox.warning(self, "Загрузка", "Файл загружен, но в нём нет пути ленты для отображения")
            return

        canvas_w = self.strip_canvas_width
        canvas_h = self.strip_canvas_height
        canvas_path = [
            (x * canvas_w / screen_w, y * canvas_h / screen_h)
            for x, y in led_path
        ]

        self.strip_total_leds_label.setText(str(led_count))
        if led_count:
            # Синхронизируем конфиг: иначе следующее сохранение настроек
            # вернуло бы старое число диодов и лента снова «обрезалась» бы
            self.config["wled_led_count"] = led_count
            self._queue_save()

        if exact_positions:
            self.strip_manual_points = canvas_path
            self.strip_path_points = []
            target_mode = "manual"
        else:
            self.strip_path_points = canvas_path
            self.strip_manual_points = []
            target_mode = "straight"

        idx = self.strip_draw_mode_combo.findData(target_mode)
        if idx >= 0:
            # blockSignals - чтобы не запустить лишний _on_strip_draw_mode_changed
            # посреди обновления (он сам всё равно ничего не сломает, но незачем)
            self.strip_draw_mode_combo.blockSignals(True)
            self.strip_draw_mode_combo.setCurrentIndex(idx)
            self.strip_draw_mode_combo.blockSignals(False)
            self.strip_draw_mode = target_mode

        self.strip_preview_point = None
        self._update_strip_length_label()
        self._update_manual_progress_label()
        self._draw_strip_canvas()

    # -- Вкладка профилей --

    def _build_profiles_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        info = QLabel(
            "Профили применяются автоматически по активному окну.\n"
            "desktop — рабочий стол/прочее, movie — плеер (Opera), game — игры из списка ниже."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        profiles_box = QGroupBox("Настройки профилей")
        profiles_form = QFormLayout(profiles_box)

        for name in ("desktop", "movie", "game"):
            profile = self.config["profiles"][name]

            mode_combo = QComboBox()
            mode_combo.addItems(["quadrants", "edges", "center_quadrants"])
            mode_combo.setCurrentText(profile["mode"])
            mode_combo.currentTextChanged.connect(
                lambda text, n=name: self._set_profile_field(n, "mode", text)
            )

            delay_spin = QDoubleSpinBox()
            delay_spin.setRange(0.01, 1.0)
            delay_spin.setSingleStep(0.01)
            delay_spin.setValue(profile["delay"])
            delay_spin.valueChanged.connect(
                lambda v, n=name: self._set_profile_field(n, "delay", v)
            )

            row = QHBoxLayout()
            row.addWidget(QLabel("режим:"))
            row.addWidget(mode_combo)
            row.addWidget(QLabel("задержка, с:"))
            row.addWidget(delay_spin)
            container = QWidget()
            container.setLayout(row)
            profiles_form.addRow(name, container)

        layout.addWidget(profiles_box)

        unknown_box = QGroupBox("Профиль для неизвестных процессов")
        unknown_form = QFormLayout(unknown_box)
        unknown_combo = QComboBox()
        unknown_combo.addItem("desktop — рабочий стол, браузеры", "desktop")
        unknown_combo.addItem("movie — как плеер", "movie")
        unknown_combo.addItem("game — как игра", "game")
        current_unknown = self.config.get("unknown_process_profile", "desktop")
        index = unknown_combo.findData(current_unknown)
        unknown_combo.setCurrentIndex(max(0, index))
        unknown_combo.currentIndexChanged.connect(
            lambda i: self._set_top("unknown_process_profile", unknown_combo.itemData(i))
        )
        unknown_form.addRow("Если процесс не в списках:", unknown_combo)
        unknown_form.addRow(QLabel(
            "Сюда попадают все приложения, которых нет в списке игр.\n"
            "Раньше здесь жёстко стоял game — и профиль desktop не включался никогда."
        ))
        layout.addWidget(unknown_box)

        games_box = QGroupBox("Список игровых процессов (по одному на строку, например cs2.exe)")
        games_layout = QVBoxLayout(games_box)
        self.games_edit = QPlainTextEdit()
        self.games_edit.setPlainText("\n".join(self.config.get("game_processes", [])))
        self.games_edit.textChanged.connect(self._on_games_edited)
        games_layout.addWidget(self.games_edit)
        layout.addWidget(games_box)

        # --- привязка приложений к сценам ---
        app_box = QGroupBox("Сцены для приложений")
        app_form = QFormLayout(app_box)
        app_form.addRow(QLabel(
            "Одна строка — одно приложение: «cs2.exe = Игра».\n"
            "Когда это окно станет активным, приложение включит указанную сцену."
        ))
        self.app_scenes_edit = QPlainTextEdit()
        self.app_scenes_edit.setMaximumHeight(110)
        self.app_scenes_edit.setPlainText(
            "\n".join(f"{proc} = {scene}"
                      for proc, scene in (self.config.get("app_scenes") or {}).items())
        )
        self.app_scenes_edit.textChanged.connect(self._on_app_scenes_edited)
        app_form.addRow(self.app_scenes_edit)

        app_row = QHBoxLayout()
        self.app_scene_combo = QComboBox()
        for scene_name in SCENE_PRESETS:
            self.app_scene_combo.addItem(scene_name, scene_name)
        bind_btn = QPushButton("Привязать активное окно")
        bind_btn.clicked.connect(self._on_bind_active_window)
        app_row.addWidget(bind_btn)
        app_row.addWidget(self.app_scene_combo)
        app_row.addStretch()
        app_form.addRow(app_row)
        layout.addWidget(app_box)

        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("Целевой FPS захвата экрана:"))
        fps_spin = QSpinBox()
        fps_spin.setRange(10, 60)
        fps_spin.setValue(self.config.get("target_fps", 30))
        fps_spin.valueChanged.connect(lambda v: self._set_top("target_fps", v))
        fps_row.addWidget(fps_spin)
        fps_row.addStretch()
        layout.addLayout(fps_row)

        layout.addStretch()
        return w

    # -- Вкладка «Система» --

    def _build_system_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        # --- производительность ---
        perf_box = QGroupBox("Производительность")
        perf_form = QFormLayout(perf_box)

        adaptive = self.config.get("adaptive_fps", {})
        adaptive_cb = QCheckBox("Снижать частоту кадров, когда картинка не меняется")
        adaptive_cb.setToolTip(
            "На статичном рабочем столе незачем гонять 60 кадров/с:\n"
            "меньше нагрузка на процессор и на Wi-Fi платы."
        )
        adaptive_cb.setChecked(adaptive.get("enabled", True))
        adaptive_cb.stateChanged.connect(
            lambda s: self._set_nested("adaptive_fps", "enabled", s == Qt.Checked)
        )
        perf_form.addRow(adaptive_cb)
        _slider_row(
            perf_form, "Минимум кадров на статике", 1, 30,
            int(adaptive.get("min_fps", 10)),
            lambda v: self._set_nested("adaptive_fps", "min_fps", v), " FPS"
        )

        backend_combo = QComboBox()
        backend_combo.addItem("DXGI (быстрый, по умолчанию)", "dxgi")
        backend_combo.addItem("WGC (работает в HDR и эксклюзивном полном экране)", "wgc")
        current_backend = self.config.get("capture_backend", "dxgi")
        backend_combo.setCurrentIndex(max(0, backend_combo.findData(current_backend)))
        backend_combo.currentIndexChanged.connect(
            lambda i: self._set_top("capture_backend", backend_combo.itemData(i))
        )
        perf_form.addRow("Способ захвата экрана:", backend_combo)
        perf_form.addRow(QLabel(
            "WGC требует пакет windows-capture. Если он не установлен,\n"
            "приложение автоматически вернётся к DXGI."
        ))
        layout.addWidget(perf_box)

        # --- питание ---
        power_box = QGroupBox("Ограничение по току (защита блока питания)")
        power_form = QFormLayout(power_box)
        power_form.addRow(QLabel(
            "На белой вспышке 90 диодов легко упираются в возможности блока.\n"
            "Приложение мягко приглушит кадр, если оценка превысит лимит."
        ))
        power_cfg = self.config.get("power", {})
        limit_spin = QSpinBox()
        limit_spin.setRange(0, 50000)
        limit_spin.setSingleStep(100)
        limit_spin.setSuffix(" мА")
        limit_spin.setSpecialValueText("не ограничивать")
        limit_spin.setValue(int(power_cfg.get("limit_ma", 0)))
        limit_spin.valueChanged.connect(lambda v: self._set_nested("power", "limit_ma", v))
        power_form.addRow("Лимит блока питания:", limit_spin)

        led_ma_spin = QSpinBox()
        led_ma_spin.setRange(1, 200)
        led_ma_spin.setSuffix(" мА/диод")
        led_ma_spin.setValue(int(power_cfg.get("led_ma", 55)))
        led_ma_spin.valueChanged.connect(lambda v: self._set_nested("power", "led_ma", v))
        power_form.addRow("Потребление диода:", led_ma_spin)
        layout.addWidget(power_box)

        # --- пауза ---
        pause_box = QGroupBox("Пауза")
        pause_form = QFormLayout(pause_box)
        pause_cfg = self.config.get("auto_pause", {})
        pause_enabled = QCheckBox("Включить автопаузу")
        pause_enabled.setChecked(pause_cfg.get("enabled", True))
        pause_enabled.stateChanged.connect(
            lambda s: self._set_nested("auto_pause", "enabled", s == Qt.Checked)
        )
        pause_form.addRow(pause_enabled)

        lock_cb = QCheckBox("Останавливать при блокировке экрана (Win+L)")
        lock_cb.setChecked(pause_cfg.get("pause_on_lock", True))
        lock_cb.stateChanged.connect(
            lambda s: self._set_nested("auto_pause", "pause_on_lock", s == Qt.Checked)
        )
        pause_form.addRow(lock_cb)

        idle_row = QHBoxLayout()
        idle_cb = QCheckBox("Останавливать при простое дольше")
        idle_cb.setChecked(pause_cfg.get("idle_minutes", 5) > 0)
        idle_spin = QSpinBox()
        idle_spin.setRange(1, 120)
        idle_spin.setSuffix(" мин")
        idle_spin.setValue(max(1, int(pause_cfg.get("idle_minutes", 5))))
        idle_cb.stateChanged.connect(lambda s: self._set_nested(
            "auto_pause", "idle_minutes", idle_spin.value() if s == Qt.Checked else 0))
        idle_spin.valueChanged.connect(lambda v: self._set_nested(
            "auto_pause", "idle_minutes", v if idle_cb.isChecked() else 0))
        idle_row.addWidget(idle_cb)
        idle_row.addWidget(idle_spin)
        idle_row.addStretch()
        pause_form.addRow(idle_row)
        layout.addWidget(pause_box)

        # --- расписание и рассвет ---
        sched_box = QGroupBox("Расписание и рассвет")
        sched_form = QFormLayout(sched_box)
        sched = self.config.get("schedule", {})
        sched_cb = QCheckBox("Переключать сцену днём и ночью")
        sched_cb.setChecked(sched.get("enabled", False))
        sched_cb.stateChanged.connect(
            lambda s: self._set_nested("schedule", "enabled", s == Qt.Checked)
        )
        sched_form.addRow(sched_cb)

        night_row = QHBoxLayout()
        night_row.addWidget(QLabel("с"))
        self.night_start_edit = QTimeEdit()
        self.night_start_edit.setDisplayFormat("HH:mm")
        self.night_start_edit.setTime(QTime.fromString(sched.get("night_start", "23:00"), "HH:mm"))
        self.night_start_edit.timeChanged.connect(
            lambda t: self._set_nested("schedule", "night_start", t.toString("HH:mm")))
        night_row.addWidget(self.night_start_edit)
        night_row.addWidget(QLabel("до"))
        self.night_end_edit = QTimeEdit()
        self.night_end_edit.setDisplayFormat("HH:mm")
        self.night_end_edit.setTime(QTime.fromString(sched.get("night_end", "07:00"), "HH:mm"))
        self.night_end_edit.timeChanged.connect(
            lambda t: self._set_nested("schedule", "night_end", t.toString("HH:mm")))
        night_row.addWidget(self.night_end_edit)
        night_row.addStretch()
        sched_form.addRow("Ночные часы:", night_row)

        self.night_scene_combo = QComboBox()
        for scene_name in SCENE_PRESETS:
            self.night_scene_combo.addItem(scene_name, scene_name)
        self.night_scene_combo.setCurrentIndex(
            max(0, self.night_scene_combo.findData(sched.get("night_scene", "Ночь"))))
        self.night_scene_combo.currentIndexChanged.connect(
            lambda i: self._set_nested("schedule", "night_scene", self.night_scene_combo.itemData(i)))
        sched_form.addRow("Ночью включать:", self.night_scene_combo)

        self.day_scene_combo = QComboBox()
        self.day_scene_combo.addItem("— не менять —", "")
        for scene_name in SCENE_PRESETS:
            self.day_scene_combo.addItem(scene_name, scene_name)
        self.day_scene_combo.setCurrentIndex(
            max(0, self.day_scene_combo.findData(sched.get("day_scene", ""))))
        self.day_scene_combo.currentIndexChanged.connect(
            lambda i: self._set_nested("schedule", "day_scene", self.day_scene_combo.itemData(i)))
        sched_form.addRow("Днём включать:", self.day_scene_combo)

        sunrise_cfg = self.config.get("sunrise", {})
        sunrise_cb = QCheckBox("Рассвет-будильник (плавно разгорается)")
        sunrise_cb.setChecked(sunrise_cfg.get("enabled", False))
        sunrise_cb.stateChanged.connect(
            lambda s: self._set_nested("sunrise", "enabled", s == Qt.Checked)
        )
        sched_form.addRow(sunrise_cb)

        sun_row = QHBoxLayout()
        sun_row.addWidget(QLabel("к"))
        self.sunrise_time_edit = QTimeEdit()
        self.sunrise_time_edit.setDisplayFormat("HH:mm")
        self.sunrise_time_edit.setTime(QTime.fromString(sunrise_cfg.get("time", "07:00"), "HH:mm"))
        self.sunrise_time_edit.timeChanged.connect(
            lambda t: self._set_nested("sunrise", "time", t.toString("HH:mm")))
        sun_row.addWidget(self.sunrise_time_edit)
        sun_row.addWidget(QLabel("за"))
        self.sunrise_duration_spin = QSpinBox()
        self.sunrise_duration_spin.setRange(1, 120)
        self.sunrise_duration_spin.setSuffix(" мин")
        self.sunrise_duration_spin.setValue(int(sunrise_cfg.get("duration_minutes", 20)))
        self.sunrise_duration_spin.valueChanged.connect(
            lambda v: self._set_nested("sunrise", "duration_minutes", v))
        sun_row.addWidget(self.sunrise_duration_spin)
        sun_row.addStretch()
        sched_form.addRow(sun_row)
        layout.addWidget(sched_box)

        # --- горячие клавиши ---
        keys_box = QGroupBox("Горячие клавиши (работают из любого приложения)")
        keys_form = QFormLayout(keys_box)
        hotkeys_cfg = self.config.get("hotkeys", {})
        hotkeys_cb = QCheckBox("Включить глобальные горячие клавиши")
        hotkeys_cb.setChecked(hotkeys_cfg.get("enabled", True))
        hotkeys_cb.stateChanged.connect(self._on_hotkeys_toggled)
        keys_form.addRow(hotkeys_cb)
        keys_form.addRow(QLabel(
            "Ctrl+Alt+L — запустить / остановить подсветку\n"
            "Ctrl+Alt+↑ / ↓ — яркость больше / меньше\n"
            "Ctrl+Alt+B — погасить ленту\n"
            "Ctrl+Alt+D — диагностика"
        ))
        layout.addWidget(keys_box)

        # --- пульт с телефона ---
        remote_box = QGroupBox("Пульт с телефона")
        remote_form = QFormLayout(remote_box)
        remote_cfg = self.config.get("remote", {})
        remote_cb = QCheckBox("Включить веб-пульт (телефон в той же Wi-Fi)")
        remote_cb.setChecked(remote_cfg.get("enabled", True))
        remote_cb.stateChanged.connect(self._on_remote_toggled)
        remote_form.addRow(remote_cb)

        self.remote_url_label = QLabel("Пульт выключен")
        self.remote_url_label.setStyleSheet("color: #8fb8ff;")
        self.remote_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        remote_form.addRow(self.remote_url_label)

        remote_buttons = QHBoxLayout()
        qr_btn = QPushButton("QR-код для телефона")
        qr_btn.clicked.connect(self._on_show_qr)
        remote_buttons.addWidget(qr_btn)
        open_btn = QPushButton("Открыть на этом ПК")
        open_btn.clicked.connect(self._on_open_remote)
        remote_buttons.addWidget(open_btn)
        remote_buttons.addStretch()
        remote_form.addRow(remote_buttons)

        remote_form.addRow(QLabel(
            "⚠ Адрес нужно открывать именно с http:// — Safari по умолчанию\n"
            "пробует https:// и пишет «не удалось установить безопасное соединение».\n"
            "Проще всего — отсканировать QR-код камерой телефона.\n"
            "Сервер слушает локальную сеть: любой в ней сможет управлять подсветкой."
        ))
        layout.addWidget(remote_box)

        # --- MQTT ---
        mqtt_box = QGroupBox("MQTT / Home Assistant")
        mqtt_form = QFormLayout(mqtt_box)
        mqtt_cfg = self.config.get("mqtt", {})
        mqtt_cb = QCheckBox("Публиковать состояние и принимать команды")
        mqtt_cb.setChecked(mqtt_cfg.get("enabled", False))
        mqtt_cb.stateChanged.connect(self._on_mqtt_toggled)
        mqtt_form.addRow(mqtt_cb)

        mqtt_row = QHBoxLayout()
        self.mqtt_host_edit = QLineEdit(mqtt_cfg.get("host", "127.0.0.1"))
        self.mqtt_host_edit.editingFinished.connect(
            lambda: self._set_nested("mqtt", "host", self.mqtt_host_edit.text().strip()))
        self.mqtt_port_spin = QSpinBox()
        self.mqtt_port_spin.setRange(1, 65535)
        self.mqtt_port_spin.setValue(int(mqtt_cfg.get("port", 1883)))
        self.mqtt_port_spin.valueChanged.connect(
            lambda v: self._set_nested("mqtt", "port", v))
        mqtt_row.addWidget(self.mqtt_host_edit)
        mqtt_row.addWidget(self.mqtt_port_spin)
        mqtt_form.addRow("Брокер:", mqtt_row)

        self.mqtt_prefix_edit = QLineEdit(mqtt_cfg.get("prefix", "ledambilight"))
        self.mqtt_prefix_edit.editingFinished.connect(
            lambda: self._set_nested("mqtt", "prefix", self.mqtt_prefix_edit.text().strip()))
        mqtt_form.addRow("Префикс топиков:", self.mqtt_prefix_edit)
        mqtt_form.addRow(QLabel(
            "Состояние: <префикс>/state, команды: <префикс>/set (JSON).\n"
            "Нужен пакет paho-mqtt и свой брокер (например, Mosquitto)."
        ))
        layout.addWidget(mqtt_box)

        # --- обновления ---
        updates_box = QGroupBox("Обновления")
        updates_form = QFormLayout(updates_box)
        updates_cfg = self.config.get("updates", {})
        updates_cb = QCheckBox("Проверять обновления при запуске")
        updates_cb.setChecked(updates_cfg.get("check_on_start", True))
        updates_cb.stateChanged.connect(
            lambda s: self._set_nested("updates", "check_on_start", s == Qt.Checked)
        )
        updates_form.addRow(updates_cb)

        updates_row = QHBoxLayout()
        self.update_status_label = QLabel(f"Установлена версия {__version__}")
        self.update_status_label.setStyleSheet("color: #8a8b93;")
        self.update_status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        updates_row.addWidget(self.update_status_label)
        updates_row.addStretch()
        self.update_btn = QPushButton("Проверить обновления")
        self.update_btn.clicked.connect(self._on_check_updates)
        updates_row.addWidget(self.update_btn)
        updates_form.addRow(updates_row)
        updates_form.addRow(QLabel(
            "Приложение спрашивает у GitHub номер последнего релиза.\n"
            "Наружу уходит только версия в User-Agent, ничего личного."
        ))
        layout.addWidget(updates_box)

        layout.addStretch()
        return w

    def _on_hotkeys_toggled(self, state):
        enabled = state == Qt.Checked
        self._set_nested("hotkeys", "enabled", enabled)
        if not enabled:
            if self.hotkey_filter is not None:
                self.hotkey_filter.set_enabled(False)
        elif self.hotkey_filter is None:
            self._setup_hotkeys()
        else:
            self.hotkey_filter.set_enabled(True)

    # -- Проверка обновлений --

    def _on_check_updates(self, silent=False):
        """Запускает проверку в фоне, чтобы не подвешивать интерфейс на 5-6 с."""
        if self._update_check_running:
            return
        self._update_check_running = True
        self.update_btn.setEnabled(False)
        if not silent:
            self.update_status_label.setText("Проверяю обновления...")
        threading.Thread(
            target=self._check_updates_worker, args=(bool(silent),), daemon=True
        ).start()

    def _check_updates_worker(self, silent):
        info = check_for_update(__version__)
        try:
            self.update_checked.emit((info, silent))
        except RuntimeError:
            pass  # окно уже закрыли, пока шёл запрос к GitHub

    def _on_update_checked(self, payload):
        info, silent = payload
        self._update_check_running = False
        self.update_btn.setEnabled(True)

        if info is None:
            if not silent:
                self.update_status_label.setText("Не удалось проверить обновления")
            return

        if not info.available:
            self.update_status_label.setText(
                f"Установлена последняя версия ({info.current})"
            )
            if not silent:
                QMessageBox.information(
                    self, "Обновления",
                    f"Установлена последняя версия: {info.current}.",
                )
            return

        self.update_status_label.setText(f"Доступна версия {info.latest}")
        if silent:
            # Не мешаем модальным окном: подсветим строку и трей.
            if getattr(self, "tray_icon", None) is not None:
                self.tray_icon.showMessage(
                    "LED Ambilight",
                    f"Доступна версия {info.latest}. Вкладка «Система» → «Проверить обновления».",
                    QSystemTrayIcon.Information,
                    5000,
                )
            return

        box = QMessageBox(self)
        box.setWindowTitle("Доступно обновление")
        box.setIcon(QMessageBox.Information)
        box.setText(
            f"Установлена версия {info.current}, на GitHub уже {info.latest}."
        )
        if info.notes:
            box.setInformativeText(info.notes[:600])
        open_btn = box.addButton("Открыть релиз", QMessageBox.AcceptRole)
        box.addButton("Позже", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl(info.url))

    def _apply_scene(self, name):
        """Применяет готовый пресет, выставляя значения виджетов.

        Виджеты сами прогоняют изменения через свои обработчики, поэтому
        конфиг и интерфейс остаются согласованными."""
        preset = SCENE_PRESETS.get(name)
        if not preset:
            return

        # во сколько раз значение пресета отличается от целого значения слайдера
        slider_scale = {
            "max_scale": 100.0,
            "min_brightness": 1.0,
            "smoothing_min": 100.0,
            "smoothing_max": 100.0,
            "dominant_weight": 100.0,
        }
        for key, scale in slider_scale.items():
            slider = self._scene_sliders.get(key)
            if slider is None or key not in preset:
                continue
            slider.setValue(int(round(preset[key] * scale)))

        audio_combo = getattr(self, "audio_mode_combo", None)
        if audio_combo is not None and preset.get("audio"):
            index = audio_combo.findData(preset["audio"])
            if index >= 0:
                audio_combo.setCurrentIndex(index)

        self.status_label.setText(f"Сцена: {name}")
        self._last_scene_name = name
        log.info("Применён пресет %s", name)

    # -- Мастер первого запуска --

    def _maybe_show_welcome(self):
        if self.config.get("first_run_done", False):
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Быстрый старт")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)

        title = QLabel("С чего начать")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        steps = QLabel(
            "1. Подключи ленту: вкладка «Подключение» → выбери устройство → «Подключить».\n\n"
            "2. Укажи метраж и плотность ленты на вкладке «Конфигурация ленты».\n\n"
            "3. Нарисуй путь ленты на схеме монитора и нажми «Построить маппинг».\n\n"
            "4. Запусти «Тест-развёртку»: диоды зажигаются по одному. Если бежит не в ту\n"
            "    сторону — «Реверс пути».\n\n"
            "5. Нажми «▶ Запустить Ambilight» на вкладке «Подключение»."
        )
        steps.setWordWrap(True)
        layout.addWidget(steps)

        skip_cb = QCheckBox("Больше не показывать этот мастер")
        skip_cb.setChecked(True)
        layout.addWidget(skip_cb)

        buttons = QHBoxLayout()
        to_connection = QPushButton("К подключению")
        to_connection.clicked.connect(lambda: (self._goto_tab("Подключение"), dialog.accept()))
        to_strip = QPushButton("К ленте")
        to_strip.clicked.connect(lambda: (self._goto_tab("Конфигурация ленты"), dialog.accept()))
        ok_btn = QPushButton("Понятно")
        ok_btn.setObjectName("primaryAction")
        ok_btn.clicked.connect(dialog.accept)
        buttons.addWidget(to_connection)
        buttons.addWidget(to_strip)
        buttons.addStretch()
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)

        dialog.exec_()
        if skip_cb.isChecked():
            self._set_top("first_run_done", True)

    def _goto_tab(self, title):
        tabs = self.findChild(QTabWidget)
        if tabs is None:
            return
        for index in range(tabs.count()):
            if tabs.tabText(index) == title:
                tabs.setCurrentIndex(index)
                return

    # -- Расписание и рассвет --

    def _check_schedule(self):
        try:
            self._apply_schedule()
            self._apply_sunrise()
        except Exception:
            log.debug("Ошибка расписания", exc_info=True)

    def _apply_schedule(self):
        sched = self.config.get("schedule", {})
        if not sched.get("enabled", False):
            return
        now = QTime.currentTime()
        start = QTime.fromString(sched.get("night_start", "23:00"), "HH:mm")
        end = QTime.fromString(sched.get("night_end", "07:00"), "HH:mm")
        in_night = self._time_in_range(now, start, end)
        scene = sched.get("night_scene") if in_night else sched.get("day_scene")
        if scene in SCENE_PRESETS and scene != self._last_scene_name:
            self._apply_scene(scene)
            self.status_label.setText(f"Расписание: {scene}")

    @staticmethod
    def _time_in_range(now, start, end):
        if not start.isValid() or not end.isValid():
            return False
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    def _apply_sunrise(self):
        cfg = self.config.get("sunrise", {})
        if not cfg.get("enabled", False):
            self.worker.set_wake_ramp(0.0)
            return
        target = QTime.fromString(cfg.get("time", "07:00"), "HH:mm")
        if not target.isValid():
            return
        duration = max(1, int(cfg.get("duration_minutes", 20)))
        now = QTime.currentTime()
        now_minutes = now.hour() * 60 + now.minute()
        target_minutes = target.hour() * 60 + target.minute()
        remaining = (target_minutes - now_minutes) % (24 * 60)
        if remaining <= duration:
            self.worker.set_wake_ramp(1.0 - remaining / duration)
        else:
            self.worker.set_wake_ramp(0.0)

    # -- Пульт с телефона --

    def _start_remote(self):
        cfg = self.config.get("remote", {})
        if not cfg.get("enabled", True) or self._remote is not None:
            return
        try:
            from .remote import RemoteServer
            server = RemoteServer(
                submit=self._remote_queue.put,
                state_snapshot=lambda: self._remote_state,
                handle_action=self._remote_action,
                port=int(cfg.get("port", 8765)),
            )
            if server.start():
                self._remote = server
                self._update_remote_label()
            else:
                self._remote = None
        except Exception:
            log.exception("Не удалось запустить пульт с телефона")
            self._remote = None

    def _remote_action(self, path, payload):
        """Реакция на кнопку пульта. Выполняется в потоке сервера - поэтому
        не трогаем виджеты, а кладём действие в очередь GUI."""
        if path == "/api/toggle":
            self._remote_queue.put(self._on_tray_toggle_capture)
            return True
        if path == "/api/blackout":
            self._remote_queue.put(self.worker.blackout)
            return True
        if path == "/api/brightness":
            try:
                delta = int(payload.get("delta", 0))
            except (TypeError, ValueError):
                delta = 0
            self._remote_queue.put(lambda: self._nudge_brightness(delta / 100.0))
            return True
        if path == "/api/scene":
            self._remote_queue.put(lambda: self._apply_scene(payload.get("name")))
            return True
        if path == "/api/audio":
            enabled = bool(payload.get("enabled"))
            mode = payload.get("mode", "spectrum")
            self._remote_queue.put(lambda: self._set_audio_from_remote(enabled, mode))
            return True
        return None

    def _drain_remote_queue(self):
        while True:
            try:
                action = self._remote_queue.get_nowait()
            except queue.Empty:
                break
            try:
                action()
            except Exception:
                log.exception("Ошибка выполнения команды с пульта")

    def _refresh_remote_state(self):
        """Кэш состояния для пульта: читать виджеты из чужого потока нельзя."""
        self._remote_state = {
            "connected": bool(self._connected_flag),
            "capturing": bool(self._capture_running),
            "device": self.device_mode_label.text() if hasattr(self, "device_mode_label") else "",
            "brightness": int(round(float(self.config.get("brightness", {}).get("max_scale", 1.0)) * 100)),
            "scene": self._last_scene_name,
        }
        if self._mqtt is not None:
            self._mqtt.publish_state()

    def _set_audio_from_remote(self, enabled, mode):
        self.config["audio"]["enabled"] = bool(enabled)
        if mode:
            self.config["audio"]["mode"] = mode
        self._save_and_push_config()
        if enabled:
            self.worker.start_audio()
        else:
            self.worker.stop_audio()
        combo = getattr(self, "audio_mode_combo", None)
        if combo is not None and mode:
            index = combo.findData(mode)
            if index >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
                self._on_audio_mode_changed(mode, save=False)
        self.status_label.setText("Звук: " + ("включён" if enabled else "выключен"))

    def _on_remote_toggled(self, state):
        enabled = state == Qt.Checked
        self._set_nested("remote", "enabled", enabled)
        if enabled:
            self._start_remote()
        elif self._remote is not None:
            self._remote.stop()
            self._remote = None
        self._update_remote_label()

    # -- MQTT --

    def _start_mqtt(self):
        cfg = self.config.get("mqtt", {})
        if not cfg.get("enabled", False) or self._mqtt is not None:
            return
        try:
            from .mqtt_bridge import MqttBridge
            bridge = MqttBridge(
                host=cfg.get("host", "127.0.0.1"),
                port=int(cfg.get("port", 1883)),
                prefix=cfg.get("prefix", "ledambilight"),
                submit=self._remote_queue.put,
                state_snapshot=lambda: self._remote_state,
                on_command=self._on_mqtt_command,
            )
            if bridge.start():
                self._mqtt = bridge
                self.status_label.setText("MQTT подключён")
            else:
                self._mqtt = None
        except Exception:
            log.exception("Не удалось запустить MQTT")
            self._mqtt = None

    def _on_mqtt_toggled(self, state):
        enabled = state == Qt.Checked
        self._set_nested("mqtt", "enabled", enabled)
        if enabled:
            self._start_mqtt()
        elif self._mqtt is not None:
            self._mqtt.stop()
            self._mqtt = None

    def _on_mqtt_command(self, payload):
        """Команда из MQTT (выполняется в потоке GUI)."""
        if "capture" in payload:
            want = bool(payload["capture"])
            if want != self._capture_running:
                self._on_tray_toggle_capture()
        if "brightness" in payload:
            try:
                value = max(10, min(150, int(payload["brightness"])))
            except (TypeError, ValueError):
                value = None
            if value is not None and getattr(self, "brightness_slider", None) is not None:
                self.brightness_slider.setValue(value)
        if payload.get("blackout"):
            self.worker.blackout()
        if "scene" in payload:
            self._apply_scene(payload["scene"])

    def _update_remote_label(self):
        label = getattr(self, "remote_url_label", None)
        if label is None:
            return
        if self._remote is not None and self._remote.is_running:
            label.setText(f"Открой на телефоне: {self._remote.url()}")
        else:
            label.setText("Пульт выключен")

    def _on_show_qr(self):
        """QR-код со ссылкой: телефон откроет правильный http-адрес."""
        if self._remote is None or not self._remote.is_running:
            QMessageBox.information(self, "Пульт", "Сначала включи пульт галочкой выше")
            return

        url = self._remote.url()
        dialog = QDialog(self)
        dialog.setWindowTitle("Пульт с телефона")
        layout = QVBoxLayout(dialog)

        title = QLabel("Наведи камеру телефона на код")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        hint = QLabel(
            f"Откроется: {url}\n\n"
            "Адрес обязательно с http:// — Safari по умолчанию пробует https://\n"
            "и выдаёт ошибку «не удалось установить безопасное соединение»."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        image = QLabel()
        image.setAlignment(Qt.AlignCenter)
        pixmap = self._make_qr_pixmap(url)
        if pixmap is not None:
            image.setPixmap(pixmap)
        else:
            image.setText(url)
        layout.addWidget(image)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        dialog.exec_()

    @staticmethod
    def _make_qr_pixmap(text, size=260):
        """Рисует QR-код (пакет qrcode опционален)."""
        try:
            import io

            import qrcode

            image = qrcode.make(text).convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pixmap = QPixmap()
            if not pixmap.loadFromData(buffer.getvalue()):
                return None
            return pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:
            log.debug("Не удалось построить QR-код", exc_info=True)
            return None

    def _on_open_remote(self):
        if self._remote is None or not self._remote.is_running:
            QMessageBox.information(self, "Пульт", "Сначала включи пульт галочкой выше")
            return
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(self._remote.url()))

    # ------------------------------------------------------------------
    # Обработка изменений конфига
    # ------------------------------------------------------------------
    def _set_nested(self, section, key, value):
        self.config[section][key] = value
        self._queue_save()

    def _set_top(self, key, value):
        self.config[key] = value
        self._queue_save()

    def _set_profile_field(self, profile_name, field, value):
        self.config["profiles"][profile_name][field] = value
        self._queue_save()

    def _on_games_edited(self):
        lines = [
            line.strip() for line in self.games_edit.toPlainText().splitlines()
            if line.strip()
        ]
        self.config["game_processes"] = lines
        self._queue_save()

    def _on_app_scenes_edited(self):
        """Разбирает «процесс = сцена» в словарь привязок."""
        mapping = {}
        for line in self.app_scenes_edit.toPlainText().splitlines():
            if "=" not in line:
                continue
            process, scene = line.split("=", 1)
            process = process.strip().lower()
            scene = scene.strip()
            if process and scene in SCENE_PRESETS:
                mapping[process] = scene
        self.config["app_scenes"] = mapping
        self._queue_save()

    def _on_bind_active_window(self):
        """Привязывает сцену к текущему активному окну."""
        process = (getattr(self, "_current_process", "") or "").lower()
        if not process:
            self.status_label.setText(
                "Не вижу активное окно — запусти Ambilight, чтобы приложение отслеживало процессы"
            )
            return
        scene = self.app_scene_combo.currentData()
        lines = [
            line for line in self.app_scenes_edit.toPlainText().splitlines()
            if not line.lower().startswith(process + " =")
        ]
        lines.append(f"{process} = {scene}")
        self.app_scenes_edit.setPlainText("\n".join(lines))
        self.status_label.setText(f"Сцена «{scene}» привязана к {process}")

    def on_process_changed(self, name):
        """Активное окно сменилось — применяем привязанную сцену."""
        self._current_process = (name or "").lower()
        scene = (self.config.get("app_scenes") or {}).get(self._current_process)
        if scene and scene in SCENE_PRESETS and scene != self._last_scene_name:
            self._apply_scene(scene)

    def _queue_save(self):
        # Дебаунс: не пишем на диск/в воркер на каждый пиксель движения слайдера
        self._save_timer.start(300)

    def _save_and_push_config(self):
        try:
            save_config(self.config)
        except OSError as exc:
            # Диск может быть переполнен / папка только для чтения - приложение
            # не должно падать из-за этого, но пользователь должен узнать
            log.error("Не удалось сохранить настройки: %s", exc)
            self.status_label.setText(f"Не удалось сохранить настройки: {exc}")
        self.worker.update_config(self.config)

    # ------------------------------------------------------------------
    # Сигналы от воркера
    # ------------------------------------------------------------------

    def on_status(self, text):
        self.status_label.setText(text)

    def on_connected(self, connected):
        self._connected_flag = bool(connected)
        self.status_label.setText("Подключено" if connected else "Не подключено")
        # Зелёная точка = подключено, серая = нет
        color = "#2fa84f" if connected else "#6b6c72"
        self.status_dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        if getattr(self, "tray_icon", None) is not None:
            self.tray_icon.setIcon(self._make_tray_icon(connected))

    def on_color_updated(self, r, g, b):
        self.color_swatch.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); border: 1px solid #666; border-radius: 4px;"
        )
        # Плоская заливка - показываем в мини-превью ленты, только если давно
        # не было "настоящих" поэтевиксельных данных (иначе перекрывали бы
        # друг друга, мигая между двумя источниками)
        self._mini_preview_flat_color = (r, g, b)
        if time.time() - self._last_pixels_update_time > 1.0:
            self._mini_preview_mode = "flat"
            self._draw_mini_strip_preview()

    def on_strip_pixels_updated(self, pixels):
        """Живое мини-превью реальной ленты - что каждый диод показывает
        прямо сейчас, поэтевиксельно (только в режиме WLED с маппингом)."""
        self._mini_preview_pixels = pixels
        self._mini_preview_mode = "pixels"
        self._last_pixels_update_time = time.time()
        self._draw_mini_strip_preview()

    def _draw_mini_strip_preview(self):
        from PyQt5.QtGui import QPixmap, QPainter, QColor

        w = max(1, self.mini_strip_preview.width())
        h = max(1, self.mini_strip_preview.height())
        pixmap = QPixmap(w, h)
        pixmap.fill(QColor(0, 0, 0))
        painter = QPainter(pixmap)

        if self._mini_preview_mode == "pixels" and self._mini_preview_pixels:
            pixels = self._mini_preview_pixels
            n = len(pixels)
            seg_w = w / n
            for i, (r, g, b) in enumerate(pixels):
                painter.fillRect(int(i * seg_w), 0, int(seg_w) + 1, h, QColor(int(r), int(g), int(b)))
        else:
            r, g, b = self._mini_preview_flat_color
            painter.fillRect(0, 0, w, h, QColor(r, g, b))

        painter.end()
        self.mini_strip_preview.setPixmap(pixmap)

    def on_now_playing_changed(self, track_name):
        """Показывает, какой трек сейчас определён для цвета standby-режима
        (полезно для проверки, что Spotify-интеграция вообще что-то видит)."""
        if hasattr(self, "standby_now_playing_label"):
            self.standby_now_playing_label.setText(
                f"Сейчас играет: {track_name}" if track_name else "Сейчас играет: -"
            )

    def on_profile_changed(self, name):
        self.profile_label.setText(name)
        self.current_profile = name

    def on_frame_preview(self, arr):
        h, w = arr.shape[0], arr.shape[1]
        if h == 0 or w == 0:
            return

        self._last_preview_frame = arr

        bytes_per_line = 3 * w
        qimg = QImage(arr.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimg)

        # preview_label теперь ФИКСИРОВАННОГО размера (см. _build_preview_tab) —
        # раньше тут было "max(320, self.preview_label.width())", и из-за того,
        # что setPixmap может подвинуть sizeHint лейбла, размер зацикленно рос
        # кадр за кадром (тот самый баг "окно превью постоянно увеличивается",
        # который заодно жрал память под всё более крупные картинки).
        target_w = self.preview_label.width()
        target_h = self.preview_label.height()
        pixmap = pixmap.scaled(
            target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._preview_pixmap_size = (pixmap.width(), pixmap.height())

        self._draw_overlays(pixmap)
        self.preview_label.setPixmap(pixmap)

    def _draw_overlays(self, pixmap):
        """Рисует поверх превью: красным — зоны, обрезанные HUD-маской,
        жёлтой рамкой — реальную область анализа для режима center_quadrants."""
        w, h = pixmap.width(), pixmap.height()

        painter = QPainter(pixmap)

        hud = self.config.get("hud_mask", {})
        top = min(40, hud.get("top", 0)) / 100.0 * h
        bottom = h - min(40, hud.get("bottom", 0)) / 100.0 * h
        left = min(40, hud.get("left", 0)) / 100.0 * w
        right = w - min(40, hud.get("right", 0)) / 100.0 * w

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 0, 0, 90))
        if top > 0:
            painter.drawRect(QRectF(0, 0, w, top))
        if h - bottom > 0:
            painter.drawRect(QRectF(0, bottom, w, h - bottom))
        if left > 0:
            painter.drawRect(QRectF(0, 0, left, h))
        if w - right > 0:
            painter.drawRect(QRectF(right, 0, w - right, h))

        mode = self.config.get("profiles", {}).get(self.current_profile, {}).get("mode", "quadrants")
        if mode == "center_quadrants":
            area_pct = max(30, min(100, self.config.get("analysis_area_percent", 60))) / 100.0
            margin = (1 - area_pct) / 2.0
            cw, ch = max(0, right - left), max(0, bottom - top)
            ax = left + cw * margin
            ay = top + ch * margin
            aw = cw * (1 - 2 * margin)
            ah = ch * (1 - 2 * margin)

            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 220, 0), 2))
            painter.drawRect(QRectF(ax, ay, aw, ah))

        # Области выборки каждого диода - видно, откуда именно он берёт цвет
        if getattr(self, "show_zones_cb", None) is not None and self.show_zones_cb.isChecked():
            data = getattr(self, "_mapping_boxes", None)
            if data and data.get("boxes"):
                src_w = max(1, data.get("screen_width") or 1)
                src_h = max(1, data.get("screen_height") or 1)
                sx = w / src_w
                sy = h / src_h
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(0, 255, 140, 170), 1))
                for (y0, y1, x0, x1) in data["boxes"]:
                    painter.drawRect(QRectF(
                        x0 * sx, y0 * sy,
                        max(1.0, (x1 - x0) * sx), max(1.0, (y1 - y0) * sy),
                    ))

        painter.end()

    def on_devices_found(self, devices):
        self.device_list.clear()
        for name, address in devices:
            item = QListWidgetItem(f"{name}  —  {address}")
            item.setData(Qt.UserRole, address)
            self.device_list.addItem(item)

    def on_audio_level_update(self, volume, bass, mid, treble):
        self.vu_volume.setValue(int(volume * 100))
        self.vu_bass.setValue(int(bass * 100))
        self.vu_mid.setValue(int(mid * 100))
        self.vu_treble.setValue(int(treble * 100))

    def on_beat_update(self, bpm):
        label = getattr(self, "bpm_label", None)
        if label is None:
            return
        if bpm and bpm > 40:
            label.setText(f"Темп: {bpm:.0f} BPM")
        else:
            label.setText("Темп: —")

    def on_stats_update(self, fps, latency, packets, current_ma):
        """Строка состояния: частота кадров, задержка, пакеты, оценка тока."""
        parts = []
        if fps > 0.5:
            parts.append(f"{fps:.0f} FPS")
        if latency > 0:
            parts.append(f"{latency:.1f} мс")
        if packets:
            parts.append(f"{packets} пакетов")
        if current_ma > 0:
            parts.append(f"≈{current_ma:.0f} мА")
        self.stats_label.setText(" · ".join(parts) if parts else "—")

    def on_mapping_boxes(self, data):
        """Запоминает области выборки диодов для наложения на превью."""
        self._mapping_boxes = data
        if getattr(self, "show_zones_cb", None) is not None and self.show_zones_cb.isChecked():
            self._redraw_preview()

    def _redraw_preview(self):
        """Перерисовывает последний кадр превью (например, после включения зон)."""
        frame = getattr(self, "_last_preview_frame", None)
        if frame is not None:
            self.on_frame_preview(frame)

    def on_diagnostics(self, text):
        """Показывает отчёт диагностики в отдельном окне."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Диагностика")
        dialog.resize(620, 460)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        view.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(view)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        dialog.exec_()

    def on_capture_state_changed(self, running):
        """Воркер сообщает, реально ли идёт захват - а не «кнопку нажали»."""
        self._capture_running = bool(running)
        self._update_tray_capture_label(self._capture_running)
        if hasattr(self, "start_capture_btn"):
            self.start_capture_btn.setEnabled(not running)
        if hasattr(self, "stop_capture_btn"):
            self.stop_capture_btn.setEnabled(running)

    def on_error(self, message):
        """Ошибки фонового потока - в статус, трей и лог, без модальных окон.

        Раньше каждый сбой открывал модальный QMessageBox: он блокировал
        интерфейс, а при повторяющейся ошибке ещё и выстраивался в очередь."""
        log.warning("Ошибка: %s", message)
        now = time.time()
        if message == self._last_error_text and now - self._last_error_time < 10.0:
            return
        self._last_error_text = message
        self._last_error_time = now
        first_line = message.split("\n")[0]
        self.status_label.setText(first_line[:120])
        self.status_dot.setStyleSheet("color: #e05050; font-size: 14px;")
        if getattr(self, "tray_icon", None) is not None:
            self.tray_icon.showMessage("LED Ambilight", message, QSystemTrayIcon.Warning, 4000)

    # ------------------------------------------------------------------
    # Системный трей
    # ------------------------------------------------------------------

    def _make_tray_icon(self, connected=False):
        """Рисует простую иконку-кружок программно - без внешних файлов.
        Зелёный = подключено, серый = нет (перекликается со статусной точкой)."""
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(47, 168, 79) if connected else QColor(107, 108, 114)
        painter.setBrush(color)
        painter.setPen(QColor(30, 31, 34))
        painter.drawEllipse(4, 4, 56, 56)
        painter.end()
        return QIcon(pixmap)

    def _build_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return

        self.tray_icon = QSystemTrayIcon(self._make_tray_icon(False), self)
        self.tray_icon.setToolTip("LED Ambilight")

        menu = QMenu()

        self.tray_toggle_action = QAction("▶ Запустить Ambilight")
        self.tray_toggle_action.triggered.connect(self._on_tray_toggle_capture)
        menu.addAction(self.tray_toggle_action)

        show_action = QAction("Показать окно")
        show_action.triggered.connect(self._show_from_tray)
        menu.addAction(show_action)

        menu.addSeparator()

        quit_action = QAction("Выход")
        quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_start_capture_clicked(self):
        # Состояние обновится по сигналу capture_state_changed, когда захват
        # реально стартует (раньше галочка ставилась оптимистично, и при сбое
        # dxcam интерфейс врал, что всё работает)
        self.worker.start_capture()

    def _on_stop_capture_clicked(self):
        self.worker.stop_capture()

    def _on_tray_toggle_capture(self):
        if self._capture_running:
            self._on_stop_capture_clicked()
        else:
            self._on_start_capture_clicked()

    def _quit_from_tray(self):
        self._really_quit = True
        self.close()

    def _update_tray_capture_label(self, running: bool):
        if getattr(self, "tray_toggle_action", None) is not None:
            self.tray_toggle_action.setText("⏸ Остановить Ambilight" if running else "▶ Запустить Ambilight")

    # ------------------------------------------------------------------

    def closeEvent(self, event):
        minimize = self.config.get("minimize_to_tray", True)
        tray_ok = getattr(self, "tray_icon", None) is not None

        if minimize and tray_ok and not self._really_quit:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "LED Ambilight",
                "Приложение продолжает работать в трее. Дважды кликни по иконке, чтобы открыть окно.",
                QSystemTrayIcon.Information,
                3000,
            )
            return

        self.worker.stop_worker()
        if self.hotkey_filter is not None:
            try:
                self.hotkey_filter.set_enabled(False)
                QApplication.instance().removeNativeEventFilter(self.hotkey_filter)
            except Exception:
                log.debug("Не удалось снять горячие клавиши", exc_info=True)
        if self._remote is not None:
            self._remote.stop()
            self._remote = None
        if self._mqtt is not None:
            self._mqtt.stop()
            self._mqtt = None
        if not self.worker.wait(5000):
            # Последний шанс не оставить процесс-зомби: окно уже закрывается,
            # а зависший поток не даст приложению завершиться
            log.warning("Фоновый поток не завершился за 5 секунд, принудительно останавливаю")
            self.worker.terminate()
            self.worker.wait(1000)
        if tray_ok:
            self.tray_icon.hide()
        event.accept()
