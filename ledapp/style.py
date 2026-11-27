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

/* Выход из приложения в нижней строке окна — заметная, но не кричащая */
QPushButton#exitAction {
    background-color: #2c2e33;
    border-color: #5a3a3a;
    color: #e0b4b4;
    padding: 4px 12px;
}
QPushButton#exitAction:hover {
    background-color: #3a2a2a;
    border-color: #8a4a4a;
    color: #ffd0d0;
}
QPushButton#exitAction:pressed {
    background-color: #332525;
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
/* --- 2.0: боковая навигация и дашборд --- */
QListWidget#sidebar {
    background-color: #17181c;
    border: 1px solid #2b2d33;
    border-radius: 10px;
    padding: 6px 4px;
    outline: none;
}
QListWidget#sidebar::item {
    color: #c8cad2;
    border-radius: 7px;
    padding: 7px 8px;
    margin: 1px 3px;
}
QListWidget#sidebar::item:hover {
    background-color: #262931;
    color: #ffffff;
}
QListWidget#sidebar::item:selected {
    background-color: #2b6fd6;
    color: #ffffff;
}
QListWidget#sidebar::item:disabled {
    color: #666a74;
    background: transparent;
}
QTabWidget#mainPages::pane {
    border: none;
    background: transparent;
}
QTabWidget#mainPages QTabBar::tab {
    height: 0px;
    width: 0px;
    padding: 0px;
    margin: 0px;
}
QFrame#dashboardHero {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #1d2a44, stop:0.55 #242238, stop:1 #1a1c22);
    border: 1px solid #3b4050;
    border-radius: 14px;
}
QLabel#dashboardTitle {
    color: #ffffff;
    font-size: 25px;
    font-weight: 700;
}
QLabel#dashboardSubtitle {
    color: #b9c2d4;
    font-size: 12px;
}
QLabel#dashboardStatus {
    color: #ffb86b;
    font-size: 12px;
    font-weight: 600;
}
QLabel#dashboardStatus[live="true"] {
    color: #62e08a;
}
QFrame#dashboardImage {
    border-radius: 10px;
    border: 1px solid #3b4050;
    background: #0b0c0f;
}
QFrame#dashboardCard {
    background-color: #1b1c21;
    border: 1px solid #2f323a;
    border-radius: 10px;
}
QLabel#dashboardCardTitle {
    color: #7f8797;
    font-size: 9px;
    font-weight: 700;
}
QLabel#dashboardCardValue {
    color: #f0f2f7;
    font-size: 12px;
    font-weight: 600;
}
QPushButton[active="true"] {
    border-color: #2b6fd6;
    background-color: #243558;
}

/* --- аккуратные скроллбары --- */
QScrollBar:vertical {
    background: #17181c;
    width: 9px;
    margin: 2px 1px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #3d4350;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #505868; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal {
    background: #17181c;
    height: 9px;
    margin: 1px 2px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: #3d4350;
    min-width: 24px;
    border-radius: 4px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }

/* --- подсказка страницы и руководство --- */
QFrame#pageHelp {
    background-color: #191b21;
    border: 1px solid #2f323b;
    border-radius: 9px;
}
QLabel#pageHelpTitle {
    color: #dfe4f0;
    font-size: 12px;
    font-weight: 700;
}
QLabel#pageHelpText {
    color: #8f96a5;
    font-size: 11px;
}
QPushButton#helpButton {
    padding: 5px 10px;
    border-radius: 6px;
    background: #242833;
    border: 1px solid #3b4050;
}
QPushButton#helpButton:hover { background: #303645; }
QTextBrowser#guideBrowser {
    background-color: #1b1c21;
    border: 1px solid #2f323b;
    border-radius: 10px;
    padding: 14px;
    color: #e4e6ec;
}

QLineEdit#sidebarSearch {
    background-color: #17181c;
    border: 1px solid #2f323b;
    border-radius: 8px;
    padding: 7px 9px;
    color: #e4e6ec;
    selection-background-color: #2b6fd6;
}
QLineEdit#sidebarSearch:focus {
    border-color: #2b6fd6;
}

"""

THEME_TINTS = {
    "midnight": {},
    "ocean": {
        "#1e1f22": "#10202b", "#232428": "#182a36", "#17181c": "#0e1b24",
        "#1b1c21": "#14232e", "#191b21": "#12222c", "#1a1c22": "#10212c",
        "#1d2a44": "#12384a", "#242238": "#1c3040", "#262931": "#20333f",
        "#2a2d36": "#1b2d39",
    },
    "plum": {
        "#1e1f22": "#241a2b", "#232428": "#2d2036", "#17181c": "#1c1422",
        "#1b1c21": "#2a1f33", "#191b21": "#261a2e", "#1a1c22": "#211526",
        "#1d2a44": "#35204d", "#242238": "#3a2145", "#262931": "#33213c",
        "#2a2d36": "#302039",
    },
    "forest": {
        "#1e1f22": "#14231c", "#232428": "#1b3025", "#17181c": "#101c16",
        "#1b1c21": "#192b22", "#191b21": "#15251d", "#1a1c22": "#12221a",
        "#1d2a44": "#173b2b", "#242238": "#1c3528", "#262931": "#20382b",
        "#2a2d36": "#1d3327",
    },
}


def build_stylesheet(theme="midnight", accent="#2b6fd6"):
    css = APP_STYLESHEET
    for old, new in THEME_TINTS.get(str(theme or "midnight"), {}).items():
        css = css.replace(old, new)
    accent = str(accent or "#2b6fd6").lower()
    if len(accent) == 7 and accent.startswith("#"):
        css = css.replace("#2b6fd6", accent)
        css = css.replace("#8fb8ff", accent)
        try:
            rgb = [int(accent[i:i + 2], 16) for i in (1, 3, 5)]
            dark = "#%02x%02x%02x" % tuple(max(0, int(v * 0.45)) for v in rgb)
            css = css.replace("#243558", dark)
        except Exception:
            pass
    return css
