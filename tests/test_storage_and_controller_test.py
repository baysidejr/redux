"""The Storage page, external install destinations and the live controller test."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from redux.console_ui import ConsoleApp
from redux.console_secondary import SecondaryScreens
from redux.console_controller_test import pressed_names, axis_fraction, trigger_fraction, BUTTON_BITS
from redux.controllers import GamepadState
from redux import game_cards
from redux.buffer_install import copy_bundle_to_storage
from redux.library import Library


class StoragePageTests(unittest.TestCase):
    def app(self):
        app=object.__new__(ConsoleApp)
        app.screen="settings";app.settings_menu=False;app.settings_section="storage";app.focus="storage:internal"
        app.overlay=None;app.targets=[];app.callbacks={};app.all_games=[];app.preferences={}
        app.library=SimpleNamespace(root=Path(tempfile.gettempdir()))
        app.texts=[];app.calls=[]
        app.display=app.text=lambda x,y,value,*a,**k:app.texts.append(str(value))
        app.panel=app.rect=app.icon=lambda *a,**k:None
        app.fit_text=lambda value,*a,**k:str(value)
        app.target=lambda key,bounds,callback,label="":(app.targets.append(key),app.callbacks.__setitem__(key,callback))
        app.button_canvas=lambda key,label,*a,**k:None
        app.refresh=lambda:app.calls.append("refresh");app.disc_dialog=lambda:app.calls.append("disc")
        app.install_destination=lambda:None;app.schedule_draw=lambda:None;app._save_preferences=lambda:app.calls.append("save")
        app.status=SimpleNamespace(set=lambda text:app.calls.append(("status",text)))
        app.menu=lambda title,items,message="":setattr(app,"overlay",{"title":title,"items":items,"message":message})
        app.storage_menu=lambda root:ConsoleApp.storage_menu(app,root)
        return app

    def test_storage_page_lists_internal_then_every_external_drive_and_the_optical_drive(self):
        app=self.app()
        app.storage_drives=lambda:[dict(root="F:\\",kind="fixed",label="GAMES",usage=(10*2**30,20*2**30),card=True,games=3,ignored=False,destination=True,optical=False),
                                   dict(root="E:\\",kind="removable",label="",usage=None,card=False,games=0,ignored=False,destination=False,optical=False),
                                   dict(root="G:\\",kind="optical",label="",optical=True)]
        SecondaryScreens._settings(app)
        self.assertEqual(app.targets[:4],["storage:internal","storage:F:","storage:E:","storage:G:"])
        self.assertIn("STORAGE",app.texts);self.assertIn("GAMES (F:) · fixed",app.texts)
        self.assertIn("Extra storage • 3 games • discs install here",app.texts)
        self.assertIn("A sets it up as extra storage",app.texts)
        app.callbacks["storage:G:"]();self.assertEqual(app.calls[-1],"disc")

    def test_storage_menu_offers_what_fits_the_drive(self):
        app=self.app()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            app.storage_menu(str(root))
            self.assertEqual([label for label,_ in app.overlay["items"]],["Use as extra storage","Cancel"])
            (root/game_cards.MARKER).mkdir()
            app.storage_menu(str(root))
            labels=[label for label,_ in app.overlay["items"]]
            self.assertEqual(labels,["Install discs here","Read its games again","Stop using as extra storage","Cancel"])
            dict(app.overlay["items"])["Install discs here"]()
            self.assertEqual(app.preferences["install_destination"],str(root))
            app.install_destination=lambda:root
            app.storage_menu(str(root))
            self.assertEqual([label for label,_ in app.overlay["items"]][0],"Install discs to internal storage instead")
            dict(app.overlay["items"])["Stop using as extra storage"]()
            self.assertEqual(app.preferences["storage_ignored"],[str(root)]);self.assertEqual(app.preferences["install_destination"],"")
            app.storage_menu(str(root))
            self.assertEqual([label for label,_ in app.overlay["items"]],["Use as extra storage","Cancel"])


class ExternalInstallTests(unittest.TestCase):
    def test_a_disc_bundle_copies_into_the_drive_system_folder_and_links_in_place(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/"buffer";source.mkdir()
            (source/"Game.bin").write_bytes(b"\x00"*4096)
            (source/"Game.cue").write_text('FILE "Game.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',encoding="utf-8")
            drive=root/"drive";drive.mkdir()
            copied=[]
            folder=drive/game_cards.MARKER/game_cards.system_folder_name("psx")/"My Disc"
            entry=copy_bundle_to_storage(source/"Game.cue",folder,progress=copied.append)
            self.assertEqual(entry,folder/"Game.cue");self.assertTrue((folder/"Game.bin").is_file())
            self.assertEqual(sum(copied),4096+(source/"Game.cue").stat().st_size)
            with self.assertRaises(FileExistsError):copy_bundle_to_storage(source/"Game.cue",folder)
            library=Library(root/"library")
            game,created=library.import_game(entry,system="psx",linked=True,title="My Disc")
            self.assertTrue(created);self.assertEqual(game["storage"],"linked")
            self.assertEqual(game_cards.system_folder_name("neo_geo_cd"),"Neo Geo CD");self.assertEqual(game_cards.system_folder_name("jaguar"),"Atari Jaguar")

    def test_cancellation_stops_the_copy(self):
        from redux.library import ImportCancelled
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/"Game.j64").write_bytes(b"x"*10)
            cancelled=threading.Event();cancelled.set()
            with self.assertRaises(ImportCancelled):copy_bundle_to_storage(root/"Game.j64",root/"out",cancelled=cancelled)


class ControllerTestScreenTests(unittest.TestCase):
    def app(self):
        app=object.__new__(ConsoleApp)
        app.screen="controller_test";app.overlay=None;app.busy=False;app.embedded=None;app.playback=None
        app.test_slot=None;app.test_states={};app.test_hold_since=None;app.calls=[]
        app.schedule_draw=lambda:app.calls.append("draw");app.back=lambda:app.calls.append("back")
        return app

    def test_names_axes_and_triggers_decode(self):
        self.assertEqual(pressed_names(BUTTON_BITS["a"]|BUTTON_BITS["up"]|BUTTON_BITS["lb"]),["up","lb","a"])
        self.assertEqual((axis_fraction(32767),axis_fraction(-32768),axis_fraction(0)),(1.0,-1.0,0.0))
        self.assertEqual((trigger_fraction(255),trigger_fraction(0),trigger_fraction(32767)),(1.0,0.0,1.0))

    def test_the_pad_that_moves_becomes_the_one_on_screen_and_holding_b_leaves(self):
        import redux.console_controller_test as module
        app=self.app()
        states={0:GamepadState(),1:GamepadState()}
        app.controller=SimpleNamespace(api=SimpleNamespace(state=lambda slot:states.get(slot)))
        app._poll_controller_test()
        self.assertEqual(app.test_slot,0)
        states[1]=GamepadState(buttons=BUTTON_BITS["x"])
        app._poll_controller_test()
        self.assertEqual(app.test_slot,1)
        states[1]=GamepadState(buttons=BUTTON_BITS["b"])
        clock=[100.0];module.time=SimpleNamespace(monotonic=lambda:clock[0])
        try:
            app._poll_controller_test();self.assertNotIn("back",app.calls)
            clock[0]+=module.HOLD_TO_LEAVE+0.1
            app._poll_controller_test();self.assertIn("back",app.calls)
        finally:
            import time;module.time=time

    def test_a_quick_b_tap_does_not_leave(self):
        import redux.console_controller_test as module
        app=self.app();app.test_slot=0
        states={0:GamepadState(buttons=BUTTON_BITS["b"])}
        app.controller=SimpleNamespace(api=SimpleNamespace(state=lambda slot:states.get(slot)))
        clock=[5.0];module.time=SimpleNamespace(monotonic=lambda:clock[0])
        try:
            app._poll_controller_test();clock[0]+=0.4;app._poll_controller_test()
            states[0]=GamepadState();app._poll_controller_test()
            self.assertNotIn("back",app.calls);self.assertIsNone(app.test_hold_since)
        finally:
            import time;module.time=time


if __name__=="__main__":unittest.main()
