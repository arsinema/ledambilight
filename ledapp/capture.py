"""
Захват экрана (dxcam) и определение активного профиля по активному процессу.
Работает только на Windows (dxcam, win32gui, win32process).

Есть два бэкенда захвата:
  * DXGI (dxcam) — самый быстрый, но не работает в HDR и в некоторых играх
    с эксклюзивным полным экраном (кадр приходит чёрным);
  * WGC (windows-capture) — медленнее, зато умеет и то, и другое.
WGC — опциональная зависимость: если пакета нет, приложение молча работает
через DXGI.
"""

import logging
import threading

import numpy as np

from .color_engine import compute_dominant_color, mean_color

log = logging.getLogger(__name__)

try:
    import dxcam
except ImportError:
    dxcam = None

try:
    from windows_capture import WindowsCapture  # type: ignore
    HAS_WGC = True
except Exception:
    WindowsCapture = None
    HAS_WGC = False

try:
    import win32gui
    import win32process
    import psutil
    HAS_WIN_APIS = True
except ImportError:
    HAS_WIN_APIS = False


class ScreenCapture:
    def __init__(self, target_fps=30, backend="dxgi"):
        self.target_fps = target_fps
        self.backend = backend
        self.camera = None
        self._wgc = None
        self._wgc_control = None
        self._wgc_frame = None
        self._wgc_lock = threading.Lock()

    # ------------------------------------------------------------------

    def start(self):
        if self.backend == "wgc":
            if self._start_wgc():
                log.info("Захват экрана: WGC")
                return
            log.warning("WGC недоступен, переключаюсь на DXGI")
        self._start_dxgi()
        log.info("Захват экрана: DXGI")

    def _start_dxgi(self):
        if dxcam is None:
            raise RuntimeError(
                "dxcam недоступен. Захват экрана работает только на Windows."
            )
        # Явно запрашиваем RGB, чтобы не зависеть от дефолта библиотеки —
        # весь код ниже написан в предположении канала R,G,B по порядку.
        # max_buffer_len=2: нам нужен только самый свежий кадр (get_latest_frame),
        # а не история — дефолтный большой буфер держит в памяти десятки
        # full-HD кадров и был основной причиной раздутого потребления RAM.
        self.camera = dxcam.create(output_color="RGB", max_buffer_len=2)
        if self.camera is None:
            raise RuntimeError("Не удалось создать DXCam (проверьте GPU/драйверы).")
        self.camera.start(target_fps=self.target_fps, video_mode=True)

    def _start_wgc(self):
        """Пробует поднять захват через Windows Graphics Capture.

        Любая ошибка здесь не фатальна: вызывающий код откатится на DXGI.
        """
        if not HAS_WGC:
            return False
        try:
            capture = WindowsCapture(cursor_capture=False, draw_border=None,
                                     output_color="RGB")
            lock = self._wgc_lock

            @capture.event
            def on_frame_arrived(frame, capture_control):  # noqa: ANN001
                try:
                    data = np.array(frame.frame_buffer, copy=True)
                    if data.ndim == 3 and data.shape[2] == 4:
                        data = np.ascontiguousarray(data[:, :, :3])
                    with lock:
                        self._wgc_frame = data
                except Exception:
                    log.debug("Не удалось прочитать кадр WGC", exc_info=True)

            @capture.event
            def on_closed():  # noqa: ANN001
                log.info("Поток WGC закрыт")

            start_free = getattr(capture, "start_free_threaded", None)
            if start_free is not None:
                self._wgc_control = start_free()
            else:
                threading.Thread(target=capture.start, daemon=True).start()
            self._wgc = capture
            return True
        except Exception:
            log.exception("WGC не запустился")
            self._wgc = None
            self._wgc_control = None
            return False

    # ------------------------------------------------------------------

    def stop(self):
        if self._wgc is not None:
            control = self._wgc_control
            if control is not None:
                try:
                    control.stop()
                except Exception:
                    log.debug("Ошибка остановки WGC", exc_info=True)
            self._wgc = None
            self._wgc_control = None
            with self._wgc_lock:
                self._wgc_frame = None

        if self.camera is not None:
            try:
                self.camera.stop()
            except Exception:
                pass
            self.camera = None

    def get_frame(self):
        if self._wgc is not None:
            with self._wgc_lock:
                return self._wgc_frame
        if self.camera is None:
            return None
        frame = self.camera.get_latest_frame()
        if frame is None:
            return None
        return np.asarray(frame)


# Кэш последнего активного окна - GetForegroundWindow() дешёвый вызов, а вот
# psutil.Process(pid).name() относительно дорогой (открывает хэндл процесса в ОС).
# Без кэша это дёргалось на КАЖДОМ кадре (10+ раз в секунду), хотя активное
# окно обычно не меняется помногу секунд/минут подряд.
_process_cache = {"hwnd": None, "name": None}


def get_active_process_name():
    if not HAS_WIN_APIS:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        if hwnd == _process_cache["hwnd"]:
            return _process_cache["name"]
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return None
        process = psutil.Process(pid)
        name = process.name()
        _process_cache["hwnd"] = hwnd
        _process_cache["name"] = name
        return name
    except Exception:
        return None


