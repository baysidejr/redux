"""One button for discs: Play installs a disc the first time and plays the installed copy after; the disc must be in."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock

from redux.console_ui import ConsoleApp
from redux.disc_identity import DiscIdentity
from redux.library import Library
from redux.now_playing import DiscSnapshot


def disc(fingerprint="a" * 64, title="World Series Baseball 2", system="saturn", drive="G:"):
    identity = DiscIdentity(drive, title, system, "MK-81113", "Saturn CD", "header", (), {"area_symbols": "U"}, fingerprint)
    return DiscSnapshot(drive, title, system, "MK-81113", fingerprint, identity, False, {"year": 1996})


class DiscPlayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = Library(self.root / "library")
        app = self.app = object.__new__(ConsoleApp)
        app.busy = False;app.embedded = None;app.overlay = None;app.all_games = [];app.selected_id = None;app.screen = "home";app.focus = "hero:play"
        app.playback = SimpleNamespace(active=Mock(return_value=[]))
        app.library = self.library;app.preferences = {};app.cancelled = threading.Event();app.catalogue = None
        app.task = Mock();app.notice = Mock();app.game_menu = Mock();app.refresh = Mock();app.schedule_draw = Mock()
        app.status = SimpleNamespace(set=Mock());app.events = SimpleNamespace(put=Mock())
        app.menu = Mock(side_effect=lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items, "message": message}))
        app.install_destination = lambda: None
        app.discs = SimpleNamespace(active=None)

    def installed(self, title="World Series Baseball 2", system="saturn", fingerprint=None, storage="managed"):
        path = self.root / f"{title}.chd";path.write_bytes(b"MComprHD" + bytes(64))
        game, _ = self.library.import_game(path, system=system, linked=(storage == "linked"), title=title)
        if fingerprint:
            game = self.library.set_disc_fingerprint(game["id"], fingerprint)
        return game

    def test_play_on_an_uninstalled_disc_installs_it_and_then_plays(self):
        self.app.discs.active = disc()
        self.assertIsNone(self.app.installed_disc_game(self.app.discs.active))
        self.app.show_stage = Mock();self.app.clear_stage = Mock()
        self.app.play_disc()
        self.app.task.assert_called_once()
        self.assertIn("starts when the copy is done", self.app.task.call_args.args[2])
        self.app.show_stage.assert_called_once_with("saturn", "World Series Baseball 2")
        # The install's completion hands straight to Play with the installed game.
        done = self.app.task.call_args.args[1]
        game = self.installed()
        self.app.play = Mock()
        done(dict(game))
        self.app.play.assert_called_once();self.assertEqual(self.app.play.call_args.args[0]["id"], game["id"]);self.assertEqual(self.app.selected_id, game["id"])
        self.app.clear_stage.assert_not_called()
        done(None)
        self.app.clear_stage.assert_called_once()

    def test_play_on_an_installed_disc_starts_it_without_reading_anything(self):
        game = self.installed(fingerprint="a" * 64)
        self.app.discs.active = disc()
        self.assertEqual(self.app.installed_disc_game(self.app.discs.active)["id"], game["id"])
        self.app.play = Mock()
        self.app.play_disc()
        self.app.task.assert_not_called();self.app.play.assert_called_once();self.assertEqual(self.app.selected_id, game["id"])

    def test_an_install_from_before_fingerprints_is_recognised_by_title_and_remembered(self):
        game = self.installed()
        self.assertEqual(game["disc_fingerprint"], "")
        self.app.discs.active = disc(fingerprint="b" * 64)
        found = self.app.installed_disc_game(self.app.discs.active)
        self.assertEqual(found["id"], game["id"]);self.assertEqual(self.library.game(game["id"])["disc_fingerprint"], "b" * 64)
        self.app.discs.active = disc(fingerprint="c" * 64, title="A Different Game")
        self.assertIsNone(self.app.installed_disc_game(self.app.discs.active))
        # A game on a card is never claimed by a disc.
        card = self.installed(title="Card Copy", storage="linked")
        self.app.discs.active = disc(fingerprint="d" * 64, title="Card Copy")
        self.assertIsNone(self.app.installed_disc_game(self.app.discs.active));self.assertEqual(self.library.game(card["id"])["disc_fingerprint"], "")

    def test_the_options_menu_leads_with_play_and_says_whether_the_disc_is_installed(self):
        self.app.discs.active = disc()
        self.app.inserted_disc_options()
        self.assertEqual([label for label, _ in self.app.overlay["items"]], ["Play", "Game information", "Configure playback", "Back"])
        self.assertIn("Not installed", self.app.overlay["message"])
        self.installed(fingerprint="a" * 64);self.app.all_games = [1]
        self.app.inserted_disc_options()
        self.assertIn("Installed • Play starts it", self.app.overlay["message"])

    def test_a_disc_game_plays_only_with_its_disc_in_the_drive(self):
        game = dict(self.installed(fingerprint="a" * 64), available=True)
        self.assertEqual(self.app.disc_requirement(game), "Put the World Series Baseball 2 disc in the drive to play it.")
        self.app.discs.active = disc(fingerprint="z" * 64, title="Another Disc")
        self.assertIn("The disc in the drive is Another Disc", self.app.disc_requirement(game))
        self.app.discs.active = disc()
        self.assertIsNone(self.app.disc_requirement(game))
        # Play itself stops with a notice and starts nothing when the disc is missing.
        self.app.discs.active = None;self.app.screen = "details";self.app.rows = [game]
        self.app._game_context = lambda: game
        self.app.play()
        self.app.notice.assert_called_once();self.assertEqual(self.app.notice.call_args.args[0], "Insert the disc");self.app.task.assert_not_called()

    def test_cards_pc_games_and_older_managed_discs_follow_the_rule_as_written(self):
        card = dict(self.installed(title="Card Game", storage="linked"), available=True)
        self.assertIsNone(self.app.disc_requirement(card), "a game on a card never needs a disc")
        self.assertIsNone(self.app.disc_requirement({"system": "pc", "storage": "linked", "title": "Doom", "available": True}))
        self.assertIsNone(self.app.disc_requirement({"system": "nes", "storage": "managed", "title": "Mario", "available": True}), "cartridge systems never need a disc")
        legacy = dict(self.installed(title="Old Install"), available=True)
        self.assertEqual(self.app.disc_requirement(legacy), "Put the Old Install disc in the drive to play it.")
        self.app.discs.active = disc(fingerprint="e" * 64, title="OLD INSTALL")
        self.assertIsNone(self.app.disc_requirement(legacy));self.assertEqual(self.library.game(legacy["id"])["disc_fingerprint"], "e" * 64)

    def test_busy_or_running_game_never_starts_a_disc(self):
        self.app.discs.active = disc()
        self.app.busy = True;self.app.play_disc();self.app.task.assert_not_called()
        self.app.busy = False;self.app.embedded = object();self.app.play_disc();self.app.game_menu.assert_called_once();self.app.task.assert_not_called()

    def test_the_firmware_prompt_still_guards_the_one_play_path(self):
        game = dict(self.installed(fingerprint="a" * 64), available=True, region="U")
        self.app.firmware_directory = lambda: self.root / "bios";self.app.choose_firmware = lambda: None;self.app.copy_firmware_menu = lambda: None
        ConsoleApp._start_integrated_game(self.app, game, self.root / "core.dll")
        self.assertEqual(self.app.overlay["title"], "Saturn firmware needed");self.assertIn("mpr-17933.bin", self.app.overlay["message"]);self.app.task.assert_not_called()
        (self.root / "bios").mkdir();(self.root / "bios" / "mpr-17933.bin").write_bytes(b"x")
        self.app.overlay = None
        ConsoleApp._start_integrated_game(self.app, game, self.root / "core.dll")
        self.assertIsNone(self.app.overlay);self.app.task.assert_called_once()

    def test_buffered_playback_is_gone(self):
        import importlib
        self.assertFalse(hasattr(ConsoleApp, "play_inserted_disc"));self.assertFalse(hasattr(ConsoleApp, "_install_buffered_game"))
        self.assertFalse(hasattr(importlib.import_module("redux.disc_session"), "DiscEmbeddedGame"))


if __name__ == "__main__":
    unittest.main()
