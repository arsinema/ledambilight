"""
Системные проверки Windows: заблокирован ли экран и сколько пользователь
бездействует. Нужны для автопаузы подсветки.
"""

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

DESKTOP_READOBJECTS = 0x0001
MAX_IDLE_SECONDS = 10 ** 6


def is_session_locked() -> bool:
    """True, если рабочий стол заблокирован (Win+L, экран блокировки).

    Надёжный способ без WTS-уведомлений: попытаться открыть входной рабочий
    стол. Когда сессия заблокирована, система не даёт к нему доступ.
    """
    try:
        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        desktop = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not desktop:
            return True
        user32.CloseDesktop(desktop)
        return False
    except Exception:
        log.debug("Не удалось проверить блокировку экрана", exc_info=True)
        return False


def idle_seconds() -> int:
    """Сколько секунд прошло с последнего движения мыши/нажатия клавиши."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0
        millis = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return int(millis // 1000)
    except Exception:
        log.debug("Не удалось получить время простоя", exc_info=True)
        return 0


def is_fullscreen_app() -> bool:
    """True, если на переднем плане полноэкранное приложение.

    Windows сама знает это через SHQueryUserNotificationState:
      2 — QUNS_BUSY (полноэкранное приложение),
      3 — QUNS_RUNNING_D3D_FULL_SCREEN (игра/видео в эксклюзивном режиме),
      4 — QUNS_PRESENTATION_MODE (показ слайдов).
    Нужно автопаузе: иначе она принимает тишину в фильме за простой и гасит
    подсветку посреди просмотра.
    """
    try:
        state = ctypes.c_int()
        hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if hr != 0:
            return False
        return state.value in (2, 3, 4)
    except Exception:
        log.debug("Не удалось определить полноэкранный режим", exc_info=True)
        return False
