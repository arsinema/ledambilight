import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from ledapp.room_view import RoomView

_APP = QApplication.instance() or QApplication([])


class RoomViewTests(unittest.TestCase):
    def test_renders_with_pixels(self):
        view = RoomView()
        view.resize(640, 480)
        pixels = [(255, 0, 0), (0, 255, 0), (0, 0, 255)] * 10
        view.set_leds(pixels, offset=2, total=32)
        image = view.grab()
        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 0)

    def test_perimeter_count(self):
        points = RoomView._perimeter(1.0, 0.5, 40)
        self.assertEqual(len(points), 40)
        self.assertTrue(all(abs(x) <= 0.5 + 1e-9 for x, _ in points))
        self.assertTrue(all(abs(y) <= 0.25 + 1e-9 for _, y in points))


if __name__ == "__main__":
    unittest.main()
