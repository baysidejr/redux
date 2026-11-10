"""Bounded browser model fixtures; no drive or native picker interaction."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux import file_browser as browser


class ScandirFixture:
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *_):
        return False


def fake_entry(name, *, directory=False, attributes=0, error=None):
    def value(**kwargs):
        if error:
            raise error
        return SimpleNamespace(st_mode=(stat.S_IFDIR if directory else stat.S_IFREG),
                               st_file_attributes=attributes, st_size=123)
    return SimpleNamespace(name=name, stat=value)


class FileBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_default_media_listing_is_folders_first_filtered_sized_and_immutable(self):
        for name in ("zeta", "Alpha", ".internal"):
            (self.root / name).mkdir()
        for name in ("z.ISO", "A.cue", "readme.txt", ".hidden.iso"):
            (self.root / name).write_bytes(b"12345")
        listing = browser.list_directory(self.root)
        self.assertIsNone(listing.error)
        self.assertEqual([entry.label for entry in listing.entries], ["Alpha", "zeta", "A.cue", "z.ISO"])
        self.assertEqual([entry.is_dir for entry in listing.entries], [True, True, False, False])
        self.assertEqual(listing.entries[-1].size, 5)
        self.assertEqual(listing.entries[0].name, "Alpha")
        self.assertEqual(listing.parent, self.root.parent)
        self.assertFalse(listing.truncated)
        with self.assertRaises(FrozenInstanceError):
            listing.entries[0].label = "changed"
        with self.assertRaises(FrozenInstanceError):
            listing.error = "changed"

    def test_folder_only_and_custom_extensions_have_no_recursive_discovery(self):
        folder = self.root / "Games"
        folder.mkdir()
        (folder / "nested.iso").write_bytes(b"nested")
        for name in ("game.iso", "game.nes", "cover.PNG"):
            (self.root / name).write_bytes(b"file")
        self.assertEqual([item.name for item in browser.list_directory(self.root, folders_only=True).entries], ["Games"])
        self.assertEqual([item.name for item in browser.list_directory(self.root, extensions=set()).entries], ["Games"])
        listing = browser.list_directory(self.root, extensions={"*.png", "ISO"})
        self.assertEqual([item.name for item in listing.entries], ["Games", "cover.PNG", "game.iso"])

    def test_default_media_hides_plain_markdown_and_preserves_binary_megadrive_files(self):
        (self.root / "README.md").write_text("# Project documentation\nThis is not a game.\n", encoding="utf-8")
        (self.root / "Game.md").write_bytes(b"\0\xff\x80SEGA MEGA DRIVE" + bytes(4096))
        listing = browser.list_directory(self.root)
        self.assertEqual([entry.name for entry in listing.entries], ["Game.md"])
        explicit = browser.list_directory(self.root, extensions={".md"})
        self.assertEqual([entry.name for entry in explicit.entries], ["Game.md", "README.md"])

    def test_removed_permission_and_not_directory_errors_are_displayable(self):
        missing = browser.list_directory(self.root / "removed")
        self.assertIsNotNone(missing.error)
        self.assertEqual(missing.entries, ())
        file = self.root / "file.iso"
        file.write_bytes(b"file")
        self.assertIsNotNone(browser.list_directory(file).error)
        with patch.object(browser.os, "scandir", side_effect=PermissionError("Access denied")):
            denied = browser.list_directory(self.root)
        self.assertIn("Access denied", denied.error)
        self.assertEqual(denied.parent, self.root.parent)

    def test_internal_reparse_hidden_and_inaccessible_children_are_omitted(self):
        entries = [fake_entry("normal", directory=True), fake_entry("game.iso"),
                   fake_entry("junction", directory=True, attributes=browser._REPARSE),
                   fake_entry("hidden", directory=True, attributes=browser._HIDDEN),
                   fake_entry("system.iso", attributes=browser._SYSTEM),
                   fake_entry("gone.iso", error=FileNotFoundError()), fake_entry(".private", directory=True)]
        with patch.object(browser.os, "scandir", return_value=ScandirFixture(entries)):
            listing = browser.list_directory(self.root)
        self.assertEqual([item.name for item in listing.entries], ["normal", "game.iso"])

    def test_explicit_reparse_root_is_not_followed(self):
        info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=browser._REPARSE)
        with patch.object(Path, "lstat", return_value=info), \
             patch.object(browser.os, "scandir", side_effect=AssertionError("Do not traverse")):
            listing = browser.list_directory(self.root)
        self.assertIn("link or junction", listing.error)

    def test_large_directory_has_a_bounded_scan_and_truncation_flag(self):
        checked = []
        def entries():
            for number in range(100):
                item = fake_entry(f"game-{number}.iso")
                original = item.stat
                def record(original=original, number=number, **kwargs):
                    checked.append(number)
                    return original(**kwargs)
                item.stat = record
                yield item
        with patch.object(browser, "MAX_DIRECTORY_ENTRIES", 7), \
             patch.object(browser.os, "scandir", return_value=ScandirFixture(entries())):
            listing = browser.list_directory(self.root)
        self.assertTrue(listing.truncated)
        self.assertEqual(len(listing.entries), 7)
        self.assertEqual(checked, list(range(7)))

    def test_locations_are_explicit_deduplicated_and_do_not_scan_drive_contents(self):
        home = self.root / "home"
        (home / "Downloads").mkdir(parents=True)
        (home / "Documents").mkdir()
        app = self.root / "app"
        library = self.root / "library"
        with patch.object(Path, "home", return_value=home), \
             patch.object(browser, "_logical_drives", return_value=(self.root, library)), \
             patch.object(browser.os, "scandir", side_effect=AssertionError("No root scan")):
            entries = browser.list_locations(library, app)
        self.assertEqual([item.label for item in entries],
                         ["Application", "Game library", "Downloads", "Documents", "Home",
                          f"{self.root.drive} Drive" if self.root.drive else "File system"])
        self.assertEqual(len({entry.path for entry in entries}), len(entries))
        self.assertTrue(all(entry.is_directory for entry in entries))
        self.assertEqual(entries[-1].kind, "drive")

    def test_windows_drive_mask_keeps_all_assigned_drive_letters(self):
        mask = (1 << 2) | (1 << 4) | (1 << 6) | (1 << 25)
        drives = browser._drives_from_mask(mask)
        self.assertEqual([str(path) for path in drives], ["C:\\", "E:\\", "G:\\", "Z:\\"])
        self.assertEqual(browser._drives_from_mask(0), ())

    def test_drive_enumeration_failure_does_not_remove_explicit_locations(self):
        with patch.object(Path, "home", side_effect=RuntimeError("No home")), \
             patch.object(browser, "_logical_drives", side_effect=OSError("Drive query failed")):
            entries = browser.list_locations(self.root / "library", self.root / "app")
        self.assertEqual([item.label for item in entries], ["Application", "Game library"])


if __name__ == "__main__":
    unittest.main()
