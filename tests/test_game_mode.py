import copy
import unittest
from unittest.mock import patch

from ledapp.game_mode import DEFAULT_IGNORE, get_foreground_game, normalize_profile
from ledapp.gui import MainWindow


class GameModeTests(unittest.TestCase):
    def test_known_modes(self):
        for mode in ("ambilight", "ambilight_audio", "visualizer", "spectrum", "ignore"):
            self.assertEqual(normalize_profile({"mode": mode})["mode"], mode)

    def test_unknown_mode_defaults_to_audio_ambilight(self):
        self.assertEqual(normalize_profile({"mode": "??"})["mode"], "ambilight_audio")
        self.assertEqual(normalize_profile({})["mode"], "ambilight_audio")

    def test_browsers_and_players_are_ignored(self):
        self.assertIn("chrome.exe", DEFAULT_IGNORE)
        self.assertIn("vlc.exe", DEFAULT_IGNORE)
        self.assertNotIn("cs2.exe", DEFAULT_IGNORE)


class FakeWorker:
    def __init__(self):
        self.config = None
        self.started = 0
        self.stopped = 0

    def update_config(self, config):
        self.config = config

    def start_audio(self):
        self.started += 1

    def stop_audio(self):
        self.stopped += 1


class FakeStatus:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class GameSessionRestoreTests(unittest.TestCase):
    def _bare_window(self, config):
        window = MainWindow.__new__(MainWindow)
        window.config = config
        window.worker = FakeWorker()
        window.status_label = FakeStatus()
        window._game_snapshot = None
        window._game_session = None
        window._last_scene_name = "Игра"
        window._queue_save = lambda: None
        window._sync_audio_ui = lambda: None
        return window

    def test_audio_mode_uses_loopback(self):
        config = {"audio": {"enabled": False, "source": "input", "mode": "hybrid"}}
        window = self._bare_window(config)
        window._apply_game_audio("ambilight_audio")
        self.assertTrue(config["audio"]["enabled"])
        self.assertEqual(config["audio"]["source"], "loopback")
        self.assertEqual(config["audio"]["mode"], "hybrid")
        self.assertEqual(window.worker.started, 1)

        window = self._bare_window(config)
        window._apply_game_audio("ambilight")
        self.assertFalse(config["audio"]["enabled"])
        self.assertEqual(window.worker.stopped, 1)

    def test_end_session_restores_snapshot(self):
        previous_audio = {
            "enabled": True,
            "source": "input",
            "device_name": "Line In",
            "mode": "visualizer",
            "input_gain": 4.0,
        }
        previous_brightness = {"max_scale": 0.65, "min_brightness": 0}
        previous_filters = {"gamma": 1.1, "neighbor_blend": 0.2}
        game_config = {
            "audio": {"enabled": True, "source": "loopback", "mode": "hybrid"},
            "brightness": {"max_scale": 0.95, "min_brightness": 10},
            "filters": {"gamma": 1.0, "neighbor_blend": 0.35},
            "last_scene": "Игра",
        }
        window = self._bare_window(game_config)
        window._game_session = {"pid": 1, "process": "fake.exe", "mode": "ambilight_audio"}
        window._game_snapshot = {
            "scene": "Кино",
            "last_scene": "Кино",
            "audio": copy.deepcopy(previous_audio),
            "brightness": copy.deepcopy(previous_brightness),
            "filters": copy.deepcopy(previous_filters),
        }
        window._end_game_session()
        self.assertEqual(game_config["audio"], previous_audio)
        self.assertEqual(game_config["brightness"], previous_brightness)
        self.assertEqual(game_config["filters"], previous_filters)
        self.assertEqual(game_config["last_scene"], "Кино")
        self.assertEqual(window._last_scene_name, "Кино")
        self.assertIsNone(window._game_session)
        self.assertEqual(window.worker.started, 1)
        self.assertIn("восстановлены", window.status_label.text)


class FakePsutilProcess:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class DetectionTests(unittest.TestCase):
    def _fullscreen_patches(self, title="Game"):
        return [
            patch("ledapp.game_mode.win32gui.GetForegroundWindow", return_value=123),
            patch("ledapp.game_mode.win32gui.GetWindowRect", return_value=(0, 0, 1920, 1080)),
            patch("ledapp.game_mode.win32gui.GetWindowText", return_value=title),
            patch("ledapp.game_mode.win32api.MonitorFromWindow", return_value=1),
            patch("ledapp.game_mode.win32api.GetMonitorInfo",
                  return_value={"Monitor": (0, 0, 1920, 1080)}),
            patch("ledapp.game_mode.win32process.GetWindowThreadProcessId",
                  return_value=(1, 777)),
        ]

    def test_fullscreen_game_is_detected(self):
        patches = self._fullscreen_patches("Counter-Strike 2")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            with patch("ledapp.game_mode.psutil.Process",
                       return_value=FakePsutilProcess("cs2.exe")):
                game = get_foreground_game({"game_mode": {}})
        self.assertIsNotNone(game)
        self.assertEqual(game.process, "cs2.exe")
        self.assertEqual(game.title, "Counter-Strike 2")

    def test_fullscreen_browser_is_ignored(self):
        patches = self._fullscreen_patches("YouTube")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            with patch("ledapp.game_mode.psutil.Process",
                       return_value=FakePsutilProcess("chrome.exe")):
                self.assertIsNone(get_foreground_game({"game_mode": {}}))

    def test_custom_ignore_list(self):
        patches = self._fullscreen_patches("My Game")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            with patch("ledapp.game_mode.psutil.Process",
                       return_value=FakePsutilProcess("mygame.exe")):
                self.assertIsNotNone(get_foreground_game({"game_mode": {}}))
                self.assertIsNone(get_foreground_game({
                    "game_mode": {"ignore_processes": ["mygame.exe"]},
                }))

    def test_windowed_app_is_not_a_game(self):
        with patch("ledapp.game_mode.win32gui.GetForegroundWindow", return_value=123):
            with patch("ledapp.game_mode.win32gui.GetWindowRect", return_value=(100, 100, 800, 600)):
                with patch("ledapp.game_mode.win32api.MonitorFromWindow", return_value=1):
                    with patch("ledapp.game_mode.win32api.GetMonitorInfo",
                               return_value={"Monitor": (0, 0, 1920, 1080)}):
                        self.assertIsNone(get_foreground_game({"game_mode": {}}))


if __name__ == "__main__":
    unittest.main()
