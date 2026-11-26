"""Telegram Bot API 2.0: уведомления, команды и события для автоматизации."""

from __future__ import annotations

import logging
import queue
import threading
import time

from . import secrets

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, token="", chat_id="", on_message=None, on_status=None):
        self.token = token or ""
        self.chat_id = str(chat_id or "")
        self.on_message = on_message
        self.on_status = on_status
        self._thread = None
        self._running = False
        self._stop = threading.Event()
        self._outbox = queue.Queue()
        self._offset = 0
        self.last_error = ""
        self._generation = 0

    # ------------------------------------------------------------------

    def configure(self, token=None, chat_id=None):
        if token is not None:
            self.token = str(token or "").strip()
            secrets.set_secret("telegram_token", self.token)
        if chat_id is not None:
            self.chat_id = str(chat_id or "").strip()
            secrets.set_secret("telegram_chat_id", self.chat_id)

    def start(self):
        if self._running:
            return True
        if not self.token:
            self.token = secrets.get_secret("telegram_token")
        if not self.token:
            self.last_error = "нет токена бота"
            return False
        if not self.chat_id:
            self.chat_id = secrets.get_secret("telegram_chat_id")
        self._running = True
        self._stop.clear()
        self._generation += 1
        self._thread = threading.Thread(
            target=self._loop, args=(self._generation,), daemon=True,
            name="telegram-bot",
        )
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        self._stop.set()
        self._generation += 1
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=3)

    @property
    def running(self):
        return self._running

    def send(self, text, chat_id=None):
        text = str(text or "").strip()
        if not text:
            return False
        target = str(chat_id or self.chat_id or "").strip()
        if not target:
            self.last_error = "не указан chat_id"
            return False
        self._outbox.put((target, text[:4000]))
        return True

    # ------------------------------------------------------------------

    def _api(self, method, payload=None, timeout=35):
        import requests

        url = API.format(token=self.token, method=method)
        response = requests.post(url, json=payload if payload is not None else {},
                                 timeout=timeout)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(result.get("description") or "Telegram API error")
        return result.get("result")

    def _loop(self, generation):
        while self._running and generation == self._generation:
            try:
                while not self._outbox.empty():
                    target, text = self._outbox.get_nowait()
                    try:
                        self._api("sendMessage", {
                            "chat_id": target, "text": text,
                            "disable_web_page_preview": True,
                        }, timeout=15)
                    except Exception as exc:
                        self.last_error = str(exc)
                        log.warning("Telegram send: %s", exc)
                updates = self._api("getUpdates", {
                    "offset": self._offset, "timeout": 5,
                    "allowed_updates": ["message"],
                }, timeout=15)
                for update in updates or []:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    message = update.get("message") or {}
                    text = (message.get("text") or "").strip()
                    chat = (message.get("chat") or {}).get("id")
                    sender = (message.get("from") or {}).get("username", "")
                    if text and chat is not None and callable(self.on_message):
                        if self.chat_id and str(chat) != str(self.chat_id):
                            log.info("Telegram: сообщение из чужого чата проигнорировано")
                            continue
                        try:
                            self.on_message(str(chat), text, sender)
                        except Exception:
                            log.exception("Telegram callback")
                self.last_error = ""
                if callable(self.on_status):
                    self.on_status("connected", "")
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("Telegram polling: %s", exc)
                if callable(self.on_status):
                    self.on_status("error", str(exc))
                time.sleep(3)
        self._running = False
        if callable(self.on_status):
            self.on_status("stopped", "")
