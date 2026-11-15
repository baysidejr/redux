"""Game cards: a drive becomes a card only when the player says what it is for; its games are linked in place."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from PIL import Image

from redux import game_cards
from redux.library import Library


class GameCardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.library=Library(self.root/"library")
        self.drive=self.root/"card";self.drive.mkdir()
        self.marker=self.drive/game_cards.MARKER

    def rom(self,folder,name,payload=b"ROM"):
        path=self.marker/folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload);return path

    def installed(self,name,exe="Game.exe",gog=False,where=""):
        folder=self.drive/where/name if where else self.drive/name
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/exe;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"MZ..")
        if gog:
            (folder/"unins000.exe").write_bytes(b"MZ..")
            (folder/"goggame-1.info").write_text(json.dumps({"playTasks":[{"category":"tool","path":"unins000.exe"},{"isPrimary":True,"category":"game","path":exe.replace("/","\\")}]}),encoding="utf-8")
        return folder,path

    def test_only_drives_with_the_marker_folder_are_cards(self):
        other=self.root/"usb-stick";other.mkdir()
        self.assertEqual(game_cards.find_cards([self.drive,other,self.root/"missing"]),[])
        self.marker.mkdir()
        self.assertEqual(game_cards.find_cards([self.drive,other]),[self.marker])

    def test_system_folders_accept_names_and_ids_and_ignore_strangers(self):
        self.rom("PlayStation","Game A.chd");self.rom("sega_cd","Game B.chd");self.rom("Jaguar","Game C.j64")
        self.rom("Photos","holiday.jpg");self.rom("Saturn","notes.txt")
        found=game_cards.scan_card(self.marker)
        self.assertEqual([(g.system,g.path.name) for g in found],[("jaguar","Game C.j64"),("psx","Game A.chd"),("sega_cd","Game B.chd")])

    def test_installed_pc_games_are_found_at_the_root_and_under_game_roots_only(self):
        gog,_=self.installed("Expedition 33","bin/game.exe",gog=True)
        plain,_=self.installed("Plain","Plain.exe")
        nested,_=self.installed("Nested","Nested.exe",where="GOG Games")
        self.installed("Unclear","a.exe");(self.drive/"Unclear"/"b.exe").write_bytes(b"MZ..")
        self.installed("Windows","explorer.exe");self.installed("$RECYCLE.BIN","x.exe")
        found=game_cards.find_installed_pc_games(self.drive)
        self.assertEqual([(folder.name,exe.name) for folder,exe in found],[("Expedition 33","game.exe"),("Nested","Nested.exe"),("Plain","Plain.exe")])

    def test_each_kind_writes_only_its_own_folders_and_records_itself(self):
        console=self.root/"console";console.mkdir()
        self.assertEqual(game_cards.prepare_card(console,"console"),[])
        marker=console/game_cards.MARKER
        self.assertEqual(sorted(p.name for p in marker.iterdir() if p.is_dir()),sorted(game_cards.CONSOLE_FOLDERS))
        self.assertIn("NES",game_cards.CONSOLE_FOLDERS);self.assertIn("Genesis",game_cards.CONSOLE_FOLDERS);self.assertIn("Neo Geo",game_cards.CONSOLE_FOLDERS)
        self.assertNotIn("PlayStation",game_cards.CONSOLE_FOLDERS);self.assertIn("Game Boy Advance",game_cards.CONSOLE_FOLDERS,"mGBA plays it")
        self.assertEqual(game_cards.card_kind(marker),"console")
        document=json.loads((marker/game_cards.CARD_FILE).read_text(encoding="utf-8"))
        self.assertEqual((document["kind"],document["label"]),("console","Console cartridge"))
        self.assertIn("neogeo.zip",(marker/"README.txt").read_text(encoding="utf-8"))
        storage=self.root/"storage";storage.mkdir()
        game_cards.prepare_card(storage,"storage")
        self.assertEqual(sorted(p.name for p in (storage/game_cards.MARKER).iterdir() if p.is_dir()),sorted(game_cards.STORAGE_FOLDERS))
        self.assertEqual(game_cards.STORAGE_FOLDERS,("PlayStation","Saturn","Sega CD","PC Engine CD","Neo Geo CD","3DO","Jaguar CD"))
        self.assertEqual(game_cards.card_kind(storage/game_cards.MARKER),"storage")
        with self.assertRaises(ValueError):game_cards.prepare_card(self.root/"odd","usb")
        self.assertFalse((self.root/"odd").exists(),"a refused kind writes nothing")
        # A card from before 0.14 has no card file and keeps working.
        self.marker.mkdir()
        self.assertEqual(game_cards.card_kind(self.marker),"legacy")
        self.assertIsNone(game_cards.card_kind(self.root/"nothing"))
        (self.marker/game_cards.CARD_FILE).write_text("not json",encoding="utf-8")
        self.assertEqual(game_cards.card_kind(self.marker),"legacy")

    def test_preparing_a_gog_cartridge_writes_one_config_per_pc_game_without_touching_games(self):
        gog,exe=self.installed("Expedition 33","bin/game.exe",gog=True)
        written=game_cards.prepare_card(self.drive,"gog")
        self.assertEqual(sorted(p.name for p in self.marker.iterdir() if p.is_dir()),["PC"])
        self.assertEqual(game_cards.card_kind(self.marker),"gog")
        self.assertTrue((self.marker/"README.txt").is_file())
        config_folder=self.marker/"PC"/"Expedition 33"
        self.assertEqual(written,[config_folder])
        self.assertTrue((config_folder/"art").is_dir())
        document=json.loads((config_folder/"game.json").read_text(encoding="utf-8"))
        self.assertEqual(document["title"],"Expedition 33")
        self.assertEqual(document["executable"],"Expedition 33/bin/game.exe")
        self.assertEqual(document["art"]["header"],"art/header.png")
        self.assertEqual(sorted(path.name for path in gog.iterdir()),["bin","goggame-1.info","unins000.exe"],"the game's own folder is untouched")
        (config_folder/"game.json").write_text('{"title": "Edited", "executable": "Expedition 33/bin/game.exe"}',encoding="utf-8")
        self.assertEqual(game_cards.prepare_card(self.drive,"gog"),[],"a second pass writes nothing new")
        self.installed("Later","Later.exe")
        self.assertEqual(game_cards.write_pc_configs(self.drive),[self.marker/"PC"/"Later"],"a later read adds settings for a new install only")
        self.assertIn("Edited",(config_folder/"game.json").read_text(encoding="utf-8"))

    def test_only_configured_pc_games_are_offered_and_a_broken_config_adds_nothing(self):
        self.installed("Expedition 33","bin/game.exe",gog=True)
        self.installed("Skipped")
        game_cards.prepare_card(self.drive,"gog")
        (self.marker/"PC"/"Skipped").rename(self.marker/"PC"/"Skipped-off")  # user renamed away the config folder
        (self.marker/"PC"/"Skipped-off"/"game.json").unlink()
        broken=self.marker/"PC"/"Broken";broken.mkdir();(broken/"game.json").write_text('{"executable": "nowhere.exe"}',encoding="utf-8")
        found=game_cards.scan_card(self.marker)
        self.assertEqual([(g.title,g.path.name) for g in found],[("Expedition 33","game.exe")])

    def test_sync_links_games_in_place_once_applies_card_art_and_reports_the_rest(self):
        self.rom("Jaguar","Tempest 2000.j64",b"T2K");self.rom("Jaguar","Doom.jag",b"DOOM")
        self.installed("Plain")
        game_cards.prepare_card(self.drive,"gog")
        art=self.marker/"PC"/"Plain"/"art"
        Image.new("RGB",(300,420),"red").save(art/"boxart.jpg");Image.new("RGB",(640,480),"blue").save(art/"gameimage.png")
        result=game_cards.sync_card(self.library,self.marker)
        self.assertEqual((len(result.added),result.existing,result.errors,result.cancelled),(3,0,[],False))
        games=self.library.games()
        self.assertTrue(all(game["storage"]=="linked" for game in games))
        self.assertEqual({game["system"] for game in games},{"jaguar","pc"})
        self.assertFalse(list((self.library.root/"media").rglob("*.j64")),"nothing is copied off the card")
        pc=next(game for game in games if game["system"]=="pc")
        for role in ("cover","screenshot","tile"):
            self.assertTrue(Path(self.library.artwork_for(pc,role)).is_file(),role)
        again=game_cards.sync_card(self.library,self.marker)
        self.assertEqual((again.added,again.existing),([],3))
        self.assertIn("3 already in the library",again.summary)
        self.assertEqual(len(self.library.games()),3)

    def test_edited_config_changes_title_arguments_and_working_folder_on_the_next_read(self):
        folder,exe=self.installed("Plain")
        game_cards.prepare_card(self.drive,"gog")
        config=self.marker/"PC"/"Plain"/"game.json"
        document=json.loads(config.read_text(encoding="utf-8"))
        document.update(title="Plain Deluxe",arguments=["-windowed"],working_directory=str(folder))
        config.write_text(json.dumps(document),encoding="utf-8")
        game_cards.sync_card(self.library,self.marker)
        game=self.library.games()[0]
        self.assertEqual(game["title"],"Plain Deluxe")
        arguments,working=self.library.pc_game_config(game)
        self.assertEqual((arguments,Path(working)),(["-windowed"],folder))

    def test_cancellation_stops_a_sync_without_partial_errors(self):
        self.rom("Jaguar","One.j64");self.rom("Jaguar","Two.j64")
        cancelled=threading.Event()
        def progress(index,total,title):cancelled.set()
        result=game_cards.sync_card(self.library,self.marker,cancelled=cancelled,progress=progress)
        self.assertTrue(result.cancelled);self.assertEqual(result.errors,[])
        self.assertIn("cancelled",result.summary)

    def test_broken_media_is_reported_not_fatal(self):
        cue=self.rom("PlayStation","Broken.cue",b'FILE "missing.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
        self.rom("Jaguar","Fine.j64")
        result=game_cards.sync_card(self.library,self.marker)
        self.assertEqual(len(result.added),1);self.assertEqual(len(result.errors),1)
        self.assertTrue(result.errors[0].startswith(cue.name))

    def test_a_gog_install_keeps_its_product_id_and_store_title(self):
        folder,exe=self.installed("Expedition 33","bin/game.exe",gog=True)
        (folder/"goggame-1.info").write_text(json.dumps({"gameId":"1903020710","rootGameId":"1903020710","name":"Clair Obscur: Expedition 33",
            "playTasks":[{"isPrimary":True,"category":"game","path":"bin\\game.exe"}]}),encoding="utf-8")
        (folder/"goggame-2.info").write_text(json.dumps({"gameId":"2222","rootGameId":"1903020710","name":"Expedition 33 - Soundtrack","playTasks":[]}),encoding="utf-8")
        self.assertEqual(game_cards.gog_info(folder),("1903020710","Clair Obscur: Expedition 33"))
        self.assertEqual(game_cards.gog_info(self.root),("",""))
        game_cards.prepare_card(self.drive,"gog")
        document=json.loads((self.marker/"PC"/"Expedition 33"/"game.json").read_text(encoding="utf-8"))
        self.assertEqual((document["gog_id"],document["title"]),("1903020710","Clair Obscur: Expedition 33"))
        found=game_cards.scan_card(self.marker)
        self.assertEqual([(g.title,g.store_id) for g in found],[("Clair Obscur: Expedition 33","1903020710")])
        game_cards.sync_card(self.library,self.marker)
        game=self.library.games()[0]
        self.assertEqual((game["title"],game["store_id"]),("Clair Obscur: Expedition 33","1903020710"))
        # A plain folder has no id; the id is never overwritten once set.
        self.library.add_pc_game(exe,store_id="9999")
        self.assertEqual(self.library.games()[0]["store_id"],"1903020710")
        plain,plain_exe=self.installed("Plain")
        added=self.library.add_pc_game(plain_exe,store_id="not a valid id!")
        self.assertEqual(added["store_id"],"")

    def test_a_card_label_names_its_kind(self):
        game_cards.prepare_card(self.drive,"gog")
        self.assertTrue(game_cards.sync_card(self.library,self.marker).summary.startswith("GOG cartridge"))
        self.marker.rename(self.drive/"gone");(self.drive/game_cards.MARKER).mkdir()
        self.assertTrue(game_cards.sync_card(self.library,self.marker).summary.startswith("Game card"))

    def test_the_windows_drive_is_never_prepared(self):
        import os
        system=os.environ.get("SystemDrive","C:")+"\\"
        self.assertFalse(game_cards.can_prepare(system))


if __name__=="__main__":unittest.main()
