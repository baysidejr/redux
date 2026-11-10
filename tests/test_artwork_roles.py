"""Portable multi-role artwork and visible portrait-cover crop regressions."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

from PIL import Image,ImageDraw

from redux.console_layout import ReferenceLayout
from redux.library import ARTWORK_FIELDS,ImportCancelled,Library
from redux.ui_assets import Artwork


class ArtworkRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.lib=Library(self.base/"library")
        self.media=self.base/"Original.nes";self.media.write_bytes(b"original test media")
        self.game=self.lib.import_game(self.media)[0]

    def image(self,name,color="red",size=(100,150)):
        path=self.base/name;Image.new("RGB",size,color).save(path);return path

    def test_legacy_cover_api_and_role_fields_coexist(self):
        cover=self.image("cover.png");tile=self.image("tile.png","blue",(320,224))
        old=self.lib.set_artwork(self.game["id"],cover)
        updated=self.lib.set_artwork(self.game["id"],tile,role="tile")
        self.assertEqual(updated["artwork_path"],old["artwork_path"])
        self.assertEqual(self.lib.artwork_for(updated).read_bytes(),cover.read_bytes())
        self.assertEqual(self.lib.artwork_for(updated,"tile").read_bytes(),tile.read_bytes())
        self.assertTrue(all(field in updated for field in ARTWORK_FIELDS.values()))
        self.assertEqual(set(self.lib.artwork_provenance(self.game["id"])),{"cover","tile"})

    def test_every_role_and_public_provenance_survive_library_move(self):
        sources={role:self.image(role+".png",color) for role,color in
                 zip(ARTWORK_FIELDS,("red","blue","green","yellow","purple"))}
        original={role:path.read_bytes() for role,path in sources.items()}
        self.lib.set_artworks(self.game["id"],sources,provenance={
            role:{"provider":"fixture","provider_game_id":"42","source_url":"https://art.example/"+role+".png"}
            for role in sources})
        for path in sources.values():path.unlink()
        moved=self.base/"moved";shutil.move(self.lib.root,moved)
        library=Library(moved);game=library.game(self.game["id"])
        for role,field in ARTWORK_FIELDS.items():
            self.assertFalse(Path(game[field]).is_absolute())
            self.assertEqual(library.artwork_for(game,role).read_bytes(),original[role])
            record=library.artwork_provenance(game["id"])[role]
            self.assertEqual(record["sha256"],hashlib.sha256(original[role]).hexdigest())
            self.assertEqual(record["provider_game_id"],"42")

    def test_scraping_preserves_existing_cover_and_fills_other_roles(self):
        chosen=self.image("chosen.png");remote=self.image("remote.png","blue")
        first=self.lib.set_artwork(self.game["id"],chosen)
        updated=self.lib.set_artworks(self.game["id"],{"cover":remote,"hero":remote},
                                     provenance={"provider":"fixture"})
        self.assertEqual(updated["artwork_path"],first["artwork_path"])
        self.assertTrue(updated["hero_path"])
        records=self.lib.artwork_provenance(self.game["id"])
        self.assertEqual(records["cover"]["provider"],"local")
        self.assertEqual(records["hero"]["provider"],"fixture")
        self.assertEqual(updated["artwork_updated_roles"],["hero"])
        overwritten=self.lib.set_artworks(self.game["id"],{"cover":remote},overwrite=True)
        self.assertNotEqual(overwritten["artwork_path"],first["artwork_path"])

    def test_failed_or_cancelled_batch_does_not_commit_partial_roles(self):
        cover=self.image("cover.png");tile=self.image("tile.png","blue")
        with self.assertRaises(FileNotFoundError):
            self.lib.set_artworks(self.game["id"],{"cover":cover,"tile":self.base/"missing.png"})
        self.assertEqual(self.lib.game(self.game["id"])["artwork_path"],"")
        import redux.library as module
        cancelled=threading.Event();original=module._hash_file
        def cancel_after_first(*args,**kwargs):
            result=original(*args,**kwargs);cancelled.set();return result
        with patch.object(module,"_hash_file",side_effect=cancel_after_first):
            with self.assertRaises(ImportCancelled):
                self.lib.set_artworks(self.game["id"],{"cover":cover,"tile":tile},cancelled=cancelled)
        game=self.lib.game(self.game["id"])
        self.assertFalse(game["artwork_path"] or game["tile_path"])
        self.assertEqual(self.lib.artwork_provenance(game["id"]),{})
        self.assertFalse(list((self.lib.root/"artwork").glob(".art-*")))

    def test_database_rejection_rolls_back_all_role_references(self):
        first=self.lib.set_artwork(self.game["id"],self.image("old.png"))
        with self.lib.connect() as db:
            db.execute("CREATE TRIGGER reject_art BEFORE UPDATE OF hero_path ON games "
                       "WHEN NEW.hero_path!='' BEGIN SELECT RAISE(ABORT,'fixture rejection'); END")
        art=self.image("new.png","blue")
        with self.assertRaises(sqlite3.IntegrityError):
            self.lib.set_artworks(self.game["id"],{"cover":art,"hero":art},overwrite=True)
        game=self.lib.game(self.game["id"])
        self.assertEqual(game["artwork_path"],first["artwork_path"])
        self.assertEqual(game["hero_path"],"")
        self.assertEqual(set(self.lib.artwork_provenance(game["id"])),{"cover"})

    def test_new_manual_cover_wins_over_an_inflight_missing_art_scrape(self):
        import redux.library as module
        remote=self.image("remote.png");manual=self.image("manual.png","blue")
        original=module._hash_file;changed=[]
        def select_while_copying(*args,**kwargs):
            result=original(*args,**kwargs)
            if not changed:
                changed.append(True);self.lib.set_artwork(self.game["id"],manual)
            return result
        with patch.object(module,"_hash_file",side_effect=select_while_copying):
            result=self.lib.set_artworks(self.game["id"],{"cover":remote})
        self.assertEqual(self.lib.artwork_for(result).read_bytes(),manual.read_bytes())
        self.assertEqual(result["artwork_updated_roles"],[])

    def test_missing_managed_art_is_repaired_but_missing_external_art_is_retained(self):
        first=self.lib.set_artwork(self.game["id"],self.image("old.png"))
        self.lib.artwork_for(first).unlink()
        source=self.image("replacement.png","blue")
        repaired=self.lib.set_artworks(self.game["id"],{"cover":source})
        self.assertEqual(repaired["artwork_updated_roles"],["cover"])
        self.assertEqual(self.lib.artwork_for(repaired).read_bytes(),source.read_bytes())
        offline=self.base/"unplugged-usb"/"user-cover.png"
        self.lib.update_metadata(self.game["id"],artwork_path=str(offline))
        kept=self.lib.set_artworks(self.game["id"],{"cover":source})
        self.assertEqual(kept["artwork_path"],str(offline))
        self.assertEqual(kept["artwork_updated_roles"],[])

    def test_public_source_records_never_persist_credentials_or_url_queries(self):
        result=self.lib.set_artwork(self.game["id"],self.image("cover.png"),provenance={
            "provider":"fixture","provider_game_id":"123","password":"secret-password",
            "api_key":"secret-key","request":{"token":"secret-token"},
            "source_url":"https://art.example/image.png?token=secret-query#secret-fragment"})
        record=self.lib.artwork_provenance(result["id"])["cover"]
        self.assertEqual(record["source_url"],"https://art.example/image.png")
        self.assertNotIn("secret",json.dumps(record))
        self.lib.set_artwork(result["id"],self.image("other.png","blue"),role="hero",
                             provenance={"source_url":"https://user:secret@art.example/image.png"})
        self.assertNotIn("source_url",self.lib.artwork_provenance(result["id"])["hero"])

    def test_role_paths_cannot_escape_and_remote_metadata_cannot_set_paths(self):
        with self.assertRaises(ValueError):self.lib.update_metadata(self.game["id"],tile_path="../../private.png")
        with self.assertRaises(ValueError):self.lib.artwork_for({"tile_path":"../../private.png"},"tile")
        with self.assertRaises(ValueError):self.lib.set_artwork(self.game["id"],self.image("a.png"),role="unknown")
        self.assertIsNone(self.lib.artwork_for({},"tile"))

    def test_v2_migration_adds_roles_without_changing_existing_cover(self):
        self.lib.set_artwork(self.game["id"],self.image("cover.png"))
        with self.lib.connect() as db:
            for field in ("tile_path","hero_path","screenshot_path","logo_path","artwork_provenance"):
                db.execute(f"ALTER TABLE games DROP COLUMN {field}")
        again=Library(self.lib.root);game=again.game(self.game["id"])
        self.assertTrue(again.artwork_for(game).is_file())
        self.assertFalse(game["tile_path"] or game["hero_path"])
        with again.connect() as db:self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],2)

    def app(self):
        app=object.__new__(ReferenceLayout);app.library=self.lib;app.focus="none"
        for name in ("rect","display","icon","target","open_game","text","panel","back","play","_top_shade","_identity"):
            setattr(app,name,Mock())
        app._badge=Mock(return_value=32);app.fit_text=lambda value,*args:value
        app.art=Artwork(None,self.base/"empty-art");app.photos=[]
        return app

    def test_portrait_card_keeps_top_and_bottom_of_cover_visible(self):
        source=self.image("portrait.png","black",(100,300))
        with Image.open(source) as opened:
            image=opened.copy();draw=ImageDraw.Draw(image)
            draw.rectangle((0,0,99,15),fill="red");draw.rectangle((0,284,99,299),fill="blue")
            image.save(source)
        game=self.lib.set_artwork(self.game["id"],source);app=self.app();rendered=[]
        def picture(path,x,y,w,h,**kwargs):
            with patch("redux.ui_assets.ImageTk.PhotoImage",side_effect=lambda image,**kw:image.copy()):
                image=app.art.image(path,w,h,**kwargs)
            if image is not None:rendered.append(image);return True
            return False
        app.picture=picture;app._card(game,0,0,213,149)
        image=rendered[0]
        self.assertEqual(image.size,(213,149))
        self.assertEqual(image.getpixel((106,0)),(255,0,0,255))
        self.assertEqual(image.getpixel((106,148)),(0,0,255,255))
        self.assertEqual(image.getpixel((0,70))[3],0,"Portrait cover should fit, leaving side space")

    def test_dedicated_tile_wins_and_cover_never_becomes_hero(self):
        game=self.lib.set_artworks(self.game["id"],{
            "cover":self.image("cover.png"),"tile":self.image("tile.png","blue",(320,224))})
        app=self.app();app.picture=Mock(return_value=True)
        app._card(game,0,0,213,149)
        self.assertEqual(app.picture.call_args_list[0].args[0],self.lib.artwork_for(game,"tile"))
        self.assertIsNone(app._game_hero(game))
        game=self.lib.set_artwork(game["id"],self.image("screen.png","green",(320,240)),role="screenshot")
        self.assertEqual(app._game_hero(game),self.lib.artwork_for(game,"screenshot"))

    def test_game_details_fit_cover_and_use_distinct_backdrop(self):
        game=self.lib.set_artwork(self.game["id"],self.image("cover.png"));app=self.app()
        app.selected=lambda:game;app.details_tab="play";app.picture=Mock(return_value=True)
        app._game_details()
        self.assertNotEqual(app.picture.call_args_list[0].args[0],self.lib.artwork_for(game))
        cover_calls=[call for call in app.picture.call_args_list if call.args[0]==self.lib.artwork_for(game)]
        self.assertEqual(len(cover_calls),1)
        self.assertEqual(cover_calls[0].kwargs["mode"],"contain")


if __name__=="__main__":unittest.main()
