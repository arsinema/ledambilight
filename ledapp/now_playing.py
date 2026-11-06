"""
Получение информации о "сейчас играет" через системный API Windows
(System Media Transport Controls) - тот же механизм, что показывает
плеер на экране блокировки и в диспетчере громкости. Работает с любым
приложением, которое его поддерживает (Spotify, браузеры, Windows Media
Player и т.п.), не требует входа в аккаунт или токенов.

Требует пакет winsdk (pip install winsdk). Если он не установлен -
функции просто возвращают None, остальное приложение не ломается.
"""

import io
import logging
import threading
import time
from typing import Optional, Tuple

log = logging.getLogger(__name__)

try:
    import asyncio
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    )
    from winsdk.windows.storage.streams import DataReader
    HAS_WINSDK = True
except Exception:
    HAS_WINSDK = False

try:
    from PIL import Image
    import numpy as np
    HAS_PIL = True
except Exception:
    HAS_PIL = False


async def _get_now_playing_async():
    """Спрашивает у Windows, что сейчас играет (в любом поддерживаемом
    приложении - активная медиа-сессия). Возвращает dict с title/artist/
    thumbnail (сырые байты картинки) или None, если ничего не играет."""
    manager = await MediaManager.request_async()
    session = manager.get_current_session()
    if session is None:
        return None

    info = await session.try_get_media_properties_async()
    thumb_bytes = None
    thumb_ref = info.thumbnail
    if thumb_ref is not None:
        try:
            stream = await thumb_ref.open_read_async()
            size = stream.size
            reader = DataReader(stream)
            await reader.load_async(size)
            buf = bytearray(size)
            reader.read_bytes(buf)
            thumb_bytes = bytes(buf)
        except Exception:
            thumb_bytes = None

    return {
        "title": info.title or "",
        "artist": info.artist or "",
        "thumbnail": thumb_bytes,
    }


def _extract_color_from_image_bytes(data: bytes) -> Optional[Tuple[int, int, int]]:
    """Декодирует картинку обложки и берёт усреднённый цвет."""
    if not HAS_PIL or not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img = img.resize((32, 32))
        arr = np.asarray(img).reshape(-1, 3).astype(np.float32)
        avg = arr.mean(axis=0)
        return (int(avg[0]), int(avg[1]), int(avg[2]))
    except Exception:
        return None


class NowPlayingMonitor:
    """Фоновый поток: раз в несколько секунд спрашивает Windows, что сейчас
    играет, и при смене трека вычисляет средний цвет обложки. Результат
    хранится потокобезопасно и читается через get_color()/get_track_name()."""

    def __init__(self, check_interval: float = 5.0):
        self.check_interval = check_interval
        self._lock = threading.Lock()
        self._color: Optional[Tuple[int, int, int]] = None
        self._track_name: str = ""
        self._last_key = None
        self._thread = None
        self._running = False
        self._generation = 0  # защита от «воскрешения» старого потока

    @staticmethod
    def is_available() -> bool:
        return HAS_WINSDK and HAS_PIL

    def start(self):
        if self._running or not self.is_available():
            return
        self._generation += 1
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(self._generation,), daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._generation += 1

    @property
    def is_running(self) -> bool:
        return self._running

    def get_color(self) -> Optional[Tuple[int, int, int]]:
        with self._lock:
            return self._color

    def get_track_name(self) -> str:
        with self._lock:
            return self._track_name

    def _loop(self, generation):
        while self._running and generation == self._generation:
            try:
                info = asyncio.run(_get_now_playing_async())
                if generation != self._generation:
                    return
                if info is None:
                    with self._lock:
                        self._color = None
                        self._track_name = ""
                    self._last_key = None
                else:
                    key = (info["artist"], info["title"])
                    track_name = f"{info['artist']} - {info['title']}".strip(" -")
                    if key != self._last_key:
                        self._last_key = key
                        color = _extract_color_from_image_bytes(info["thumbnail"]) if info["thumbnail"] else None
                        with self._lock:
                            self._color = color
                            self._track_name = track_name
                    else:
                        with self._lock:
                            self._track_name = track_name
            except Exception:
                log.debug("Не удалось получить информацию о треке", exc_info=True)
            time.sleep(self.check_interval)
