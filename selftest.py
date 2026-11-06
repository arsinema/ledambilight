"""
Самотест приложения: проверяет то, что легко сломать правками и что не требует
железа (ленты, Bluetooth, экрана).

Запуск:
    python selftest.py

Проверяется:
  * векторный цветовой пайплайн совпадает со скалярным (HSV-коррекция);
  * калибровка и множитель яркости реально применяются к массиву диодов;
  * зонный расчёт и HUD-маска дают ожидаемые значения;
  * маппинг ленты: равномерная раскладка по длине пути и обрезка по HUD;
  * формат UDP-пакета DRGB и заголовок Adalight;
  * merge конфига не теряет пользовательские значения.
"""

import copy
import json
import os
import random
import socket
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# Консоль Windows и CI-раннеры часто живут в cp1252/cp866: русские строки
# в print падают с UnicodeEncodeError. Принудительный UTF-8 это лечит.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from ledapp.capture import bgra_to_rgb, hud_rect, apply_hud_mask, get_quadrant_color
from ledapp.color_engine import (
    apply_calibration,
    apply_calibration_array,
    compute_dominant_color,
    improve_color,
    improve_color_array,
    process_pixel_frame,
)
from ledapp.config import DEFAULT_CONFIG, _deep_merge, _sanitize
from ledapp.strip_mapper import StripMapper
from ledapp.wled_controller import WLEDController
from ledapp.wled_serial_controller import WLEDSerialController

PASSED = 0
FAILED = []


