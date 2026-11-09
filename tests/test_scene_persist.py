import copy
import unittest

from ledapp.config import DEFAULT_CONFIG
from ledapp.gui import SCENE_PRESETS, detect_scene, scene_matches_config


def config_for(name):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    preset = SCENE_PRESETS[name]
    cfg["brightness"]["max_scale"] = preset["max_scale"]
    cfg["brightness"]["min_brightness"] = preset["min_brightness"]
    cfg["filters"]["smoothing_min"] = preset["smoothing_min"]
    cfg["filters"]["smoothing_max"] = preset["smoothing_max"]
    cfg["filters"]["dominant_weight"] = preset["dominant_weight"]
    cfg["audio"]["mode"] = preset["audio"]
    return cfg


class ScenePersistTests(unittest.TestCase):
    def test_detects_each_scene(self):
        for name in SCENE_PRESETS:
            cfg = config_for(name)
            self.assertTrue(scene_matches_config(cfg, name), name)
            self.assertEqual(detect_scene(cfg), name)

    def test_manual_change_is_not_a_scene(self):
        cfg = config_for("Кино")
        cfg["brightness"]["max_scale"] = 0.5
        self.assertFalse(scene_matches_config(cfg, "Кино"))
        self.assertEqual(detect_scene(cfg), "")

    def test_tolerance_for_slider_rounding(self):
        cfg = config_for("Игра")
        cfg["filters"]["smoothing_min"] = 0.2501
        self.assertTrue(scene_matches_config(cfg, "Игра"))


if __name__ == "__main__":
    unittest.main()
