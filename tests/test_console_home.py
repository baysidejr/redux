"""Kazeta-style Home: a shelf of every console, a stage that shows a game loading, a disc that plays itself."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from redux.console_layout import ReferenceLayout
from redux.console_stage import ConsoleStage, FrameAnimation, HOME_ORDER, AUTOPLAY_DELAY
from redux.console_ui import ConsoleApp
from redux.disc_identity import DiscIdentity
from redux.library import SYSTEMS, Library
from redux.now_playing import DiscSnapshot


def disc(fingerprint="a" * 64, title="Eternal Champions", system="sega_cd"):
    identity = DiscIdentity("G:", title, system, "T-93175", "Sega CD", "header", (), {"area_symbols": "U"}, fingerprint)
    return DiscSnapshot("G:", title, system, "T-93175", fingerprint, identity, False, {})


class HomeShelfTests(unittest.TestCase):
    def app(self):
        app = object.__new__(ConsoleApp)
        app.screen = "home";app.focus = None;app.overlay = None;app.busy = False;app.embedded = None;app.text_prompt = None
        app.playback = SimpleNamespace(active=Mock(return_value=[]));app.all_games = [];app.rows = [];app.selected_id = None
        app.targets = [];app.callbacks = {};app.texts = [];app.pictures = [];app.calls = [];app.history = SimpleNamespace(push=Mock(), pop=Mock(return_value=None))
        app.display = app.text = lambda x, y, value, *a, **k: app.texts.append(str(value))
        app.panel = app.rect = app.icon = app._top_shade = lambda *a, **k: None
        app._identity = lambda *a, **k: None;app._badge = lambda system, x, y: 40;app._controller_hint = lambda *a, **k: None
        app.fit_text = lambda value, *a, **k: str(value)
        app.picture = lambda path, *a, **k: (app.pictures.append(str(path) if path else None), bool(path))[1]
        app.art = SimpleNamespace(path=lambda system, kind="hero", custom=None: f"{system}-{kind}.png", bundled=lambda *parts: None, hero=lambda: "hero-banner.jpg")
        app.target = lambda key, bounds, callback, label="": (app.targets.append(key), app.callbacks.__setitem__(key, callback))
        app.schedule_draw = lambda: app.calls.append("draw");app.refresh = lambda: app.calls.append("refresh")
        app.status = SimpleNamespace(set=lambda text: app.calls.append(("status", text)), get=lambda: "")
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items, "message": message})
        app.notice = lambda title, message: app.calls.append(("notice", title))
        app.discs = SimpleNamespace(active=None);app.stage_animation = None;app.disc_autoplay = None
        app.navigate = Mock(side_effect=lambda screen, **k: setattr(app, "screen", screen))
        app.root = SimpleNamespace(after=lambda ms, callback: app.calls.append(("after", ms, callback)))
        app.installed_disc_game = lambda d: None
        return app

    def test_home_shows_every_console_with_counts_and_no_welcome_art(self):
        app = self.app()
        app.all_games = [{"id": 1, "system": "nes"}, {"id": 2, "system": "nes"}, {"id": 3, "system": "psx"}]
        ReferenceLayout._home(app)
        shelf = [t for t in app.targets if t.startswith("home-system:")]
        self.assertEqual(shelf, [f"home-system:{s}" for s in HOME_ORDER[:8]], "the first page holds eight consoles")
        self.assertEqual(len(app.home_systems()), len(SYSTEMS) - 1, "every console, nothing hidden")
        self.assertIn("Consoles", app.texts);self.assertIn("2", app.texts, "the NES tile carries its count")
        self.assertNotIn("welcome:card", app.targets);self.assertFalse(any("Welcome" in t for t in app.texts))
        self.assertEqual(app.home_focused_system(), "nes", "the strip names the first console you own")
        self.assertEqual(app.pictures[0], "hero-banner.jpg", "the stage shows the Redux banner, not the console's art")
        self.assertNotIn("nes-hero.png", app.pictures);self.assertIn("NES  ·  2 GAMES", app.texts)
        self.assertEqual(app.targets[0], "hero:play")

    def test_moving_on_the_shelf_pages_by_index_and_reaches_the_stage(self):
        app = self.app();systems = app.home_systems()
        app.focus = f"home-system:{systems[0]}"
        self.assertTrue(ConsoleApp._home_shelf_move(app, "right"));self.assertEqual(app.focus, f"home-system:{systems[1]}")
        self.assertTrue(ConsoleApp._home_shelf_move(app, "down"));self.assertEqual(app.focus, f"home-system:{systems[5]}")
        self.assertTrue(ConsoleApp._home_shelf_move(app, "down"));self.assertEqual(app.focus, f"home-system:{systems[9]}", "down from the second row turns the page")
        ReferenceLayout._home(app)
        self.assertIn(f"home-system:{systems[9]}", app.targets);self.assertNotIn(f"home-system:{systems[0]}", app.targets)
        app.focus = f"home-system:{systems[2]}"
        self.assertTrue(ConsoleApp._home_shelf_move(app, "up"));self.assertEqual(app.focus, "hero:play", "up from the top row is the stage")
        self.assertTrue(ConsoleApp._home_shelf_move(app, "down"));self.assertEqual(app.focus, f"home-system:{systems[2]}")
        app.focus = f"home-system:{systems[4]}"
        self.assertFalse(ConsoleApp._home_shelf_move(app, "left"), "left from the first column leaves the shelf for the lane")

    def test_a_console_tile_opens_the_consoles_page_not_a_game_list(self):
        from redux.console_about import console_facts
        app = self.app()
        ReferenceLayout._home(app)
        app.callbacks["home-system:sega_cd"]()
        app.navigate.assert_called_once_with("console");self.assertEqual((app.console_system, app.home_system), ("sega_cd", "sega_cd"))
        self.assertIsNone(app.stage_animation, "no zoom: the stage keeps the banner")
        entry = console_facts("sega_cd")
        self.assertEqual(entry["title"], "Sega CD");self.assertEqual(entry["facts"]["release_na"], "October 15, 1992");self.assertIn("CC BY-SA", entry["license"])
        app.texts = [];app.targets = [];app.focus = "console:back";app.back = lambda: None;app.all_games = [{"id": 1, "system": "sega_cd"}]
        ConsoleApp._console(app)
        self.assertIn("SEGA CD", app.texts);self.assertIn("Made by", app.texts);self.assertIn("Sega", app.texts);self.assertIn("North America", app.texts)
        self.assertIn("1 game on your cartridges", app.texts);self.assertTrue(any("Wikipedia" in t and "CC BY-SA" in t for t in app.texts))
        self.assertTrue(any(t.startswith("The Sega CD, known as Mega-CD") for t in app.texts));self.assertEqual(app.targets, ["console:back"])
        self.assertEqual(console_facts("no_such_system"), {})
        for system in HOME_ORDER:
            page = console_facts(system)
            self.assertTrue(page.get("summary") and page.get("url"), f"{system} has a Wikipedia summary")
            if system != "pc":
                self.assertTrue(page["facts"], f"{system} has facts")

    def test_a_game_tile_plays_instead_of_opening_a_page(self):
        app = self.app();app.play = Mock()
        ConsoleApp._play_game(app, 42)
        self.assertEqual(app.selected_id, 42);app.play.assert_called_once()
        self.assertFalse(hasattr(ConsoleApp, "_details_navigation"));self.assertFalse(hasattr(ConsoleApp, "collection_menu"))

    def test_the_stage_shows_the_disc_and_then_the_loading_animation(self):
        app = self.app();app.discs.active = disc()
        ReferenceLayout._home(app)
        self.assertIn("DISC INSERTED", app.texts);self.assertIn("ETERNAL CHAMPIONS", app.texts);self.assertIn("Not installed  ·  A to install and play", app.texts)
        self.assertEqual(app.pictures[0], "hero-banner.jpg");self.assertNotIn("sega_cd-hero.png", app.pictures);self.assertEqual(app.callbacks["hero:play"], app.play_disc)
        app.texts = [];app.busy = True;app.status = SimpleNamespace(get=lambda: "Installing disc • Reading and checking • 42%", set=lambda t: None)
        ConsoleStage.start_stage(app, "sega_cd", "Eternal Champions")
        ReferenceLayout._home(app)
        self.assertIn("NOW LOADING", app.texts);self.assertIn("42%", app.texts);self.assertIn("Installing disc • Reading and checking • 42%", app.texts)

    def test_a_disc_that_goes_in_while_redux_runs_plays_on_its_own_but_a_start_up_disc_waits(self):
        app = self.app();app.play_disc = Mock()
        startup = SimpleNamespace(kind="inserted", drive="G:", identity=disc().identity, fingerprint="a" * 64, startup=True)
        ConsoleStage.note_disc_insert(app, startup)
        self.assertIsNone(app.disc_autoplay)
        event = SimpleNamespace(kind="inserted", drive="G:", identity=disc().identity, fingerprint="a" * 64, startup=False)
        ConsoleStage.note_disc_insert(app, event)
        self.assertIsNotNone(app.disc_autoplay)
        app.discs.active = disc()
        app.disc_autoplay["at"] -= AUTOPLAY_DELAY + 1
        app.busy = True;ConsoleStage._stage_tick(app)
        self.assertIsNotNone(app.disc_autoplay, "nothing happens while something else is running")
        app.busy = False;app.screen = "library";ConsoleStage._stage_tick(app)
        self.assertIsNone(app.disc_autoplay);app.navigate.assert_called_with("home", remember=False)
        self.assertEqual(app.stage_animation["title"], "Eternal Champions");self.assertEqual(app.focus, "hero:play")
        self.assertEqual(app.home_system, "sega_cd");self.assertIsNone(app.stage_animation["origin"])
        delay, callback = [c for c in app.calls if isinstance(c, tuple) and c[0] == "after"][-1][1:]
        self.assertIs(callback, app.play_disc)
        # A disc swapped out before the delay elapsed is forgotten.
        ConsoleStage.note_disc_insert(app, event);app.disc_autoplay["at"] -= AUTOPLAY_DELAY + 1
        app.discs.active = disc(fingerprint="b" * 64, title="Another");ConsoleStage._stage_tick(app)
        self.assertIsNone(app.disc_autoplay)

    def test_the_owners_sprite_sheets_play_intro_loop_and_outro(self):
        from redux.console_stage import SpriteSheetAnimation
        from redux.ui_assets import Artwork
        import json
        manifest = Artwork.bundled("animations", "animations.json")
        self.assertIsNotNone(manifest, "the sheets ship with the app")
        settings = json.loads(Path(manifest).read_text(encoding="utf-8"))
        for system in HOME_ORDER:
            if system in ("gba", "pc"):
                continue
            self.assertIn(system, settings["systems"], f"{system} has a sheet");self.assertTrue(Artwork.bundled("animations", settings["systems"][system]["sheet"]), system)
        sheet = SpriteSheetAnimation(Artwork.bundled("animations", "sega_cd.png"), settings["systems"]["sega_cd"], settings)
        self.assertTrue(sheet);self.assertEqual((sheet.fps, sheet.columns, sheet.width, sheet.height), (12, 8, 480, 270))
        self.assertEqual([sheet.index_at(t) for t in (0, 1.0, 1.99, 2.0, 3.99, 4.0)], [0, 12, 23, 24, 47, 24], "intro once, then the loop")
        self.assertEqual((sheet.index_at(9, finishing_since=0), sheet.index_at(9, finishing_since=9)), (48, 59), "the outro plays once the load is done and holds its last frame")
        self.assertEqual(sheet.box(9), (480, 270, 960, 540))
        # The stage draws the sheet on the right and keeps the words on the left.
        app = self.app();app.art = SimpleNamespace(path=lambda system, kind="hero", custom=None: f"{system}-{kind}.png", bundled=Artwork.bundled, hero=lambda: "hero-banner.jpg")
        drawn = [];app.sprite = lambda sheet_path, box, x, y, w, h: drawn.append((Path(sheet_path).name, box, round(w), round(h))) or True
        app.busy = True;app.status = SimpleNamespace(get=lambda: "Installing disc • Saving to library • 7 MiB", set=lambda t: None)
        ConsoleStage.start_stage(app, "sega_cd", "Eternal Champions")
        ReferenceLayout._home(app)
        self.assertEqual(drawn[0][0], "sega_cd.png");self.assertEqual(drawn[0][1], (0, 0, 480, 270), "the intro starts at cell 0")
        self.assertEqual(round(drawn[0][2] / drawn[0][3], 2), 1.78, "cells keep their 16:9 shape")
        self.assertIn("NOW LOADING", app.texts);self.assertIn("ETERNAL CHAMPIONS", app.texts)
        # A second start for the same console carries the running animation on; finishing switches to the outro.
        started = app.stage_animation["started"]
        ConsoleStage.start_stage(app, "sega_cd", "Eternal Champions")
        self.assertEqual(app.stage_animation["started"], started)
        ConsoleStage.finish_stage(app)
        drawn.clear();ReferenceLayout._home(app)
        self.assertEqual(drawn[0][1], sheet.box(48), "the outro begins at its first cell")
        ConsoleStage.start_stage(app, "nes", "Mario")
        self.assertNotEqual(app.stage_animation["started"], started, "another console starts its own animation")

    def test_frame_animations_loop_from_their_marked_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for index in range(4):(folder / f"frame_{index:03d}.png").write_bytes(b"png")
            (folder / "animation.json").write_text('{"fps": 2, "loop_from": 2}', encoding="utf-8")
            animation = FrameAnimation(folder)
            self.assertTrue(animation);self.assertEqual(animation.fps, 2)
            self.assertEqual([animation.frame_at(t).name for t in (0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)],
                             ["frame_000.png", "frame_001.png", "frame_002.png", "frame_003.png", "frame_002.png", "frame_003.png", "frame_002.png"])
            self.assertFalse(FrameAnimation(folder / "missing"))


class DeleteStateTests(unittest.TestCase):
    def test_the_pause_menu_offers_delete_and_a_slot_can_be_removed(self):
        from redux.embedded import EmbeddedGame
        import json
        with tempfile.TemporaryDirectory() as temporary:
            states = Path(temporary) / "states";states.mkdir()
            game = object.__new__(EmbeddedGame);game.states = states;game.game = {"bundle_hash": "g" * 64};game.core_hash = "c" * 64
            game._lock = threading.RLock()
            digest = "d" * 64;(states / (digest + ".state")).write_bytes(b"state")
            item = {"schema": 1, "slot": 0, "game": "g" * 64, "core": "c" * 64, "sha256": digest, "size": 5, "saved_at": "2026-11-15T00:00:00+00:00"}
            (states / "slot-0.json").write_text(json.dumps(item), encoding="utf-8")
            (states / "slot-1.json").write_text(json.dumps(dict(item, slot=1)), encoding="utf-8")
            slot = game.delete(0)
            self.assertFalse(slot.exists);self.assertFalse((states / "slot-0.json").exists())
            self.assertTrue((states / (digest + ".state")).exists(), "slot 1 still points at the same state")
            game.delete(1)
            self.assertFalse((states / (digest + ".state")).exists())
            with self.assertRaises(ValueError):game.delete(1)
        app = object.__new__(ConsoleApp);app.busy = False;app.overlay = None
        app.embedded = SimpleNamespace(pause=lambda: None, play_seconds=12);app.embedded_game = {"title": "Game"};app.held_keys = set();app.input_release_gate = False
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items, "message": message})
        ConsoleApp.game_menu(app)
        self.assertIn("Delete a save state", [label for label, _ in app.overlay["items"]])


class WishlistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.library = Library(Path(self.temporary.name) / "library")

    def test_entries_keep_their_order_move_and_leave_cleanly(self):
        self.assertTrue(self.library.wishlist_add("sega_cd", "sonic-cd", "Sonic CD"))
        self.assertTrue(self.library.wishlist_add("psx", "123", "Metal Gear Solid"))
        self.assertFalse(self.library.wishlist_add("sega_cd", "sonic-cd", "Sonic CD again"), "no duplicates")
        self.assertEqual([e["title"] for e in self.library.wishlist()], ["Sonic CD", "Metal Gear Solid"])
        self.assertTrue(self.library.wishlist_has("psx", "123"));self.assertFalse(self.library.wishlist_has("psx", "999"))
        second = self.library.wishlist()[1]["id"]
        self.library.wishlist_move(second, -1)
        self.assertEqual([e["title"] for e in self.library.wishlist()], ["Metal Gear Solid", "Sonic CD"])
        self.library.wishlist_move(second, -5)
        self.assertEqual(self.library.wishlist()[0]["id"], second, "the ends clamp")
        self.library.wishlist_remove("psx", "123")
        self.assertEqual([(e["title"], e["position"]) for e in self.library.wishlist()], [("Sonic CD", 1)])
        with self.assertRaises(KeyError):self.library.wishlist_move(second, 1)

    def test_the_database_page_toggles_the_wishlist_and_the_screen_lists_it(self):
        from redux.console_gamedb import ConsoleGameDatabase
        from redux.console_secondary import SecondaryScreens
        app = object.__new__(ConsoleApp);app.library = self.library;app.calls = []
        app.status = SimpleNamespace(set=lambda text: app.calls.append(text));app.schedule_draw = lambda: None
        game = {"id": "sonic-cd", "title": "Sonic CD", "system": "sega_cd"}
        ConsoleGameDatabase.toggle_wishlist(app, "sega_cd", game)
        self.assertTrue(self.library.wishlist_has("sega_cd", "sonic-cd"));self.assertEqual(app.calls[-1], "Added to your wishlist")
        ConsoleGameDatabase.toggle_wishlist(app, "sega_cd", game)
        self.assertFalse(self.library.wishlist_has("sega_cd", "sonic-cd"))
        ConsoleGameDatabase.toggle_wishlist(app, "sega_cd", game)
        # The Wishlist screen draws the entry and A opens its menu.
        app.texts = [];app.targets = [];app.callbacks = {};app.focus = None;app.pages = {}
        app.display = app.text = lambda x, y, value, *a, **k: app.texts.append(str(value))
        app.panel = app.rect = app.icon = lambda *a, **k: None;app.fit_text = lambda value, *a, **k: str(value)
        app.picture = lambda *a, **k: False;app.button_canvas = lambda key, label, *a, **k: app.targets.append(key)
        app.target = lambda key, bounds, callback, label="": (app.targets.append(key), app.callbacks.__setitem__(key, callback))
        app._secondary_heading = lambda *a, **k: app.texts.append(a[1]);app._secondary_pager = lambda *a, **k: None
        app._game_database = SimpleNamespace(game=lambda system, gid: {"id": gid, "title": "Sonic CD", "year": 1993}, art=lambda entry, role: None)
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items, "message": message})
        SecondaryScreens._collections(app)
        self.assertIn("Wishlist", app.texts);self.assertIn("Sonic CD", app.texts);self.assertIn("Sega CD  ·  1993", app.texts)
        entry = self.library.wishlist()[0]
        app.callbacks[f"wish:{entry['id']}"]()
        self.assertEqual([label for label, _ in app.overlay["items"]], ["Open in the Database", "Move up", "Move down", "Remove from wishlist", "Back"])
        app.refresh = lambda: None
        dict(app.overlay["items"])["Remove from wishlist"]()
        self.assertEqual(self.library.wishlist(), []);self.assertEqual(app.focus, "wishlist:database")
        app.texts = [];app.targets = []
        SecondaryScreens._collections(app)
        self.assertIn("Nothing on your wishlist yet", app.texts);self.assertIn("wishlist:database", app.targets)


if __name__ == "__main__":
    unittest.main()
