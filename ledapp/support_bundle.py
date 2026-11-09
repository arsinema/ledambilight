"""Сбор ZIP-отчёта для поддержки.

Отчёт помогает разобрать проблему без переписки «пришлите лог»: внутри логи,
конфиг (с вырезанными адресами), текущий маппинг и текст диагностики. Ничего
никуда не отправляется — пользователь сам прикладывает файл к issue.
"""

import copy
import ctypes
import datetime as _dt
import json
import os
import platform
import re
import sys
import zipfile

from . import __version__
from .logging_setup import log_directory

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
HOME = os.path.expanduser("~")

# Эти поля не должны попадать в публичный отчёт: адреса плат, MAC, COM-порт.
_REDACT_TOP = {"wled_host", "wled_serial_port", "device_address", "device_name"}


def redact_text(text):
    if not isinstance(text, str):
        return text
    text = IP_RE.sub("***.***.***.***", text)
    text = MAC_RE.sub("**:**:**:**:**:**", text)
    if HOME and HOME in text:
        text = text.replace(HOME, "%USERPROFILE%")
    return text


def redact_config(config):
    data = copy.deepcopy(config)

    def walk_replace(node):
        if isinstance(node, dict):
            for key in list(node):
                value = node[key]
                if isinstance(value, (dict, list)):
                    node[key] = walk_replace(value)
                elif isinstance(value, str):
                    node[key] = redact_text(value)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                node[i] = walk_replace(value)
        return node

    data = walk_replace(data)
    for key in _REDACT_TOP:
        if key in data:
            data[key] = "***"
    mqtt = data.get("mqtt")
    if isinstance(mqtt, dict) and "host" in mqtt:
        mqtt["host"] = "***"
    return data


def system_info():
    lines = [
        f"LED Ambilight: {__version__}",
        f"Frozen exe: {bool(getattr(sys, 'frozen', False))}",
        f"Executable: {sys.executable}",
        f"Python: {sys.version.split()[0]}",
        f"OS: {platform.platform()}",
        f"Machine: {platform.machine()}",
        f"Processor: {platform.processor()}",
    ]
    try:
        import psutil
        mem = psutil.virtual_memory()
        lines.append(
            f"RAM: {mem.total / (1024 ** 3):.1f} GB "
            f"(available {mem.available / (1024 ** 3):.1f} GB)"
        )
        lines.append(f"CPU cores: {psutil.cpu_count(logical=True)}")
    except Exception:
        pass
    try:
        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
        lines.append(f"Screen: {width}x{height}")
    except Exception:
        pass
    try:
        import dxcam
        info = dxcam.device_info()
        if info:
            lines.append("GPU/DXGI:")
            lines.extend("  " + line for line in str(info).splitlines())
    except Exception:
        pass
    return "\n".join(lines)


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def build_report(destination, config, diagnostics="", mapping=None):
    """Создаёт ZIP и возвращает путь. Исключения пробрасываются вызывающему."""
    destination = os.path.abspath(destination)
    if not destination.lower().endswith(".zip"):
        destination += ".zip"
    parent = os.path.dirname(destination)
    if parent:
        os.makedirs(parent, exist_ok=True)

    logs_dir = log_directory()
    log_files = []
    for name in sorted(os.listdir(logs_dir)):
        if name.startswith("app.log"):
            path = os.path.join(logs_dir, name)
            if os.path.isfile(path):
                log_files.append(path)

    readme = (
        "LED Ambilight support report\n"
        "============================\n\n"
        "Contents:\n"
        "  system.txt             — app/OS/Python/GPU information\n"
        "  config.redacted.json   — settings with IP/MAC/COMPORT removed\n"
        "  diagnostics.txt        — text from the Diagnostics dialog\n"
        "  mapping.json           — current LED mapping, if configured\n"
        "  logs/app.log*          — application logs (IP/MAC redacted)\n\n"
        "Nothing was uploaded automatically. Review the files and attach this\n"
        "ZIP to a GitHub issue if you want help.\n"
    )

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr("system.txt", redact_text(system_info()) + "\n")
        archive.writestr(
            "config.redacted.json",
            json.dumps(redact_config(config), ensure_ascii=False, indent=2),
        )
        if diagnostics:
            archive.writestr("diagnostics.txt", redact_text(diagnostics))
        if mapping:
            archive.writestr(
                "mapping.json",
                json.dumps(mapping, ensure_ascii=False, indent=2),
            )
        for path in log_files:
            text = redact_text(_read_text(path))
            archive.writestr(f"logs/{os.path.basename(path)}", text)

    return destination


def default_report_name():
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
    return f"LedAmbilight-report-{stamp}.zip"
