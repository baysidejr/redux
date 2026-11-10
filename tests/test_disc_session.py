"""Disc identities stay transient and leased files outlive native workers."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux.disc_identity import DiscIdentity
from redux.disc_session import DiscEmbeddedGame, prepare_disc_game
from redux.library import ImportCancelled, Library


class LeaseFixture:
    def __init__(self, entry, identity):
        self.entry_path = entry
        self.identity = identity
        self.closed = False
        self.cleanup_error = None
        self.fail_cleanup = False
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        if self.fail_cleanup:
            self.cleanup_error = "Unowned file found; retained"
            return False
        self.closed = True
        self.cleanup_error = None
        return True


class HostFixture:
    def __init__(self):
        self.fail_start = False
        self.fail_stop = False
        self.stop_calls = 0
        self.worker_alive = False
        self._thread = SimpleNamespace(is_alive=lambda: self.worker_alive)

    def start(self):
        if self.fail_start:
            raise RuntimeError("Core failed to start")
        return self

    def stop(self):
        self.stop_calls += 1
        if self.fail_stop:
            raise RuntimeError("Native shutdown timed out")


class DiscPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "temporary-disc"
        self.source.mkdir()
        self.entry = self.source / "disc.cue"
        self.entry.write_text('FILE "track.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n', encoding="utf-8")
        (self.source / "track.bin").write_bytes(bytes(2352 * 2))
        self.identity = DiscIdentity("G:", "Header Game", "saturn", "MK-TEST",
            "Saturn CD", "header", (), {"region": "USA"}, "a" * 64)
        self.lease = LeaseFixture(self.entry, self.identity)
        self.library = Library(self.root / "library")

    def test_read_only_preparation_uses_the_same_manifest_as_library_import(self):
        before = {path.name: path.read_bytes() for path in self.source.iterdir()}
        progress = []
        game = prepare_disc_game(self.library, self.lease, progress=progress.append)
        self.assertEqual(self.library.games(), [])
        self.assertEqual(list(self.library.media.iterdir()), [])
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.source.iterdir()})
        self.assertFalse(self.lease.closed)
        self.assertEqual(game["storage"], "linked")
        self.assertTrue(game["transient_disc"])
        self.assertTrue(game["available"])
        self.assertEqual(self.library.path_for(game), self.entry)
        self.assertEqual(game["game_id"], "MK-TEST")
        self.assertEqual(sum(progress), game["size"])
        self.assertGreaterEqual(game["id"], 1 << 62)
        self.assertLess(game["id"], 1 << 63)
        imported, _ = self.library.import_game(self.entry, system="saturn", linked=True)
        self.assertEqual(imported["bundle_hash"], game["bundle_hash"])

    def test_matching_installed_bundle_reuses_real_id_without_modifying_its_row(self):
        installed, _ = self.library.import_game(self.entry, system="saturn")
        self.library.update_metadata(installed["id"], title="User's title", publisher="Publisher")
        stored_before = self.library.game(installed["id"])
        game = prepare_disc_game(self.library, self.lease)
        self.assertEqual(game["id"], installed["id"])
        self.assertEqual(game["title"], self.identity.title)
        self.assertEqual(game["publisher"], "Publisher")
        self.assertEqual(game["path"], str(self.entry))
        self.assertEqual(self.library.game(installed["id"]), stored_before)
        self.assertEqual(len(self.library.games()), 1)

    def test_same_relative_bundle_after_rebuffering_has_stable_identity(self):
        first = prepare_disc_game(self.library, self.lease)
        other = self.root / "another-session"
        other.mkdir()
        for path in self.source.iterdir():
            (other / path.name).write_bytes(path.read_bytes())
        second = prepare_disc_game(self.library, LeaseFixture(other / "disc.cue", self.identity))
        self.assertEqual(first["bundle_hash"], second["bundle_hash"])
        self.assertEqual(first["id"], second["id"])

    def test_header_identity_wins_and_catalogue_fields_are_optional(self):
        metadata = {"title": "Wrong title", "system": "psx", "game_id": "WRONG",
                    "description": "Description", "year": 1997, "publisher": "Publisher",
                    "developer": "Developer", "genre": "Sports"}
        game = prepare_disc_game(self.library, self.lease, metadata)
        for field in ("title", "system", "game_id"):
            self.assertEqual(game[field], getattr(self.identity, field))
        for field in ("description", "year", "publisher", "developer", "genre"):
            self.assertEqual(game[field], metadata[field])
        self.assertEqual(game["region"], "USA")

    def test_cancellation_does_not_close_the_callers_lease_or_import_a_row(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(ImportCancelled):
            prepare_disc_game(self.library, self.lease, cancelled=event)
        event.clear()
        with self.assertRaises(ImportCancelled):
            prepare_disc_game(self.library, self.lease, cancelled=event,
                              progress=lambda _: event.set())
        self.assertFalse(self.lease.closed)
        self.assertEqual(self.library.games(), [])

    def test_mutation_during_hashing_is_refused(self):
        changed = False
        def mutate(_):
            nonlocal changed
            if not changed:
                changed = True
                self.entry.write_bytes(self.entry.read_bytes() + b"REM changed\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            prepare_disc_game(self.library, self.lease, progress=mutate)
        self.assertFalse(self.lease.closed)
        self.assertEqual(self.library.games(), [])

    def test_descriptor_dependencies_are_reinspected_after_hashing(self):
        (self.source / "new.bin").write_bytes(bytes(2352))
        calls = 0
        def change_descriptor_after_its_hash(_):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.entry.write_text('FILE "new.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n')
        with self.assertRaisesRegex(ValueError, "references changed"):
            prepare_disc_game(self.library, self.lease, progress=change_descriptor_after_its_hash)
        self.assertEqual(self.library.games(), [])

    def test_a_closed_lease_cannot_be_prepared(self):
        self.lease.closed = True
        with self.assertRaisesRegex(ValueError, "open media lease"):
            prepare_disc_game(self.library, self.lease)


class DiscSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.media = self.root / "disc-buffer" / "game.iso"
        self.media.parent.mkdir()
        self.media.write_bytes(bytes(2048))
        self.core = self.root / "test.dll"
        self.core.write_bytes(b"Fake core; never loaded")
        self.lease = LeaseFixture(self.media, None)
        self.game = {"id": (1 << 62) + 1, "bundle_hash": "a" * 64, "storage": "linked",
                     "path": str(self.media), "system": "psx", "transient_disc": True}
        self.library = SimpleNamespace(root=self.root / "library", path_for=lambda game: Path(game["path"]))
        self.host = HostFixture()
        patcher = patch("redux.libretro_host.EmbeddedSession", return_value=self.host)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = [0.0]
        self.session = DiscEmbeddedGame(self.library, self.game, self.core, media_lease=self.lease,
                                        _clock=lambda: self.clock[0], audio=False)

    def test_successful_stop_releases_once_and_saves_are_outside_the_lease(self):
        self.assertTrue(self.session.directory.is_relative_to(self.library.root))
        self.assertFalse(self.session.directory.is_relative_to(self.media.parent))
        self.session.start()
        self.clock[0] = 10
        self.assertFalse(self.lease.closed)
        self.session.stop()
        self.assertTrue(self.lease.closed)
        self.assertFalse(self.session.lease_retained)
        self.assertEqual(self.session.play_seconds, 10)
        self.assertEqual(self.host.stop_calls, 1)

    def test_failed_native_shutdown_retains_media_until_a_successful_retry(self):
        self.session.start()
        self.host.fail_stop = True
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self.session.stop()
        self.assertTrue(self.session.lease_retained)
        self.assertEqual(self.lease.close_calls, 0)
        self.assertIsNone(self.session.finished_seconds)
        self.host.fail_stop = False
        self.session.stop()
        self.assertFalse(self.session.lease_retained)
        self.assertIsNone(self.session.lease_cleanup_error)

    def test_returned_stop_with_a_live_worker_still_retains_media(self):
        self.session.start()
        self.host.worker_alive = True
        with self.assertRaisesRegex(RuntimeError, "still alive"):
            self.session.stop()
        self.assertEqual(self.lease.close_calls, 0)
        self.assertTrue(self.session.lease_retained)
        self.host.worker_alive = False
        self.session.stop()
        self.assertTrue(self.lease.closed)

    def test_start_failure_stops_native_host_before_releasing(self):
        self.host.fail_start = True
        with self.assertRaisesRegex(RuntimeError, "failed to start"):
            self.session.start()
        self.assertEqual(self.host.stop_calls, 1)
        self.assertTrue(self.lease.closed)
        self.assertEqual(self.session.finished_seconds, 0)

    def test_start_failure_and_shutdown_timeout_retains_media_and_original_error(self):
        self.host.fail_start = self.host.fail_stop = True
        with self.assertRaisesRegex(RuntimeError, "failed to start") as caught:
            self.session.start()
        self.assertTrue(self.session.lease_retained)
        self.assertIn("timed out", self.session.lease_cleanup_error)
        self.assertTrue(any("lease retained" in note for note in caught.exception.__notes__))
        self.assertEqual(self.lease.close_calls, 0)
        self.host.fail_stop = False
        self.session.stop()
        self.assertFalse(self.session.lease_retained)

    def test_cleanup_refusal_is_visible_and_retry_does_not_restart_or_stop_native_again(self):
        self.session.start()
        self.lease.fail_cleanup = True
        self.session.stop()
        self.assertTrue(self.session.lease_retained)
        self.assertIn("Unowned file", self.session.lease_cleanup_error)
        self.assertIsNotNone(self.session.finished_seconds)
        self.lease.fail_cleanup = False
        self.session.stop()
        self.assertFalse(self.session.lease_retained)
        self.assertEqual(self.host.stop_calls, 1)

    def test_duplicate_start_does_not_stop_a_running_session(self):
        self.session.start()
        with self.assertRaisesRegex(RuntimeError, "only be started once"):
            self.session.start()
        self.assertEqual(self.host.stop_calls, 0)
        self.assertEqual(self.lease.close_calls, 0)

    def test_lease_must_match_the_game_playback_path(self):
        wrong = LeaseFixture(self.root / "another.iso", None)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            DiscEmbeddedGame(self.library, self.game, self.core, media_lease=wrong)
        self.assertEqual(wrong.close_calls, 0)


if __name__ == "__main__":
    unittest.main()
