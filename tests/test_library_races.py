"""Import atomicity and cancellation regressions; synthetic files, no emulator."""

from contextlib import contextmanager
from pathlib import Path
import queue
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from redux.library import ImportCancelled, Library, inspect_bundle


class LibraryRaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="redux-test-")
        self.root = Path(temporary.name).resolve()
        # The resolved cleanup target is exactly the directory created for this test.
        self.assertTrue(self.root.name.startswith("redux-test-"))
        self.addCleanup(temporary.cleanup)
        self.source = self.root / "source"
        self.source.mkdir()
        self.library = Library(self.root / "library")

    def write_rom(self):
        image = self.source / "demo.nes"
        image.write_bytes(b"NES\x1a" + bytes(range(64)))
        return image

    def test_changed_descriptor_cannot_publish_missing_tracks(self):
        (self.source / "a.bin").write_bytes(b"first track" * 10)
        (self.source / "b.bin").write_bytes(b"replacement track" * 10)
        cue = self.source / "z.cue"
        cue.write_text('FILE "a.bin" BINARY\n  TRACK 01 MODE1/2352\n', encoding="utf-8")
        changed = threading.Event()

        def mutate_descriptor_after_first_payload_chunk(_count):
            if not changed.is_set():
                cue.write_text('FILE "b.bin" BINARY\n  TRACK 01 MODE1/2352\n', encoding="utf-8")
                changed.set()

        try:
            game, added = self.library.import_game(
                cue, progress=mutate_descriptor_after_first_payload_chunk)
        except (ValueError, FileNotFoundError):
            # Rejecting a changed source is a valid outcome; a coherent snapshot
            # is also valid, so the test does not require a particular strategy.
            self.assertTrue(changed.is_set())
            self.assertEqual(self.library.games(), [])
            self.assertEqual(list(self.library.media.iterdir()), [])
        else:
            self.assertTrue(changed.is_set())
            self.assertTrue(added)
            published = self.library.path_for(game)
            # Opening every descriptor reference catches an entry whose hashes
            # are correct but whose named tracks were never copied.
            bundle = inspect_bundle(published)
            self.assertTrue(all(path.is_file() for path in bundle.files))
            self.assertTrue(all(row["status"] == "ok" for row in self.library.verify(game["id"])))

    def test_database_failure_leaves_no_media_orphan_and_retry_works(self):
        image = self.write_rom()
        with self.library.connect() as db:
            db.execute("""CREATE TRIGGER reject_test_import BEFORE INSERT ON games
                          BEGIN SELECT RAISE(ABORT, 'injected import failure'); END""")
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected import failure"):
                self.library.import_game(image)
            self.assertEqual(self.library.games(), [])
            self.assertEqual(list(self.library.media.iterdir()), [])
        finally:
            with self.library.connect() as db:
                db.execute("DROP TRIGGER reject_test_import")

        game, added = self.library.import_game(image)
        self.assertTrue(added)
        self.assertEqual(len(self.library.games()), 1)
        self.assertEqual(self.library.path_for(game).read_bytes(), image.read_bytes())

    def test_cancellation_while_waiting_for_database_lock_does_not_commit(self):
        image = self.write_rom()
        cancelled = threading.Event()
        acquiring_writer_lock = threading.Event()
        results = queue.Queue()
        original_connect = self.library.connect

        @contextmanager
        def observed_connect():
            with original_connect() as db:
                # SQLite invokes this before executing BEGIN, allowing the test
                # to cancel after the pre-transaction checks with no sleeps.
                db.set_trace_callback(lambda sql: acquiring_writer_lock.set()
                                      if sql.strip().upper() == "BEGIN IMMEDIATE" else None)
                yield db

        def import_worker():
            try:
                results.put(self.library.import_game(image, cancelled=cancelled))
            except Exception as error:
                results.put(error)

        with original_connect() as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with patch.object(self.library, "connect", observed_connect):
                worker = threading.Thread(target=import_worker, daemon=True)
                worker.start()
                try:
                    self.assertTrue(acquiring_writer_lock.wait(10), "import never reached SQLite lock")
                    cancelled.set()
                finally:
                    blocker.commit()
                    worker.join(10)

        self.assertFalse(worker.is_alive(), "import worker did not finish after lock release")
        result = results.get_nowait()
        self.assertIsInstance(result, ImportCancelled)
        self.assertEqual(self.library.games(), [])
        self.assertEqual(list(self.library.media.iterdir()), [])

    def test_cue_alias_reference_is_rejected_or_preserved(self):
        real = self.source / "real.bin"
        real.write_bytes(b"track" * 10)
        alias = self.source / "alias.bin"
        try:
            alias.symlink_to(real)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        cue = self.source / "disc.cue"
        cue.write_text('FILE "alias.bin" BINARY\n  TRACK 01 MODE1/2352\n', encoding="utf-8")
        try:
            game, _ = self.library.import_game(cue)
        except ValueError:
            self.assertEqual(self.library.games(), [])
            self.assertEqual(list(self.library.media.iterdir()), [])
        else:
            inspect_bundle(self.library.path_for(game))


if __name__ == "__main__":
    unittest.main()
