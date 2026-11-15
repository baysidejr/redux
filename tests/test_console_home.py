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
        app.art = SimpleNamespace(path=lambda system, kind="hero", custom=None: f"{system}-{kind}.png", bundled=lambda *parts: None)
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
        self.assertEqual(app.home_focused_system(), "nes", "the stage starts on the first console you own")
        self.assertIn("nes-hero.png", app.pictures);self.assertIn("NES", app.texts);self.assertIn("2 games  ·  A to open", app.texts)
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

    def test_a_console_tile_zooms_into_the_stage_and_then_opens_its_games(self):
        app = self.app()
        ReferenceLayout._home(app)
        app.callbacks["home-system:snes"]()
        self.assertEqual(app.stage_animation["system"], "snes");self.assertEqual(app.stage_animation["kind"], "zoom")
        self.assertIsNotNone(app.stage_animation["origin"])
        delay, land = [c for c in app.calls if isinstance(c, tuple) and c[0] == "after"][-1][1:]
        self.assertGreater(delay, 300)
        land()
        app.navigate.assert_called_once_with("library", system="snes", view="all", reset_scope=True);self.assertIsNone(app.stage_animation)

    def test_a_game_tile_plays_instead_of_opening_a_page(self):
        app = self.app();app.play = Mock()
        ConsoleApp._play_game(app, 42)
        self.assertEqual(app.selected_id, 42);app.play.assert_called_once()
        self.assertFalse(hasattr(ConsoleApp, "_details_navigation"));self.assertFalse(hasattr(ConsoleApp, "collection_menu"))

    def test_the_stage_shows_the_disc_and_then_the_loading_animation(self):
        app = self.app();app.discs.active = disc()
        ReferenceLayout._home(app)
        self.assertIn("DISC INSERTED", app.texts);self.assertIn("ETERNAL CHAMPIONS", app.texts);self.assertIn("Not installed  ·  A to install and play", app.texts)
        self.assertIn("sega_cd-hero.png", app.pictures);self.assertEqual(app.callbacks["hero:play"], app.play_disc)
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
        self.assertEqual(app.home_system, "sega_cd");self.assertEqual(app.stage_animation["origin"], (360 + 226.5, 348 + 176, 213, 149), "the Sega CD tile grows into the stage")
        delay, callback = [c for c in app.calls if isinstance(c, tuple) and c[0] == "after"][-1][1:]
        self.assertIs(callback, app.play_disc)
        # A disc swapped out before the delay elapsed is forgotten.
        ConsoleStage.note_disc_insert(app, event);app.disc_autoplay["at"] -= AUTOPLAY_DELAY + 1
        app.discs.active = disc(fingerprint="b" * 64, title="Another");ConsoleStage._stage_tick(app)
        self.assertIsNone(app.disc_autoplay)

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
