"""Определение активной игры по активному окну.

Игра = полноэкранное окно чужого процесса. Браузеры, плееры, лаунчеры и
служебные окна исключены: их полноэкранный режим не должен переключать
подсветку. Незнакомое полноэкранное приложение считается кандидатом —
пользователь один раз выбирает для него режим, дальше приложение помнит.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psutil

log = logging.getLogger(__name__)

try:
    import win32api
    import win32con
    import win32gui
    import win32process

    HAS_WIN_APIS = True
except Exception:
    HAS_WIN_APIS = False


DEFAULT_IGNORE = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "opera_gx.exe",
    "brave.exe", "vivaldi.exe", "yandex.exe", "browser.exe",
    "vlc.exe", "mpv.exe", "wmplayer.exe", "mpc-hc64.exe", "mpc-be64.exe",
    "potplayermini64.exe", "potplayer64.exe", "kmplayer.exe", "kodi.exe",
    "plex.exe", "plexmediaplayer.exe", "explorer.exe", "steam.exe",
    "steamwebhelper.exe", "discord.exe", "telegram.exe", "zoom.exe",
    "skype.exe", "teams.exe", "ledambilight.exe", "deepseek harness.exe",
}


@dataclass
class GameWindow:
    pid: int
    process: str
    title: str
    hwnd: int


def _is_fullscreen(hwnd):
    if not HAS_WIN_APIS or not hwnd:
        return False
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(monitor)
        ml, mt, mr, mb = info["Monitor"]
        width = max(1, mr - ml)
        height = max(1, mb - mt)
        covered_w = max(0, min(right, mr) - max(left, ml))
        covered_h = max(0, min(bottom, mb) - max(top, mt))
        return covered_w >= width * 0.95 and covered_h >= height * 0.95
    except Exception:
        return False


def get_foreground_game(config=None):
    """Возвращает GameWindow для полноэкранного неисключённого процесса."""
    if not HAS_WIN_APIS:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not _is_fullscreen(hwnd):
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return None
        process = psutil.Process(pid).name()
        title = win32gui.GetWindowText(hwnd) or ""
        ignore = set(DEFAULT_IGNORE)
        game_cfg = (config or {}).get("game_mode", {}) if config else {}
        ignore.update(str(name).lower() for name in game_cfg.get("ignore_processes", []))
        if process.lower() in ignore:
            return None
        return GameWindow(pid=pid, process=process, title=title, hwnd=hwnd)
    except (psutil.Error, OSError, Exception):
        return None


def process_alive(pid):
    try:
        return bool(pid) and psutil.pid_exists(pid)
    except Exception:
        return False


def normalize_profile(raw):
    """Допустимые режимы игры."""
    mode = str((raw or {}).get("mode", "ambilight_audio"))
    if mode == "ignore":
        return {"mode": "ignore"}
    if mode not in ("ambilight", "ambilight_audio", "visualizer", "spectrum"):
        mode = "ambilight_audio"
    return {"mode": mode}
