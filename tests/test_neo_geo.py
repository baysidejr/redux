"""Neo Geo AES/MVS cartridges: zipped ROM sets on a game card, named from the shipped table, played by FinalBurn Neo."""
from pathlib import Path
import json
import tempfile
import unittest

from redux import game_cards
from redux.core_profiles import PROFILES
from redux.embedded import HOSTED_DEFAULTS
from redux.library import Library, discover_media, ARCHIVE_EXTENSIONS, MEDIA_EXTENSIONS, SYSTEMS
from redux.neo_geo import game_facts, known_sets


class NeoGeoNamesTests(unittest.TestCase):
    def test_shipped_table_names_common_sets(self):
        self.assertGreater(known_sets(), 250)
        self.assertEqual(game_facts("E:/Redux Games/Neo Geo/MSLUG.zip"), {"title": "Metal Slug - Super Vehicle-001", "publisher": "Nazca"})
        self.assertEqual(game_facts(Path("garou.7z"))["title"], "Garou - Mark of the Wolves")
        self.assertIsNone(game_facts("notagame.zip"))
        # Titles are the English name alone, without set numbers or board codes.
        for stem, title in (("kof98", "The King of Fighters '98 - The Slugfest"), ("wh1", "World Heroes"), ("samsho2", "Samurai Shodown II")):
            self.assertEqual(game_facts(stem + ".zip")["title"], title)

    def test_system_core_and_hosting(self):
        self.assertEqual(SYSTEMS["neo_geo"], "Neo Geo")
        self.assertIn("neo_geo", HOSTED_DEFAULTS)
        profile = PROFILES["neo_geo"]
        self.assertEqual((profile.filename, profile.family), ("fbneo_libretro.dll", "FinalBurn Neo"))
        self.assertEqual(dict(profile.default_options), {"fbneo-neogeo-mode": "AES"})
        self.assertTrue(ARCHIVE_EXTENSIONS <= MEDIA_EXTENSIONS)


class NeoGeoCardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.drive = Path(self.temporary.name)
        self.marker = self.drive / game_cards.MARKER;self.marker.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def rom(self, folder, name, payload=b"PK fake"):
        path = self.marker / folder / name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(payload);return path

    def test_archives_count_only_in_the_neo_geo_folder(self):
        self.rom("Neo Geo", "mslug.zip");self.rom("Neo Geo", "kof98.7z");self.rom("Neo Geo", "Metal Slug.chd")
        self.rom("PlayStation", "Game A.chd");self.rom("PlayStation", "Game A.zip");self.rom("AES", "garou.zip")
        found = game_cards.scan_card(self.marker)
        self.assertEqual([(g.system, g.path.name, g.title) for g in found],
                         [("neo_geo", "garou.zip", "Garou - Mark of the Wolves"), ("neo_geo", "kof98.7z", "The King of Fighters '98 - The Slugfest"),
                          ("neo_geo", "mslug.zip", "Metal Slug - Super Vehicle-001"), ("psx", "Game A.chd", None)])
        self.assertEqual([p.name for p in discover_media(self.marker / "PlayStation")], ["Game A.chd"])
        self.assertEqual([p.name for p in discover_media(self.marker / "Neo Geo", system="neo_geo")], ["kof98.7z", "mslug.zip"])

    def test_console_cartridges_get_a_neo_geo_folder_and_synced_games_take_their_titles(self):
        game_cards.prepare_card(self.drive, "console")
        self.assertTrue((self.marker / "Neo Geo").is_dir());self.assertIn("neogeo.zip", (self.marker / "README.txt").read_text(encoding="utf-8"))
        self.rom("Neo Geo", "mslug.zip");self.rom("Neo Geo", "homebrew.zip")
        with tempfile.TemporaryDirectory() as home:
            library = Library(Path(home) / "library")
            result = game_cards.sync_card(library, self.marker)
            self.assertEqual(len(result.added), 2, result.errors)
            games = {g["title"]: g for g in library.games()}
            self.assertEqual(games["Metal Slug - Super Vehicle-001"]["system"], "neo_geo")
            self.assertEqual(games["Metal Slug - Super Vehicle-001"]["publisher"], "Nazca")
            self.assertIn("homebrew", games, "an unknown set keeps its file name")
            self.assertEqual(game_cards.sync_card(library, self.marker).existing, 2)


if __name__ == "__main__":
    unittest.main()
