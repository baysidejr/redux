"""The shipped database fills in collection games and inserted discs; the scraper is gone."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import tempfile
import unittest

from redux.console_layout import ReferenceLayout
from redux.console_ui import ConsoleApp
from redux.gamedb import GameDatabase
from redux.gamedb_link import metadata_fields, link_game, backfill, disc_enrichment, database_game
from redux.importing import import_identified
from redux.library import Library


class DatabaseFactsTests(unittest.TestCase):
    def setUp(self):
        self.database = GameDatabase()
        self.sonic = database_game(self.database, "sega_cd", title="Sonic CD")
        self.assertIsNotNone(self.sonic)

    def test_metadata_fields_come_from_the_entry(self):
        fields = metadata_fields(self.sonic)
        self.assertEqual((fields["year"], fields["publisher"], fields["developer"], fields["genre"]),
                         (1993, "Sega of America, Inc.", "Sonic Team", "Platform"))
        self.assertNotIn("region", fields, "region stays with the disc header and file name")
        self.assertTrue(fields["description"].startswith("Sonic the Hedgehog"))
        self.assertEqual(metadata_fields({"title": "Nothing"}), {})
        self.assertIsNone(database_game(self.database, "nes", title="Sonic CD"), "cartridge systems have no catalog")
        self.assertIsNone(database_game(self.database, "sega_cd"))

    def test_disc_enrichment_gives_facts_and_bundled_art(self):
        facts, art = disc_enrichment(self.database, SimpleNamespace(system="sega_cd", game_id="4407", title="Sonic CD"))
        self.assertEqual(facts["year"], 1993)
        self.assertTrue(art["cover_path"].endswith("boxart.jpg"));self.assertTrue(Path(art["cover_path"]).is_file())
        self.assertTrue(art["hero_path"].endswith("hero.jpg"))
        self.assertEqual(disc_enrichment(self.database, SimpleNamespace(system="sega_cd", game_id=None, title="Unknown Disc 77")), ({}, {}))


class CollectionLinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name);self.library = Library(self.base / "library")

    def media(self, name):
        path = self.base / name;path.write_bytes(b"MComprHD" + bytes(64));return path

    def test_an_imported_game_takes_the_database_facts_it_lacks(self):
        game, created, _ = import_identified(self.library, self.media("Sonic CD (USA).chd"), system="sega_cd", linked=True)
        self.assertTrue(created)
        self.assertEqual((game["year"], game["publisher"], game["genre"]), (1993, "Sega of America, Inc.", "Platform"))
        self.assertTrue(game["description"].startswith("Sonic the Hedgehog"))
        # The user's own values are never overwritten.
        self.library.update_metadata(game["id"], publisher="My label", description="")
        link_game(self.library, self.library.game(game["id"]), GameDatabase())
        again = self.library.game(game["id"])
        self.assertEqual(again["publisher"], "My label");self.assertTrue(again["description"].startswith("Sonic"))

    def test_backfill_fills_older_games_once(self):
        game = self.library.import_game(self.media("Sonic CD.chd"), system="sega_cd", linked=True)[0]
        other = self.library.import_game(self.media("Mystery.chd"), system="sega_cd", linked=True)[0]
        self.assertEqual(game["description"], "")
        self.assertEqual(backfill(self.library, GameDatabase()), 1)
        self.assertTrue(self.library.game(game["id"])["description"]);self.assertEqual(self.library.game(other["id"])["description"], "")
        self.assertEqual(backfill(self.library, GameDatabase()), 0)

    def test_screens_fall_back_to_database_art_when_a_game_has_none(self):
        game = self.library.import_game(self.media("Sonic CD.chd"), system="sega_cd", linked=True)[0]
        app = object.__new__(ConsoleApp);app.library = self.library
        self.assertTrue(ReferenceLayout._game_artwork(app, game, "cover").endswith("boxart.jpg"))
        self.assertTrue(ReferenceLayout._game_artwork(app, game, "hero").endswith("hero.jpg"))
        self.assertTrue(ReferenceLayout._game_artwork(app, game, "screenshot").endswith("gameimage.png"))
        self.assertIsNone(ReferenceLayout._game_artwork(app, game, "tile"))
        unknown = self.library.import_game(self.media("Mystery.chd"), system="sega_cd", linked=True)[0]
        self.assertIsNone(ReferenceLayout._game_artwork(app, unknown, "cover"))


class ScraperGoneTests(unittest.TestCase):
    def test_scraper_modules_and_menus_are_gone(self):
        for name in ("redux.console_scraper", "redux.scraping", "redux.scraper_providers", "redux.scraper_settings", "redux.disc_artwork"):
            self.assertIsNone(importlib.util.find_spec(name), name)
        self.assertFalse(hasattr(ConsoleApp, "scrape_selected_game"));self.assertTrue(hasattr(ConsoleApp, "game_artwork_menu"))
        self.assertTrue(hasattr(ConsoleApp, "_credential_keyboard"), "the full keyboard stays for PC arguments")

    def test_start_up_removes_the_old_credential_file_and_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "library";(root / "settings").mkdir(parents=True);(root / "cache" / "disc_artwork").mkdir(parents=True)
            (root / "settings" / "scraper.json").write_text("{}")
            user = Path(temporary) / "user";user.mkdir();(user / "scraper-credentials.dpapi").write_bytes(b"x")
            app = object.__new__(ConsoleApp);app.library = SimpleNamespace(root=root)
            import redux.windows_paths as paths
            original = paths.redux_user_data
            paths.redux_user_data = lambda: user
            try:
                app._retire_scraper_files()
            finally:
                paths.redux_user_data = original
            self.assertFalse((user / "scraper-credentials.dpapi").exists());self.assertFalse((root / "settings" / "scraper.json").exists())
            self.assertFalse((root / "cache" / "disc_artwork").exists())


if __name__ == "__main__":
    unittest.main()
