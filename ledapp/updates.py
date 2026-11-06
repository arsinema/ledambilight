"""Проверка обновлений через GitHub Releases.

Запрос лёгкий: один GET к api.github.com, без токена и без отправки чего-либо,
кроме версии приложения в User-Agent. Если сети нет — просто возвращаем None,
приложение продолжает работать.
"""

import logging
import re
from dataclasses import dataclass

import requests

from . import __version__

log = logging.getLogger(__name__)

REPO = "arsinema/ledambilight"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
FALLBACK_URL = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 6.0


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    available: bool
    url: str
    notes: str = ""


def version_tuple(value):
    """'v1.2.3-beta' -> (1, 2, 3, 0); нечисловые части пропускаются."""
    numbers = [int(part) for part in re.findall(r"\d+", str(value or ""))][:4]
    while len(numbers) < 4:
        numbers.append(0)
    return tuple(numbers)


def is_newer(latest, current):
    """Сравнение версий по числам, а не по строкам ('1.10' > '1.9')."""
    return version_tuple(latest) > version_tuple(current)


def check_for_update(current=None, timeout=TIMEOUT):
    """Возвращает UpdateInfo или None, если проверить не удалось."""
    current = current or __version__
    try:
        response = requests.get(
            LATEST_API,
            timeout=timeout,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"LEDAmbilight/{current}",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # сеть, DNS, лимит GitHub, прокси и т.п.
        log.info("Проверка обновлений не удалась: %s", exc)
        return None

    tag = str(payload.get("tag_name") or "").strip()
    latest = tag[1:] if tag[:1].lower() == "v" else tag
    if not latest:
        log.warning("GitHub не вернул tag_name в последнем релизе")
        return None

    return UpdateInfo(
        current=current,
        latest=latest,
        available=is_newer(latest, current),
        url=payload.get("html_url") or FALLBACK_URL,
        notes=payload.get("body") or "",
    )