def check(name, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print(f"  OK   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name} {detail}")


def test_color_pipeline():
    print("\n[1] Цветовой пайплайн")
    random.seed(42)
    worst = 0
    for _ in range(500):
        r, g, b = (random.randint(0, 255) for _ in range(3))
        scalar = improve_color(r, g, b)
        vector = tuple(int(round(v)) for v in improve_color_array([[r, g, b]])[0])
        worst = max(worst, max(abs(scalar[i] - vector[i]) for i in range(3)))
    check("векторная HSV-коррекция совпадает со скалярной (допуск 1)",
          worst <= 1, f"максимальное расхождение {worst}")

    check("очень тёмный цвет становится чёрным",
          tuple(int(round(v)) for v in improve_color_array([[3, 5, 4]])[0]) == (0, 0, 0))

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["calibration"]["r_gain"] = 0.5
    config["brightness"]["max_scale"] = 0.5
    out = process_pixel_frame([(200, 100, 50)], config)
    check("калибровка и множитель яркости применяются к диодам",
          out[0][0] < 60, f"получилось {out[0]} (ожидалось R около 50)")

    config = copy.deepcopy(DEFAULT_CONFIG)
    out = process_pixel_frame([(200, 100, 50)], config)
    # Яркость и калибровка нейтральны, значит результат обязан совпасть
    # с HSV-коррекцией одиночного цвета (тем самым путём, что и для BLE)
    scalar = improve_color(200, 100, 50)
    check("при нейтральных настройках пайплайн совпадает с одиночным цветом",
          all(abs(out[0][i] - scalar[i]) <= 1 for i in range(3)),
          f"получилось {out[0]}, ожидалось {scalar}")

    config["brightness"]["min_brightness"] = 120
    dark = process_pixel_frame([(10, 10, 10)], config)
    check("минимальная яркость поднимает тёмный кадр",
          max(dark[0]) >= 100, f"получилось {dark[0]}")

    patch = np.zeros((8, 8, 3), dtype=np.uint8)
    patch[:, :] = (250, 10, 10)
    r, g, b = compute_dominant_color(patch, sample_stride=1, quant=32)
    check("доминантный цвет на однородном участке", r > 200 and g < 40 and b < 40,
          f"получилось {(r, g, b)}")


def test_calibration_array():
    print("\n[2] Калибровка массивом")
    calib = {"r_gain": 2.0, "g_gain": 0.5, "b_gain": 1.0,
             "r_offset": 10, "g_offset": -5, "b_offset": 0}
    out = apply_calibration_array([(100, 100, 100)], calib)[0]
    expected = apply_calibration(100, 100, 100, calib)
    check("массив и скаляр дают одно и то же",
          tuple(int(round(v)) for v in out) == expected,
          f"{tuple(int(round(v)) for v in out)} != {expected}")
    clipped = apply_calibration_array([(255, 255, 255)], calib)[0]
    check("значения не выходят за 0..255", all(0 <= v <= 255 for v in clipped))


def test_zones_and_hud():
    print("\n[3] Зоны и HUD-маска")
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[:, :] = (10, 20, 30)
    frame[0:10, :] = (255, 0, 0)  # «таскбар» сверху

    rect = hud_rect(100, 100, {"top": 10})
    check("hud_rect считает границы", rect == (10, 100, 0, 100), str(rect))

    masked = apply_hud_mask(frame, {"top": 10})
    check("HUD-маска обрезает кадр", masked.shape[0] == 90, str(masked.shape))
    r, g, b = get_quadrant_color(masked, "top_left")
    check("после маски верхняя зона больше не красная", r < 20, f"R={r}")

    scaled = apply_hud_mask(frame, {"top": 40, "bottom": 40, "left": 30, "right": 30})
    check("сильная маска не ломает кадр", scaled.shape[0] > 0 and scaled.shape[1] > 0,
          str(scaled.shape))


def test_strip_mapper():
    print("\n[4] Маппинг ленты")
    mapper = StripMapper(led_count=10, screen_width=100, screen_height=100)
    mapper.led_path = [(0, 50), (100, 50)]
    ok = mapper.build_mapping(strip_width=4)
    check("маппинг строится", ok and len(mapper.led_boxes) == 10)
    centers = [((b[2] + b[3]) // 2) for b in mapper.led_boxes]
    check("диоды распределены по всей длине пути",
          centers[0] <= 3 and centers[-1] >= 96, str(centers))

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[:, :] = (200, 200, 200)
    frame[:, 90:] = (255, 0, 0)  # правый край - красный

    colors = mapper.get_all_leds_colors(frame)
    check("правый диод видит правый край", colors[-1][0] > 240 and colors[0][0] < 210,
          f"первый {colors[0]}, последний {colors[-1]}")

    # С HUD-маской справа диод не должен брать красный цвет
    rect = hud_rect(100, 100, {"right": 20})
    clipped = mapper.get_all_leds_colors(frame, clip_rect=rect)
    check("HUD-маска не пускает цвет исключённого края на диод",
          clipped[-1][0] < 210, f"последний диод {clipped[-1]}")

    empties = [i for i, box in enumerate(mapper.led_boxes) if box[1] <= box[0]]
    check("нет пустых областей выборки", not empties, str(empties))


def test_protocol():
    print("\n[5] Протоколы передачи")
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.settimeout(2.0)

    controller = WLEDController("127.0.0.1", port, led_count=3)
    controller.min_send_interval = 0.0
    check("сокет создаётся", controller.connect())
    pixels = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    check("кадр отправляется", controller.send_frame(pixels))

    data, _ = server.recvfrom(65535)
    check("код протокола DRGB = 2", data[0] == 2, str(data[:2]))
    check("длина пакета 2 + 3*3", len(data) == 11, str(len(data)))
    check("цвета на месте", tuple(data[2:5]) == (255, 0, 0) and tuple(data[8:11]) == (0, 0, 255),
          data.hex())

    controller.send_frame(pixels * 100)
    big, _ = server.recvfrom(65535)
    check("больше, чем led_count, не отправляется", len(big) == 11, str(len(big)))
    controller.disconnect()

    # Adalight (USB) - только сборка пакета, без реального порта
    class FakeSerial:
        def __init__(self):
            self.buf = b""
            self.is_open = True

        def write(self, payload):
            self.buf += payload

        def close(self):
            pass

    serial_ctl = WLEDSerialController("COM_TEST", 115200)
    serial_ctl.serial_conn = FakeSerial()
    serial_ctl.led_count = 3
    serial_ctl.min_send_interval = 0.0
    serial_ctl.send_frame(pixels)
    packet = serial_ctl.serial_conn.buf
    n = 3 - 1
    hi, lo = (n >> 8) & 0xFF, n & 0xFF
    check("заголовок Adalight",
          packet[:3] == b"Ada" and packet[3] == hi and packet[4] == lo
          and packet[5] == (hi ^ lo ^ 0x55), packet[:6].hex())
    check("заголовок Adalight: данные пикселей", packet[6:] == bytes([255, 0, 0, 0, 255, 0, 0, 0, 255]),
          packet[6:].hex())
    server.close()


def test_config():
    print("\n[6] Конфиг")
    merged = _deep_merge(copy.deepcopy(DEFAULT_CONFIG), {"filters": {"change_threshold": 9}})
    check("пользовательское значение сохраняется", merged["filters"]["change_threshold"] == 9)
    check("недостающие ключи подставляются из дефолтов",
          "smoothing_min" in merged["filters"], str(sorted(merged["filters"])))
    check("вложенные словари не теряются", "standby" in merged and "audio" in merged)
    check("профиль для неизвестных процессов по умолчанию desktop",
          DEFAULT_CONFIG["unknown_process_profile"] == "desktop")

    # Повреждённый конфиг не должен ронять приложение при старте
    broken = copy.deepcopy(DEFAULT_CONFIG)
    broken["brightness"] = None
    broken["wled_port"] = "не число"
    broken["wled_led_count"] = -5
    broken["strip_density"] = "abc"
    broken["profiles"] = {"desktop": "мусор"}
    broken["game_processes"] = "cs2.exe"
    broken["strip_mode"] = "нечто"
    fixed = _sanitize(broken)
    check("битая секция заменяется дефолтной", isinstance(fixed["brightness"], dict))
    check("нечисловой порт чинится", fixed["wled_port"] == 21324, str(fixed["wled_port"]))
    check("отрицательное число диодов чинится", fixed["wled_led_count"] == 1, str(fixed["wled_led_count"]))
    check("мусорная плотность чинится", fixed["strip_density"] == 30, str(fixed["strip_density"]))
    check("битый профиль заменяется дефолтным",
          isinstance(fixed["profiles"].get("desktop"), dict))
    check("список игр приводится к списку", isinstance(fixed["game_processes"], list))
    check("недопустимый режим ленты чинится", fixed["strip_mode"] == "ble", str(fixed["strip_mode"]))


def test_mapping_file_validation():
    print("\n[7] Проверка файла маппинга")
    import tempfile

    bad_cases = {
        "не объект": "[1, 2, 3]",
        "нет пути": '{"led_count": 10}',
        "одна точка": '{"led_path": [[1, 2]]}',
        "мусор в точках": '{"led_path": [["a", "b"], [1, 2]]}',
    }
    for name, content in bad_cases.items():
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            tmp.write(content)
            path = tmp.name
        try:
            StripMapper().load_from_file(path)
            check(f"битый файл ({name}) отклонён", False, "исключения не было")
        except ValueError:
            check(f"битый файл ({name}) отклонён", True)
        except Exception as exc:  # noqa: BLE001
            check(f"битый файл ({name}) отклонён", False, f"не то исключение: {exc!r}")
        finally:
            os.unlink(path)

    # Корректный файл должен грузиться и строить карту
    good = [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump({"led_count": 20, "screen_width": 100, "screen_height": 100,
                   "led_path": good}, tmp)
        path = tmp.name
    try:
        mapper = StripMapper()
        ok = mapper.load_from_file(path)
        check("корректный файл загружается и строит карту", bool(ok) and len(mapper.led_boxes) == 20)
    finally:
        os.unlink(path)


def test_audio_spectrum():
    print("\n[8] Спектроанализатор")
    import numpy as np

    from ledapp.audio_engine import SPECTRUM_BANDS, AudioCapture

    capture = AudioCapture()
    # Синтетический чанк: синус 1000 Гц, 20 итераций чтобы автоподстройка успела
    time_axis = np.arange(1024) / 44100.0
    chunk = (0.5 * np.sin(2 * np.pi * 1000 * time_axis)).reshape(-1, 1)
    for _ in range(25):
        capture._process_chunk(chunk)

    state = capture.get_state()
    check("спектр заполняется", len(state.spectrum) == SPECTRUM_BANDS
          and max(state.spectrum) > 0.3,
          f"максимум {max(state.spectrum) if state.spectrum else 0:.2f}")

    peak_band = int(np.argmax(state.spectrum))
    check("пик спектра попадает в полосу 1 кГц", 10 <= peak_band <= 24,
          f"полоса {peak_band}")

    silence = np.zeros((1024, 1))
    for _ in range(40):
        capture._process_chunk(silence)
    quiet = capture.get_state()
    check("в тишине спектр затухает", max(quiet.spectrum) < 0.4,
          f"максимум {max(quiet.spectrum):.2f}")


def test_frame_analyzer():
    print("\n[9] Анализ кадра")
    import numpy as np

    from ledapp.frame_analysis import FrameAnalyzer

    # --- чёрные полосы ---
    analyzer = FrameAnalyzer()
    frame = np.full((270, 480, 3), 120, dtype=np.uint8)
    frame[:40] = 0
    frame[-40:] = 0
    for _ in range(8):
        analyzer.update(frame)
    top, bottom, left, right = analyzer.content_rect(1920, 1080)
    check("letterbox: верх обрезан", 120 <= top <= 200, f"top={top}")
    check("letterbox: низ обрезан", 880 <= bottom <= 960, f"bottom={bottom}")
    check("фильм распознан", analyzer.is_cinema)

    # --- статика ---
    still = FrameAnalyzer()
    for _ in range(20):
        still.update(np.full((270, 480, 3), 90, dtype=np.uint8))
    check("статичная картинка распознана", still.is_static)

    # --- статичный HUD на движущейся сцене ---
    hud = FrameAnalyzer()
    rng = np.random.default_rng(0)
    for _ in range(80):
        noisy = rng.integers(0, 255, (270, 480, 3), dtype=np.uint8)
        noisy[10:60, 10:120] = 200  # неподвижный «интерфейс»
        hud.update(noisy)
    mask = hud.ui_mask()
    check("маска статичного интерфейса найдена",
          mask is not None and bool(mask[1, 2]),
          "маска пустая" if mask is None else str(int(mask.sum())))
    check("маска не закрывает весь кадр", hud.ui_mask_coverage() < 0.35,
          f"покрытие {hud.ui_mask_coverage():.2f}")

    # --- пауза: картинка остановилась ПОСЛЕ движения ---
    paused = FrameAnalyzer()
    rng2 = np.random.default_rng(1)
    for _ in range(40):
        paused.update(rng2.integers(0, 255, (270, 480, 3), dtype=np.uint8))
    for _ in range(20):
        paused.update(np.full((270, 480, 3), 80, dtype=np.uint8))
    check("пауза после движения распознана", paused.paused_content())

    desktop = FrameAnalyzer()
    for _ in range(20):
        desktop.update(np.full((270, 480, 3), 80, dtype=np.uint8))
    check("статичный стол не считается паузой", not desktop.paused_content())


def test_ui_mask_shift():
    print("\n[10] Сдвиг выборки из статичного HUD")
    import numpy as np

    mapper = StripMapper(4, 100, 100)
    mapper.led_path = [(10, 10), (10, 60), (50, 60), (90, 60)]
    mapper.build_mapping(strip_width=4)
    base = list(mapper.led_boxes)

    mask = np.zeros((18, 32), dtype=bool)
    y0, y1, x0, x1 = base[0]
    row = int(((y0 + y1) // 2) / 100 * 18)
    col = int(((x0 + x1) // 2) / 100 * 32)
    mask[row, col] = True

    changed = mapper.apply_ui_mask(mask, 100, 100)
    check("диод уходит из-под интерфейса", changed and mapper.led_boxes[0] != base[0],
          str(mapper.led_boxes[0]))

    mapper.apply_ui_mask(None, 100, 100)
    check("без маски раскладка возвращается", mapper.led_boxes[0] == base[0])


def test_capture_color():
    print("\n[11] Захват: BGRA -> RGB без OpenCV")
    bgra = np.zeros((2, 2, 4), dtype=np.uint8)
    bgra[0, 0] = (0, 0, 255, 255)    # синий в BGRA
    bgra[0, 1] = (0, 255, 0, 255)    # зелёный
    bgra[1, 0] = (255, 0, 0, 255)    # красный
    bgra[1, 1] = (10, 20, 30, 128)   # альфа не должна влиять
    rgb = bgra_to_rgb(bgra)
    check("BGRA -> RGB: каналы не перепутаны",
          tuple(rgb[0, 0]) == (255, 0, 0)
          and tuple(rgb[0, 1]) == (0, 255, 0)
          and tuple(rgb[1, 0]) == (0, 0, 255),
          str(rgb.tolist()))
    check("альфа-канал отброшен", rgb.shape == (2, 2, 3), str(rgb.shape))
    check("результат непрерывный uint8",
          rgb.dtype == np.uint8 and rgb.flags["C_CONTIGUOUS"])

    bgr = np.array([[[255, 0, 0], [0, 255, 0]]], dtype=np.uint8)
    rgb3 = bgra_to_rgb(bgr)
    check("BGR -> RGB (3 канала) тоже поддержан",
          tuple(rgb3[0, 0]) == (0, 0, 255) and tuple(rgb3[0, 1]) == (0, 255, 0),
          str(rgb3.tolist()))


def main():
    print("=" * 60)
    print("Самотест LED Ambilight")
    print("=" * 60)

    test_color_pipeline()
    test_calibration_array()
    test_zones_and_hud()
    test_strip_mapper()
    test_protocol()
    test_config()
    test_mapping_file_validation()
    test_audio_spectrum()
    test_frame_analyzer()
    test_ui_mask_shift()
    test_capture_color()

    print("\n" + "=" * 60)
    if FAILED:
        print(f"ПРОВАЛЕНО {len(FAILED)} из {len(FAILED) + PASSED}: {', '.join(FAILED)}")
        return 1
    print(f"ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({PASSED})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
