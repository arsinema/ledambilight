"""
Единый стиль приложения (QSS). Тёмная тема, аккуратные отступы,
выделенные акцентным цветом основные кнопки (запуск/подключение).
"""

APP_STYLESHEET = """
QWidget {
    background-color: #1e1f22;
    color: #e4e4e7;
    font-size: 13px;
}

QMainWindow {
    background-color: #1e1f22;
}

/* -- Вкладки -- */
QTabWidget::pane {
    border: 1px solid #33353a;
    border-radius: 6px;
    top: -1px;
    background-color: #232428;
}

QTabBar::tab {
    background-color: #232428;
    color: #9a9ba3;
    padding: 8px 16px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    border: 1px solid #33353a;
    border-bottom: none;
}

QTabBar::tab:selected {
    background-color: #2b6fd6;
    color: #ffffff;
}

QTabBar::tab:hover:!selected {
    background-color: #2c2e33;
    color: #e4e4e7;
}

/* -- Группы настроек -- */
QGroupBox {
    border: 1px solid #33353a;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: 600;
    color: #c8c9ce;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #8fb8ff;
}

/* -- Кнопки -- */
QPushButton {
    background-color: #2c2e33;
    color: #e4e4e7;
    border: 1px solid #40424a;
    border-radius: 6px;
    padding: 6px 14px;
}

QPushButton:hover {
    background-color: #34363c;
    border-color: #52545e;
}

QPushButton:pressed {
    background-color: #26282c;
}

QPushButton:disabled {
    color: #6b6c72;
    background-color: #25262a;
    border-color: #2e2f34;
}

/* Основное действие (запуск) - зелёный акцент через objectName */
QPushButton#primaryAction {
    background-color: #2fa84f;
    border-color: #2fa84f;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryAction:hover {
    background-color: #35bb58;
}
QPushButton#primaryAction:pressed {
    background-color: #268f43;
}

/* Останавливающее/деструктивное действие - приглушённый красный */
QPushButton#dangerAction {
    background-color: #3a2a2a;
    border-color: #6b3838;
    color: #f0b4b4;
}
QPushButton#dangerAction:hover {
    background-color: #4a3232;
    border-color: #854747;
}

/* -- Поля ввода -- */
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #26272b;
    border: 1px solid #3a3c42;
    border-radius: 5px;
    padding: 4px 6px;
    selection-background-color: #2b6fd6;
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #2b6fd6;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #26272b;
    color: #e4e4e7;
    selection-background-color: #2b6fd6;
    border: 1px solid #3a3c42;
}

/* -- Списки -- */
QListWidget {
    background-color: #26272b;
    border: 1px solid #3a3c42;
    border-radius: 5px;
}

QListWidget::item {
    padding: 4px;
}

QListWidget::item:selected {
    background-color: #2b6fd6;
    color: #ffffff;
}

/* -- Чекбоксы -- */
QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #52545e;
    background-color: #26272b;
}

QCheckBox::indicator:checked {
    background-color: #2b6fd6;
    border-color: #2b6fd6;
}

/* -- Слайдеры -- */
QSlider::groove:horizontal {
    height: 4px;
    background: #35373d;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #2b6fd6;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::sub-page:horizontal {
    background: #2b6fd6;
    border-radius: 2px;
}

/* -- Прогресс-бары (VU-метр) -- */
QProgressBar {
    background-color: #26272b;
    border: 1px solid #3a3c42;
    border-radius: 4px;
    text-align: center;
    height: 14px;
}

QProgressBar::chunk {
    background-color: #2b6fd6;
    border-radius: 3px;
}

/* -- Скроллбары -- */
QScrollBar:vertical {
    background: #1e1f22;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3a3c42;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #4a4c54;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollArea {
    border: none;
    background: transparent;
}

/* -- Статусная плашка вверху окна -- */
QLabel#statusHeader {
    font-weight: 600;
    padding: 4px 2px;
}
"""
