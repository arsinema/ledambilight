"""Рисует иконку приложения кодом (без внешних картинок).

Запуск:  python tools/make_icon.py

Появляются assets/icon.png (256x256) и assets/icon.ico (набор размеров для
Windows и сборки в .exe). Иконка — монитор со светящейся по периметру
лентой: красный → жёлтый → зелёный → голубой → синий.
"""

import colorsys
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
SIZE = 256


def rounded_rect_points(left, top, right, bottom, radius, steps_per_corner=14):
    """Точки по ВСЕМУ периметру прямоугольника со скруглёнными углами.

    Углы рисуются дугами, прямые участки — плотной выборкой между ними.
    """
    import math

    anchors = []
    corners = [
        (right - radius, top + radius, 270, 360),    # правый верх
        (right - radius, bottom - radius, 0, 90),    # правый низ
        (left + radius, bottom - radius, 90, 180),   # левый низ
        (left + radius, top + radius, 180, 270),     # левый верх
    ]
    for cx, cy, start, end in corners:
        for step in range(steps_per_corner + 1):
            angle = math.radians(start + (end - start) * step / steps_per_corner)
            anchors.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))

    # добиваем прямые участки: между соседними якорями ставим точки через ~3 px
    points = []
    for index, (x1, y1) in enumerate(anchors):
        x2, y2 = anchors[(index + 1) % len(anchors)]
        distance = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(distance / 3))
        for step in range(steps):
            t = step / steps
            points.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    return points


def main():
    os.makedirs(ASSETS, exist_ok=True)

    # --- фон: тёмный скруглённый квадрат ---
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=52, fill=(30, 31, 34, 255))

    # --- монитор ---
    screen = [40, 56, SIZE - 40, 188]
    draw.rounded_rectangle(screen, radius=14, fill=(12, 12, 14, 255))
    # подставка
    draw.rectangle([SIZE // 2 - 26, 188, SIZE // 2 + 26, 206], fill=(70, 72, 80, 255))
    draw.rounded_rectangle([SIZE // 2 - 52, 206, SIZE // 2 + 52, 216], radius=5,
                           fill=(70, 72, 80, 255))

    # --- свечение ленты по периметру ---
    points = rounded_rect_points(screen[0] - 6, screen[1] - 6, screen[2] + 6,
                                 screen[3] + 6, 16)
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)

    total = len(points)
    for index, (x, y) in enumerate(points):
        hue = (index / total) * 0.75  # красный -> синий через остальные
        r, g, b = colorsys.hsv_to_rgb(hue, 0.95, 1.0)
        color = (int(r * 255), int(g * 255), int(b * 255), 255)
        glow_draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=color)

    # мягкий ореол
    halo = glow.filter(ImageFilter.GaussianBlur(10))
    icon = Image.alpha_composite(icon, halo)
    icon = Image.alpha_composite(icon, glow)

    icon.save(os.path.join(ASSETS, "icon.png"))
    icon.save(os.path.join(ASSETS, "icon.ico"),
              sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    print("assets/icon.png и assets/icon.ico готовы")


if __name__ == "__main__":
    main()
