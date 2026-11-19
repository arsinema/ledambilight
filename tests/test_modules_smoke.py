import copy
import io
import json
import os
import tempfile
import unittest

import numpy as np
import requests
from PIL import Image

from ledapp import i18n
from ledapp.audio_engine import AudioCapture, is_available as audio_available
from ledapp.ble_controller import color_command, power_command
from ledapp.color_engine import (
    apply_calibration,
    apply_neighbor_blend,
    compute_dominant_color,
    improve_color,
    mean_color,
    process_pixel_frame,
)
from ledapp.config import DEFAULT_CONFIG, _deep_merge, _sanitize
from ledapp.frame_analysis import FrameAnalyzer
from ledapp.mqtt_bridge import discovery_messages, ha_state_payload
from ledapp.now_playing import _extract_color_from_image_bytes
from ledapp.presets import apply_preset, export_preset, validate_preset
from ledapp.remote import RemoteServer
from ledapp.strip_mapper import StripMapper
from ledapp.support_bundle import redact_config, redact_text
from ledapp.weather import _classify_condition
from ledapp.win_utils import idle_seconds, is_fullscreen_app, is_session_locked


class ColorSmokeTests(unittest.TestCase):
    def test_color_math(self):
        self.assertEqual(improve_color(0, 0, 0), (0, 0, 0))
        self.assertGreater(mean_color(np.array([[[255, 0, 0]]]), linear=True)[0], 200)
        self.assertGreater(compute_dominant_color(np.full((8, 8, 3), (10, 200, 30), np.uint8), 1, 32)[1], 150)
        calib = {"r_gain": 0.5, "g_gain": 1.0, "b_gain": 1.0,
                 "r_offset": 0, "g_offset": 0, "b_offset": 0}
        self.assertEqual(apply_calibration(200, 10, 10, calib)[0], 100)

    def test_pixel_pipeline(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        out = process_pixel_frame([(200, 100, 50)], cfg)
        self.assertEqual(len(out), 1)
        blended = apply_neighbor_blend([(255, 0, 0), (0, 255, 0), (0, 0, 255)], 0.5)
        self.assertEqual(len(blended), 3)
        self.assertNotEqual(blended[1], (0, 255, 0))


class StripMapperSmokeTests(unittest.TestCase):
    def test_roundtrip_and_colors(self):
        mapper = StripMapper(8, 100, 100)
        mapper.led_path = [(10, 10), (90, 10), (90, 90), (10, 90)]
        self.assertTrue(mapper.build_mapping(strip_width=10))
        frame = np.zeros((100, 100, 3), np.uint8)
        frame[:, :] = (10, 200, 30)
        colors = mapper.get_all_leds_colors(frame)
        self.assertEqual(len(colors), 8)
        self.assertTrue(all(c[1] > 100 for c in colors))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mapping.json")
            mapper.save_to_file(path)
            loaded = StripMapper(1, 1, 1)
            self.assertTrue(loaded.load_from_file(path))
            self.assertEqual(loaded.led_count, 8)


class AudioSmokeTests(unittest.TestCase):
    def test_dsp_state_and_settings(self):
        self.assertIsInstance(audio_available(), bool)
        capture = AudioCapture()
        capture.update_settings({
            "sensitivity": 1.0, "bass_gain": 1.0, "mid_gain": 1.0,
            "treble_gain": 1.0, "source": "input", "input_gain": 3.0,
        })
        data = np.zeros((1024, 2), dtype=np.float32)
        data[:, 0] = np.sin(np.linspace(0, 2 * np.pi * 4, 1024))
        for _ in range(5):
            capture._process_chunk(data)
        state = capture.get_state()
        self.assertEqual(len(state.spectrum), 32)
        self.assertGreaterEqual(state.volume, 0.0)
        self.assertFalse(capture.is_running)


class FrameAnalysisSmokeTests(unittest.TestCase):
    def test_analyzer_updates(self):
        analyzer = FrameAnalyzer()
        small = np.zeros((90, 160, 3), np.uint8)
        small[:8, :, :] = 0
        small[:, :8, :] = 0
        small[8:82, 8:152, :] = 90
        for _ in range(4):
            analyzer.update(small)
        rect = analyzer.content_rect(1920, 1080)
        self.assertIsNotNone(rect)
        self.assertGreaterEqual(analyzer.ui_mask_coverage(), 0.0)


class PresetSmokeTests(unittest.TestCase):
    def test_export_validate_apply(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["brightness"]["max_scale"] = 0.77
        data = export_preset(cfg, name="test")
        self.assertEqual(validate_preset(data)["format"], data["format"])
        new = apply_preset(cfg, data)
        self.assertAlmostEqual(new["brightness"]["max_scale"], 0.77)


class BleProtocolSmokeTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(color_command(300, -1, 128)[4], 255)
        self.assertEqual(color_command(0, 0, 0)[5], 0)
        self.assertEqual(power_command(True)[3], 0x04)
        self.assertEqual(power_command(False)[3], 0x00)


class MqttPayloadSmokeTests(unittest.TestCase):
    def test_discovery_and_state(self):
        messages = discovery_messages("led/test", "homeassistant")
        self.assertEqual(len(messages), 1)
        self.assertIn("homeassistant/light/", next(iter(messages)))
        state = ha_state_payload({"capturing": True, "brightness": 70, "scene": "Игра", "connected": True})
        self.assertEqual(state["state"], "ON")
        self.assertEqual(state["brightness"], 70)


class MiscSmokeTests(unittest.TestCase):
    def test_weather_classification(self):
        self.assertEqual(_classify_condition("heavy rain"), "heavy_rain")
        self.assertEqual(_classify_condition("light snow"), "snow")
        self.assertEqual(_classify_condition("clear sky"), "sunny")

    def test_now_playing_image_color(self):
        img = Image.new("RGB", (8, 8), (30, 100, 200))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        color = _extract_color_from_image_bytes(buf.getvalue())
        self.assertIsNotNone(color)
        self.assertGreater(color[2], 150)

    def test_i18n(self):
        old = i18n.current_language()
        try:
            i18n.set_language("en")
            self.assertEqual(i18n.tr("Комната"), "Room")
        finally:
            i18n.set_language(old)

    def test_support_redaction(self):
        text = "IP 192.168.1.10 MAC BE:68:2C:01:46:3B"
        redacted = redact_text(text)
        self.assertNotIn("192.168.1.10", redacted)
        self.assertNotIn("BE:68:2C:01:46:3B", redacted)
        cfg = redact_config({"wled_host": "192.168.1.10", "wled_led_count": 90})
        self.assertNotIn("192.168.1.10", json.dumps(cfg))

    def test_config_deep_merge_sanitize(self):
        base = copy.deepcopy(DEFAULT_CONFIG)
        merged = _deep_merge(base, {"audio": {"source": "input"}, "wled_led_count": 5000})
        sanitized = _sanitize(merged)
        self.assertEqual(sanitized["audio"]["source"], "input")
        self.assertLessEqual(sanitized["wled_led_count"], 1000)

    def test_win_utils_calls(self):
        self.assertIsInstance(idle_seconds(), int)
        self.assertGreaterEqual(idle_seconds(), 0)
        self.assertIsInstance(is_session_locked(), bool)
        self.assertIsInstance(is_fullscreen_app(), bool)


class RemoteSmokeTests(unittest.TestCase):
    def test_server_pages_and_actions(self):
        actions = []
        server = RemoteServer(
            submit=lambda fn: None,
            state_snapshot=lambda: {"capturing": True, "brightness": 50},
            handle_action=lambda path, payload: actions.append(path) or True,
            port=0,
        )
        self.assertTrue(server.start())
        base = f"http://127.0.0.1:{server.port}"
        try:
            self.assertEqual(requests.get(base + "/", timeout=5).status_code, 200)
            self.assertEqual(requests.get(base + "/api/state", timeout=5).json()["brightness"], 50)
            self.assertEqual(requests.post(base + "/api/brightness", json={"delta": 5}, timeout=5).status_code, 200)
            self.assertIn("/api/brightness", actions)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
