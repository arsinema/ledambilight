"""Сборка LED Ambilight в один .exe (PyInstaller).

Собранному приложению Python не нужен: человек скачивает файл и запускает.

    python build_exe.py           # dist/LEDAmbilight.exe  (один файл)
    python build_exe.py --dir     # dist/LEDAmbilight/    (стартует быстрее)

Что важно знать:
  * PyInstaller не входит в requirements — поставь отдельно:
        pip install pyinstaller
  * конфиг рядом с exe создаётся сам при первом запуске; если папка только
    для чтения (Program Files), приложение уйдёт в %APPDATA%\\LedAmbilight;
  * антивирусы иногда ругаются на onefile-сборки «неизвестного» exe —
    это ложное срабатывание, режим --dir обычно спокойнее.
"""

import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
NAME = "LEDAmbilight"

# Пакеты, которые PyInstaller иногда не находит сам (динамические импорты)
HIDDEN_IMPORTS = (
    "dxcam",
    "bleak",
    "bleak.backends.winrt",
    "bleak.backends.winrt.client",
    "soundcard",
    "serial",
    "serial.tools.list_ports",
    "win32api",
    "win32gui",
    "win32process",
    "win32con",
    "psutil",
    "qrcode",
)


def main():
    onefile = "--dir" not in sys.argv

    import importlib.util
    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller не установлен. Выполни:")
        print("    pip install pyinstaller")
        return 1

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed",
        "--name", NAME,
    ]
    icon_path = os.path.join(APP_DIR, "assets", "icon.ico")
    if os.path.exists(icon_path):
        args += ["--icon", icon_path]
    assets_dir = os.path.join(APP_DIR, "assets")
    if os.path.isdir(assets_dir):
        # ресурсы (иконка окна) должны лежать внутри exe
        args += ["--add-data", f"{assets_dir};assets"]
    if onefile:
        args.append("--onefile")
    for module in HIDDEN_IMPORTS:
        args += ["--hidden-import", module]
    args += ["--collect-submodules", "bleak"]
    args.append(os.path.join(APP_DIR, "main.py"))

    print("Собираю:", " ".join(args))
    result = subprocess.run(args, cwd=APP_DIR)
    if result.returncode != 0:
        print("\nСборка не удалась (код %s)" % result.returncode)
        return result.returncode

    if onefile:
        exe_path = os.path.join(APP_DIR, "dist", NAME + ".exe")
    else:
        exe_path = os.path.join(APP_DIR, "dist", NAME, NAME + ".exe")

    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\nГотово: {exe_path}  ({size_mb:.1f} МБ)")
        print("Конфиг создастся рядом с exe при первом запуске.")
        return 0

    print("\nСборка завершилась, но exe не найден — проверь папку dist/")
    return 1


if __name__ == "__main__":
    sys.exit(main())
