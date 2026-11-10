"""Install only the existing buffer; preserve independent and active save data."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux import buffer_install as install
from redux.disc_identity import DiscIdentity
from redux.disc_session import prepare_disc_game
from redux.library import ImportCancelled, Library


class BufferInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.entry = self.root / "buffer" / "disc.iso"
        self.entry.parent.mkdir()
        self.entry.write_bytes(bytes(4096))
        identity = DiscIdentity("G:", "Buffered Game", "saturn", "TEST-1", "Saturn CD",
                                "header", (), {}, "a" * 64)
        self.lease = SimpleNamespace(entry_path=self.entry, identity=identity, closed=False)
        self.library = Library(self.root / "library")
        self.game = prepare_disc_game(self.library, self.lease)
        self.game.update(region="North America", description="Header metadata", publisher="Publisher")
        self.source = self.library.root / "game_data" / str(self.game["id"]) / self.game["bundle_hash"]
        self.battery = self.source / "battery" / "test-core" / "disc.srm"
        self.battery.parent.mkdir(parents=True)
        self.battery.write_bytes(b"current SRAM")

    def perform(self, **kwargs):
        return install.install_buffered_disc(self.library, self.lease, self.game, **kwargs)

    def test_installs_verified_bytes_and_metadata_without_drive_access_or_releasing_buffer(self):
        progress = []
        with patch("redux.disc_buffer.buffer_disc", side_effect=AssertionError("No buffer recapture")), \
             patch("redux.disc_identity.identify_drive", side_effect=AssertionError("No drive read")), \
             patch("redux.raw_acquisition.capture_raw_cd", side_effect=AssertionError("No capture")), \
             patch("redux.mounted_images.mounted_image_source", side_effect=AssertionError("No mounted resolver")):
            managed = self.perform(progress=progress.append)
        self.assertEqual(managed["storage"], "managed")
        self.assertEqual((managed["title"], managed["region"]), ("Buffered Game", "North America"))
        self.assertEqual(managed["description"], "Header metadata")
        self.assertEqual(self.library.path_for(managed).read_bytes(), self.entry.read_bytes())
        self.assertNotEqual(self.library.path_for(managed), self.entry)
        self.assertFalse(self.lease.closed)
        self.assertTrue(self.entry.exists())
        self.assertEqual(sum(progress), self.game["size"])
        handoff = self.lease._redux_save_handoff
        self.assertEqual((handoff.target / "battery/test-core/disc.srm").read_bytes(), b"current SRAM")
        self.assertEqual(self.battery.read_bytes(), b"current SRAM")

    def test_repeated_install_reuses_entry_and_preserves_user_metadata(self):
        first = self.perform()
        self.library.update_metadata(first["id"], title="Edited title", region="Japan", description="")
        again = self.perform()
        self.assertEqual(again["id"], first["id"])
        self.assertEqual((again["title"], again["region"], again["description"]), ("Edited title", "Japan", ""))
        self.assertEqual(len(self.library.games()), 1)
        self.assertFalse(self.lease.closed)

    def test_existing_managed_identity_can_share_its_existing_save_namespace(self):
        managed, _ = self.library.import_game(self.entry, system="saturn")
        self.game = prepare_disc_game(self.library, self.lease)
        self.assertEqual(self.game["id"], managed["id"])
        result = self.perform()
        handoff = self.lease._redux_save_handoff
        self.assertEqual(handoff.source, handoff.target)
        self.assertEqual(result["id"], managed["id"])
        self.assertFalse(self.lease.closed)

    def test_wrong_expected_identity_is_rejected_before_importing_any_row(self):
        self.game["bundle_hash"] = "f" * 64
        with patch.object(self.library, "import_game", wraps=self.library.import_game) as importer:
            with self.assertRaisesRegex(ValueError, "expected game identity"):
                self.perform()
            importer.assert_not_called()
        self.assertEqual(self.library.games(), [])
        self.assertFalse(self.lease.closed)

    def test_source_change_after_preflight_cannot_publish_a_different_identity(self):
        importer = self.library.import_game
        def change_then_import(*args, **kwargs):
            self.entry.write_bytes(b"changed after preflight")
            return importer(*args, **kwargs)
        with patch.object(self.library, "import_game", side_effect=change_then_import):
            with self.assertRaises(ValueError):
                self.perform()
        self.assertEqual(self.library.games(), [])
        self.assertEqual(list(self.library.media.iterdir()), [])
        self.assertFalse(self.lease.closed)

    def test_cancel_before_and_during_copy_leaves_the_buffer_and_no_game_row(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(ImportCancelled):
            self.perform(cancelled=event)
        event.clear()
        with self.assertRaises(ImportCancelled):
            self.perform(cancelled=event, progress=lambda _: event.set())
        self.assertFalse(self.lease.closed)
        self.assertEqual(self.entry.read_bytes(), bytes(4096))
        self.assertEqual(self.library.games(), [])
        self.assertEqual(list(self.library.media.iterdir()), [])

    def test_copy_failure_preserves_source_and_cleans_only_import_staging(self):
        from redux import library as library_module
        original_hash = library_module._hash_file
        def fail_sink(path, cancelled=None, sink=None, progress=None):
            if sink is not None:
                sink.write(b"partial output")
                raise OSError("Destination is full")
            return original_hash(path, cancelled, sink=sink, progress=progress)
        with patch.object(library_module, "_hash_file", side_effect=fail_sink):
            with self.assertRaisesRegex(OSError, "Destination is full"):
                self.perform()
        self.assertEqual(self.library.games(), [])
        self.assertEqual(list(self.library.media.iterdir()), [])
        self.assertEqual(self.entry.read_bytes(), bytes(4096))
        self.assertFalse(self.lease.closed)

    def test_post_commit_snapshot_conflict_exposes_installed_game_and_retains_both_copies(self):
        target = self.library.root / "game_data" / "1" / self.game["bundle_hash"] / "battery/test-core/disc.srm"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"Independent managed save")
        with self.assertRaises(install.BufferSaveConflict) as caught:
            self.perform()
        managed = caught.exception.installed_game
        self.assertEqual(managed["storage"], "managed")
        self.assertEqual(len(self.library.games()), 1)
        self.assertIn(str(self.source), str(caught.exception))
        self.assertIn(str(target.parent.parent.parent), str(caught.exception))
        self.assertEqual(target.read_bytes(), b"Independent managed save")
        self.assertEqual(self.battery.read_bytes(), b"current SRAM")
        self.assertFalse(self.lease.closed)
        target.unlink()
        self.lease._redux_save_handoff.sync()
        self.assertEqual(target.read_bytes(), b"current SRAM")

    def test_final_snapshot_updates_only_owned_previous_files_and_publishes_json_last(self):
        self.perform()
        handoff = self.lease._redux_save_handoff
        state = self.source / "states/test-core/new.state"
        manifest = self.source / "states/test-core/slot-0.json"
        state.parent.mkdir(parents=True)
        state.write_bytes(b"New immutable state blob")
        manifest.write_text('{"path":"new.state"}')
        self.battery.write_bytes(b"Later SRAM")
        order = []
        original_sync = install._sync_file
        def tracked(source, target, expected_source, previous):
            order.append(Path(target).name)
            return original_sync(source, target, expected_source, previous)
        with patch.object(install, "_sync_file", side_effect=tracked):
            handoff.sync()
        self.assertLess(order.index("new.state"), order.index("slot-0.json"))
        self.assertEqual((handoff.target / "battery/test-core/disc.srm").read_bytes(), b"Later SRAM")
        self.assertEqual(self.battery.read_bytes(), b"Later SRAM")
        target = handoff.target / "battery/test-core/disc.srm"
        target.write_bytes(b"User independently edited this")
        self.battery.write_bytes(b"Newest running save")
        with self.assertRaises(install.BufferSaveConflict):
            handoff.sync()
        self.assertEqual(target.read_bytes(), b"User independently edited this")
        self.assertEqual(self.battery.read_bytes(), b"Newest running save")

    def test_metadata_failure_after_publication_still_exposes_installed_identity(self):
        with patch.object(self.library, "update_metadata", side_effect=OSError("Metadata update failed")):
            with self.assertRaises(install.BufferInstallError) as caught:
                self.perform()
        self.assertEqual(caught.exception.installed_game["storage"], "managed")
        self.assertEqual(self.lease._redux_installed_game["id"], caught.exception.installed_game["id"])
        self.assertIsNotNone(self.lease._redux_save_handoff)
        self.assertFalse(self.lease.closed)
        self.assertTrue(self.entry.exists())

    def test_racing_managed_namespace_is_not_clobbered_by_initial_directory_snapshot(self):
        from redux import raw_acquisition
        publish = raw_acquisition._publish_directory
        def race(stage, target):
            target.mkdir()
            (target / "independent.txt").write_bytes(b"Unrelated managed data")
            return publish(stage, target)
        with patch.object(raw_acquisition, "_publish_directory", side_effect=race):
            with self.assertRaises(install.BufferSaveConflict) as caught:
                self.perform()
        self.assertEqual(caught.exception.installed_game["storage"], "managed")
        handoff = self.lease._redux_save_handoff
        self.assertEqual((handoff.target / "independent.txt").read_bytes(), b"Unrelated managed data")
        self.assertEqual(self.battery.read_bytes(), b"current SRAM")
        self.assertFalse(self.lease.closed)
        self.assertEqual(list(handoff.target.parent.glob(".redux-save-snapshot-*")), [])


if __name__ == "__main__":
    unittest.main()
