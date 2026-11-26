"""Fallback для входящих Telegram-сообщений без winsdk.

Читает только заголовок окна Telegram Desktop и число непрочитанных.
Содержимое сообщений не читается: локально, без сети.
"""

from __future__ import annotations

import logging
import re
import threading
import time

log = logging.getLogger(__name__)

try:
    import win32gui
    HAS_WIN32 = True
except Exception:
    HAS_WIN32 = False


def parse_unread(title):
    text = str(title or "")
    match = re.search(r"\((\d+)\)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*(?:unread|непрочит)", text, re.IGNORECASE)
    return int(match.group(1)) if match else 0


class TelegramDesktopMonitor:
    def __init__(self, on_notification=None, on_status=None):
        self.on_notification = on_notification
        self.on_status = on_status
        self._thread = None
        self._running = False
        self._stop = threading.Event()
        self._last_unread = 0
        self.last_error = ""
        self.last_title = ""

    def start(self):
        if not HAS_WIN32:
            self.last_error = "win32gui недоступен"
            return False
        if self._running:
            return True
        self._running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="telegram-desktop")
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    @property
    def running(self):
        return self._running

    def _find_title(self):
        titles = []

        def callback(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = win32gui.GetWindowText(hwnd) or ""
                if "telegram" in title.lower():
                    titles.append(title)
            except Exception:
                pass

        try:
            win32gui.EnumWindows(callback, None)
        except Exception:
            pass
        return max(titles, key=parse_unread) if titles else ""

    def _loop(self):
        self._status("connected", "")
        first = True
        while self._running and not self._stop.is_set():
            title = self._find_title()
            unread = parse_unread(title)
            self.last_title = title
            if first:
                self._last_unread = unread
                first = False
            elif unread > self._last_unread:
                self._last_unread = unread
                if callable(self.on_notification):
                    try:
                        self.on_notification({
                            "app": "telegram",
                            "text": f"Telegram: {unread} непрочитанных",
                            "id": int(time.time() * 1000) % 1_000_000,
                        })
                    except Exception:
                        log.exception("Telegram desktop callback")
            elif unread < self._last_unread:
                self._last_unread = unread
            self._stop.wait(2.0)
        self._running = False
        self._status("stopped", "")

    def _status(self, state, message):
        if callable(self.on_status):
            try:
                self.on_status(state, message)
            except Exception:
                pass
