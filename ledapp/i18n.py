"""Перевод интерфейса на английский.

Каталог `locales_en.py` собран из русских строк интерфейса; переводы можно
поправлять руками. Перевод применяется к уже созданным виджетам и к виджетам,
которые показываются позже (диалоги, сообщения): фильтр событий обходит
дерево при показе.
"""

import logging

from PyQt5.QtCore import QEvent, QObject, QTimer
from PyQt5.QtWidgets import (
    QAbstractButton, QAction, QApplication, QComboBox, QGroupBox, QLabel,
    QLineEdit, QMenu, QTabWidget, QTableWidget, QWidget,
)

from .locales_en import CATALOG

log = logging.getLogger(__name__)

LANGUAGE = "ru"
_FILTER = None
_TIMER = None


def set_language(language):
    global LANGUAGE
    LANGUAGE = "en" if str(language).lower().startswith("en") else "ru"


def current_language():
    return LANGUAGE


def tr(text):
    """Переводит точную строку; незнакомые строки остаются как есть."""
    if LANGUAGE != "en" or not isinstance(text, str):
        return text
    return CATALOG.get(text, text)


class _TranslationFilter(QObject):
    def eventFilter(self, obj, event):  # noqa: N802
        if (
            LANGUAGE == "en"
            and event.type() in (QEvent.Show, QEvent.Polish)
            and isinstance(obj, QWidget)
        ):
            translate_tree(obj)
        return False


def install(language):
    """Вызывается из MainWindow после загрузки конфига."""
    global _FILTER, _TIMER
    set_language(language)
    app = QApplication.instance()
    if app is not None and _FILTER is None:
        _FILTER = _TranslationFilter(app)
        app.installEventFilter(_FILTER)
        if LANGUAGE == "en":
            _TIMER = QTimer(app)
            _TIMER.setInterval(1000)
            _TIMER.timeout.connect(_translate_top_levels)
            _TIMER.start()


def _translate_top_levels():
    if LANGUAGE != "en":
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if widget.isVisible():
            translate_tree(widget)


def translate_widget(widget):
    if LANGUAGE != "en":
        return
    try:
        if hasattr(widget, "windowTitle"):
            title = widget.windowTitle()
            if title:
                widget.setWindowTitle(tr(title))
        if isinstance(widget, QLabel):
            widget.setText(tr(widget.text()))
        elif isinstance(widget, QAbstractButton):
            widget.setText(tr(widget.text()))
        elif isinstance(widget, QGroupBox):
            widget.setTitle(tr(widget.title()))
        elif isinstance(widget, QTabWidget):
            for i in range(widget.count()):
                widget.setTabText(i, tr(widget.tabText(i)))
        elif isinstance(widget, QComboBox):
            for i in range(widget.count()):
                widget.setItemText(i, tr(widget.itemText(i)))
        elif isinstance(widget, QLineEdit):
            placeholder = widget.placeholderText()
            if placeholder:
                widget.setPlaceholderText(tr(placeholder))
        elif isinstance(widget, QMenu):
            widget.setTitle(tr(widget.title()))
            for action in widget.actions():
                translate_action(action)
        elif isinstance(widget, QTableWidget):
            for col in range(widget.columnCount()):
                item = widget.horizontalHeaderItem(col)
                if item is not None:
                    item.setText(tr(item.text()))
        tip = widget.toolTip()
        if tip:
            widget.setToolTip(tr(tip))
    except Exception:
        log.debug("Не удалось перевести виджет %r", widget, exc_info=True)


def translate_action(action):
    if LANGUAGE != "en" or not isinstance(action, QAction):
        return
    try:
        action.setText(tr(action.text()))
        tip = action.toolTip()
        if tip:
            action.setToolTip(tr(tip))
    except Exception:
        log.debug("Не удалось перевести действие", exc_info=True)


def translate_tree(root):
    if LANGUAGE != "en" or root is None:
        return
    translate_widget(root)
    try:
        for child in root.findChildren(QWidget):
            translate_widget(child)
        for action in root.findChildren(QAction):
            translate_action(action)
    except Exception:
        log.debug("Не удалось обойти дерево виджетов", exc_info=True)
