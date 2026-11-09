import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ledapp import capture

class FakeCamera:
    def __init__(self): self.started = None
    def start(self, target_fps=30, video_mode=False): self.started = (target_fps, video_mode)
    def stop(self): pass

class MonitorTests(unittest.TestCase):
    def test_dxgi_uses_monitor_index(self):
        calls = {}
        def fake_create(**kwargs):
            calls.update(kwargs)
            return FakeCamera()
        with mock.patch.object(capture.dxcam, "create", side_effect=fake_create):
            cap = capture.ScreenCapture(target_fps=10, backend="dxgi", region=(10, 20, 300, 200), monitor_index=2)
            cap._start_dxgi()
        self.assertEqual(calls["output_idx"], 2)
        self.assertEqual(calls["region"], (10, 20, 310, 220))
        self.assertEqual(cap.monitor_index, 2)

    def test_wgc_not_used_for_secondary(self):
        cap = capture.ScreenCapture(monitor_index=1)
        self.assertFalse(cap._start_wgc())

if __name__ == "__main__":
    unittest.main()
