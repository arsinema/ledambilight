"""
Глобальные горячие клавиши Windows через RegisterHotKey.

Работают из любого приложения, не требуют прав администратора.
События приходят в Qt через нативный фильтр сообщений и превращаются
в сигнал triggered(action).
"""

import ctypes
import logging
from ctypes import wintypes

from PyQt5.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

VK_CODES = {
    "l": 0x4C,
    "b": 0x42,
    "d": 0x44,
    "up": 0x26,
    "down": 0x28,
}

# id -> (описание, действие)
BINDINGS = {
    1: ("Ctrl+Alt+L", "toggle_capture"),
    2: ("Ctrl+Alt+Up", "brightness_up"),
    3: ("Ctrl+Alt+Down", "brightness_down"),
    4: ("Ctrl+Alt+B", "blackout"),
    5: ("Ctrl+Alt+D", "diagnostics"),
}

_KEYS = {
    1: ("l", MOD_CONTROL | MOD_ALT),
    2: ("up", MOD_CONTROL | MOD_ALT),
    3: ("down", MOD_CONTROL | MOD_ALT),
    4: ("b", MOD_CONTROL | MOD_ALT),
    5: ("d", MOD_CONTROL | MOD_ALT),
}


class HotkeyFilter(QAbstractNativeEventFilter, QObject):
    """Ловит WM_HOTKEY и отдаёт действие сигналом."""

    triggered = pyqtSignal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._registered = False

    # ------------------------------------------------------------------

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY:
                    action = BINDINGS.get(int(msg.wParam), (None, None))[1]
                    if action:
                        self.triggered.emit(action)
                    return True, 0
        except Exception:
            log.debug("Ошибка обработки нативного сообщения", exc_info=True)
        return False, 0

    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool):
        if enabled and not self._registered:
            self._register()
        elif not enabled and self._registered:
            self._unregister()

    def _register(self):
        user32 = ctypes.windll.user32
        ok_all = True
        for hotkey_id, (key, mods) in _KEYS.items():
            vk = VK_CODES.get(key)
            if vk is None:
                continue
            if not user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                # Комбинацию мог занять другой процесс - это не фатально
                log.warning("Не удалось занять горячую клавишу %s",
                            BINDINGS[hotkey_id][0])
                ok_all = False
        self._registered = True
        if ok_all:
            log.info("Горячие клавиши включены")

    def _unregister(self):
        user32 = ctypes.windll.user32
        for hotkey_id in _KEYS:
            try:
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
        self._registered = False
        log.info("Горячие клавиши выключены")
