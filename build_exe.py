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
import tempfile

# CI и консоли Windows с cp1252/cp866 не умеют печатать кириллицу —
# без этого сборка падает с UnicodeEncodeError ещё до PyInstaller.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
NAME = "LEDAmbilight"

try:
    from ledapp import __version__ as APP_VERSION
except Exception:  # сборка из неполной копии — не повод падать
    APP_VERSION = "0.0.0"

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


def _version_quad(version):
    """'1.2.3' -> (1, 2, 3, 0) для FixedFileInfo."""
    numbers = []
    for part in version.replace("-", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            numbers.append(int(digits))
        if len(numbers) == 4:
            break
    while len(numbers) < 4:
        numbers.append(0)
    return tuple(numbers[:4])


def write_version_file(path):
    """Генерирует version-файл PyInstaller: версия видна в свойствах exe.

    Строки намеренно латиницей: version-файл читается сборщиком как Python-
    литерал, и кириллица в нём — лишний риск кодировки.
    """
    v = _version_quad(APP_VERSION)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v},
    prodvers={v},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'arsinema'),
         StringStruct('FileDescription', 'LED Ambilight - screen ambient backlight'),
         StringStruct('FileVersion', '{APP_VERSION}'),
         StringStruct('InternalName', 'LEDAmbilight'),
         StringStruct('LegalCopyright', 'MIT License'),
         StringStruct('OriginalFilename', 'LEDAmbilight.exe'),
         StringStruct('ProductName', 'LED Ambilight'),
         StringStruct('ProductVersion', '{APP_VERSION}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


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

    version_file = os.path.join(
        tempfile.gettempdir(), f"{NAME}-{APP_VERSION}-version.txt"
    )
    try:
        write_version_file(version_file)
    except OSError as exc:
        print("Не удалось подготовить version-файл:", exc)
        version_file = None
    else:
        args += ["--version-file", version_file]

    print(f"Версия: {APP_VERSION}")
    print("Собираю:", " ".join(args))
    try:
        result = subprocess.run(args, cwd=APP_DIR)
    finally:
        if version_file and os.path.exists(version_file):
            os.remove(version_file)
    if result.returncode != 0:
        print("\nСборка не удалась (код %s)" % result.returncode)
        return result.returncode

    if onefile:
        exe_path = os.path.join(APP_DIR, "dist", NAME + ".exe")
    else:
        exe_path = os.path.join(APP_DIR, "dist", NAME, NAME + ".exe")

    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\nГотово: {exe_path}  ({size_mb:.1f} МБ), версия {APP_VERSION}")
        print("Конфиг создастся рядом с exe при первом запуске.")
        return 0

    print("\nСборка завершилась, но exe не найден — проверь папку dist/")
    return 1


if __name__ == "__main__":
    sys.exit(main())
