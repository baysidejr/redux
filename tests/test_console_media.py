"""Optical presentation integration without Tk, native drives, or network IO."""
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from redux.console_media import catalogue_disc_match
from redux.console_ui import ConsoleApp
from redux.disc_identity import DiscIdentity
from redux.media_watch import MediaEvent
from redux.metadata import Catalogue, REGIONS, normalize_title
from redux.now_playing import DiscRegistry


def identity(drive="G:", fingerprint="saturn-one", title="World Series BASEBALL 2", *, virtual=False):
    return DiscIdentity(drive, title, "saturn", "MK-81113", "Saturn CD", "header", (),
        {"area_symbols": "U", "region": "U"}, fingerprint,
        Path("C:/fixture.iso") if virtual else None, virtual)


def event(kind, medium, *, at=100.0):
    return MediaEvent(kind, medium.drive, None if kind == "removed" else medium,
        medium.fingerprint, medium.is_virtual, "2026-11-08T00:00:00Z", at)


class FakeWatcher:
    def __init__(self):
        self.events = []

    def poll_events(self):
        result, self.events = self.events, []
        return result


class ConsoleMediaTests(unittest.TestCase):
    def test_unknown_or_multiregion_saturn_header_cannot_acquire_a_single_region_catalogue_match(self):
        from dataclasses import replace
        catalogue=SimpleNamespace(match=Mock(return_value={"title":"USA-only match","region":"North America"}))
        for region in ("", "UJ", "unknown"):
            medium=replace(identity(),details={"area_symbols":region})
            self.assertIsNone(catalogue_disc_match(catalogue,medium))
        catalogue.match.assert_not_called()

    def bare(self, screen="library"):
        app = object.__new__(ConsoleApp)
        app.busy = False
        app.embedded = None
        app.playback = SimpleNamespace(active=lambda: [])
        app.overlay = None
        app.screen = screen
        app.focus = "game:7"
        app.selected_id = 7
        app.rows = [{"id": 7, "title": "Diablo", "system": "psx", "available": True}]
        app.all_games = list(app.rows)
        app.library = SimpleNamespace(root=Path("C:/fixture-library"),
            game=lambda number: next(game for game in app.all_games if game["id"] == number),
            toggle_favorite=Mock())
        app.discs = DiscRegistry()
        app.media_watcher = FakeWatcher()
        app.media_results = Queue()
        app.media_requests = Queue()
        app.media_versions = {}
        app.catalogue = None
        app.status = SimpleNamespace(set=Mock())
        app.schedule_draw = Mock()
        app.notice = Mock()
        app.targets = []
        app.callbacks = {}
        app.keyboard_visible = False
        return app

    def deliver(self, app, *events):
        app.media_watcher.events.extend(events)
        app._poll_media()

    def test_insert_changes_only_disc_presentation_and_queues_enrichment(self):
        app = self.bare()
        app.import_files = Mock(side_effect=AssertionError("Detection must not import"))
        app.play = Mock(side_effect=AssertionError("Detection must not launch"))
        app.navigate = Mock(side_effect=AssertionError("Detection must not navigate"))
        app.refresh = Mock(side_effect=AssertionError("Detection must not change library selection"))
        before = (app.screen, app.focus, app.selected_id, list(app.rows), list(app.all_games))
        medium = identity()
        self.deliver(app, event("inserted", medium))
        self.assertEqual((app.screen, app.focus, app.selected_id, app.rows, app.all_games), before)
        self.assertEqual(app.inserted_disc().game_id, "MK-81113")
        version, queued, catalogue, cache = app.media_requests.get_nowait()
        self.assertEqual((version, queued, catalogue), (1, medium, None))
        self.assertEqual(cache, app.library.root / "cache" / "disc_artwork")
        app.import_files.assert_not_called()
        app.play.assert_not_called()
        app.navigate.assert_not_called()
        app.refresh.assert_not_called()

    def test_background_insertion_keeps_existing_overlay_and_task_status(self):
        for mode in ("busy", "embedded", "overlay"):
            with self.subTest(mode=mode):
                app = self.bare("details")
                setattr(app, mode, True if mode == "busy" else object())
                before = (app.screen, app.focus, app.selected_id, app.overlay)
                self.deliver(app, event("inserted", identity()))
                self.assertEqual((app.screen, app.focus, app.selected_id, app.overlay), before)
                app.status.set.assert_not_called()
                self.assertEqual(app.inserted_disc().drive, "G:")

    def test_removing_virtual_f_does_not_replace_or_clear_physical_g(self):
        app = self.bare("home")
        virtual = identity("F:", "diablo", "Diablo", virtual=True)
        physical = identity()
        self.deliver(app, event("inserted", virtual), event("inserted", physical, at=101))
        self.assertEqual(app.inserted_disc().drive, "G:")
        app.schedule_draw.reset_mock()
        app.status.set.reset_mock()
        self.deliver(app, event("removed", virtual, at=102))
        self.assertEqual(app.inserted_disc().drive, "G:")
        self.assertEqual(app.inserted_disc().fingerprint, physical.fingerprint)
        app.schedule_draw.assert_not_called()
        app.status.set.assert_not_called()

    def test_late_enrichment_version_cannot_overwrite_replacement(self):
        app = self.bare()
        first = identity()
        second = identity(fingerprint="saturn-two", title="Replacement game")
        self.deliver(app, event("inserted", first))
        self.deliver(app, event("changed", second, at=101))
        app.media_results.put((1, first, {"title": "OLD METADATA"}, {"hero_path": "old.png"}))
        app._poll_media()
        self.assertEqual(app.inserted_disc().title, "Replacement game")
        self.assertIsNone(app.inserted_disc().hero_path)
        app.media_results.put((2, second, {"title": "Verified new title"}, {"hero_path": "new.png"}))
        app._poll_media()
        self.assertEqual(app.inserted_disc().title, "Verified new title")
        self.assertEqual(app.inserted_disc().hero_path, "new.png")

    def test_matching_version_with_wrong_fingerprint_is_still_rejected(self):
        app = self.bare()
        old = identity()
        current = identity(fingerprint="replacement", title="Current game")
        self.deliver(app, event("inserted", current))
        app.media_results.put((1, old, {"title": "Wrong game"}, {"cover_path": "wrong.png"}))
        app._poll_media()
        self.assertEqual(app.inserted_disc().title, "Current game")
        self.assertIsNone(app.inserted_disc().cover_path)

    def test_late_enrichment_cannot_restore_removed_disc_or_prior_insertion(self):
        app = self.bare()
        medium = identity()
        self.deliver(app, event("inserted", medium), event("removed", medium, at=101))
        app.media_results.put((1, medium, {"title": "Removed disc"}, {"hero_path": "old.png"}))
        app._poll_media()
        self.assertIsNone(app.inserted_disc())
        self.deliver(app, event("inserted", medium, at=102))
        app.media_results.put((1, medium, {"title": "Prior insertion"}, {"hero_path": "old.png"}))
        app._poll_media()
        self.assertEqual(app.inserted_disc().title, medium.title)
        self.assertIsNone(app.inserted_disc().hero_path)

    def test_controller_hero_shortcuts_target_disc_and_never_hidden_library_game(self):
        app = self.bare("home")
        self.deliver(app, event("inserted", identity()))
        app.inserted_disc_information = Mock()
        app.inserted_disc_options = Mock()
        app.open_game = Mock()
        app.play = Mock(side_effect=AssertionError("Hidden Diablo must not launch"))
        for focus in ("hero:play", "hero:details"):
            app.focus = focus
            self.assertIsNone(app._game_context())
            app.input_action("y")
            app.input_action("start")
            app.input_action("x")
        self.assertEqual(app.inserted_disc_information.call_count, 2)
        self.assertEqual(app.inserted_disc_options.call_count, 2)
        app.open_game.assert_not_called()
        app.play.assert_not_called()
        app.library.toggle_favorite.assert_not_called()
        self.assertEqual(app.selected_id, 7)

    def test_insertion_dispatches_current_hero_before_stale_library_callbacks(self):
        app = self.bare("home")
        stale = Mock(side_effect=AssertionError("Stale Diablo callback ran before redraw"))
        app.callbacks = {"hero:play": stale, "hero:details": stale}
        self.deliver(app, event("inserted", identity()))
        app.inserted_disc_options = Mock()
        app.inserted_disc_information = Mock()
        app.focus = "hero:play"
        app.activate()
        app.focus = "hero:details"
        app.activate()
        app.inserted_disc_options.assert_called_once()
        app.inserted_disc_information.assert_called_once()
        stale.assert_not_called()

    def test_controller_library_context_targets_visible_selection_and_blocks_hidden_game(self):
        app = self.bare()
        self.deliver(app, event("inserted", identity()))
        app.all_games.append({"id": 8, "title": "Visible game", "system": "saturn", "available": True})
        app.rows = [app.all_games[-1]]
        app.focus = "game:8"
        app.open_game = Mock()
        # Old selected Diablo is excluded by the current visible library rows.
        app.input_action("y")
        with patch("redux.embedded.find_core", side_effect=AssertionError("Hidden game launched")):
            app.input_action("start")
        app.open_game.assert_not_called()
        app.selected_id = 8
        app.input_action("y")
        app.open_game.assert_called_once_with(8, tab="manage")
        played = []
        app.play = lambda: played.append(app._game_context()["id"])
        app.input_action("start")
        self.assertEqual(played, [8])

    def test_home_layout_uses_inserted_disc_art_and_disc_callbacks(self):
        app = self.bare("home")
        self.deliver(app, event("inserted", identity()))
        app.discs.update("G:", "saturn-one", artwork={"hero_path": "saturn-game.png"})
        app.focus = "hero:play"
        app.art = SimpleNamespace(path=lambda system, kind: f"{system}-{kind}.png")
        app.picture = Mock(return_value=False)
        app.rect = Mock()
        app.display = Mock()
        app._top_shade = Mock()
        app._identity = Mock()
        app._badge = Mock(return_value=36)
        app._controller_hint = Mock()
        app._system_tile = Mock()
        app._card = Mock()
        app.fit_text = lambda value, *_: value
        app.inserted_disc_options = Mock()
        app.inserted_disc_information = Mock()
        app.play = Mock(side_effect=AssertionError("Hero must not play Diablo"))
        app._home()
        self.assertEqual(app.picture.call_args_list[0].args[0], "saturn-game.png")
        self.assertTrue(any("WORLD SERIES BASEBALL 2" in str(call) for call in app.display.call_args_list))
        app.callbacks["hero:play"]()
        app.callbacks["hero:details"]()
        app.inserted_disc_options.assert_called_once()
        app.inserted_disc_information.assert_called_once()
        app.play.assert_not_called()

    def test_stale_disc_menu_cannot_acquire_a_replacement_disc(self):
        app = self.bare("home")
        medium = identity()
        self.deliver(app, event("inserted", medium))
        captured = {}
        def menu(title, items, message=""):
            captured.update(items=dict(items))
            app.overlay = {"title": title, "items": items}
        app.menu = menu
        app.install_inserted_disc = Mock()
        app.inserted_disc_options()
        replacement = identity(fingerprint="replacement", title="Another disc")
        self.deliver(app, event("changed", replacement, at=101))
        captured["items"]["Install disc to library"]()
        app.install_inserted_disc.assert_not_called()
        app.notice.assert_called_once()


