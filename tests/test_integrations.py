import io
import os
import tempfile
import unittest

from PIL import Image

from ledapp import secrets
from ledapp.spotify_client import make_pkce, palette_from_image
from ledapp.telegram_bot import TelegramBot
from ledapp.telegram_desktop import parse_unread
from ledapp.windows_notifications import HAS_WINSDK, WindowsNotificationMonitor


class SecretTests(unittest.TestCase):
    def test_roundtrip(self):
        if not secrets.is_supported():
            self.skipTest("DPAPI недоступен")
        old = os.environ.get("APPDATA")
        with tempfile.TemporaryDirectory() as folder:
            os.environ["APPDATA"] = folder
            try:
                secrets.set_secret("test", "super-secret")
                self.assertEqual(secrets.get_secret("test"), "super-secret")
                self.assertTrue(secrets.has_secret("test"))
                secrets.delete_secret("test")
                self.assertEqual(secrets.get_secret("test"), "")
            finally:
                if old is None:
                    os.environ.pop("APPDATA", None)
                else:
                    os.environ["APPDATA"] = old


class SpotifyPureTests(unittest.TestCase):
    def test_pkce(self):
        verifier, challenge = make_pkce()
        self.assertGreater(len(verifier), 40)
        self.assertEqual(len(challenge), 43)
        self.assertNotEqual(verifier, challenge)

    def test_palette(self):
        image = Image.new("RGB", (64, 64), (20, 120, 220))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        colors = palette_from_image(buf.getvalue())
        self.assertTrue(colors)
        self.assertEqual(len(colors[0]), 3)

    def test_empty_palette(self):
        self.assertEqual(palette_from_image(b""), [])


class TelegramTests(unittest.TestCase):
    def test_send_without_chat(self):
        bot = TelegramBot()
        self.assertFalse(bot.send("hello"))
        bot.chat_id = "123"
        self.assertTrue(bot.send("hello"))
        self.assertFalse(bot.running)


class WindowsNotificationTests(unittest.TestCase):
    def test_availability_is_boolean(self):
        self.assertIsInstance(HAS_WINSDK, bool)
        monitor = WindowsNotificationMonitor()
        if not HAS_WINSDK:
            self.assertFalse(monitor.start())
        self.assertFalse(monitor.running)

    def test_telegram_title_parser(self):
        self.assertEqual(parse_unread("Telegram (3)"), 3)
        self.assertEqual(parse_unread("Telegram (12) — chats"), 12)
        self.assertEqual(parse_unread("Telegram"), 0)
        self.assertEqual(parse_unread("5 unread"), 5)


if __name__ == "__main__":
    unittest.main()
