"""Сборка Windows-установщика через Inno Setup.

Требуется ISCC.exe (Inno Setup 6). Скрипт ищет его в стандартных местах,
включая per-user установку в %LOCALAPPDATA%\\Programs\\InnoSetup6.

    python build_installer.py

Перед сборкой должен быть готов onedir-дистрибутив:

    python build_exe.py --portable
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ISS = ROOT / "installer" / "LEDAmbilight.iss"


def find_iscc():
    candidates = [
        os.environ.get("INNO_SETUP_ISCC"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\InnoSetup6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup\ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def main():
    iscc = find_iscc()
    if iscc is None:
        print("ISCC.exe не найден. Установи Inno Setup 6:")
        print("  winget install JRSoftware.InnoSetup --scope user")
        return 1
    if not ISS.is_file():
        print(f"Нет файла сценария: {ISS}")
        return 1
    portable = ROOT / "dist" / "LEDAmbilight" / "LEDAmbilight.exe"
    if not portable.is_file():
        print("Нет dist\\LEDAmbilight. Сначала: python build_exe.py --portable")
        return 1
    print(f"ISCC: {iscc}")
    result = subprocess.run([iscc, str(ISS)], cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode
    installers = sorted((ROOT / "dist" / "installer").glob("LEDAmbilight-*-setup.exe"))
    if installers:
        setup = installers[-1]
        print(f"Готово: {setup} ({setup.stat().st_size / (1024 * 1024):.1f} МБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
