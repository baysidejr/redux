import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest

from redux.library import Library, ImportCancelled, discover_media, inspect_bundle


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.source=self.base/"source"
        self.source.mkdir()
        self.library=Library(self.base/"library")

    def tearDown(self):
        self.temp.cleanup()

    def file(self,name,data=b"test media bytes"):
        path=self.source/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
        return path

    def cue(self):
        self.file("track 01.bin",bytes(range(256))*8)
        self.file("audio.bin",b"audio"*100)
        return self.file("Disc.cue",b'FILE "track 01.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\nFILE "audio.bin" BINARY\n TRACK 02 AUDIO\n INDEX 01 00:00:00\n')

    def test_multitrack_cue_import_retains_exact_bytes_and_references(self):
        cue=self.cue()
        originals={p.name:p.read_bytes() for p in self.source.iterdir()}
        game,created=self.library.import_game(cue,system="psx")
        self.assertTrue(created)
        imported=inspect_bundle(self.library.path_for(game))
        self.assertEqual({p.name:p.read_bytes() for p in imported.files},originals)
        self.assertEqual({p.name:p.read_bytes() for p in self.source.iterdir()},originals)
        self.assertTrue(all(r["status"]=="ok" for r in self.library.verify(game["id"])))

    def test_managed_library_survives_move_to_different_storage_root(self):
        game,_=self.library.import_game(self.cue(),system="psx")
        moved=self.base/"sd_card"/"Games"
        moved.parent.mkdir()
        shutil.move(str(self.library.root),moved)
        library=Library(moved)
        restored=library.game(game["id"])
        self.assertTrue(library.path_for(restored).is_file())
        self.assertTrue(all(r["status"]=="ok" for r in library.verify(game["id"])))

    def test_duplicate_import_has_single_entry_and_no_staging_directory(self):
        source=self.file("Game.nes")
        first,created=self.library.import_game(source)
        again,created_again=self.library.import_game(source)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"],again["id"])
        self.assertEqual(len(self.library.games()),1)
        self.assertFalse(list(self.library.media.glob(".import-*")))

    def test_missing_cue_track_creates_no_library_entry_or_copied_files(self):
        cue=self.file("Missing.cue",b'FILE "missing.bin" BINARY\n')
        with self.assertRaises(FileNotFoundError): self.library.import_game(cue)
        self.assertEqual(self.library.games(),[])
        self.assertEqual(list(self.library.media.iterdir()),[])

    def test_m3u_import_retains_multiple_discs_and_scan_avoids_duplicate_tracks(self):
        self.cue()
        self.file("disc2.iso",b"disc two")
        playlist=self.file("Series.m3u",b"# playlist\nDisc.cue\ndisc2.iso\n")
        self.assertEqual(discover_media(self.source),[playlist])
        game,_=self.library.import_game(playlist,system="psx")
        self.assertEqual(len(inspect_bundle(self.library.path_for(game)).files),5)
        self.assertEqual(len(self.library.verify(game["id"])),5)

    def test_m3u_cycle_is_rejected(self):
        playlist=self.file("cycle.m3u",b"cycle.m3u\n")
        with self.assertRaisesRegex(ValueError,"Cyclic"): self.library.import_game(playlist)

    def test_out_of_bundle_reference_cannot_copy_other_files(self):
        (self.base/"outside.bin").write_bytes(b"unselected data")
        cue=self.file("outside.cue",b'FILE "../outside.bin" BINARY\n')
        with self.assertRaisesRegex(ValueError,"leaves"): self.library.import_game(cue)

    def test_absolute_windows_reference_rejected_even_on_other_platforms(self):
        cue=self.file("absolute.cue",b'FILE "C:\\unselected.bin" BINARY\n')
        with self.assertRaisesRegex(ValueError,"relative"): self.library.import_game(cue)

    def test_cancelled_copy_publishes_nothing(self):
        source=self.file("large.iso",b"x"*(3*1024*1024))
        cancelled=threading.Event()
        with self.assertRaises(ImportCancelled):
            self.library.import_game(source,cancelled=cancelled,progress=lambda _:cancelled.set())
        self.assertEqual(self.library.games(),[])
        self.assertEqual(list(self.library.media.iterdir()),[])

    def test_linked_media_remains_on_source_and_reports_offline_or_changed(self):
        source=self.file("Game.sfc",b"original")
        game,_=self.library.import_game(source,linked=True)
        self.assertEqual(self.library.path_for(game),source)
        self.assertEqual(list(self.library.media.iterdir()),[])
        source.write_bytes(b"modified")
        self.assertEqual(self.library.verify(game["id"])[0]["status"],"changed")
        source.unlink()
        self.assertEqual(self.library.verify(game["id"])[0]["status"],"missing")

    def test_search_treats_wildcards_and_sql_syntax_literally(self):
        self.library.import_game(self.file("100% Complete.nes"))
        self.library.import_game(self.file("Normal.nes"))
        self.assertEqual(len(self.library.games("%")),1)
        self.assertEqual(self.library.games("' OR 1=1 --"),[])

    def test_unknown_disc_system_can_be_assigned_without_changing_media(self):
        game,_=self.library.import_game(self.file("Disc.iso"))
        self.assertEqual(game["system"],"unknown")
        self.library.update_details(game["id"],title="Disc title",system="saturn")
        self.assertEqual(self.library.games(system="saturn")[0]["title"],"Disc title")
        self.assertEqual(self.library.verify(game["id"])[0]["status"],"ok")

    def test_clonecd_retains_img_and_optional_subchannel_sidecar(self):
        ccd=self.file("Game.ccd",b"[CloneCD]\nVersion=3\n")
        self.file("Game.img",b"sector data")
        self.file("Game.sub",b"subchannel data")
        game,_=self.library.import_game(ccd,system="psx")
        self.assertEqual(len(inspect_bundle(self.library.path_for(game)).files),3)

    def test_existing_unrelated_sqlite_database_is_not_modified(self):
        root=self.base/"other"
        root.mkdir()
        with closing(sqlite3.connect(root/"library.sqlite3")) as db, db:
            db.execute("CREATE TABLE private_data(value TEXT)")
            db.execute("INSERT INTO private_data VALUES('keep')")
        with self.assertRaisesRegex(ValueError,"not a supported"): Library(root)
        with closing(sqlite3.connect(root/"library.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT value FROM private_data").fetchone()[0],"keep")
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],0)


if __name__=="__main__": unittest.main()
