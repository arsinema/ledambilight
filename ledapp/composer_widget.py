"""Редактор слоистых композиций 2.0."""

from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .effects import EFFECT_LABELS
from .scene_engine import SceneRenderer
from .scene_model import (
    BLEND_MODES,
    Composition,
    Layer,
    default_compositions,
    normalize_compositions,
)

TYPE_LABELS = {
    "screen": "Экран",
    "audio": "Звук",
    "effect": "Эффект",
    "notification": "Уведомление",
    "spotify": "Spotify",
    "solid": "Цвет",
    "gradient": "Градиент",
}
BLEND_LABELS = {
    "normal": "Обычный",
    "add": "Сложение",
    "screen": "Экран",
    "multiply": "Умножение",
    "overlay": "Перекрытие",
}
MASK_LABELS = {"all": "Вся лента", "range": "Диапазон", "indices": "Список"}


def _default_layer(layer_type):
    effect = {
        "screen": "screen", "audio": "audio_bars", "notification": "notification",
        "spotify": "spotify_palette", "solid": "solid", "gradient": "gradient",
    }.get(layer_type, "breathing")
    params = {}
    if layer_type == "solid":
        params["color"] = [80, 140, 255]
    elif layer_type in ("effect", "gradient"):
        params["colors"] = [[20, 40, 120], [180, 40, 140], [30, 200, 180]]
    return Layer(
        id=f"{layer_type}-{id(params)}", type=layer_type, effect=effect,
        params=params,
    )


