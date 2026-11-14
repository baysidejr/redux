"""The preloaded game database: reading it, recognising discs, and the Database pages."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest

from redux.console_ui import ConsoleApp
from redux.console_secondary import SecondaryScreens
from redux.console_gamedb import ConsoleGameDatabase, PAGE_SIZE
from redux.gamedb import GameDatabase, title_key, serial_variants, players_text, database_root, SYSTEM_ORDER
from redux.ui_model import ScreenHistory

SHIPPED = database_root()


def sample_database(root):
    """A two-game Sega CD database with real-looking entries and one art file."""
    system = Path(root) / "sega_cd"
    (system / "art" / "sonic-cd").mkdir(parents=True)
    (system / "art" / "sonic-cd" / "boxart.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    games = [
        {"id": "sonic-cd", "title": "Sonic CD", "system": "sega_cd", "year": 1993, "developer": "Sonic Team",
         "publisher": "Sega", "genres": ["Platform"], "players": 1, "rating": "E - Everyone", "description": "Time travel.",
         "releases": [{"name": "Sonic CD (USA)", "region": "USA", "serials": []}, {"name": "Sonic CD (Europe)", "region": "Europe", "serials": ["4407"]}],
         "art": {"boxart": "art/sonic-cd/boxart.jpg", "hero": "art/sonic-cd/hero.jpg"}, "sources": {}},
        {"id": "hook", "title": "Hook", "system": "sega_cd", "year": 1992,
         "releases": [{"name": "Hook (USA)", "region": "USA", "serials": ["T-93175"]}], "art": {}, "sources": {}},
        {"id": "3-ninjas-kick-back", "title": "3 Ninjas Kick Back", "system": "sega_cd", "year": 1994,
         "releases": [{"name": "3 Ninjas Kick Back (USA)", "region": "USA", "serials": ["T-93175"]}], "art": {}, "sources": {}},
        {"id": "fahrenheit-sega-cd-32x", "title": "Fahrenheit (Sega CD 32X)", "system": "sega_cd",
         "releases": [{"name": "Fahrenheit (USA) (Sega CD 32X)", "region": "USA", "serials": ["4438"]}], "art": {}, "sources": {}},
        {"id": "fahrenheit", "title": "Fahrenheit", "system": "sega_cd",
         "releases": [{"name": "Fahrenheit (USA)", "region": "USA", "serials": ["4438"]}, {"name": "Fahrenheit (Europe)", "region": "Europe", "serials": ["4438-50"]}], "art": {}, "sources": {}},
    ]
    (system / "games.json").write_text(json.dumps({"system": "sega_cd", "label": "Sega CD", "games": games}), encoding="utf-8")
    return Path(root)


class KeyTests(unittest.TestCase):
    def test_title_key_folds_the_usual_differences(self):
        self.assertEqual(title_key("The Adventures of Batman & Robin"), title_key("Adventures of Batman and Robin, The"))
        self.assertEqual(title_key("Lunar - Eternal Blue (USA)"), title_key("Lunar: Eternal Blue"))
        self.assertEqual(title_key("Shadow of the Beast II"), title_key("Shadow of the Beast 2"))

    def test_serial_variants_cover_region_suffixes_and_hyphens(self):
        self.assertEqual(serial_variants("T-93175-50"), ["T-93175-50", "T-93175", "T9317550", "T93175"])
        self.assertEqual(serial_variants(" 4407 "), ["4407"])
        self.assertEqual(serial_variants(None), [])

    def test_players_text(self):
        self.assertEqual((players_text(1), players_text(2), players_text(0), players_text("x")), ("1 player", "2 players", None, None))


class SampleDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = GameDatabase(sample_database(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_systems_report_every_planned_system_with_counts(self):
        systems = self.db.systems()
        self.assertEqual([s["system"] for s in systems], list(SYSTEM_ORDER))
        sega = systems[0]
        self.assertEqual((sega["available"], sega["games"], sega["boxart"], sega["hero"], sega["descriptions"]), (True, 5, 1, 1, 1))
        self.assertFalse(systems[1]["available"]);self.assertEqual(systems[1]["games"], 0)

    def test_games_sort_by_title_and_art_only_names_files_that_exist(self):
        titles = [g["title"] for g in self.db.games("sega_cd")]
        self.assertEqual(titles[:2], ["3 Ninjas Kick Back", "Fahrenheit"])
        sonic = self.db.game("sega_cd", "sonic-cd")
        self.assertTrue(self.db.art(sonic, "boxart").is_file())
        self.assertIsNone(self.db.art(sonic, "hero"), "hero.jpg is listed but missing on disk")
        self.assertIsNone(self.db.art(sonic, "gameimage"))

    def test_a_disc_is_found_by_serial_and_ties_are_settled_by_title(self):
        self.assertEqual(self.db.find("sega_cd", serial="4407")["id"], "sonic-cd")
        self.assertEqual(self.db.find("sega_cd", serial="4407-00", title="SONIC THE HEDGEHOG CD")["id"], "sonic-cd")
        self.assertEqual(self.db.find("sega_cd", serial="T-93175", title="HOOK")["id"], "hook")
        self.assertEqual(self.db.find("sega_cd", serial="T93175", title="3 NINJAS KICK BACK")["id"], "3-ninjas-kick-back")
        # Same serial, unhelpful title: the plain edition, not the 32X one.
        self.assertEqual(self.db.find("sega_cd", serial="4438", title="FAHRENHEIT")["id"], "fahrenheit")
        self.assertEqual(self.db.find("sega_cd", serial="4438-50")["id"], "fahrenheit")
        # No serial on record: the header title alone.
        self.assertEqual(self.db.find("sega_cd", serial="", title="Sonic CD")["id"], "sonic-cd")
        self.assertIsNone(self.db.find("sega_cd", serial="ZZZ", title="Nothing Like This"))
        self.assertIsNone(self.db.find("psx", serial="SLUS-00001"))

    def test_facts_and_releases_read_well(self):
        sonic = self.db.game("sega_cd", "sonic-cd")
        self.assertEqual(GameDatabase.facts(sonic), [("Released", "1993"), ("Developer", "Sonic Team"), ("Publisher", "Sega"),
                                                      ("Genre", "Platform"), ("Players", "1 player"), ("Rating", "E - Everyone")])
        self.assertEqual(GameDatabase.releases_text(sonic), "USA · Europe 4407")
        self.assertEqual(GameDatabase.facts(self.db.game("sega_cd", "fahrenheit")), [])


@unittest.skipUnless((SHIPPED / "sega_cd" / "games.json").is_file(), "the shipped Sega CD database is not present")
class ShippedDatabaseTests(unittest.TestCase):
    def test_the_sega_cd_database_is_consistent(self):
        db = GameDatabase()
        games = db.games("sega_cd")
        self.assertGreater(len(games), 150)
        ids = [g["id"] for g in games]
        self.assertEqual(len(ids), len(set(ids)), "game ids repeat")
        for game in games:
            self.assertEqual(game["system"], "sega_cd")
            for role, relative in (game.get("art") or {}).items():
                self.assertTrue((db.root / "sega_cd" / relative).is_file(), f"{game['id']} lists missing {role}")
        self.assertEqual(db.find("sega_cd", serial="4407", title="SONIC THE HEDGEHOG-CD")["id"], "sonic-cd")
        self.assertEqual(db.find("sega_cd", serial="T-95035", title="SNATCHER")["id"], "snatcher")
        self.assertEqual(db.find("sega_cd", serial="T-93175", title="HOOK")["id"], "hook")
        self.assertTrue(db.art(db.game("sega_cd", "snatcher"), "boxart").is_file())

    def test_every_planned_system_is_listed_even_when_unbuilt(self):
        systems = GameDatabase().systems()
        self.assertEqual(systems[0]["label"], "Sega CD");self.assertTrue(systems[0]["available"])
        self.assertEqual(len(systems), len(SYSTEM_ORDER))


class DatabasePagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = sample_database(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def app(self, screen="settings"):
        app = object.__new__(ConsoleApp)
        app._game_database = GameDatabase(self.root)
        app.screen = screen;app.settings_menu = False;app.settings_section = "database";app.focus = "setting:database"
        app.overlay = None;app.targets = [];app.callbacks = {};app.all_games = [];app.pages = {}
        app.history = ScreenHistory();app.view = "all";app.collection_id = None;app.system_filter = None
        app.search_text = "";app.facet_filters = {};app.facet_tab = "system";app.sort = "title";app.details_tab = "play"
        app.selected_id = None;app.keyboard_visible = True;app.rows = [];app.embedded = None;app.busy = False
        app.texts = [];app.calls = [];app.pictures = []
        app.display = app.text = lambda x, y, value, *a, **k: app.texts.append(str(value))
        app.panel = app.rect = app.icon = lambda *a, **k: None
        app._top_shade = lambda *a, **k: None;app._badge = lambda system, x, y: 40
        app.picture = lambda path, *a, **k: (app.pictures.append(path), bool(path))[1]
        app.fit_text = lambda value, *a, **k: str(value)
        app.art = SimpleNamespace(path=lambda system, kind="hero", custom=None: None)
        app.target = lambda key, bounds, callback, label="": (app.targets.append(key), app.callbacks.__setitem__(key, callback))
        app.button_canvas = lambda key, label, x, y, w, h, callback, *a, **k: app.target(key, (x, y, x + w, y + h), callback, label)
        app.refresh = lambda: app.calls.append("refresh");app.schedule_draw = lambda: None
        app.notice = lambda title, message: app.calls.append(("notice", title))
        app._empty = lambda message: app.texts.append(message)
        return app

    def test_the_system_menu_lists_database_after_storage(self):
        sections = [item[0] for item in SecondaryScreens.SYSTEM_ITEMS]
        self.assertEqual(sections[:3], ["home", "storage", "database"])

    def test_the_database_section_offers_built_systems_only(self):
        app = self.app()
        SecondaryScreens._settings(app)
        self.assertEqual(app.targets, ["gamedb:system:sega_cd"])
        self.assertIn("DATABASE", app.texts);self.assertIn("5 games · 1 covers · 1 banners", app.texts)
        self.assertEqual(app.texts.count("Not built yet"), len(SYSTEM_ORDER) - 1)
        app.callbacks["gamedb:system:sega_cd"]()
        self.assertEqual((app.screen, app.gamedb_system, app.focus), ("gamedb", "sega_cd", "gamedb:0"))

    def test_an_unbuilt_system_is_refused_with_a_notice(self):
        app = self.app()
        app.open_gamedb("psx")
        self.assertEqual(app.calls[-1], ("notice", "Not in the database yet"));self.assertEqual(app.screen, "settings")

    def test_the_system_list_pages_and_opens_a_game(self):
        app = self.app("gamedb");app.gamedb_system = "sega_cd";app.focus = "gamedb:0"
        ConsoleGameDatabase._gamedb(app)
        self.assertEqual(app.targets, [f"gamedb:{i}" for i in range(5)])
        self.assertEqual(app.texts[:2], ["SEGA CD", "5 games in the database"])
        self.assertIn("3 Ninjas Kick Back", app.texts)
        app.callbacks["gamedb:4"]()
        self.assertEqual((app.screen, app.gamedb_game_id, app.focus), ("gamedb_game", "sonic-cd", "gamedb:back"))
        # Many games: a pager appears and the focus lands on the page's first row.
        app._game_database.games = lambda system: [dict(id=f"g{i}", title=f"Game {i}", system="sega_cd", art={}) for i in range(PAGE_SIZE + 3)]
        app.targets = [];app.callbacks = {};app.texts = []
        ConsoleGameDatabase._gamedb(app)
        self.assertIn("gamedb:next", app.targets);self.assertNotIn("gamedb:prev", app.targets)
        self.assertEqual(len([t for t in app.targets if t[7:].isdigit()]), PAGE_SIZE)
        app.callbacks["gamedb:next"]()
        self.assertEqual(app.pages["gamedb"], 1)

    def test_the_game_page_shows_facts_description_releases_and_ownership(self):
        app = self.app("gamedb_game");app.gamedb_system = "sega_cd";app.gamedb_game_id = "sonic-cd";app.focus = "gamedb:back"
        ConsoleGameDatabase._gamedb_game(app)
        self.assertIn("Sonic CD", app.texts);self.assertIn("Time travel.", app.texts)
        self.assertIn("Developer: Sonic Team   ·   Publisher: Sega   ·   Genre: Platform   ·   Players: 1 player   ·   Rating: E - Everyone", app.texts)
        self.assertIn("Releases: USA · Europe 4407", app.texts)
        self.assertEqual(app.targets, ["gamedb:back"])
        self.assertTrue(any(p and Path(p).name == "boxart.jpg" for p in app.pictures))
        app.all_games = [dict(id=7, system="sega_cd", title="Sonic CD (USA)")]
        app.targets = [];app.callbacks = {};app.texts = []
        ConsoleGameDatabase._gamedb_game(app)
        self.assertIn("IN MY COLLECTION", app.texts);self.assertIn("gamedb:open", app.targets)
        app.callbacks["gamedb:open"]()
        self.assertEqual((app.selected_id, app.screen), (7, "details"))

    def test_back_from_a_game_returns_to_its_list_and_then_to_the_database_page(self):
        app = self.app()
        SecondaryScreens._settings(app)
        app.callbacks["gamedb:system:sega_cd"]()
        app.open_gamedb_game("hook")
        app.back()
        self.assertEqual((app.screen, app.focus), ("gamedb", "gamedb:0"))
        app.back()
        self.assertEqual((app.screen, app.settings_menu, app.settings_section), ("settings", False, "database"))


if __name__ == "__main__":
    unittest.main()
