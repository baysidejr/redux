"""Durable frontend features and migration against an actual legacy DB layout."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from redux.library import Library,discover_media


class LibraryFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.source=self.base/"source"
        self.source.mkdir()
        self.lib=Library(self.base/"library")

    def tearDown(self): self.temp.cleanup()

    def game(self,name="Game.nes",data=b"original game fixture",linked=False):
        path=self.source/name
        path.write_bytes(data)
        return self.lib.import_game(path,linked=linked)[0]

    def test_persistent_flags_and_views_combine_with_system_search(self):
        one=self.game("One.nes",b"one")
        two=self.game("Two.sfc",b"two")
        self.lib.set_flags(one["id"],favorite=True,wishlist=True)
        self.lib.set_flags(two["id"],completed=True)
        reopened=Library(self.lib.root)
        self.assertTrue(reopened.game(one["id"])["favorite"])
        self.assertEqual([g["id"] for g in reopened.games(view="favorites")],[one["id"]])
        self.assertEqual(reopened.games(system="snes",view="favorites"),[])
        self.assertEqual([g["id"] for g in reopened.games(query="Two",view="completed")],[two["id"]])
        self.assertTrue(reopened.toggle_favorite(one["id"])["favorite"] is False)

    def test_metadata_year_search_and_sort_are_durable(self):
        one=self.game("Z.nes",b"one")
        two=self.game("A.nes",b"two")
        self.lib.update_metadata(one["id"],year="1994",publisher="Independent Studio",description="Original\nfixture",region="USA",genre="Puzzle")
        self.lib.update_metadata(two["id"],year=2001)
        self.assertEqual([g["id"] for g in self.lib.games(sort="year")],[one["id"],two["id"]])
        self.assertEqual(self.lib.games(query="Studio")[0]["id"],one["id"])
        self.assertEqual(Library(self.lib.root).game(one["id"])["description"],"Original\nfixture")
        for change in ({"year":True},{"year":1800},{"system":"invalid"},{"title":""},{"__sql":"x"}):
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
                self.lib.update_metadata(one["id"],**change)

    def test_collections_memberships_unicode_uniqueness_and_delete_only_membership(self):
        game=self.game()
        collection=self.lib.create_collection("  Straße  ")
        with self.assertRaises(ValueError): self.lib.create_collection("STRASSE")
        self.lib.add_to_collection(collection["id"],game["id"])
        self.lib.add_to_collection(collection["id"],game["id"])
        self.assertEqual(self.lib.collection(collection["id"])["game_count"],1)
        self.assertEqual(self.lib.games(collection_id=collection["id"])[0]["id"],game["id"])
        self.assertEqual(self.lib.game_collections(game["id"])[0]["name"],"Straße")
        self.lib.rename_collection(collection["id"],"My collection")
        self.lib.delete_collection(collection["id"])
        self.assertEqual(self.lib.collections(),[])
        self.assertTrue(self.lib.path_for(self.lib.game(game["id"])).is_file())

    def test_remove_from_collection_and_missing_ids_are_explicit(self):
        game=self.game()
        collection=self.lib.create_collection("Arcade")
        self.lib.add_to_collection(collection["id"],game["id"])
        self.lib.remove_from_collection(collection["id"],game["id"])
        self.assertEqual(self.lib.games(collection_id=collection["id"]),[])
        with self.assertRaises(KeyError):self.lib.add_to_collection(collection["id"],99999)
        with self.assertRaises(KeyError):self.lib.add_to_collection(99999,game["id"])
        with self.assertRaises(ValueError):self.lib.games(view="collection")

    def test_session_finish_is_idempotent_and_persists_playtime(self):
        game=self.game()
        session=self.lib.start_session(game["id"],started_at="2026-11-08T12:00:00-05:00")
        finished=self.lib.finish_session(session,ended_at="2026-11-08T17:02:30Z",exit_code=0)
        repeated=self.lib.finish_session(session,duration_seconds=999,ended_at="2026-11-08T18:00:00Z")
        self.assertEqual(finished,repeated)
        refreshed=Library(self.lib.root).game(game["id"])
        self.assertEqual(refreshed["play_seconds"],150)
        self.assertEqual(refreshed["launch_count"],1)
        self.assertEqual(refreshed["last_played"],"2026-11-08T17:02:30Z")
        self.assertEqual(self.lib.games(view="continue")[0]["id"],game["id"])
        self.lib.set_flags(game["id"],completed=True)
        self.assertEqual(self.lib.games(view="continue"),[])
        self.assertEqual(self.lib.games(view="recent")[0]["id"],game["id"])

    def test_record_sessions_totals_and_older_record_does_not_reorder_last_played(self):
        one=self.game("One.nes",b"one")
        two=self.game("Two.nes",b"two")
        self.lib.record_session(one["id"],60,played_at="2026-11-08T17:00:00Z")
        self.lib.record_session(one["id"],20,played_at="2026-11-07T17:00:00Z")
        self.lib.record_session(two["id"],10,played_at="2026-11-08T18:00:00Z")
        self.assertEqual(self.lib.game(one["id"])["play_seconds"],80)
        self.assertEqual(self.lib.game(one["id"])["launch_count"],2)
        self.assertEqual(self.lib.game(one["id"])["last_played"],"2026-11-08T17:00:00Z")
        self.assertEqual(self.lib.games(sort="playtime")[0]["id"],one["id"])
        self.assertEqual(self.lib.games(sort="recent")[0]["id"],two["id"])
        self.assertEqual(len(self.lib.sessions(one["id"])),2)
        for seconds in (-1,float("nan"),float("inf"),True,"12"):
            with self.subTest(seconds=seconds),self.assertRaises((ValueError,TypeError)):
                self.lib.record_session(one["id"],seconds)

    def test_invalid_finish_does_not_change_running_session_or_game_stats(self):
        game=self.game()
        sid=self.lib.start_session(game["id"],started_at="2026-11-08T18:00:00Z")
        with self.assertRaises(ValueError): self.lib.finish_session(sid,ended_at="2026-11-08T17:00:00Z")
        self.assertEqual(self.lib.sessions()[0]["status"],"running")
        self.assertEqual(self.lib.game(game["id"])["play_seconds"],0)
        with self.assertRaises(ValueError): self.lib.record_session(game["id"],1,played_at="2026-11-08T17:00:00")

    def test_health_sees_missing_referenced_track_even_when_entry_exists(self):
        (self.source/"track.bin").write_bytes(b"track")
        cue=self.source/"Game.cue"
        cue.write_text('FILE "track.bin" BINARY\n')
        game=self.lib.import_game(cue,linked=True)[0]
        (self.source/"track.bin").unlink()
        info=self.lib.game(game["id"])
        self.assertTrue(info["entry_available"])
        self.assertFalse(info["available"])
        self.assertEqual(info["health"],"partial")
        self.assertEqual(self.lib.games(view="available"),[])
        self.assertEqual(self.lib.games(view="offline")[0]["id"],game["id"])
        self.assertEqual(self.lib.media_health(game["id"])["files"][1]["status"],"missing")

    def test_hash_health_distinguishes_changed_files_from_missing(self):
        game=self.game(linked=True)
        self.lib.path_for(game).write_bytes(b"changed")
        self.assertTrue(self.lib.media_health(game["id"])["available"])
        health=self.lib.media_health(game["id"],verify_hashes=True)
        self.assertFalse(health["available"])
        self.assertEqual(health["status"],"changed")

    def test_removal_preserves_media_and_reimport_verifies_and_reuses_it(self):
        source=self.source/"Game.nes"
        source.write_bytes(b"original")
        game=self.lib.import_game(source)[0]
        path=self.lib.path_for(game)
        collection=self.lib.create_collection("Keep files")
        self.lib.add_to_collection(collection["id"],game["id"])
        self.lib.record_session(game["id"],25)
        removed=self.lib.remove_game(game["id"])
        self.assertTrue(removed["media_retained"])
        self.assertEqual(self.lib.games(),[])
        self.assertTrue(path.is_file())
        self.assertTrue(source.is_file())
        self.assertEqual(self.lib.collection(collection["id"])["game_count"],0)
        self.assertEqual(self.lib.sessions(),[])
        reimported,created=self.lib.import_game(source)
        self.assertTrue(created)
        self.assertEqual(self.lib.path_for(reimported),path)
        self.assertEqual(self.lib.verify(reimported["id"])[0]["status"],"ok")
        self.assertFalse(list(self.lib.media.glob(".import-*")))

    def test_retained_corrupt_files_are_not_silently_overwritten_on_reimport(self):
        game=self.game(data=b"original")
        path=self.lib.path_for(game)
        self.lib.remove_game(game["id"])
        path.write_bytes(b"user changed this")
        with self.assertRaises(FileExistsError): self.lib.import_game(self.source/"Game.nes")
        self.assertEqual(path.read_bytes(),b"user changed this")
        self.assertEqual(self.lib.games(),[])

    def test_artwork_copy_survives_source_deletion_and_library_move(self):
        game=self.game()
        artwork=self.source/"cover.png"
        artwork.write_bytes(b"synthetic PNG for filesystem test")
        self.lib.set_artwork(game["id"],artwork)
        artwork.unlink()
        moved=self.base/"moved_library"
        shutil.move(self.lib.root,moved)
        library=Library(moved)
        new=library.game(game["id"])
        self.assertFalse(Path(new["artwork_path"]).is_absolute())
        self.assertEqual(library.artwork_for(new).read_bytes(),b"synthetic PNG for filesystem test")
        with self.assertRaises(ValueError): library.update_metadata(game["id"],artwork_path="../../private.png")

    def test_folder_scanner_ignores_plain_markdown_but_preserves_binary_md(self):
        (self.source/"README.md").write_text("# Documentation\nThis is text.\n")
        binary=self.source/"Game.md"
        binary.write_bytes(b"\x00\xff\x02\x01"*128)
        self.assertEqual(discover_media(self.source),[binary])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/"library"
        self.root.mkdir()
        self.db=self.root/"library.sqlite3"
        media=self.root/"media"/"legacyhash"
        media.mkdir(parents=True)
        (media/"Legacy.nes").write_bytes(b"legacy content")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript("""CREATE TABLE games(id INTEGER PRIMARY KEY,title TEXT NOT NULL,system TEXT NOT NULL,
                storage TEXT NOT NULL,path TEXT NOT NULL,source_path TEXT NOT NULL,bundle_hash TEXT NOT NULL UNIQUE,
                size INTEGER NOT NULL,imported_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%SZ','now')));
                CREATE TABLE media_files(game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,PRIMARY KEY(game_id,relative_path));
                CREATE TABLE launchers(system TEXT PRIMARY KEY,executable TEXT NOT NULL,arguments TEXT NOT NULL);
                PRAGMA application_id=1347573809; PRAGMA user_version=1;""")
            db.execute("INSERT INTO games VALUES(7,'Legacy','nes','managed','media/legacyhash/Legacy.nes','D:/source/Legacy.nes','legacyhash',14,'2020-01-01T00:00:00Z')")
            db.execute("INSERT INTO media_files VALUES(7,'Legacy.nes',?,14)",(hashlib.sha256(b"legacy content").hexdigest(),))
            db.execute("INSERT INTO launchers VALUES('nes','C:/emulator.exe','[\"{game}\"]')")

    def tearDown(self): self.temp.cleanup()

    def test_v1_migrates_idempotently_without_changing_identity_files_or_launcher(self):
        lib=Library(self.root)
        self.assertEqual(lib.game(7)["title"],"Legacy")
        self.assertEqual(lib.game(7)["play_seconds"],0)
        self.assertFalse(lib.game(7)["favorite"])
        self.assertEqual(lib.verify(7)[0]["status"],"ok")
        self.assertEqual(lib.launcher("nes")["arguments"],["{game}"])
        lib.set_flags(7,favorite=True)
        again=Library(self.root)
        self.assertTrue(again.game(7)["favorite"])
        self.assertEqual(again.game(7)["imported_at"],"2020-01-01T00:00:00Z")
        with again.connect() as db:self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],2)

    def test_incompatible_feature_table_rolls_back_entire_migration(self):
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute("CREATE TABLE collections(arbitrary TEXT)")
        with self.assertRaises(ValueError):Library(self.root)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],1)
            self.assertNotIn("favorite",{row[1] for row in db.execute("PRAGMA table_info(games)")})
            self.assertEqual(db.execute("SELECT title FROM games WHERE id=7").fetchone()[0],"Legacy")


if __name__=="__main__":unittest.main()
