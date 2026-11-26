"""Необязательный слушатель Windows-уведомлений через winsdk.

Позволяет реагировать на входящие Telegram/другие toast-уведомления, не
читая личные чаты. Windows требует явного разрешения пользователя; при
отказе модуль просто выключен.
"""

from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger(__name__)

try:
    from winsdk.windows.ui.notifications import NotificationKinds
    from winsdk.windows.ui.notifications.management import (
        UserNotificationListener,
        UserNotificationListenerAccessStatus,
    )
    HAS_WINSDK = True
except Exception:
    NotificationKinds = None
    UserNotificationListener = None
    UserNotificationListenerAccessStatus = None
    HAS_WINSDK = False


class WindowsNotificationMonitor:
    def __init__(self, on_notification=None, on_status=None):
        self.on_notification = on_notification
        self.on_status = on_status
        self._thread = None
        self._running = False
        self._stop = threading.Event()
        self._seen = set()
        self.last_error = ""
        self.access = "unknown"

    def start(self):
        if not HAS_WINSDK:
            self.last_error = "winsdk не установлен"
            return False
        if self._running:
            return True
        self._running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="windows-notifications")
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

    def _run(self):
        try:
            asyncio.run(self._loop())
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Windows notifications: %s", exc)
            self._status("error", str(exc))
        finally:
            self._running = False
            self._status("stopped", "")

    async def _loop(self):
        listener = UserNotificationListener.current
        status = await listener.request_access_async()
        allowed = getattr(UserNotificationListenerAccessStatus, "ALLOWED", 1)
        self.access = str(status)
        if status != allowed and getattr(status, "value", status) != allowed:
            self.last_error = f"нет разрешения Windows: {status}"
            self._status("denied", self.last_error)
            return
        self._status("connected", "")
        while self._running and not self._stop.is_set():
            try:
                items = await listener.get_notifications_async(NotificationKinds.TOAST)
                for item in items or []:
                    key = (int(getattr(item, "id", 0)), str(getattr(item, "creation_time", "")))
                    if key in self._seen:
                        continue
                    self._seen.add(key)
                    app = ""
                    try:
                        app = item.app_info.display_name or ""
                    except Exception:
                        pass
                    text = ""
                    try:
                        binding = item.notification.visual.bindings[0]
                        text = " ".join(
                            str(element.text) for element in binding.get_text_elements()
                        )
                    except Exception:
                        pass
                    if callable(self.on_notification):
                        try:
                            self.on_notification({"app": app, "text": text,
                                                  "id": key[0]})
                        except Exception:
                            log.exception("Windows notification callback")
                if len(self._seen) > 500:
                    self._seen = set(list(self._seen)[-200:])
            except Exception as exc:
                self.last_error = str(exc)
                log.debug("Notification poll: %s", exc)
            await asyncio.sleep(3)

    def _status(self, state, message):
        if callable(self.on_status):
            try:
                self.on_status(state, message)
            except Exception:
                pass
