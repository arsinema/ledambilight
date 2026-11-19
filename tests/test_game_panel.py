import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton

from ledapp.game_setup_panel import GameSetupPanel

_APP = QApplication.instance() or QApplication([])


class GameSetupPanelTests(unittest.TestCase):
    def test_button_emits_mode_and_closes(self):
        panel = GameSetupPanel("testgame.exe")
        chosen = []
        panel.selected.connect(chosen.append)
        buttons = {b.text(): b for b in panel.findChildren(QPushButton)}
        self.assertIn("Только Ambilight", buttons)
        buttons["Только Ambilight"].click()
        self.assertEqual(chosen, ["ambilight"])
        self.assertFalse(panel.isVisible())

    def test_ignore_button(self):
        panel = GameSetupPanel("testgame.exe")
        chosen = []
        panel.selected.connect(chosen.append)
        buttons = {b.text(): b for b in panel.findChildren(QPushButton)}
        buttons["Игнорировать игру"].click()
        self.assertEqual(chosen, ["ignore"])


if __name__ == "__main__":
    unittest.main()
