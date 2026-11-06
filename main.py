"""
Точка входа приложения.

Здесь же:
  * настройка логирования (файл в %LOCALAPPDATA%\\LedAmbilight\\app.log);
  * High-DPI, чтобы интерфейс не мылился на масштабе 125-150%;
  * защита от второго экземпляра: два окна = два потока UDP = мерцание ленты;
  * перехват необработанных исключений, чтобы они попадали в лог, а не только
    в невидимую консоль.
"""

import logging
import sys

from PyQt5.QtCore import QSharedMemory, Qt
from PyQt5.QtWidgets import QApplication, QMessageBox

from ledapp.gui import MainWindow
from ledapp.logging_setup import setup_logging
from ledapp.style import APP_STYLESHEET

SINGLE_INSTANCE_KEY = "LedAmbilightELK-SingleInstance"


def _install_excepthook(log):
    """Логирует необработанные исключения (в т.ч. из слотов Qt)."""
    previous = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_tb)
            return
        log.critical("Необработанное исключение", exc_info=(exc_type, exc_value, exc_tb))
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def main():
    # Должно быть выставлено ДО создания QApplication
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    log_path = setup_logging()
    log = logging.getLogger("ledapp.main")
    _install_excepthook(log)
    log.info("Запуск приложения (лог: %s)", log_path)

    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)

    guard = QSharedMemory(SINGLE_INSTANCE_KEY)
    if not guard.create(1):
        if guard.error() == QSharedMemory.AlreadyExists:
            log.warning("Обнаружен уже запущенный экземпляр приложения")
            QMessageBox.information(
                None,
                "LED Ambilight",
                "Приложение уже запущено.\n\n"
                "Второй экземпляр не нужен: два окна одновременно отправляют\n"
                "разные кадры на одну ленту, и она начинает мерцать.\n\n"
                "Если окна не видно — проверь значок в системном трее.",
            )
            return 1
        # Другая причина (например, ограничение прав) - не мешаем запуску
        log.warning("Не удалось включить защиту от второго экземпляра: %s",
                    guard.errorString())

    window = MainWindow()
    window.show()
    try:
        return app.exec_()
    finally:
        guard.detach()
        log.info("Приложение завершено")


if __name__ == "__main__":
    sys.exit(main())
