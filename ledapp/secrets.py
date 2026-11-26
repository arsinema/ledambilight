"""Шифрованное хранилище токенов интеграций (Windows DPAPI)."""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
import threading

log = logging.getLogger(__name__)

try:
    import win32crypt
except Exception:
    win32crypt = None

_LOCK = threading.Lock()


def _path():
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "LedAmbilight")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "integrations.json")


def _load():
    try:
        with open(_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save(data):
    path = _path()
    fd, tmp = tempfile.mkstemp(prefix="integrations-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def protect(value):
    if win32crypt is None or not value:
        return ""
    try:
        blob = win32crypt.CryptProtectData(
            str(value).encode("utf-8"), "LedAmbilight", None, None, None, 0
        )
        return base64.b64encode(blob).decode("ascii")
    except Exception:
        log.exception("Не удалось зашифровать секрет")
        return ""


def unprotect(value):
    if win32crypt is None or not value:
        return ""
    try:
        blob = base64.b64decode(str(value).encode("ascii"))
        result = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        # pywin32 возвращает (description, data), но старые версии — только data.
        if isinstance(result, tuple):
            data = result[1] if len(result) > 1 else result[0]
        else:
            data = result
        if isinstance(data, bytes):
            return data.decode("utf-8")
        return str(data)
    except Exception:
        log.warning("Не удалось расшифровать секрет", exc_info=True)
        return ""


def get_secret(name):
    with _LOCK:
        data = _load()
    return unprotect(data.get(str(name), ""))


def set_secret(name, value):
    value = str(value or "").strip()
    with _LOCK:
        data = _load()
        if value:
            data[str(name)] = protect(value)
        else:
            data.pop(str(name), None)
        _save(data)


def delete_secret(name):
    set_secret(name, "")


def has_secret(name):
    return bool(get_secret(name))


def is_supported():
    return win32crypt is not None
