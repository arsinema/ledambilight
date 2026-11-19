"""Объёмный предпросмотр комнаты на основе рендера Blender.

Пять кадров с камерой под разными углами дают ощущение поворота; поверх
кадра рисуются реальные цвета ленты. Позиции диодов берутся из текущей
раскладки, поэтому лента идёт по своему пути, а не ровным кольцом.

Если файлов рендера нет (запуск из исходников без assets), включается
простой запасной фон — приложение не должно падать.
"""

from __future__ import annotations

import json
import math
import os
import sys

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt5.QtWidgets import QWidget

from .config import RESOURCE_DIR

ROOM_DIR = os.path.join(RESOURCE_DIR, "assets", "room")


def _lerp_color(a, b, t):
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() * (1 - t) + b.red() * t),
        int(a.green() * (1 - t) + b.green() * t),
        int(a.blue() * (1 - t) + b.blue() * t),
    )


class RoomView(QWidget):
    """Комната с монитором; тянется мышью, колесо — приближение."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self._view_pos = 2.0
        self._zoom = 1.0
        self._drag = None
        self._pixels = []
        self._offset = 0
        self._total = 90
        self._flat = QColor(90, 130, 255)
        self._path = []
        self._linear = False
        self._auto_rotate = False
        self._phase = 0.0
        self._views = []
        self._pixmaps = []
        self._manifest = {}
        self._assets_loaded = False
        # Ассеты комнаты — пять full-HD QPixmap (~40 МБ). Грузим их только
        # при первом показе вкладки, а таймер анимации не крутим впустую,
        # пока комната скрыта.
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._animate)

    def _ensure_assets(self):
        if not self._assets_loaded:
            self._load_assets()
            self._assets_loaded = True

    def showEvent(self, event):  # noqa: N802
        self._ensure_assets()
        if not self._timer.isActive():
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _load_assets(self):
        candidates = [
            ROOM_DIR,
            os.path.join(getattr(sys, "_MEIPASS", ROOM_DIR), "assets", "room"),
            os.path.join(os.path.dirname(__file__), "assets", "room"),
        ]
        for directory in candidates:
            manifest_path = os.path.join(directory, "room.json")
            if not os.path.exists(manifest_path):
                continue
            try:
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    self._manifest = json.load(handle)
                views = self._manifest.get("views", [])
                pixmaps = []
                for view in views:
                    path = os.path.join(directory, view.get("file", ""))
                    pixmap = QPixmap(path)
                    if pixmap.isNull():
                        raise OSError(path)
                    # Room — превью, а не фоторедактор: 1280 px по ширине
                    # достаточно и экономит ~25 МБ на пяти full-HD снимках.
                    if pixmap.width() > 1280:
                        pixmap = pixmap.scaledToWidth(1280, Qt.SmoothTransformation)
                    pixmaps.append(pixmap)
                self._views = views
                self._pixmaps = pixmaps
                return
            except (OSError, ValueError, TypeError):
                self._views = []
                self._pixmaps = []

    # ------------------------------------------------------------------
    # Данные
    # ------------------------------------------------------------------
    def set_leds(self, pixels, offset=0, total=0):
        self._pixels = [tuple(int(v) for v in p) for p in (pixels or [])]
        self._offset = max(0, int(offset or 0))
        self._total = max(len(self._pixels) + self._offset, int(total or 0))
        if self._pixels:
            avg = [sum(p[i] for p in self._pixels) // len(self._pixels) for i in range(3)]
            self._flat = QColor(*avg)
        self.update()

    def set_flat_color(self, rgb):
        if not self._pixels:
            try:
                self._flat = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
            except (TypeError, ValueError):
                pass
            self.update()

    def set_strip_points(self, points):
        """Нормализованные точки текущей раскладки (0..1 по экрану)."""
        clean = []
        for point in points or []:
            try:
                clean.append((max(0.0, min(1.0, float(point[0]))),
                              max(0.0, min(1.0, float(point[1])))))
            except (TypeError, ValueError, IndexError):
                continue
        self._path = clean
        self.update()

    def set_linear_mode(self, enabled):
        self._linear = bool(enabled)
        self.update()

    def set_auto_rotate(self, enabled):
        self._auto_rotate = bool(enabled)

    def reset_view(self):
        self._view_pos = (len(self._pixmaps) - 1) / 2 if self._pixmaps else 2.0
        self._zoom = 1.0
        self.update()

    def _animate(self):
        self._phase += 0.03
        if self._auto_rotate and self._pixmaps:
            self._view_pos += 0.006
            if self._view_pos > len(self._pixmaps) - 1:
                self._view_pos = 0.0
        self.update()

    # ------------------------------------------------------------------
    # Преобразование изображения
    # ------------------------------------------------------------------
    def _image_rect(self):
        if self._pixmaps:
            w = self._pixmaps[0].width()
            h = self._pixmaps[0].height()
        else:
            w, h = 1600, 1000
        if w <= 0 or h <= 0:
            return QRectF(0, 0, self.width(), self.height())
        if self._linear:
            # В режиме развёртки оставляем нижнюю полосу под линейку диодов.
            target_h = self.height() * 0.78
            scale = max(self.width() / w, target_h / h) * self._zoom
            dw, dh = w * scale, h * scale
            return QRectF((self.width() - dw) / 2, max(0.0, target_h - dh), dw, dh)
        scale = max(self.width() / w, self.height() / h) * self._zoom
        dw, dh = w * scale, h * scale
        return QRectF((self.width() - dw) / 2, (self.height() - dh) / 2, dw, dh)

    def _map(self, nx, ny):
        rect = self._image_rect()
        return QPointF(rect.left() + nx * rect.width(), rect.top() + ny * rect.height())

    def _quad(self, quad):
        return [self._map(nx, ny) for nx, ny in quad]

    @staticmethod
    def _quad_point(quad, u, v):
        tl, tr, br, bl = quad
        top = ((1 - u) * tl[0] + u * tr[0], (1 - u) * tl[1] + u * tr[1])
        bottom = ((1 - u) * bl[0] + u * br[0], (1 - u) * bl[1] + u * br[1])
        return (1 - v) * top[0] + v * bottom[0], (1 - v) * top[1] + v * bottom[1]

    # ------------------------------------------------------------------
    # Отрисовка
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        self._ensure_assets()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor(8, 9, 12))

        if not self._pixmaps:
            self._paint_fallback(painter)
            painter.end()
            return

        pos = max(0.0, min(len(self._pixmaps) - 1.0, self._view_pos))
        index = int(math.floor(pos))
        frac = pos - index
        rect = self._image_rect()
        painter.drawPixmap(rect, self._pixmaps[index], QRectF(self._pixmaps[index].rect()))
        if frac > 0.01 and index + 1 < len(self._pixmaps):
            painter.setOpacity(frac)
            next_pixmap = self._pixmaps[index + 1]
            painter.drawPixmap(rect, next_pixmap, QRectF(next_pixmap.rect()))
            painter.setOpacity(1.0)

        view = self._views[index] if self._views else {}
        screen_quad = view.get("screen") or []
        bezel_quad = view.get("bezel") or []
        if len(screen_quad) != 4 or len(bezel_quad) != 4:
            painter.end()
            return

        screen_px = self._quad(screen_quad)
        bezel_px = self._quad(bezel_quad)

        # Мягкое свечение на стене/столе от ленты.
        center = QPointF(
            sum(p.x() for p in screen_px) / 4,
            sum(p.y() for p in screen_px) / 4,
        )
        base = QColor(self._flat)
        radius = max(80.0, abs(screen_px[1].x() - screen_px[0].x()) * 0.95)
        glow = QRadialGradient(center, radius)
        glow.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 42))
        glow.setColorAt(0.55, QColor(base.red(), base.green(), base.blue(), 14))
        glow.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(center, radius, radius * 0.82)

        # Экран: слегка подкрашиваем под текущий свет, но не заливаем.
        path = QPainterPath()
        path.moveTo(screen_px[0])
        for p in screen_px[1:]:
            path.lineTo(p)
        path.closeSubpath()
        painter.setClipPath(path)
        tint = QLinearGradient(screen_px[0], screen_px[2])
        tint.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 28))
        tint.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillPath(path, QBrush(tint))
        painter.setClipping(False)

        # Линейная «развёртка» — лента в порядке 1..N.
        if self._linear:
            self._paint_linear(painter)
        else:
            self._paint_path(painter, bezel_quad, bezel_px)

        painter.setPen(QPen(QColor(215, 220, 235, 165)))
        painter.drawText(14, 22, "Комната — потяни мышью, колесо — масштаб")
        painter.end()

    def _paint_path(self, painter, bezel_quad, bezel_px):
        count = max(1, self._total)
        if len(self._path) >= 2:
            positions = [(u, v) for u, v in self._path]
            # Добиваем/обрезаем до числа физических диодов.
            if len(positions) < count:
                step = (len(positions) - 1) / max(1, count - 1)
                positions = [positions[int(round(i * step))] for i in range(count)]
            elif len(positions) > count:
                positions = positions[:count]
        else:
            raw = self._perimeter(2.0, 2.0, count)
            positions = [((x + 1) / 2, (y + 1) / 2) for x, y in raw]

        font = QFont()
        font.setPointSizeF(max(6.0, min(9.0, self.height() / 55)))
        painter.setFont(font)
        for index, (u, v) in enumerate(positions):
            if len(self._path) >= 2:
                point = self._quad_point(bezel_quad, u, v)
                px = self._map(point[0], point[1])
            else:
                px = self._quad_point(
                    [(q.x(), q.y()) for q in bezel_px], u, v
                )
                px = QPointF(px[0], px[1])
            color = self._led_color(index)
            self._draw_led(painter, px, color, index)

    def _paint_linear(self, painter):
        count = max(1, self._total)
        panel_top = int(self.height() * 0.80)
        panel = QLinearGradient(0, panel_top, 0, self.height())
        panel.setColorAt(0.0, QColor(12, 14, 20, 245))
        panel.setColorAt(1.0, QColor(20, 22, 30, 245))
        painter.fillRect(0, panel_top, self.width(), self.height() - panel_top, QBrush(panel))
        painter.setPen(QPen(QColor(70, 74, 88)))
        painter.drawLine(0, panel_top, self.width(), panel_top)

        left = self.width() * 0.07
        right = self.width() * 0.93
        y = self.height() * 0.905
        painter.setPen(QPen(QColor(120, 125, 140, 80), 1))
        painter.drawLine(int(left), int(y), int(right), int(y))
        font = QFont()
        font.setPointSizeF(max(6.0, min(8.0, self.height() / 65)))
        painter.setFont(font)
        for index in range(count):
            t = index / max(1, count - 1)
            point = QPointF(left + (right - left) * t, y)
            spacing = (right - left) / max(1, count - 1)
            self._draw_led(painter, point, self._led_color(index), index,
                           show_number=False, spacing=spacing)
        painter.setPen(QPen(QColor(175, 180, 195, 210)))
        label_font = QFont()
        label_font.setPointSizeF(7.5)
        painter.setFont(label_font)
        for index in (0, 9, 19, 29, 39, 49, 59, 69, 79, count - 1):
            if 0 <= index < count:
                t = index / max(1, count - 1)
                point = QPointF(left + (right - left) * t, y + 17)
                painter.drawText(point, str(index + 1))
        painter.setPen(QPen(QColor(210, 214, 225, 190)))
        painter.drawText(int(left), panel_top + 18,
                         f"Развёртка ленты · {count} диодов · порядок 1 → {count}")

    def _draw_led(self, painter, point, color, index, show_number=True, spacing=8.0):
        color = self._visible_color(color)
        pulse = 0.75 + 0.25 * math.sin(self._phase + index * 0.25)
        glow_radius = max(5.0, min(11.0, spacing * 0.85))
        core_radius = max(2.0, min(3.6, spacing * 0.28))
        glow = QRadialGradient(point, glow_radius)
        glow.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), int(150 * pulse)))
        glow.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(point, glow_radius, glow_radius)
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(QColor(205, 220, 245, 120), 0.8))
        painter.drawEllipse(point, core_radius, core_radius)
        if show_number and (index == 0 or (index + 1) % 10 == 0):
            painter.setPen(QPen(QColor(230, 232, 240, 200)))
            painter.drawText(point + QPointF(8, -6), str(index + 1))

    @staticmethod
    def _visible_color(color):
        """Даже тёмный кадр не должен выглядеть как «диодов нет»."""
        try:
            r, g, b = color.red(), color.green(), color.blue()
        except AttributeError:
            return QColor(70, 110, 180)
        if max(r, g, b) < 28:
            return QColor(92, 138, 225)
        if max(r, g, b) < 70:
            return QColor(
                max(70, int(r * 1.8)),
                max(85, int(g * 1.8)),
                max(115, int(b * 1.8)),
            )
        return color

    def _paint_fallback(self, painter):
        bg = QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0.0, QColor(12, 14, 20))
        bg.setColorAt(1.0, QColor(24, 22, 30))
        painter.fillRect(self.rect(), bg)
        painter.setPen(QPen(QColor(150, 155, 170, 160)))
        painter.drawText(14, 22, "Комната — нет файлов рендера, включён запасной фон")
        count = max(1, self._total)
        left = self.width() * 0.1
        right = self.width() * 0.9
        y = self.height() * 0.55
        for index in range(count):
            t = index / max(1, count - 1)
            point = QPointF(left + (right - left) * t, y)
            self._draw_led(painter, point, self._led_color(index), index)

    def _led_color(self, index):
        if index < self._offset:
            return QColor(8, 8, 10)
        active = index - self._offset
        if 0 <= active < len(self._pixels):
            r, g, b = self._pixels[active]
            return QColor(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
        color = QColor(self._flat)
        return color.darker(150)

    @staticmethod
    def _perimeter(width, height, count):
        positions = []
        w, h = width, height
        for index in range(count):
            t = index / count * 4.0
            if t < 1:
                positions.append((-w / 2 + w * t, h / 2))
            elif t < 2:
                positions.append((w / 2, h / 2 - h * (t - 1)))
            elif t < 3:
                positions.append((w / 2 - w * (t - 2), -h / 2))
            else:
                positions.append((-w / 2, -h / 2 + h * (t - 3)))
        return positions

    # ------------------------------------------------------------------
    # Мышь
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        delta = event.pos() - self._drag
        self._drag = event.pos()
        if self._pixmaps:
            self._view_pos = max(
                0.0,
                min(len(self._pixmaps) - 1.0, self._view_pos + delta.x() * 0.012),
            )
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.setCursor(Qt.ArrowCursor)

    def wheelEvent(self, event):
        self._zoom = max(1.0, min(1.35, self._zoom * (1.0 + event.angleDelta().y() / 1600.0)))
        self.update()
