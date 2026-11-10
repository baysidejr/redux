"""Transient playback tests use injected identifiers/captures, never a drive."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux import disc_buffer as buffer
from redux.disc_identity import DiscIdentity
from redux.raw_acquisition import RawCaptureCancelled, RawCaptureResult


def identity(**changes):
    value = DiscIdentity("G:", "World Series Baseball 2", "saturn", "MK-81113",
        "Saturn CD", "header", (), {}, "a" * 64)
    return replace(value, **changes)


def capture(drive, target, *, cancelled=None, progress=None):
    """Tiny BIN/CUE fixture carrying the real raw-capture result contract."""
    target = Path(target)
    target.mkdir()
    data = bytes(2352 * 2)
    digest = hashlib.sha256(data).hexdigest()
    cue = b'FILE "disc.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
    (target / "disc.bin").write_bytes(data)
    (target / "disc.cue").write_bytes(cue)
    (target / "manifest.json").write_text(json.dumps({"format": "redux-cd-main-channel", "version": 1,
        "sha256": digest, "cue_sha256": hashlib.sha256(cue).hexdigest(),
        "bytes_written": len(data), "verified_output": True, "verified_source_passes": 2}), encoding="utf-8")
    if progress:
        for index, phase in enumerate(("write", "verify_output", "verify_source"), 1):
            progress(index * len(data), 3 * len(data), phase)
    return RawCaptureResult(target, target / "disc.cue", target / "disc.bin", target / "manifest.json",
        len(data), digest, digest, ())


class DiscBufferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cache = self.root / "playback-cache"
        self.disc = identity()
        self.identifier = patch.object(buffer, "identify_drive", return_value=self.disc).start()
        self.resolver = patch.object(buffer, "mounted_image_source", return_value=None).start()
        self.copier = patch.object(buffer, "capture_raw_cd", side_effect=capture).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.temporary.cleanup)

    def prepare(self, **kwargs):
        return buffer.buffer_disc("g", self.cache, **kwargs)

    def test_physical_disc_lease_is_verified_temporary_disk_and_cleanup_is_explicit(self):
        progress = []
        lease = self.prepare(expected_fingerprint=self.disc.fingerprint,
                             progress=lambda *value: progress.append(value))
        self.assertTrue(lease.temporary)
        self.assertEqual(lease.storage, "temporary_disk")
        self.assertEqual(lease.path, lease.entry_path)
        self.assertEqual(lease.path.name, "disc.cue")
        self.assertEqual(lease.identity.game_id, "MK-81113")
        self.assertTrue(lease.path.exists())
        self.assertEqual(self.identifier.call_count, 2)
        self.assertEqual(progress[-1], (2352 * 2 * 3, 2352 * 2 * 3, "verify_source"))
        self.assertEqual({p.name for p in self.root.iterdir()}, {"playback-cache"})
        self.assertTrue(lease.close())
        self.assertFalse(lease.directory.exists())
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertTrue(lease.close())

    def test_separate_leases_and_existing_cache_files_are_isolated(self):
        self.cache.mkdir()
        existing = self.cache / "keep.srm"
        existing.write_bytes(b"save")
        first, second = self.prepare(), self.prepare()
        self.assertNotEqual(first.directory, second.directory)
        self.assertTrue(first.close())
        self.assertTrue(second.path.exists())
        self.assertEqual(existing.read_bytes(), b"save")
        self.assertTrue(second.close())

    def test_disc_change_and_expected_disc_mismatch_are_refused(self):
        with self.assertRaises(buffer.DiscBufferError) as error:
            self.prepare(expected_fingerprint="b" * 64)
        self.assertEqual(error.exception.reason, "media_changed")
        self.copier.assert_not_called()
        self.assertFalse(self.cache.exists())
        self.identifier.side_effect = [self.disc, identity(fingerprint="c" * 64)]
        with self.assertRaises(buffer.DiscBufferError) as error:
            self.prepare()
        self.assertEqual(error.exception.reason, "media_changed")
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_cancellation_before_work_has_no_side_effects(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(buffer.DiscBufferCancelled):
            self.prepare(cancelled=event)
        self.identifier.assert_not_called()
        self.copier.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_cancellation_during_capture_and_after_capture_cleans_owned_files(self):
        event = threading.Event()
        def cancelled_capture(*args, **kwargs):
            capture(*args, **kwargs)
            raise RawCaptureCancelled()
        self.copier.side_effect = cancelled_capture
        with self.assertRaises(RawCaptureCancelled):
            self.prepare(cancelled=event)
        self.assertEqual(list(self.cache.iterdir()), [])
        def cancel_after(*args, **kwargs):
            result = capture(*args, **kwargs)
            event.set()
            return result
        self.copier.side_effect = cancel_after
        with self.assertRaises(buffer.DiscBufferCancelled):
            self.prepare(cancelled=event)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_capture_validation_rejects_unverified_result_and_bad_manifest(self):
        def unverified(*args, **kwargs):
            return replace(capture(*args, **kwargs), verified_source_passes=1)
        self.copier.side_effect = unverified
        with self.assertRaises(buffer.DiscBufferError) as error:
            self.prepare()
        self.assertEqual(error.exception.reason, "verification_failed")
        self.assertEqual(list(self.cache.iterdir()), [])
        def bad_manifest(*args, **kwargs):
            result = capture(*args, **kwargs)
            data = json.loads(result.manifest_path.read_text())
            data["sha256"] = "b" * 64
            result.manifest_path.write_text(json.dumps(data))
            return result
        self.copier.side_effect = bad_manifest
        with self.assertRaises(buffer.DiscBufferError):
            self.prepare()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_no_fallback_or_library_import_on_unsupported_raw_capture(self):
        self.copier.side_effect = buffer.OpticalError("Unsupported track layout", reason="unsupported_layout")
        with patch("redux.library.Library") as library:
            with self.assertRaises(buffer.OpticalError) as error:
                self.prepare()
            library.assert_not_called()
        self.assertEqual(error.exception.reason, "unsupported_layout")
        self.assertEqual(list(self.cache.iterdir()), [])

    def mounted(self):
        source = self.root / "disc.cue"
        source.write_text('FILE "source.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        track = self.root / "source.bin"
        track.write_bytes(bytes(2352))
        value = identity(source_image=source, is_virtual=True)
        self.identifier.return_value = value
        self.resolver.return_value = source
        return source, track, value

    def test_mounted_image_uses_original_bundle_and_close_retains_it(self):
        source, track, value = self.mounted()
        lease = self.prepare(expected_fingerprint=value.fingerprint)
        self.assertEqual(lease.path, source)
        self.assertFalse(lease.temporary)
        self.assertEqual(lease.storage, "mounted_source")
        self.assertIsNone(lease.directory)
        self.copier.assert_not_called()
        self.assertFalse(self.cache.exists())
        self.assertTrue(lease.close())
        self.assertTrue(source.exists())
        self.assertEqual(track.read_bytes(), bytes(2352))

    def test_mounted_source_remount_or_referenced_file_change_is_refused(self):
        source, track, value = self.mounted()
        self.resolver.side_effect = [source, None]
        with self.assertRaises(buffer.DiscBufferError) as error:
            self.prepare()
        self.assertEqual(error.exception.reason, "media_changed")
        self.resolver.side_effect = None
        def identify_changing_track(_):
            if self.identifier.call_count == 2:
                track.write_bytes(b"changed")
            return value
        self.identifier.reset_mock()
        self.identifier.side_effect = identify_changing_track
        with self.assertRaises(buffer.DiscBufferError) as error:
            self.prepare()
        self.assertEqual(error.exception.reason, "media_changed")
        self.copier.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_cleanup_refuses_extra_save_or_changed_marker_without_deleting_files(self):
        lease = self.prepare()
        save = lease.directory / "capture" / "disc.srm"
        save.write_bytes(b"save")
        self.assertFalse(lease.close())
        self.assertTrue(lease.path.exists())
        self.assertEqual(save.read_bytes(), b"save")
        save.unlink()
        marker = lease.directory / buffer._MARKER
        original = marker.read_bytes()
        marker.write_bytes(b"changed")
        self.assertFalse(lease.close())
        self.assertTrue(lease.path.exists())
        marker.write_bytes(original)
        self.assertTrue(lease.close())

    def test_cleanup_refuses_capture_symlink_and_retains_outside_files(self):
        lease = self.prepare()
        outside = self.root / "outside"
        outside.mkdir()
        save = outside / "save.srm"
        save.write_bytes(b"save")
        original_capture = lease.directory / "capture"
        moved_capture = lease.directory / "held-capture"
        original_capture.rename(moved_capture)
        try:
            original_capture.symlink_to(outside, target_is_directory=True)
        except OSError:
            moved_capture.rename(original_capture)
            lease.close()
            self.skipTest("Creating symlinks is unavailable on this Windows configuration")
        self.assertFalse(lease.close())
        self.assertEqual(save.read_bytes(), b"save")
        self.assertTrue((moved_capture / "disc.bin").exists())
        original_capture.unlink()
        moved_capture.rename(original_capture)
        self.assertTrue(lease.close())

    def test_reparse_points_are_refused_even_without_symlink_privileges(self):
        lease = self.prepare()
        capture_path = lease.directory / "capture"
        real_lstat = Path.lstat
        def reparse_lstat(path, *args, **kwargs):
            if path == capture_path:
                return SimpleNamespace(st_mode=0o40700, st_file_attributes=0x400)
            return real_lstat(path, *args, **kwargs)
        with patch.object(Path, "lstat", reparse_lstat):
            self.assertFalse(lease.close())
            self.assertTrue(lease.path.exists())
        self.assertTrue(lease.close())

    def test_changed_cue_checksum_and_outside_capture_result_are_rejected(self):
        def changed_cue(*args, **kwargs):
            result = capture(*args, **kwargs)
            result.cue_path.write_text(result.cue_path.read_text() + "REM changed\n")
            return result
        self.copier.side_effect = changed_cue
        with self.assertRaises(buffer.DiscBufferError) as error:
            self.prepare()
        self.assertEqual(error.exception.reason, "verification_failed")
        self.assertEqual(list(self.cache.iterdir()), [])
        outside = self.root / "keep.cue"
        outside.write_bytes(b"keep")
        def outside_result(*args, **kwargs):
            return replace(capture(*args, **kwargs), cue_path=outside)
        self.copier.side_effect = outside_result
        with self.assertRaises(buffer.DiscBufferError) as error:
            self.prepare()
        self.assertEqual(error.exception.reason, "verification_failed")
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_context_manager_closes_only_after_caller_finishes(self):
        with self.prepare() as lease:
            self.assertTrue(lease.path.exists())
        self.assertTrue(lease.closed)
        self.assertFalse(lease.directory.exists())


if __name__ == "__main__":
    unittest.main()
