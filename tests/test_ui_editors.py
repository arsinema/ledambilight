import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from ledapp.automation_widget import AutomationWidget
from ledapp.composer_widget import ComposerWidget
from ledapp.config import DEFAULT_CONFIG
from ledapp.integrations_widget import IntegrationsWidget

_APP = QApplication.instance() or QApplication([])


class EditorWidgetTests(unittest.TestCase):
    def test_composer_add_layer_and_enable(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        changes = []
        widget = ComposerWidget(config, on_change=lambda: changes.append(1))
        self.assertGreaterEqual(widget.comp_combo.count(), 4)
        widget.enabled_cb.setChecked(True)
        layer_type = widget.layer_type_combo.findData("effect")
        widget.layer_type_combo.setCurrentIndex(layer_type)
        before = widget.layers_list.count()
        widget._add_layer()
        self.assertEqual(widget.layers_list.count(), before + 1)
        self.assertTrue(config["scene_composer"]["enabled"])
        self.assertTrue(changes)

    def test_automation_add_rule(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        widget = AutomationWidget(config)
        widget.enabled_cb.setChecked(True)
        before = len(config["automation"]["rules"])
        widget._add_rule()
        self.assertEqual(len(config["automation"]["rules"]), before + 1)
        self.assertTrue(config["automation"]["enabled"])

    def test_integrations_fields(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        widget = IntegrationsWidget(config)
        widget.spotify_client_id.setText("client-123")
        widget._on_spotify_id()
        self.assertEqual(config["integrations"]["spotify"]["client_id"], "client-123")
        widget.telegram_chat.setText("999")
        widget._on_telegram_config()
        self.assertEqual(config["integrations"]["telegram"]["chat_id"], "999")


if __name__ == "__main__":
    unittest.main()