def detect_profile(config):
    """Определяет имя профиля (ключ в config['profiles']) по активному процессу."""
    process_name = get_active_process_name()

    if not process_name:
        return "desktop"

    process_lower = process_name.lower()

    process_map = config.get("process_profile_map", {})
    for proc, profile in process_map.items():
        if process_lower == proc.lower():
            return profile

    game_processes = config.get("game_processes", [])
    for game in game_processes:
        if process_lower == game.lower():
            return "game"

    return config.get("unknown_process_profile", "desktop")


def hud_rect(height, width, hud_mask):
    """Границы области анализа после HUD-маски: (top, bottom, left, right).

    Одна и та же функция используется и для обрезки кадра, и для ограничения
    зон выборки каждого светодиода - иначе HUD-маска работала бы для общего
    цвета, но не для поэтевиксельной отправки."""
    top_pct = max(0, min(40, hud_mask.get("top", 0))) / 100.0
    bottom_pct = max(0, min(40, hud_mask.get("bottom", 0))) / 100.0
    left_pct = max(0, min(40, hud_mask.get("left", 0))) / 100.0
    right_pct = max(0, min(40, hud_mask.get("right", 0))) / 100.0

    top = int(height * top_pct)
    bottom = height - int(height * bottom_pct)
    left = int(width * left_pct)
    right = width - int(width * right_pct)

    if bottom <= top:
        top, bottom = 0, height
    if right <= left:
        left, right = 0, width
    return top, bottom, left, right


def apply_hud_mask(img, hud_mask):
    """Обрезает края изображения согласно hud_mask (проценты 0-40 на каждый край)."""
    h, w = img.shape[:2]
    top, bottom, left, right = hud_rect(h, w, hud_mask)
    if bottom <= top or right <= left:
        return img
    return img[top:bottom, left:right]


def downsize_for_preview(img, max_width=320):
    """Уменьшает кадр для лёгкого превью в GUI. Кадр уже в RGB (см. ScreenCapture.start)."""
    h, w = img.shape[:2]
    stride = max(1, w // max_width)
    small = img[::stride, ::stride, :3]
    return np.ascontiguousarray(small, dtype=np.uint8)


def _mean_color(area, linear=True):
    """Средний цвет области. Пустая область (крошечный кадр, сильная маска)
    раньше давала nan и «ломанный» цвет на ленте - теперь просто чёрный."""
    if area.size == 0:
        return (0.0, 0.0, 0.0)
    red, green, blue = mean_color(area[:, :, :3], linear=linear)
    if not np.all(np.isfinite((red, green, blue))):
        return (0.0, 0.0, 0.0)
    return (float(red), float(green), float(blue))


def _zone_thickness(h, w):
    """Толщина полосы у края для режима edges."""
    thickness = max(10, int(min(h, w) * 0.08))
    # На очень маленьком кадре полоса не должна вылезать за его половину
    return max(1, min(thickness, max(1, h // 2), max(1, w // 2)))


def get_zone_color(img, zone):
    h, w = img.shape[:2]
    thickness = _zone_thickness(h, w)

    if zone == "top":
        area = img[0:thickness, :]
    elif zone == "bottom":
        area = img[h - thickness:h, :]
    elif zone == "left":
        area = img[:, 0:thickness]
    elif zone == "right":
        area = img[:, w - thickness:w]
    else:
        area = img

    return _mean_color(area)


def get_zone_dominant_color(img, zone):
    """Доминантный (самый частый) цвет в конкретной зоне, а не среднее."""
    h, w = img.shape[:2]
    thickness = _zone_thickness(h, w)

    if zone == "top":
        area = img[0:thickness, :]
    elif zone == "bottom":
        area = img[h - thickness:h, :]
    elif zone == "left":
        area = img[:, 0:thickness]
    elif zone == "right":
        area = img[:, w - thickness:w]
    else:
        area = img

    return compute_dominant_color(area, sample_stride=4, quant=28)


def get_quadrant_color(img, quadrant):
    h, w = img.shape[:2]
    half_h, half_w = h // 2, w // 2

    if quadrant == "top_left":
        area = img[0:half_h, 0:half_w]
    elif quadrant == "top_right":
        area = img[0:half_h, half_w:w]
    elif quadrant == "bottom_left":
        area = img[half_h:h, 0:half_w]
    elif quadrant == "bottom_right":
        area = img[half_h:h, half_w:w]
    else:
        area = img

    return _mean_color(area)


def get_quadrant_dominant_color(img, quadrant):
    """Доминантный (самый частый) цвет в конкретном квадранте, а не среднее."""
    h, w = img.shape[:2]
    half_h, half_w = h // 2, w // 2

    if quadrant == "top_left":
        area = img[0:half_h, 0:half_w]
    elif quadrant == "top_right":
        area = img[0:half_h, half_w:w]
    elif quadrant == "bottom_left":
        area = img[half_h:h, 0:half_w]
    elif quadrant == "bottom_right":
        area = img[half_h:h, half_w:w]
    else:
        area = img

    return compute_dominant_color(area, sample_stride=4, quant=28)


def get_center_area(img, area_percent=60):
    """Вырезает центральную область экрана. area_percent — доля экрана (50-80 рекомендуется)."""
    area_percent = max(30, min(100, area_percent))
    h, w = img.shape[:2]

    margin = (100 - area_percent) / 2.0 / 100.0

    left = int(w * margin)
    right = int(w * (1 - margin))
    top = int(h * margin)
    bottom = int(h * (1 - margin))

    if right <= left or bottom <= top:
        return img

    return img[top:bottom, left:right]
