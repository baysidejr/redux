"""One predictable BIOS folder with explicit custom selection taking priority."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from redux.firmware import firmware_directory


class FirmwareDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"

    def test_default_is_project_bios_without_creating_any_folder(self):
        for preferences in (None, {}, {"firmware_dir": None}, {"firmware_dir": ""}, {"firmware_dir": "  "}):
            with self.subTest(preferences=preferences):
                self.assertEqual(firmware_directory(preferences, self.project), self.project / "bios")
        self.assertFalse(self.project.exists())

    def test_explicit_absolute_custom_folder_wins_even_when_unavailable(self):
        custom = self.base / "chosen-firmware"
        preferences = {"firmware_dir": str(custom)}
        self.assertEqual(firmware_directory(preferences, self.project), custom)
        self.assertEqual(preferences, {"firmware_dir": str(custom)})
        self.assertFalse(custom.exists())

    def test_relative_custom_path_is_portable_and_independent_of_working_directory(self):
        preferences = {"firmware_dir": "firmware/../my-bios"}
        with patch("os.getcwd", return_value=str(self.base / "unrelated-working-directory")):
            selected = firmware_directory(preferences, self.project)
        self.assertEqual(selected, self.project / "my-bios")
        self.assertEqual(firmware_directory({"firmware_dir": "../shared-bios"}, self.project),
                         self.base / "shared-bios")

    def test_explicit_pathlike_selection_is_supported(self):
        self.assertEqual(firmware_directory({"firmware_dir": self.base / "custom"}, self.project),
                         self.base / "custom")

    def test_home_expansion_happens_only_for_explicit_home_path(self):
        home = self.base / "explicit-home"
        with patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}):
            self.assertEqual(firmware_directory({"firmware_dir": "~/my-bios"}, self.project), home / "my-bios")
            self.assertEqual(firmware_directory({}, self.project), self.project / "bios")

    def test_resolution_does_not_search_or_inspect_the_filesystem(self):
        with patch.object(Path, "mkdir", side_effect=AssertionError("No creation")), \
             patch.object(Path, "iterdir", side_effect=AssertionError("No scan")), \
             patch.object(Path, "exists", side_effect=AssertionError("No inspection")), \
             patch.object(Path, "resolve", side_effect=AssertionError("No symlink traversal")), \
             patch.object(Path, "open", side_effect=AssertionError("No file access")):
            self.assertEqual(firmware_directory({}, self.project), self.project / "bios")
            self.assertEqual(firmware_directory({"firmware_dir": "selected"}, self.project),
                             self.project / "selected")

    def test_invalid_settings_are_reported_instead_of_becoming_an_unintended_path(self):
        for configured in (True, 12, [], b"binary-path"):
            with self.subTest(configured=configured), self.assertRaises(TypeError):
                firmware_directory({"firmware_dir": configured}, self.project)
        with self.assertRaises(TypeError):
            firmware_directory([], self.project)


if __name__ == "__main__":
    unittest.main()
