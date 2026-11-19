import unittest

import numpy as np

from ledapp.audio_engine import AudioCapture, CHUNK_SIZE


class AudioInputTests(unittest.TestCase):
    def test_input_source_and_gain_are_loaded(self):
        capture = AudioCapture()
        capture.update_settings({
            "source": "input",
            "device_name": "Line In",
            "input_gain": 4.0,
            "sensitivity": 1.5,
        })
        self.assertEqual(capture.source, "input")
        self.assertEqual(capture.device_name, "Line In")
        self.assertAlmostEqual(capture.input_gain, 4.0)
        self.assertAlmostEqual(capture.sensitivity, 1.5)

    def test_input_gain_raises_level(self):
        quiet = (np.sin(np.arange(CHUNK_SIZE) * 0.1) * 0.01).astype(np.float32)
        low = AudioCapture()
        low.update_settings({"source": "input", "input_gain": 1.0, "sensitivity": 1.0})
        high = AudioCapture()
        high.update_settings({"source": "input", "input_gain": 4.0, "sensitivity": 1.0})
        low._process_chunk(quiet)
        high._process_chunk(quiet)
        self.assertGreater(high.get_state().volume, low.get_state().volume)


if __name__ == "__main__":
    unittest.main()
