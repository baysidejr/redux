"""Wrapper durability/timing tests with a fake host, never a real core DLL."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux.embedded import EmbeddedGame


class HostFixture:
    def __init__(self):
        self.bytes = b"wrapper-test-state"
        self.load_calls = []
        self.pause_calls = self.resume_calls = self.stop_calls = 0
        self.fail_save = False
        self.fail_pause = self.fail_resume = False

    def start(self):
        return self

    def pause(self):
        if self.fail_pause:
            raise RuntimeError("pause failed")
        self.pause_calls += 1

    def resume(self):
        if self.fail_resume:
            raise RuntimeError("resume failed")
        self.resume_calls += 1

    def stop(self):
        self.stop_calls += 1

    def save_state(self, path):
        Path(path).write_bytes(self.bytes)
        if self.fail_save:
            raise RuntimeError("serialization failed")
        return path

    def load_state(self, path):
        self.load_calls.append((Path(path), Path(path).read_bytes()))
        return path

    def save_screenshot(self, path):
        Path(path).write_bytes(b"screenshot fixture")
        return path


class EmbeddedGameTests(unittest.TestCase):
    def test_new_state_thumbnail_is_the_explicit_hashed_capture_for_that_slot(self):
        import hashlib
        from PIL import Image
        from redux.state_presentation import cards
        def screenshot(path):
            Image.new("RGB",(32,24),(20,60,90)).save(path,format="PNG")
            return path
        self.host.save_screenshot=screenshot
        self.embedded.save(2)
        item=json.loads(self.manifest(2).read_text())
        path=self.embedded.states/item["thumbnail"]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),item["thumbnail_sha256"])
        card=cards(self.embedded)[2]
        self.assertEqual(card.thumbnail_path,path)
        self.assertTrue(card.compatible)
        self.assertEqual(list(self.embedded.states.glob(".thumb-*")),[])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.core = self.root / "test-core.dll"
        self.core.write_bytes(b"fake DLL data, never executed")
        self.media = self.root / "original.nes"
        self.media.write_bytes(b"original media bytes")
        self.library = SimpleNamespace(root=self.root / "library", path_for=lambda _: self.media)
        self.game = {"id": 1, "bundle_hash": "a" * 64}
        self.host = HostFixture()
        self.clock = [0.0]
        self.patcher = patch("redux.libretro_host.EmbeddedSession", return_value=self.host)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.embedded = EmbeddedGame(self.library, self.game, self.core, _clock=lambda: self.clock[0])

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self, slot=0):
        return self.embedded.states / f"slot-{slot}.json"

    def test_core_hash_file_handle_is_closed_and_input_mapping_copied(self):
        handles = []
        original_open = Path.open
        def tracked(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path == self.core:
                handles.append(stream)
            return stream
        with patch.object(Path, "open", tracked):
            game = EmbeddedGame(self.library, self.game, self.core)
        self.assertTrue(handles)
        self.assertTrue(all(handle.closed for handle in handles))
        self.game["bundle_hash"] = "b" * 64
        self.assertEqual(game.game["bundle_hash"], "a" * 64)

    def test_slot_round_trip_uses_verified_temporary_snapshot(self):
        saved = self.embedded.save(4)
        self.assertTrue(saved.exists)
        self.assertEqual(self.embedded.load(4), saved)
        path, loaded = self.host.load_calls[0]
        self.assertNotEqual(path, saved.path)
        self.assertEqual(loaded, self.host.bytes)
        self.assertFalse(path.exists())
        self.assertEqual([slot.slot for slot in self.embedded.slots() if slot.exists], [4])
        self.assertEqual(self.media.read_bytes(), b"original media bytes")

    def test_wrong_game_core_slot_and_manifest_tampering_are_rejected(self):
        self.embedded.save()
        original = self.manifest().read_bytes()
        for field, value in (("game", "b" * 64), ("core", "c" * 64), ("slot", 1), ("slot", False), ("schema", 2), ("schema", True),
                             ("sha256", "../../outside"), ("size", True), ("size", 999999999999),
                             ("saved_at", "2026-01-01")):
            item = json.loads(original)
            item[field] = value
            self.manifest().write_text(json.dumps(item))
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                self.embedded.load()
        self.assertEqual(self.host.load_calls, [])
        self.manifest().write_bytes(b'{"game":1,"game":2}')
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            self.embedded.load()
        self.manifest().write_bytes(b"x" * 8193)
        with self.assertRaisesRegex(RuntimeError, "limit"):
            self.embedded.load()

    def test_state_payload_tamper_is_rejected_before_host_call(self):
        saved = self.embedded.save()
        saved.path.write_bytes(b"X" * saved.size)
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            self.embedded.load()
        self.assertEqual(self.host.load_calls, [])
        self.assertEqual(list(self.embedded.states.glob(".load-*")), [])

    def test_failed_host_load_closes_and_removes_verified_snapshot(self):
        saved = self.embedded.save()
        with patch.object(self.host, "load_state", side_effect=RuntimeError("native state rejected")):
            with self.assertRaisesRegex(RuntimeError, "native state rejected"):
                self.embedded.load()
        self.assertEqual(list(self.embedded.states.glob(".load-*")), [])
        self.assertTrue(saved.path.is_file())
        self.assertEqual(self.embedded.slot(0), saved)

    def test_serialization_failure_preserves_existing_manifest(self):
        saved = self.embedded.save()
        before = self.manifest().read_bytes()
        self.host.bytes = b"new state which fails"
        self.host.fail_save = True
        with self.assertRaisesRegex(RuntimeError, "serialization"):
            self.embedded.save()
        self.assertEqual(self.manifest().read_bytes(), before)
        self.assertEqual(saved.path.read_bytes(), b"wrapper-test-state")
        self.assertEqual(list(self.embedded.states.glob("*.tmp")), [])

    def test_manifest_publish_failure_preserves_previous_slot(self):
        saved = self.embedded.save()
        before = self.manifest().read_bytes()
        self.host.bytes = b"a newly serialized state"
        original_replace = Path.replace
        def fail_manifest(path, target):
            if path.name.endswith(".json.tmp"):
                raise OSError("disk full during publication")
            return original_replace(path, target)
        with patch.object(Path, "replace", fail_manifest), self.assertRaisesRegex(OSError, "disk full"):
            self.embedded.save()
        self.assertEqual(self.manifest().read_bytes(), before)
        self.assertTrue(saved.path.is_file())
        self.assertEqual(self.embedded.load(), saved)
        self.assertEqual(list(self.embedded.states.glob("*.tmp")), [])

    def test_new_core_build_can_replace_incompatible_slot(self):
        self.embedded.save()
        self.core.write_bytes(b"a changed core build")
        replacement = EmbeddedGame(self.library, self.game, self.core)
        with self.assertRaisesRegex(RuntimeError, "another game or core"):
            replacement.load()
        self.host.bytes = b"new core state"
        self.assertTrue(replacement.save().exists)
        self.assertEqual(replacement.load().path.read_bytes(), b"new core state")

    def test_paused_time_and_repeated_pause_are_excluded_even_at_clock_zero(self):
        self.embedded.start()
        self.embedded.pause()
        self.clock[0] = 10
        self.embedded.pause()
        self.assertEqual(self.embedded.play_seconds, 0)
        self.embedded.resume()
        self.embedded.resume()
        self.clock[0] = 15
        self.assertEqual(self.embedded.play_seconds, 5)
        self.embedded.pause()
        self.clock[0] = 40
        self.embedded.stop()
        self.embedded.stop()
        self.clock[0] = 100
        self.assertEqual(self.embedded.play_seconds, 5)
        self.assertEqual((self.host.pause_calls, self.host.resume_calls, self.host.stop_calls), (2, 1, 1))
        with self.assertRaises(RuntimeError):
            self.embedded.start()

    def test_failed_pause_resume_do_not_change_timing(self):
        self.embedded.start()
        self.clock[0] = 5
        self.host.fail_pause = True
        with self.assertRaises(RuntimeError):
            self.embedded.pause()
        self.clock[0] = 10
        self.assertEqual(self.embedded.play_seconds, 10)
        self.host.fail_pause = False
        self.embedded.pause()
        self.host.fail_resume = True
        self.clock[0] = 20
        with self.assertRaises(RuntimeError):
            self.embedded.resume()
        self.assertEqual(self.embedded.play_seconds, 10)

    def test_bounds_and_capture_destination(self):
        for number in (-1, 10, True, "0", 2.5):
            with self.assertRaises(ValueError):
                self.embedded.save(number)
        for game_id in (0, -1, True, "../media"):
            with self.assertRaises(ValueError):
                EmbeddedGame(self.library, {**self.game, "id": game_id}, self.core)
        captured = self.embedded.capture()
        self.assertTrue(captured.is_relative_to(self.embedded.screenshots))


if __name__ == "__main__":
    unittest.main()
