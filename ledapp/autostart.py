"""
Автозапуск приложения вместе с Windows через реестр
(HKEY_CURRENT_USER\\...\\CurrentVersion\\Run — не требует прав администратора).
"""

import logging
import os
import sys

log = logging.getLogger(__name__)

try:
    import winreg
except ImportError:
    winreg = None  # не Windows — автозапуск недоступен

APP_NAME = "LedAmbilightELK"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_supported():
    return winreg is not None


def _main_py_path():
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "main.py")
    return os.path.normpath(main_py)


def _build_command():
    """Команда запуска: в собранной версии — сам .exe, иначе pythonw + main.py."""
    if getattr(sys, "frozen", False):
        # PyInstaller: sys.executable — это и есть наш exe
        return f'"{sys.executable}"'

    python_exe = sys.executable
    pythonw_exe = python_exe

    lower = python_exe.lower()
    if lower.endswith("python.exe"):
        candidate = python_exe[: -len("python.exe")] + "pythonw.exe"
        if os.path.exists(candidate):
            pythonw_exe = candidate

    return f'"{pythonw_exe}" "{_main_py_path()}"'


def is_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value == _build_command()
    except (FileNotFoundError, OSError):
        return False


def enable():
    if winreg is None:
        raise RuntimeError("Автозапуск поддерживается только на Windows.")
    main_py = _main_py_path()
    if not os.path.exists(main_py):
        # Иначе в реестр попадёт путь в никуда и автозапуск будет «молча не работать»
        raise RuntimeError(f"Не найден файл запуска: {main_py}")
    command = _build_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
    log.info("Автозапуск включён: %s", command)


def disable():
    if winreg is None:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        log.info("Автозапуск выключен")
    except (FileNotFoundError, OSError):
        pass
