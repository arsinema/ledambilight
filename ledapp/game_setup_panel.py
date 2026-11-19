"""Ненавязчивая панель первого запуска игры.

Показывается поверх окна, не забирая фокус при появлении. Кнопки выбирают
режим; отдельного «Сохранить» нет — выбор сразу запоминается для процесса.
Если пользователь ничего не выбрал, через 12 секунд включается безопасный
режим Ambilight + лёгкая аудио-реакция.
"""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class GameSetupPanel(QWidget):
    selected = pyqtSignal(str)

    def __init__(self, game_name, parent=None):
        super().__init__(parent)
        self.game_name = game_name
        self._chosen = False
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("""
            QWidget#gameCard {
                background: rgba(22, 24, 30, 0.97);
                border: 1px solid #3b4050;
                border-radius: 14px;
            }
            QLabel { color: #e8e8ec; }
            QPushButton {
                color: #e8e8ec; background: #2a2d36;
                border: 1px solid #3d4250; border-radius: 10px;
                padding: 10px 12px; text-align: left;
            }
            QPushButton:hover { background: #343844; }
            QPushButton#primary { background: #2fa84f; border-color: #2fa84f; }
        """)
        card = QWidget(self)
        card.setObjectName("gameCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title = QLabel("Похоже, запущена игра")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)
        name = QLabel(game_name[:90])
        name.setStyleSheet("color: #8fb8ff; font-size: 12px;")
        layout.addWidget(name)
        hint = QLabel("Выбери режим — запомню для этой игры. Изменить можно в «Профилях».")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8a8b93; font-size: 11px;")
        layout.addWidget(hint)

        buttons = [
            ("Ambilight + лёгкий звук", "ambilight_audio", True),
            ("Только Ambilight", "ambilight", False),
            ("Визуализатор", "visualizer", False),
            ("Спектроанализатор", "spectrum", False),
            ("Игнорировать игру", "ignore", False),
        ]
        grid = QGridLayout()
        for index, (text, mode, primary) in enumerate(buttons):
            button = QPushButton(text)
            if primary:
                button.setObjectName("primary")
            button.clicked.connect(lambda _=False, m=mode: self._choose(m))
            grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(grid)
        self.setFixedWidth(560)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._choose("ambilight_audio"))
        self._timer.start(12000)

    def _choose(self, mode):
        if self._chosen:
            return
        self._chosen = True
        self._timer.stop()
        self.selected.emit(mode)
        self.close()

    def showEvent(self, event):
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.adjustSize()
            self.move(
                max(area.left() + 12, area.right() - self.width() - 24),
                max(area.top() + 12, area.bottom() - self.height() - 24),
            )