class _Preview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(54)
        self.composition = None
        self.renderer = SceneRenderer()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.update)

    def set_composition(self, composition):
        self.composition = composition
        self.update()

    def showEvent(self, event):  # noqa: N802
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(10, 11, 14))
        if self.composition is None:
            painter.end()
            return
        count = max(8, min(300, self.width() // 4))
        try:
            colors = self.renderer.render(
                self.composition, screen_pixels=None, audio=None, led_count=count,
            )
            for index, value in enumerate(colors):
                x0 = int(index * self.width() / count)
                x1 = max(x0 + 1, int((index + 1) * self.width() / count))
                painter.fillRect(x0, 0, x1 - x0, self.height(),
                                 QColor(int(value[0]), int(value[1]), int(value[2])))
        except Exception:
            pass
        painter.end()


class ComposerWidget(QWidget):
    def __init__(self, config, on_change=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.on_change = on_change
        self._loading = False
        section = config.setdefault("scene_composer", {})
        comps = section.get("compositions") or default_compositions()
        self._comps = normalize_compositions(comps)
        self._build()
        self._refresh_all()

    # ------------------------------------------------------------------

    def _section(self):
        return self.config.setdefault("scene_composer", {})

    def _build(self):
        root = QVBoxLayout(self)
        intro = QLabel(
            "Композиция — это стек слоёв. Экран и звук можно смешивать с эффектами,\n"
            "Spotify и всплывающими уведомлениями. Изменения применяются сразу."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        top = QHBoxLayout()
        self.enabled_cb = QCheckBox("Включить композитор")
        self.enabled_cb.stateChanged.connect(self._on_enabled)
        top.addWidget(self.enabled_cb)
        top.addWidget(QLabel("Активная:"))
        self.comp_combo = QComboBox()
        self.comp_combo.currentIndexChanged.connect(self._on_select_comp)
        top.addWidget(self.comp_combo, 1)
        add_btn = QPushButton("Добавить")
        add_btn.clicked.connect(self._add_comp)
        top.addWidget(add_btn)
        dup_btn = QPushButton("Дублировать")
        dup_btn.clicked.connect(self._dup_comp)
        top.addWidget(dup_btn)
        del_btn = QPushButton("Удалить")
        del_btn.clicked.connect(self._delete_comp)
        top.addWidget(del_btn)
        root.addLayout(top)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Название:"))
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._on_name)
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(QLabel("Переход, мс:"))
        self.transition_spin = QSpinBox()
        self.transition_spin.setRange(0, 5000)
        self.transition_spin.setSingleStep(50)
        self.transition_spin.valueChanged.connect(self._on_transition)
        name_row.addWidget(self.transition_spin)
        root.addLayout(name_row)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.layers_list = QListWidget()
        self.layers_list.setMinimumWidth(260)
        self.layers_list.currentRowChanged.connect(self._on_layer_selected)
        left.addWidget(self.layers_list, 1)
        layer_tools = QHBoxLayout()
        self.layer_type_combo = QComboBox()
        for key, label in TYPE_LABELS.items():
            self.layer_type_combo.addItem(label, key)
        layer_tools.addWidget(self.layer_type_combo, 1)
        add_layer_btn = QPushButton("+ слой")
        add_layer_btn.clicked.connect(self._add_layer)
        layer_tools.addWidget(add_layer_btn)
        left.addLayout(layer_tools)
        up_btn = QPushButton("Выше")
        up_btn.clicked.connect(lambda: self._move_layer(-1))
        down_btn = QPushButton("Ниже")
        down_btn.clicked.connect(lambda: self._move_layer(1))
        remove_btn = QPushButton("Удалить слой")
        remove_btn.clicked.connect(self._remove_layer)
        row = QHBoxLayout()
        row.addWidget(up_btn)
        row.addWidget(down_btn)
        row.addWidget(remove_btn)
        left.addLayout(row)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        self.layer_box = QGroupBox("Слой")
        form = QFormLayout(self.layer_box)
        self.layer_enabled = QCheckBox("Включён")
        self.layer_enabled.stateChanged.connect(self._on_layer_enabled)
        form.addRow(self.layer_enabled)
        self.effect_combo = QComboBox()
        for key, label in EFFECT_LABELS.items():
            self.effect_combo.addItem(label, key)
        self.effect_combo.currentIndexChanged.connect(self._on_effect)
        form.addRow("Эффект", self.effect_combo)
        self.blend_combo = QComboBox()
        for key in BLEND_MODES:
            self.blend_combo.addItem(BLEND_LABELS.get(key, key), key)
        self.blend_combo.currentIndexChanged.connect(self._on_blend)
        form.addRow("Смешивание", self.blend_combo)
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.0, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.valueChanged.connect(self._on_opacity)
        form.addRow("Прозрачность", self.opacity_spin)
        self.color_btn = QPushButton("Выбрать цвет")
        self.color_btn.clicked.connect(self._pick_color)
        form.addRow("Цвет", self.color_btn)
        self.mask_combo = QComboBox()
        for key, label in MASK_LABELS.items():
            self.mask_combo.addItem(label, key)
        self.mask_combo.currentIndexChanged.connect(self._on_mask)
        form.addRow("Маска", self.mask_combo)
        self.mask_start = QSpinBox()
        self.mask_start.setRange(0, 999)
        self.mask_start.valueChanged.connect(self._on_mask)
        self.mask_end = QSpinBox()
        self.mask_end.setRange(1, 1000)
        self.mask_end.valueChanged.connect(self._on_mask)
        mask_row = QHBoxLayout()
        mask_row.addWidget(self.mask_start)
        mask_row.addWidget(self.mask_end)
        form.addRow("Диапазон", mask_row)
        right.addWidget(self.layer_box)

        self.preview = _Preview()
        right.addWidget(QLabel("Превью композиции:"))
        right.addWidget(self.preview)
        hint = QLabel("Экранная база видна только на реальной ленте; в превью показаны эффекты.")
        hint.setStyleSheet("color: #8a8b93;")
        hint.setWordWrap(True)
        right.addWidget(hint)
        right.addStretch()
        body.addLayout(right, 2)
        root.addLayout(body, 1)

    # ------------------------------------------------------------------

    def _current(self):
        row = self.comp_combo.currentIndex()
        if 0 <= row < len(self._comps):
            return self._comps[row]
        return None

    def _refresh_all(self):
        self._loading = True
        try:
            section = self._section()
            self.enabled_cb.setChecked(bool(section.get("enabled", False)))
            self.comp_combo.clear()
            active = section.get("active", "")
            for comp in self._comps:
                self.comp_combo.addItem(comp.name, comp.id)
            index = max(0, self.comp_combo.findData(active))
            self.comp_combo.setCurrentIndex(index)
            comp = self._current()
            if comp is not None:
                self.name_edit.setText(comp.name)
                self.transition_spin.setValue(comp.transition_ms)
                self._refresh_layers(comp)
                self.preview.set_composition(comp)
        finally:
            self._loading = False

    def _refresh_layers(self, comp):
        row = self.layers_list.currentRow()
        self.layers_list.clear()
        for layer in sorted(comp.layers, key=lambda item: item.z):
            item = QListWidgetItem(
                f"{layer.z:02d}  {TYPE_LABELS.get(layer.type, layer.type)}"
                f"  ·  {EFFECT_LABELS.get(layer.effect, layer.effect)}"
                f"{'' if layer.enabled else '  (выкл)'}"
            )
            item.setData(Qt.UserRole, layer.id)
            self.layers_list.addItem(item)
        if comp.layers:
            self.layers_list.setCurrentRow(min(max(0, row), len(comp.layers) - 1))
        self._refresh_layer_form()

    def _selected_layer(self):
        comp = self._current()
        item = self.layers_list.currentItem()
        if comp is None or item is None:
            return None
        wanted = item.data(Qt.UserRole)
        for layer in comp.layers:
            if layer.id == wanted:
                return layer
        return None

    def _refresh_layer_form(self):
        layer = self._selected_layer()
        self.layer_box.setEnabled(layer is not None)
        if layer is None:
            return
        self._loading = True
        try:
            self.layer_enabled.setChecked(layer.enabled)
            index = self.effect_combo.findData(layer.effect)
            self.effect_combo.setCurrentIndex(max(0, index))
            index = self.blend_combo.findData(layer.blend)
            self.blend_combo.setCurrentIndex(max(0, index))
            self.opacity_spin.setValue(float(layer.opacity))
            mode = layer.mask.get("mode", "all")
            self.mask_combo.setCurrentIndex(max(0, self.mask_combo.findData(mode)))
            self.mask_start.setEnabled(mode == "range")
            self.mask_end.setEnabled(mode == "range")
            self.mask_start.setValue(int(layer.mask.get("start", 0)))
            self.mask_end.setValue(int(layer.mask.get("end", 999)))
            has_color = "color" in layer.params or layer.type in (
                "solid", "notification", "effect")
            self.color_btn.setEnabled(has_color)
            color = layer.params.get("color", [80, 140, 255])
            try:
                self.color_btn.setStyleSheet(
                    f"background-color: rgb({int(color[0])},{int(color[1])},{int(color[2])});"
                )
            except Exception:
                pass
        finally:
            self._loading = False

    def _changed(self):
        section = self._section()
        section["compositions"] = [comp.to_dict() for comp in self._comps]
        comp = self._current()
        if comp is not None:
            section["active"] = comp.id
        if callable(self.on_change):
            self.on_change()

    # ------------------------------------------------------------------
    # Композиции

    def _on_enabled(self, state):
        if self._loading:
            return
        self._section()["enabled"] = bool(state)
        self._changed()

    def _on_select_comp(self, index):
        if self._loading:
            return
        comp = self._current()
        if comp is None:
            return
        self._section()["active"] = comp.id
        self._refresh_all()
        self._changed()

    def _on_name(self):
        comp = self._current()
        if comp is None or self._loading:
            return
        comp.name = self.name_edit.text().strip() or comp.name
        self._changed()
        self._refresh_all()

    def _on_transition(self, value):
        comp = self._current()
        if comp is None or self._loading:
            return
        comp.transition_ms = int(value)
        self._changed()

    def _add_comp(self):
        comp = Composition(
            id=f"scene-{len(self._comps) + 1}", name="Новая композиция",
            layers=[Layer(id="screen", type="screen", effect="screen")],
        ).normalized()
        self._comps.append(comp)
        self._section()["active"] = comp.id
        self._refresh_all()
        self._changed()

    def _dup_comp(self):
        comp = self._current()
        if comp is None:
            return
        clone = Composition.from_dict(comp.to_dict())
        clone.id = f"{comp.id}-copy-{len(self._comps)}"
        clone.name = f"{comp.name} (копия)"
        self._comps.append(clone)
        self._section()["active"] = clone.id
        self._refresh_all()
        self._changed()

    def _delete_comp(self):
        comp = self._current()
        if comp is None or len(self._comps) <= 1:
            return
        self._comps = [item for item in self._comps if item.id != comp.id]
        self._section()["active"] = self._comps[0].id
        self._refresh_all()
        self._changed()

    # ------------------------------------------------------------------
    # Слои

    def _add_layer(self):
        comp = self._current()
        if comp is None:
            return
        layer_type = self.layer_type_combo.currentData() or "effect"
        layer = _default_layer(layer_type)
        layer.id = f"{layer_type}-{len(comp.layers) + 1}"
        layer.z = len(comp.layers)
        comp.layers.append(layer)
        self._refresh_layers(comp)
        self.layers_list.setCurrentRow(len(comp.layers) - 1)
        self._changed()

    def _remove_layer(self):
        comp = self._current()
        layer = self._selected_layer()
        if comp is None or layer is None or len(comp.layers) <= 1:
            return
        comp.layers.remove(layer)
        self._refresh_layers(comp)
        self._changed()

    def _move_layer(self, delta):
        comp = self._current()
        layer = self._selected_layer()
        if comp is None or layer is None:
            return
        layers = sorted(comp.layers, key=lambda item: item.z)
        index = layers.index(layer)
        target = max(0, min(len(layers) - 1, index + delta))
        if target == index:
            return
        layers[index], layers[target] = layers[target], layers[index]
        for position, item in enumerate(layers):
            item.z = position
        comp.layers = layers
        self._refresh_layers(comp)
        self.layers_list.setCurrentRow(target)
        self._changed()

    def _on_layer_selected(self, row):
        if self._loading:
            return
        self._refresh_layer_form()
        layer = self._selected_layer()
        if layer is not None:
            comp = self._current()
            self.preview.set_composition(comp)

    def _on_layer_enabled(self, state):
        if self._loading:
            return
        layer = self._selected_layer()
        if layer is None:
            return
        layer.enabled = bool(state)
        self._refresh_layers(self._current())
        self._changed()

    def _on_effect(self, index):
        if self._loading:
            return
        layer = self._selected_layer()
        if layer is None:
            return
        layer.effect = self.effect_combo.currentData() or layer.effect
        self._refresh_layers(self._current())
        self._changed()

    def _on_blend(self, index):
        if self._loading:
            return
        layer = self._selected_layer()
        if layer is None:
            return
        layer.blend = self.blend_combo.currentData() or "normal"
        self._changed()

    def _on_opacity(self, value):
        if self._loading:
            return
        layer = self._selected_layer()
        if layer is None:
            return
        layer.opacity = float(value)
        self._changed()

    def _on_mask(self, *_):
        if self._loading:
            return
        layer = self._selected_layer()
        if layer is None:
            return
        mode = self.mask_combo.currentData() or "all"
        layer.mask = {"mode": mode}
        if mode == "range":
            layer.mask["start"] = int(self.mask_start.value())
            layer.mask["end"] = int(self.mask_end.value())
        self.mask_start.setEnabled(mode == "range")
        self.mask_end.setEnabled(mode == "range")
        self._changed()

    def _pick_color(self):
        layer = self._selected_layer()
        if layer is None:
            return
        current = layer.params.get("color", [80, 140, 255])
        try:
            initial = QColor(int(current[0]), int(current[1]), int(current[2]))
        except Exception:
            initial = QColor(80, 140, 255)
        color = QColorDialog.getColor(initial, self, "Цвет слоя")
        if not color.isValid():
            return
        layer.params["color"] = [color.red(), color.green(), color.blue()]
        self._refresh_layer_form()
        self._changed()
