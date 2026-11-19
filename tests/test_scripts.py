import os
import sys
import tempfile
import unittest

import build_exe
import build_installer


class BuildScriptTests(unittest.TestCase):
    def test_version_quad(self):
        self.assertEqual(build_exe._version_quad("1.9.0"), (1, 9, 0, 0))
        self.assertEqual(build_exe._version_quad("1.9.0-beta2"), (1, 9, 0, 2))

    def test_version_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "version.txt")
            build_exe.write_version_file(path)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("1, 9, 0, 0", text)
            self.assertIn("LED Ambilight", text)

    def test_help_does_not_launch_build(self):
        old = sys.argv[:]
        try:
            sys.argv = ["build_exe.py", "--help"]
            self.assertEqual(build_exe.main(), 0)
        finally:
            sys.argv = old

    def test_installer_parser_exists(self):
        self.assertTrue(callable(build_installer.main))


if __name__ == "__main__":
    unittest.main()
