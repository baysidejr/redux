"""Per-game choices preserve other data and survive interrupted publication."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from redux.game_preferences import GamePreferences


class GamePreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "library"
        self.preferences = GamePreferences(self.root, 1, "a" * 64)

    def test_absent_read_is_lazy_and_rating_round_trips_and_clears(self):
        self.assertIsNone(self.preferences.rating())
        self.assertFalse(self.root.exists())
        for value in (1, 5, None):
            self.preferences.set_rating(value)
            self.assertEqual(GamePreferences(self.root, 1, "a" * 64).rating(), value)
        self.assertEqual(json.loads(self.preferences.path.read_text())["rating"], None)

    def test_rating_changes_preserve_unrelated_nested_and_unicode_fields(self):
        self.preferences.directory.mkdir(parents=True)
        document = {"layout": {"zoom": 1.25, "tags": ["お気に入り", "owned"]}, "favorite": True, "rating": 2}
        self.preferences.path.write_text(json.dumps(document), encoding="utf-8")
        self.preferences.set_rating(4)
        actual = json.loads(self.preferences.path.read_text(encoding="utf-8"))
        self.assertEqual(actual, dict(document, rating=4))

    def test_bundle_hash_namespace_prevents_reused_game_id_rating_leak(self):
        self.preferences.set_rating(5)
        other = GamePreferences(self.root, 1, "b" * 64)
        self.assertIsNone(other.rating())
        other.set_rating(2)
        self.assertEqual(self.preferences.rating(), 5)
        self.assertEqual(other.rating(), 2)

    def test_bad_ratings_and_identity_are_rejected_before_writing(self):
        for value in (True, False, 0, 6, -1, 1.0, "3", []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.preferences.set_rating(value)
        self.assertFalse(self.root.exists())
        for game_id in (True, 0, -1, "1"):
            with self.subTest(game_id=game_id), self.assertRaises(ValueError):
                GamePreferences(self.root, game_id, "a" * 64)
        for bundle_hash in ("A" * 64, "a" * 63, "../outside", 1, None):
            with self.subTest(bundle_hash=bundle_hash), self.assertRaises(ValueError):
                GamePreferences(self.root, 1, bundle_hash)

    def test_invalid_existing_json_is_not_destroyed_by_an_update(self):
        self.preferences.directory.mkdir(parents=True)
        for data in (b"[]", b"not json", b'{"x":1,"x":2}', b'{"x":NaN}', b"x" * 65537, b'{"rating":true}'):
            self.preferences.path.write_bytes(data)
            with self.subTest(data=data[:30]), self.assertRaises(ValueError):
                self.preferences.rating()
            if data != b'{"rating":true}':
                with self.assertRaises(ValueError):self.preferences.set_rating(3)
                self.assertEqual(self.preferences.path.read_bytes(), data)

    def test_failed_replace_or_fsync_preserves_original_and_removes_staging_file(self):
        self.preferences.set_rating(2)
        original = self.preferences.path.read_bytes()
        for operation in ("os.replace", "os.fsync"):
            with self.subTest(operation=operation), patch("redux.game_preferences." + operation, side_effect=OSError("injected disk failure")):
                with self.assertRaises(OSError):self.preferences.set_rating(5)
            self.assertEqual(self.preferences.path.read_bytes(), original)
            self.assertEqual(list(self.preferences.directory.glob("*.tmp")), [])

    def test_concurrent_file_change_is_not_overwritten(self):
        self.preferences.set_rating(2)
        original_read = self.preferences._read_bytes
        reads = 0
        def changed_read():
            nonlocal reads
            reads += 1
            if reads == 2:self.preferences.path.write_text('{"rating":4,"external":"new"}')
            return original_read()
        with patch.object(self.preferences, "_read_bytes", side_effect=changed_read):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                self.preferences.set_rating(5)
        self.assertEqual(json.loads(self.preferences.path.read_text()), {"rating":4,"external":"new"})

    def test_unexpected_resolved_alias_outside_library_is_rejected_without_writes(self):
        outside = Path(self.temporary.name) / "other-storage"
        real_resolve = Path.resolve
        def aliased(path, *args, **kwargs):
            return outside if path == self.preferences.directory else real_resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", aliased):
            with self.assertRaisesRegex(ValueError, "outside"):
                self.preferences.set_rating(4)
        self.assertFalse(outside.exists())
        self.assertFalse(self.root.exists())

    def test_output_file_or_parent_symlinks_are_rejected(self):
        self.preferences.directory.mkdir(parents=True)
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text('{"rating":1}')
        try:
            self.preferences.path.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        with self.assertRaises(ValueError):self.preferences.rating()
        with self.assertRaises(ValueError):self.preferences.set_rating(3)
        self.assertEqual(outside.read_text(), '{"rating":1}')
        self.preferences.path.unlink()
        self.preferences.directory.rmdir()
        self.preferences.directory.symlink_to(outside.parent, target_is_directory=True)
        with self.assertRaises(ValueError):self.preferences.set_rating(3)


if __name__ == "__main__":unittest.main()