class DiscCatalogueMatchTests(unittest.TestCase):
    def provider(self, regions=(1, 2, 3), title="World Series Baseball 2"):
        catalogue = object.__new__(Catalogue)
        catalogue.asset_root = None
        rows = [{"title": title, "system": "saturn", "catalogue_region_id": region,
                 "region": REGIONS[region], "_box": "", "_tile": "", "_hero": "", "_screenshots": ""}
                for region in regions]
        catalogue._index = {(9, normalize_title(title)[0]): rows}
        return catalogue

    def test_usa_header_region_selects_exact_usa_catalogue_row(self):
        matched = catalogue_disc_match(self.provider(), identity())
        self.assertEqual(matched["catalogue_region_id"], 1)
        self.assertEqual(matched["region"], "North America")
        self.assertEqual(matched["title"], "World Series Baseball 2")
        self.assertIsNone(catalogue_disc_match(self.provider((2, 3)), identity()))
        self.assertIsNone(catalogue_disc_match(self.provider(title="World Series Baseball 3"), identity()))

    def test_missing_platform_or_catalogue_does_not_guess_metadata(self):
        self.assertIsNone(catalogue_disc_match(None, identity()))
        self.assertIsNone(catalogue_disc_match(self.provider(), SimpleNamespace(system=None)))


if __name__ == "__main__":
    unittest.main()
