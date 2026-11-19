import unittest

import numpy as np

from ledapp.adaptive_color import AdaptiveSampler


def frame_with(patch, size=64, top=10, left=10):
    frame = np.zeros((size, size, 3), np.uint8)
    frame[top:top + patch.shape[0], left:left + patch.shape[1]] = patch
    return frame


class AdaptiveColorTests(unittest.TestCase):
    def setUp(self):
        self.boxes = [(10, 21, 10, 21)]
        self.sampler = AdaptiveSampler(self.boxes, 64, 64)

    def test_solid_color_is_exact(self):
        frame = frame_with(np.full((11, 11, 3), (40, 180, 120), np.uint8))
        out = self.sampler.sample(frame)
        self.assertTrue(np.allclose(out[0], (40, 180, 120), atol=1.0))

    def test_hard_edge_does_not_turn_purple(self):
        patch = np.zeros((11, 11, 3), np.uint8)
        patch[:, :5] = (255, 0, 0)
        patch[:, 5:] = (0, 0, 255)
        out = self.sampler.sample(frame_with(patch))[0]
        r, g, b = (float(v) for v in out)
        # either red or blue, not a red+blue mix
        self.assertTrue(max(r, b) > 200)
        self.assertLess(min(r, b), 60)

    def test_tiny_bright_object_survives(self):
        patch = np.zeros((11, 11, 3), np.uint8)
        patch[5:7, 5:7] = (255, 180, 30)
        out = self.sampler.sample(frame_with(patch))[0]
        self.assertGreater(float(out.max()), 200)
        self.assertGreater(float(out[0]), 180)

    def test_clip_excludes_outside_pixels(self):
        frame = frame_with(np.full((11, 11, 3), (40, 180, 120), np.uint8))
        out = self.sampler.sample(frame, clip_rect=(30, 64, 0, 64))
        self.assertTrue(np.allclose(out[0], (0, 0, 0), atol=1.0))
        out = self.sampler.sample(frame, clip_rect=(0, 64, 0, 64))
        self.assertTrue(np.allclose(out[0], (40, 180, 120), atol=1.0))


if __name__ == "__main__":
    unittest.main()
