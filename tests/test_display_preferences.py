"""Portable per-system display settings remain independent and durable."""
from dataclasses import FrozenInstanceError, asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from redux.display_preferences import DisplayPreferences, DisplayProfile


class DisplayPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "library"
        self.preferences = DisplayPreferences(self.root)

    def test_default_profiles_are_lazy_frozen_and_system_specific(self):
        for system in ("nes", "snes", "psx", "genesis", "32x"):
            profile = self.preferences.get(system)
            self.assertEqual(profile, DisplayProfile())
            self.assertEqual(profile.scanline_strength, 40)
        for system in ("saturn", "sega_cd", "32x_cd"):
            self.assertEqual(self.preferences.get(system).filter_id, "composite")
        with self.assertRaises(FrozenInstanceError):
            self.preferences.get("nes").zoom = 110
        self.assertFalse(self.root.exists())

    def test_settings_round_trip_and_do_not_leak_to_another_system(self):
        expected = self.preferences.set("nes", filter_id="crt", scanlines=55, shape="4:3", zoom=115,
                                        offset_x=-23, offset_y=47, brightness=130)
        self.assertEqual(DisplayPreferences(self.root).get("nes"), expected)
        self.assertEqual(self.preferences.get("psx"), DisplayProfile())
        self.assertEqual(self.preferences.get("saturn").filter_id, "composite")
        document = json.loads(self.preferences.path.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["systems"]["nes"], asdict(expected))

    def test_two_instances_preserve_other_systems_and_reset_only_requested_system(self):
        self.preferences.set("saturn", filter_id="clean", brightness=110)
        other = DisplayPreferences(self.root)
        other.set("nes", shape="pixel", offset_x=200)
        self.preferences.set("saturn", zoom=105)
        self.assertEqual(other.get("nes").offset_x, 200)
        self.assertEqual(other.get("saturn").brightness, 110)
        self.assertEqual(self.preferences.reset("saturn").filter_id, "composite")
        self.assertEqual(other.get("saturn").filter_id, "composite")
        self.assertEqual(other.get("nes").shape, "pixel")
        self.assertNotIn("saturn", json.loads(self.preferences.path.read_text())["systems"])

    def test_noop_reset_and_empty_update_do_not_create_files(self):
        self.assertEqual(self.preferences.reset("nes"), DisplayProfile())
        self.assertEqual(self.preferences.set("nes"), DisplayProfile())
        self.assertFalse(self.root.exists())

    def test_system_limit_cannot_publish_a_file_that_future_reads_would_reject(self):
        self.preferences.directory.mkdir(parents=True)
        document = {"version": 1, "systems": {f"system{index}": {} for index in range(256)}}
        original = json.dumps(document).encode("utf-8")
        self.preferences.path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "too many"):
            self.preferences.set("extra", filter_id="clean")
        self.assertEqual(self.preferences.path.read_bytes(), original)
        self.assertEqual(self.preferences.get("system0"), DisplayProfile())

    def test_invalid_settings_are_rejected_before_creating_a_directory(self):
        invalid = ({"filter_id": "sharp"}, {"filter_id": "invented"}, {"shape": "16:9"}, {"zoom": 101},
                   {"zoom": 49}, {"zoom": 155}, {"zoom": True}, {"offset_x": -201}, {"offset_y": 201},
                   {"brightness": 24}, {"brightness": 201}, {"brightness": 100.0}, {"scanlines": -1},
                   {"scanlines": 101}, {"scanlines": False}, {"unknown": "field"})
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.preferences.set("nes", **changes)
        for system in (None, 1, "../nes", "NES", "", "a" * 49):
            with self.subTest(system=system), self.assertRaises(ValueError):
                self.preferences.get(system)
        self.assertFalse(self.root.exists())

    def test_malformed_or_future_json_is_not_replaced(self):
        self.preferences.directory.mkdir(parents=True)
        malformed = (b"[]", b"not-json", b'{"version":1,"systems":{},"version":1}',
                     b'{"version":true,"systems":{}}', b'{"version":2,"systems":{}}',
                     b'{"version":1,"systems":{"nes":{"zoom":101}}}',
                     b'{"version":1,"systems":{"nes":{"scanlines":NaN}}}',
                     b'{"version":1,"systems":{"nes":{"unknown":2}}}', b"x" * (128 * 1024 + 1))
        for data in malformed:
            self.preferences.path.write_bytes(data)
            with self.subTest(data=data[:80]):
                with self.assertRaises(ValueError):
                    self.preferences.get("nes")
                with self.assertRaises(ValueError):
                    self.preferences.set("nes", filter_id="clean")
                self.assertEqual(self.preferences.path.read_bytes(), data)

    def test_observed_external_edit_is_preserved_instead_of_overwritten(self):
        self.preferences.set("nes", brightness=105)
        original_read = self.preferences._read_bytes
        calls = 0
        external = {"version": 1, "systems": {"psx": {"brightness": 125}, "nes": {"zoom": 120}}}
        def changed_read():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.preferences.path.write_text(json.dumps(external), encoding="utf-8")
            return original_read()
        with patch.object(self.preferences, "_read_bytes", side_effect=changed_read):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                self.preferences.set("nes", brightness=110)
        self.assertEqual(json.loads(self.preferences.path.read_text()), external)
        self.assertEqual(list(self.preferences.directory.glob("*.tmp")), [])

    def test_disk_failure_keeps_previous_settings_and_cleans_private_staging(self):
        self.preferences.set("nes", brightness=105)
        original = self.preferences.path.read_bytes()
        for operation in ("os.fsync", "os.replace"):
            with self.subTest(operation=operation), patch("redux.display_preferences." + operation,
                                                          side_effect=OSError("Injected disk failure")):
                with self.assertRaises(OSError):
                    self.preferences.set("nes", brightness=110)
            self.assertEqual(self.preferences.path.read_bytes(), original)
            self.assertEqual(list(self.preferences.directory.glob("*.tmp")), [])

    def test_symlink_guard_and_resolved_alias_reject_outside_paths(self):
        actual_resolve = Path.resolve
        outside = Path(self.temporary.name) / "outside"
        def alias(candidate, *args, **kwargs):
            return outside if candidate == self.preferences.directory else actual_resolve(candidate, *args, **kwargs)
        with patch.object(Path, "resolve", alias):
            with self.assertRaisesRegex(ValueError, "outside"):
                self.preferences.set("nes", filter_id="clean")
        actual_symlink = Path.is_symlink
        def symlink(candidate):
            return candidate == self.preferences.path or actual_symlink(candidate)
        with patch.object(Path, "is_symlink", symlink):
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.preferences.get("nes")
        self.assertFalse(self.root.exists())
        self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
