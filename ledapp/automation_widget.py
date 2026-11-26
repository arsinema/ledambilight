"""Редактор правил Automation Studio 2.0."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .automation import (
    ACTION_FIELDS,
    ACTION_TYPES,
    TRIGGER_FIELDS,
    TRIGGER_TYPES,
    Rule,
    normalize_rules,
)


class AutomationWidget(QWidget):
    def __init__(self, config, on_change=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.on_change = on_change
        self._loading = False
        self._rules = normalize_rules(
            (config.get("automation") or {}).get("rules") or []
        )
        self._build()
        self._refresh_all()

    def _section(self):
        return self.config.setdefault("automation", {})

    def _build(self):
        root = QVBoxLayout(self)
        intro = QLabel(
            "Правило срабатывает по событию, проверяет условия и выполняет действия.\n"
            "Например: игра запущена → композиция «Игра 2.0»; Telegram → вспышка."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        top = QHBoxLayout()
        self.enabled_cb = QCheckBox("Включить автоматизацию")
        self.enabled_cb.stateChanged.connect(self._on_enabled)
        top.addWidget(self.enabled_cb)
        top.addWidget(QLabel("Правило:"))
        self.rule_combo = QComboBox()
        self.rule_combo.currentIndexChanged.connect(self._on_select)
        top.addWidget(self.rule_combo, 1)
        add = QPushButton("Добавить")
        add.clicked.connect(self._add_rule)
        top.addWidget(add)
        duplicate = QPushButton("Дублировать")
        duplicate.clicked.connect(self._dup_rule)
        top.addWidget(duplicate)
        delete = QPushButton("Удалить")
        delete.clicked.connect(self._delete_rule)
        top.addWidget(delete)
        root.addLayout(top)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Название:"))
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._on_name)
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(QLabel("Приоритет:"))
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(-100, 100)
        self.priority_spin.valueChanged.connect(self._on_priority)
        name_row.addWidget(self.priority_spin)
        name_row.addWidget(QLabel("Пауза, сек:"))
        self.cooldown_spin = QDoubleSpinBox()
        self.cooldown_spin.setRange(0, 3600)
        self.cooldown_spin.setSingleStep(1)
        self.cooldown_spin.valueChanged.connect(self._on_cooldown)
        name_row.addWidget(self.cooldown_spin)
        root.addLayout(name_row)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.rules_list = QListWidget()
        self.rules_list.currentRowChanged.connect(self._on_list_select)
        left.addWidget(self.rules_list, 1)
        self.rule_enabled = QCheckBox("Правило включено")
        self.rule_enabled.stateChanged.connect(self._on_rule_enabled)
        left.addWidget(self.rule_enabled)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        trigger_box = QGroupBox("Когда")
        tform = QFormLayout(trigger_box)
        self.trigger_form = tform
        self.trigger_combo = QComboBox()
        for key, label in TRIGGER_TYPES.items():
            self.trigger_combo.addItem(label, key)
        self.trigger_combo.currentIndexChanged.connect(self._on_trigger_type)
        tform.addRow("Событие", self.trigger_combo)
        self.trigger_value = QLineEdit()
        self.trigger_value.editingFinished.connect(self._on_trigger_value)
        tform.addRow("Значение", self.trigger_value)
        self.trigger_from = QLineEdit("22:00")
        self.trigger_from.editingFinished.connect(self._on_trigger_value)
        tform.addRow("С", self.trigger_from)
        self.trigger_to = QLineEdit("07:00")
        self.trigger_to.editingFinished.connect(self._on_trigger_value)
        tform.addRow("По", self.trigger_to)
        self.audio_metric = QComboBox()
        self.audio_metric.addItem("Громкость", "volume")
        self.audio_metric.addItem("Бас", "bass")
        self.audio_metric.addItem("Бит", "beat")
        self.audio_metric.currentIndexChanged.connect(self._on_trigger_value)
        tform.addRow("Метрика", self.audio_metric)
        self.audio_op = QComboBox()
        for op in (">", ">=", "<", "<="):
            self.audio_op.addItem(op, op)
        self.audio_op.currentIndexChanged.connect(self._on_trigger_value)
        tform.addRow("Сравнение", self.audio_op)
        self.audio_value = QDoubleSpinBox()
        self.audio_value.setRange(0.0, 1.0)
        self.audio_value.setSingleStep(0.05)
        self.audio_value.valueChanged.connect(self._on_trigger_value)
        tform.addRow("Порог", self.audio_value)
        self.game_event = QComboBox()
        self.game_event.addItem("Старт", "start")
        self.game_event.addItem("Выход", "end")
        self.game_event.currentIndexChanged.connect(self._on_trigger_value)
        tform.addRow("Событие игры", self.game_event)
        right.addWidget(trigger_box)

        condition_box = QGroupBox("Если")
        cform = QFormLayout(condition_box)
        self.condition_type = QComboBox()
        for key, label in (
            ("", "Без условия"), ("process", "Процесс"), ("title", "Заголовок"),
            ("scene", "Сцена"), ("composition", "Композиция"),
            ("audio", "Громкость"),
        ):
            self.condition_type.addItem(label, key)
        self.condition_type.currentIndexChanged.connect(self._on_condition)
        cform.addRow("Условие", self.condition_type)
        self.condition_value = QLineEdit()
        self.condition_value.editingFinished.connect(self._on_condition)
        cform.addRow("Значение", self.condition_value)
        right.addWidget(condition_box)

        action_box = QGroupBox("Тогда")
        alayout = QVBoxLayout(action_box)
        self.actions_list = QListWidget()
        self.actions_list.setMaximumHeight(110)
        self.actions_list.currentRowChanged.connect(lambda _: self._refresh_action_form())
        alayout.addWidget(self.actions_list)
        arow = QHBoxLayout()
        self.action_type = QComboBox()
        for key, label in ACTION_TYPES.items():
            self.action_type.addItem(label, key)
        arow.addWidget(self.action_type, 1)
        aadd = QPushButton("+ действие")
        aadd.clicked.connect(self._add_action)
        arow.addWidget(aadd)
        aremove = QPushButton("Удалить")
        aremove.clicked.connect(self._remove_action)
        arow.addWidget(aremove)
        alayout.addLayout(arow)
        self.action_target = QLineEdit()
        self.action_target.setPlaceholderText(
            "ID композиции (game-2, music-2) или название сцены"
        )
        self.action_target.editingFinished.connect(self._on_action_value)
        alayout.addWidget(self.action_target)
        self.action_text = QLineEdit()
        self.action_text.setPlaceholderText("текст уведомления")
        self.action_text.editingFinished.connect(self._on_action_value)
        alayout.addWidget(self.action_text)
        self.brightness_delta = QSpinBox()
        self.brightness_delta.setRange(-100, 100)
        self.brightness_delta.valueChanged.connect(self._on_action_value)
        alayout.addWidget(self.brightness_delta)
        right.addWidget(action_box)
        right.addStretch()
        body.addLayout(right, 2)
        root.addLayout(body, 1)

    # ------------------------------------------------------------------

    def _current(self):
        index = self.rule_combo.currentIndex()
        if 0 <= index < len(self._rules):
            return self._rules[index]
        return None

    def _refresh_all(self):
        self._loading = True
        try:
            self.enabled_cb.setChecked(bool(self._section().get("enabled", False)))
            self._refresh_combo()
            self.rule_combo.setCurrentIndex(0 if self._rules else -1)
            self._refresh_list()
            self._refresh_rule_form()
            self._refresh_actions()
        finally:
            self._loading = False

    def _refresh_combo(self):
        self.rule_combo.blockSignals(True)
        self.rule_combo.clear()
        for rule in self._rules:
            self.rule_combo.addItem(rule.name, rule.id)
        self.rule_combo.blockSignals(False)

    def _refresh_list(self):
        self.rules_list.blockSignals(True)
        self.rules_list.clear()
        for rule in self._rules:
            self.rules_list.addItem(rule.name)
        current = self.rule_combo.currentIndex()
        if 0 <= current < self.rules_list.count():
            self.rules_list.setCurrentRow(current)
        self.rules_list.blockSignals(False)

    def _refresh_rules(self):
        row = self.rule_combo.currentIndex()
        self._refresh_combo()
        if self._rules:
            self.rule_combo.setCurrentIndex(min(max(0, row), len(self._rules) - 1))
        self._refresh_list()
        self._refresh_rule_form()
        self._refresh_actions()

    def _refresh_rule_form(self):
        rule = self._current()
        self.rule_enabled.setEnabled(rule is not None)
        if rule is None:
            return
        self._loading = True
        try:
            self.rule_enabled.setChecked(rule.enabled)
            self.name_edit.setText(rule.name)
            self.priority_spin.setValue(rule.priority)
            self.cooldown_spin.setValue(rule.cooldown)
            ttype = rule.trigger.get("type", "process")
            self.trigger_combo.setCurrentIndex(max(0, self.trigger_combo.findData(ttype)))
            self.trigger_value.setText(str(rule.trigger.get("value", "")))
            self.trigger_from.setText(str(rule.trigger.get("from", "22:00")))
            self.trigger_to.setText(str(rule.trigger.get("to", "07:00")))
            self.audio_metric.setCurrentIndex(
                max(0, self.audio_metric.findData(rule.trigger.get("metric", "volume")))
            )
            self.audio_op.setCurrentIndex(
                max(0, self.audio_op.findData(rule.trigger.get("op", ">")))
            )
            self.audio_value.setValue(float(rule.trigger.get("value", 0.35) or 0.0))
            self.game_event.setCurrentIndex(
                max(0, self.game_event.findData(rule.trigger.get("event", "start")))
            )
            condition = rule.conditions[0] if rule.conditions else {}
            ctype = condition.get("type", "")
            self.condition_type.setCurrentIndex(
                max(0, self.condition_type.findData(ctype))
            )
            self.condition_value.setText(str(condition.get("value", "")))
            self.condition_value.setEnabled(bool(ctype))
            self._show_trigger_fields(ttype)
        finally:
            self._loading = False

    def _show_trigger_fields(self, ttype):
        is_time = ttype == "time"
        is_audio = ttype == "audio"
        is_game = ttype == "game"
        self.trigger_value.setVisible(ttype in ("process", "window_title", "spotify",
                                                "telegram", "notification"))
        self.trigger_from.setVisible(is_time)
        self.trigger_to.setVisible(is_time)
        self.audio_metric.setVisible(is_audio)
        self.audio_op.setVisible(is_audio)
        self.audio_value.setVisible(is_audio)
        self.game_event.setVisible(is_game)
        for field in (self.trigger_value, self.trigger_from, self.trigger_to,
                      self.audio_metric, self.audio_op, self.audio_value,
                      self.game_event):
            label = self.trigger_form.labelForField(field)
            if label is not None:
                label.setVisible(field.isVisible())

    def _refresh_actions(self):
        rule = self._current()
        self.actions_list.clear()
        if rule is None:
            return
        for action in rule.actions:
            self.actions_list.addItem(ACTION_TYPES.get(action.get("type"), action.get("type")))
        if rule.actions:
            self.actions_list.setCurrentRow(0)
        self._refresh_action_form()

    def _selected_action(self):
        rule = self._current()
        row = self.actions_list.currentRow()
        if rule is None or row < 0 or row >= len(rule.actions):
            return None
        return rule.actions[row]

    def _refresh_action_form(self):
        action = self._selected_action()
        if action is None:
            self.action_target.setEnabled(False)
            self.action_text.setEnabled(False)
            self.brightness_delta.setEnabled(False)
            return
        self.action_target.setEnabled(True)
        self.action_text.setEnabled(True)
        self.brightness_delta.setEnabled(True)
        atype = action.get("type", "notification")
        self.action_type.setCurrentIndex(max(0, self.action_type.findData(atype)))
        self.action_target.setText(str(
            action.get("composition") or action.get("scene") or ""
        ))
        self.action_text.setText(str(action.get("text", "")))
        try:
            self.brightness_delta.setValue(int(action.get("delta", 0)))
        except (TypeError, ValueError):
            self.brightness_delta.setValue(0)

    # ------------------------------------------------------------------

    def _changed(self):
        self._section()["rules"] = [rule.to_dict() for rule in self._rules]
        if callable(self.on_change):
            self.on_change()

    def _on_enabled(self, state):
        if self._loading:
            return
        self._section()["enabled"] = bool(state)
        self._changed()

    def _on_select(self, index):
        if not self._loading:
            self.rules_list.blockSignals(True)
            self.rules_list.setCurrentRow(index)
            self.rules_list.blockSignals(False)
            self._refresh_rule_form()
            self._refresh_actions()

    def _on_list_select(self, row):
        if not self._loading and row >= 0:
            self.rule_combo.blockSignals(True)
            self.rule_combo.setCurrentIndex(row)
            self.rule_combo.blockSignals(False)
            self._refresh_rule_form()
            self._refresh_actions()

    def _on_name(self):
        rule = self._current()
        if rule is None or self._loading:
            return
        rule.name = self.name_edit.text().strip() or rule.name
        self._refresh_rules()
        self._changed()

    def _on_priority(self, value):
        rule = self._current()
        if rule is None or self._loading:
            return
        rule.priority = int(value)
        self._changed()

    def _on_cooldown(self, value):
        rule = self._current()
        if rule is None or self._loading:
            return
        rule.cooldown = float(value)
        self._changed()

    def _on_rule_enabled(self, state):
        rule = self._current()
        if rule is None or self._loading:
            return
        rule.enabled = bool(state)
        self._changed()

    def _on_trigger_type(self, index):
        if self._loading:
            return
        rule = self._current()
        if rule is None:
            return
        ttype = self.trigger_combo.currentData() or "process"
        rule.trigger = {"type": ttype, **TRIGGER_FIELDS.get(ttype, {})}
        self._refresh_rule_form()
        self._changed()

    def _on_trigger_value(self, *_):
        if self._loading:
            return
        rule = self._current()
        if rule is None:
            return
        ttype = rule.trigger.get("type", "process")
        if ttype == "time":
            rule.trigger["from"] = self.trigger_from.text().strip() or "00:00"
            rule.trigger["to"] = self.trigger_to.text().strip() or "23:59"
        elif ttype == "audio":
            rule.trigger.update({
                "metric": self.audio_metric.currentData() or "volume",
                "op": self.audio_op.currentData() or ">",
                "value": float(self.audio_value.value()),
            })
        elif ttype == "game":
            rule.trigger["event"] = self.game_event.currentData() or "start"
        else:
            rule.trigger["value"] = self.trigger_value.text().strip()
        self._changed()

    def _on_condition(self, *_):
        if self._loading:
            return
        rule = self._current()
        if rule is None:
            return
        ctype = self.condition_type.currentData() or ""
        if not ctype:
            rule.conditions = []
            self.condition_value.setEnabled(False)
        else:
            rule.conditions = [{
                "type": ctype,
                "value": self.condition_value.text().strip(),
            }]
            self.condition_value.setEnabled(True)
        self._changed()

    def _add_action(self):
        rule = self._current()
        if rule is None:
            return
        atype = self.action_type.currentData() or "notification"
        action = {"type": atype, **ACTION_FIELDS.get(atype, {})}
        rule.actions.append(action)
        self._refresh_actions()
        self.actions_list.setCurrentRow(len(rule.actions) - 1)
        self._changed()

    def _remove_action(self):
        rule = self._current()
        row = self.actions_list.currentRow()
        if rule is None or row < 0 or row >= len(rule.actions):
            return
        del rule.actions[row]
        self._refresh_actions()
        self._changed()

    def _on_action_value(self, *_):
        rule = self._current()
        action = self._selected_action()
        if rule is None or action is None or self._loading:
            return
        atype = action.get("type")
        if atype == "set_composition":
            action["composition"] = self.action_target.text().strip()
        elif atype == "set_scene":
            action["scene"] = self.action_target.text().strip()
        elif atype == "brightness":
            action["delta"] = int(self.brightness_delta.value())
        elif atype in ("notification", "telegram"):
            action["text"] = self.action_text.text().strip()
        self._changed()

    def _add_rule(self):
        rule = Rule(
            id=f"rule-{len(self._rules) + 1}", name="Новое правило",
            trigger={"type": "process", "value": ""},
            actions=[{"type": "notification", "text": "Событие"}],
        ).normalized()
        self._rules.append(rule)
        self._refresh_rules()
        self.rule_combo.setCurrentIndex(len(self._rules) - 1)
        self._changed()

    def _dup_rule(self):
        rule = self._current()
        if rule is None:
            return
        clone = Rule.from_dict(rule.to_dict())
        clone.id = f"{rule.id}-copy-{len(self._rules)}"
        clone.name = f"{rule.name} (копия)"
        self._rules.append(clone)
        self._refresh_rules()
        self.rule_combo.setCurrentIndex(len(self._rules) - 1)
        self._changed()

    def _delete_rule(self):
        rule = self._current()
        if rule is None:
            return
        self._rules = [item for item in self._rules if item.id != rule.id]
        self._refresh_all()
        self._changed()

    def select_composition(self, composition_id):
        """Используется в редакторе действий."""
        self.action_target.setText(str(composition_id))
