"""Безопасное автообновление одного exe.

Поддерживается только onefile-сборка: скачиваем `LEDAmbilight.exe` с GitHub,
проверяем SHA-256 из API релиза, затем отдельный PowerShell-скрипт ждёт выхода
приложения, подменяет exe и запускает новую версию. При ошибке copy скрипт
возвращает старый exe. Для onedir/portable и установленной версии
самообновление не делаем: там нужен установщик или zip целиком.
"""

import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

import requests

from .updates import UpdateInfo

log = logging.getLogger(__name__)

APP_ASSET_NAME = "LEDAmbilight.exe"
MAX_ASSET_BYTES = 200 * 1024 * 1024
ALLOWED_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class UpdateError(Exception):
    pass


class UpdateCancelled(UpdateError):
    pass


def app_distribution():
    """'source' | 'onefile' | 'onedir'."""
    if not getattr(sys, "frozen", False):
        return "source"
    base = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.isdir(os.path.join(base, "_internal")):
        return "onedir"
    return "onefile"


def self_update_supported():
    return app_distribution() == "onefile"


def select_asset(info: UpdateInfo):
    """Возвращает ReleaseAsset для одного exe или None."""
    for asset in getattr(info, "assets", ()) or ():
        if asset.name == APP_ASSET_NAME:
            return asset
    return None


def can_self_update(info: UpdateInfo):
    if not self_update_supported():
        return False
    asset = select_asset(info)
    if asset is None:
        return False
    try:
        _validate_asset(asset)
        return True
    except UpdateError:
        return False


def _validate_asset(asset):
    if asset.name != APP_ASSET_NAME:
        raise UpdateError(f"Неожиданное имя файла обновления: {asset.name!r}")
    if not SHA256_RE.match(asset.sha256 or ""):
        raise UpdateError("В релизе нет SHA-256 для exe — обновление отменено")
    if asset.size <= 0 or asset.size > MAX_ASSET_BYTES:
        raise UpdateError(f"Подозрительный размер обновления: {asset.size}")
    host = (urlparse(asset.url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise UpdateError(f"Недопустимый адрес загрузки: {host!r}")


def download_update(info, destination_dir, progress=None, cancel=None, timeout=30):
    """Скачивает exe обновления, проверяет SHA-256, возвращает путь."""
    asset = select_asset(info)
    if asset is None:
        raise UpdateError("В релизе нет LEDAmbilight.exe")
    _validate_asset(asset)
    os.makedirs(destination_dir, exist_ok=True)
    destination = os.path.join(destination_dir, APP_ASSET_NAME)
    part = destination + ".part"
    hasher = hashlib.sha256()
    downloaded = 0
    try:
        with requests.get(asset.url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or asset.size)
            with open(part, "wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise UpdateCancelled("Загрузка отменена")
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_ASSET_BYTES:
                        raise UpdateError("Файл обновления больше допустимого")
                    handle.write(chunk)
                    hasher.update(chunk)
                    if progress is not None:
                        progress(downloaded, total or asset.size)
        if downloaded != asset.size:
            raise UpdateError(
                f"Размер скачанного файла {downloaded} не совпал с {asset.size}"
            )
        digest = hasher.hexdigest()
        if digest.lower() != asset.sha256.lower():
            raise UpdateError("SHA-256 скачанного файла не совпал с релизом")
        os.replace(part, destination)
        return destination
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass


def _ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def build_update_script(exe_path, new_path, script_path, delay_seconds=8):
    """Текст PowerShell-скрипта: подмена exe с откатом."""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"Start-Sleep -Seconds {int(delay_seconds)}\n"
        f"$exe = {_ps_literal(exe_path)}\n"
        f"$new = {_ps_literal(new_path)}\n"
        "$old = \"$exe.old\"\n"
        "try {\n"
        "    Copy-Item -LiteralPath $exe -Destination $old -Force\n"
        "    Copy-Item -LiteralPath $new -Destination $exe -Force\n"
        "} catch {\n"
        "    if (Test-Path -LiteralPath $old) {\n"
        "        Copy-Item -LiteralPath $old -Destination $exe -Force\n"
        "    }\n"
        "    Start-Process -FilePath $exe\n"
        "    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue\n"
        "    exit 1\n"
        "}\n"
        "Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue\n"
        "Remove-Item -LiteralPath $new -Force -ErrorAction SilentlyContinue\n"
        "Start-Process -FilePath $exe\n"
        "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue\n"
    )


def write_update_script(exe_path, new_path, delay_seconds=8):
    handle, path = tempfile.mkstemp(prefix="ledambilight-update-", suffix=".ps1")
    os.close(handle)
    script = build_update_script(exe_path, new_path, path, delay_seconds)
    with open(path, "w", encoding="utf-8-sig") as file:
        file.write(script)
    return path


def apply_self_update(new_path):
    """Запускает отдельный скрипт; вызывающий должен закрыть приложение."""
    if not self_update_supported():
        raise UpdateError("Самообновление поддерживается только для одиночного exe")
    if not os.path.isfile(new_path):
        raise UpdateError(f"Нет скачанного файла: {new_path}")
    exe_path = os.path.abspath(sys.executable)
    if os.path.normcase(os.path.dirname(new_path)) == os.path.normcase(
        os.path.dirname(exe_path)
    ):
        raise UpdateError("Новый exe лежит рядом с текущим — нечего заменять")
    script = write_update_script(exe_path, os.path.abspath(new_path))
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            script,
        ],
        close_fds=True,
        creationflags=(
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        ),
    )
    log.info("Автообновление запущено: %s -> %s", new_path, exe_path)
    return script
