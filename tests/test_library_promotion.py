"""Linked media can become a portable managed copy without a second game."""

from contextlib import contextmanager
from pathlib import Path
import queue
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from redux.library import ImportCancelled, Library, inspect_bundle


class LibraryPromotionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="redux-promotion-")
        self.root = Path(temporary.name).resolve()
        self.assertTrue(self.root.name.startswith("redux-promotion-"))
        self.addCleanup(temporary.cleanup)
        self.source = self.root / "source"
        self.source.mkdir()
        self.image = self.source / "track.bin"
        self.image.write_bytes(bytes(range(256)) * 16)
        self.entry = self.source / "disc.cue"
        self.entry.write_text('FILE "track.bin" BINARY\n  TRACK 01 MODE1/2352\n', encoding="utf-8")
        self.library = Library(self.root / "library")

    def test_promotion_preserves_identity_and_edits_after_source_removal(self):
        linked, added = self.library.import_game(self.entry, system="psx", linked=True)
        self.assertTrue(added)
        self.assertEqual(linked["import_action"], "added")
        self.library.update_details(linked["id"], title="My edited title", system="saturn")
        edited = self.library.game(linked["id"])
        with self.library.connect() as db:
            previous_files = [tuple(row) for row in db.execute(
                "SELECT * FROM media_files WHERE game_id=? ORDER BY relative_path", (linked["id"],))]

        promoted, added_again = self.library.import_game(
            self.entry, system="sega_cd", title="Do not replace my edits", linked=False)
        self.assertFalse(added_again)
        self.assertEqual(promoted["import_action"], "promoted")
        self.assertEqual(promoted["storage"], "managed")
        for field in ["id", "title", "system", "imported_at", "bundle_hash", "size"]:
            with self.subTest(field=field):
                self.assertEqual(promoted[field], edited[field])
        self.assertEqual(len(self.library.games()), 1)
        self.assertEqual(promoted["source_path"], str(self.entry.resolve()))
        self.assertFalse(Path(promoted["path"]).is_absolute())
        self.assertNotIn("import_action", self.library.game(promoted["id"]))
        with self.library.connect() as db:
            files = [tuple(row) for row in db.execute(
                "SELECT * FROM media_files WHERE game_id=? ORDER BY relative_path", (linked["id"],))]
        self.assertEqual(files, previous_files)

        self.entry.unlink()
        self.image.unlink()
        published = self.library.path_for(promoted)
        self.assertEqual(len(inspect_bundle(published).files), 2)
        self.assertEqual((published.parent / "track.bin").read_bytes(), bytes(range(256)) * 16)
        self.assertTrue(all(row["status"] == "ok" for row in self.library.verify(promoted["id"])))

        # Relative managed paths keep working after the entire library moves.
        moved_root = self.root / "portable-drive"
        self.library.root.rename(moved_root)
        moved = Library(moved_root)
        self.assertTrue(moved.path_for(moved.game(promoted["id"])).is_file())
        self.assertTrue(all(row["status"] == "ok" for row in moved.verify(promoted["id"])))

    def test_requested_link_does_not_demote_an_existing_managed_copy(self):
        managed, added = self.library.import_game(self.entry, system="psx")
        self.assertTrue(added)
        self.assertEqual(managed["import_action"], "added")
        duplicate, added_again = self.library.import_game(self.entry, linked=True)
        self.assertFalse(added_again)
        self.assertEqual(duplicate["import_action"], "duplicate")
        self.assertEqual(duplicate["id"], managed["id"])
        self.assertEqual(duplicate["path"], managed["path"])
        self.assertEqual(duplicate["storage"], "managed")
        self.assertEqual(len(self.library.games()), 1)

    def test_failed_promotion_rolls_back_to_link_and_can_be_retried(self):
        linked, _ = self.library.import_game(self.entry, system="psx", linked=True)
        with self.library.connect() as db:
            db.execute("""CREATE TRIGGER reject_test_promotion BEFORE UPDATE OF storage ON games
                          WHEN OLD.storage='linked' AND NEW.storage='managed'
                          BEGIN SELECT RAISE(ABORT, 'injected promotion failure'); END""")
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected promotion failure"):
                self.library.import_game(self.entry)
            self.assertEqual(self.library.game(linked["id"])["storage"], "linked")
            self.assertEqual(self.library.game(linked["id"])["path"], linked["path"])
            self.assertEqual(list(self.library.media.iterdir()), [])
            self.assertEqual(len(self.library.games()), 1)
        finally:
            with self.library.connect() as db:
                db.execute("DROP TRIGGER reject_test_promotion")

        promoted, added = self.library.import_game(self.entry)
        self.assertFalse(added)
        self.assertEqual(promoted["id"], linked["id"])
        self.assertEqual(promoted["import_action"], "promoted")
        self.assertTrue(all(row["status"] == "ok" for row in self.library.verify(linked["id"])))

    def test_cancelled_promotion_keeps_original_link_and_no_managed_files(self):
        linked, _ = self.library.import_game(self.entry, system="psx", linked=True)
        cancelled = threading.Event()
        acquiring_writer_lock = threading.Event()
        results = queue.Queue()
        original_connect = self.library.connect

        @contextmanager
        def observed_connect():
            with original_connect() as db:
                db.set_trace_callback(lambda sql: acquiring_writer_lock.set()
                                      if sql.strip().upper() == "BEGIN IMMEDIATE" else None)
                yield db

        def import_worker():
            try:
                results.put(self.library.import_game(self.entry, cancelled=cancelled))
            except Exception as error:
                results.put(error)

        with original_connect() as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with patch.object(self.library, "connect", observed_connect):
                worker = threading.Thread(target=import_worker, daemon=True)
                worker.start()
                try:
                    self.assertTrue(acquiring_writer_lock.wait(10))
                    cancelled.set()
                finally:
                    blocker.commit()
                    worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(results.get_nowait(), ImportCancelled)
        self.assertEqual(self.library.game(linked["id"])["storage"], "linked")
        self.assertEqual(list(self.library.media.iterdir()), [])
        self.assertEqual(len(self.library.games()), 1)


if __name__ == "__main__":
    unittest.main()
