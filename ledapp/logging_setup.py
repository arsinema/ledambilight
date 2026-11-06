"""
Единая настройка логирования приложения.

Логи пишутся в файл (с ротацией) и в консоль. Файл лежит в
%LOCALAPPDATA%\\LedAmbilight\\app.log — рядом с приложением писать нельзя,
если оно установлено в Program Files.
"""

import logging
import logging.handlers
import os
import sys

APP_DIR_NAME = "LedAmbilight"
LOG_FILE_NAME = "app.log"

_configured = False


def log_directory():
    """Каталог для логов: %LOCALAPPDATA%\\LedAmbilight (создаётся при необходимости)."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = os.path.abspath(".")
    return path


def log_file_path():
    return os.path.join(log_directory(), LOG_FILE_NAME)


def setup_logging(level=logging.INFO, console=True):
    """Настраивает корневой логгер. Повторный вызов безопасен."""
    global _configured
    if _configured:
        return log_file_path()

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:  # не падаем, если писать в файл некуда
        print(f"Не удалось открыть лог-файл: {exc}", file=sys.stderr)

    if console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    # Шумные сторонние логгеры - только предупреждения
    for noisy in ("urllib3", "requests", "bleak", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    return log_file_path()
