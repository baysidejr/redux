"""Identify imported bytes and preserve user choices across repeated scans."""
from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

from redux.importing import import_identified
from redux.library import Library
from redux.media_identity import MediaIdentity, identify_media
from redux.metadata import normalize_title


def nes_fixture():
    return b"NES\x1a\x01\x00" + bytes(10) + bytes(16384)


def saturn_fixture(title="World Series Baseball 2"):
    data=bytearray(18*2048)
    data[:16]=b"SEGA SEGASATURN "
    data[0x20:0x2a]=b"MK-81113  "
    data[0x40:0x50]=b"U".ljust(16,b" ")
    data[0x60:0xd0]=title.encode("ascii").ljust(112,b" ")
    return bytes(data)


class CatalogueFixture:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def match(self, title, system):
        self.calls.append((title, system))
        if self.error:
            raise self.error
        return self.result


class RegionCatalogueFixture(CatalogueFixture):
    def match(self,title,system):
        super().match(title,system)
        _,region=normalize_title(title)
        if region is None:
            return None  # Two regional records for the same exact title.
        return {"description":"Matched regional description","region":{1:"North America",2:"Japan",3:"Europe"}[region]}


class IdentifiedImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = Library(self.root / "library")

    def tearDown(self):
        self.temporary.cleanup()

    def media(self, name="Original (USA).bin", data=None):
        path = self.root / name
        path.write_bytes(nes_fixture() if data is None else data)
        return path

    def test_header_assigns_platform_after_copy_and_keeps_filename_title(self):
        source = self.media()
        catalogue = CatalogueFixture({"title": "Replacement", "system": "psx", "description": "Fixture description",
                                      "year": 1995, "publisher": "Independent", "developer": "Studio", "region": "North America", "genre": "Puzzle"})
        game, created, identity = import_identified(self.library, source, catalogue=catalogue)
        self.assertTrue(created)
        self.assertEqual((game["system"], identity.system), ("nes", "nes"))
        self.assertEqual(game["title"], "Original (USA)")
        self.assertEqual(catalogue.calls, [("Original (USA)", "nes")])
        self.assertEqual((game["description"], game["year"], game["publisher"]), ("Fixture description", 1995, "Independent"))

    def test_explicit_platform_survives_conflicting_header(self):
        game, _, identity = import_identified(self.library, self.media(), system="snes")
        self.assertEqual(game["system"], "snes")
        self.assertEqual(identity.system, "nes")

    def test_unknown_header_does_not_guess_platform_from_title(self):
        game, _, identity = import_identified(self.library, self.media("PlayStation.bin", b"not a console image"))
        self.assertEqual(game["system"], "unknown")
        self.assertIsNone(identity.system)

    def test_identity_comes_from_imported_snapshot_when_source_changes(self):
        source = self.media(data=b"unrecognized source before copying")
        original_import = self.library.import_game
        def changing_source(*args, **kwargs):
            source.write_bytes(nes_fixture())
            result = original_import(*args, **kwargs)
            source.write_bytes(b"source changed after completed copy")
            return result
        with patch.object(self.library, "import_game", changing_source):
            game, _, identity = import_identified(self.library, source)
        self.assertIsNone(identify_media(source).system)
        self.assertEqual(identity.system, "nes")
        self.assertEqual(game["system"], "nes")
        self.assertEqual(self.library.path_for(game).read_bytes(), nes_fixture())

    def test_duplicate_scan_preserves_edited_platform_and_blank_metadata(self):
        source = self.media()
        game, _, _ = import_identified(self.library, source)
        self.library.update_metadata(game["id"], title="My custom title", system="snes", description="", publisher="My edit")
        self.library.set_flags(game["id"], favorite=True)
        catalogue = CatalogueFixture({"description": "Must not refill a deliberately blank edit", "publisher": "Other"})
        duplicate, created, identity = import_identified(self.library, source, catalogue=catalogue)
        self.assertFalse(created)
        self.assertEqual(identity.system, "nes")
        self.assertEqual((duplicate["title"], duplicate["system"], duplicate["description"], duplicate["publisher"]),
                         ("My custom title", "snes", "", "My edit"))
        self.assertTrue(duplicate["favorite"])
        self.assertEqual(catalogue.calls, [])
        self.assertEqual(len(self.library.games()), 1)

    def test_conflicting_catalogue_regions_do_not_fail_completed_import(self):
        catalogue = CatalogueFixture(error=ValueError("conflicting regions"))
        game, created, _ = import_identified(self.library, self.media(), catalogue=catalogue)
        self.assertTrue(created)
        self.assertEqual(self.library.game(game["id"])["description"], "")

    def test_linked_to_managed_promotion_preserves_existing_user_metadata(self):
        source = self.media()
        game, _, _ = import_identified(self.library, source, linked=True)
        self.library.update_metadata(game["id"], system="snes", title="My linked edition")
        promoted, created, identity = import_identified(self.library, source)
        self.assertFalse(created)
        self.assertEqual(promoted["id"], game["id"])
        self.assertEqual(promoted["storage"], "managed")
        self.assertEqual((promoted["system"], promoted["title"]), ("snes", "My linked edition"))
        self.assertEqual(identity.system, "nes")
        self.assertNotEqual(self.library.path_for(promoted), source)

    def test_generated_disc_cue_adopts_the_validated_saturn_header_title(self):
        source=self.root/"disc.cue"
        cooked=saturn_fixture()
        sector=b"\0"+b"\xff"*10+b"\0"+b"\0\x02\0\x01"+cooked[:2048]+bytes(288)
        (self.root/"disc.bin").write_bytes(sector)
        source.write_text('FILE "disc.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n')
        catalogue=CatalogueFixture({"description":"Header title matched"})
        game,created,identity=import_identified(self.library,source,catalogue=catalogue)
        self.assertTrue(created)
        self.assertEqual(identity.confidence,"header")
        self.assertEqual((game["title"],game["system"]),("World Series Baseball 2","saturn"))
        self.assertEqual(identity.details["area_symbols"],"U")
        self.assertEqual(catalogue.calls,[("World Series Baseball 2 (USA)","saturn")])
        self.assertEqual(game["description"],"Header title matched")

    def test_only_exact_generic_descriptor_names_are_eligible(self):
        for name in ("disc.iso","IMAGE.iso","CD.iso"):
            with self.subTest(name=name):
                source=self.media(name,saturn_fixture())
                game,created,_=import_identified(self.library,source)
                self.assertTrue(created)
                self.assertEqual(game["title"],"World Series Baseball 2")
        for name in ("Baseball (USA) (Rev 1).iso","disc (USA).iso","disc (Rev 1).iso","disc 2.iso","disc.bin"):
            with self.subTest(name=name):
                source=self.media(name,saturn_fixture())
                game,created,_=import_identified(self.library,source)
                self.assertTrue(created)
                self.assertEqual(game["title"],source.stem)

    def test_generic_ccd_requires_a_positive_header_identity(self):
        source=self.media("CD.ccd",b"[CloneCD]\nVersion=3\n")
        self.media("CD.img",saturn_fixture())
        identity=MediaIdentity("World Series Baseball 2","saturn","MK-81113","CCD","header")
        with patch("redux.importing.identify_media",return_value=identity):
            game,created,_=import_identified(self.library,source)
        self.assertTrue(created)
        self.assertEqual(game["title"],"World Series Baseball 2")

    def test_generic_name_does_not_override_explicit_platform_or_user_edits(self):
        source=self.media("disc.iso",saturn_fixture())
        game,_,identity=import_identified(self.library,source,system="psx")
        self.assertEqual(identity.system,"saturn")
        self.assertEqual((game["system"],game["title"]),("psx","disc"))
        self.library.update_metadata(game["id"],title="My edition",description="")
        catalogue=CatalogueFixture({"description":"Must not restore a blank edit"})
        duplicate,created,_=import_identified(self.library,source,catalogue=catalogue)
        self.assertFalse(created)
        self.assertEqual((duplicate["title"],duplicate["system"],duplicate["description"]),("My edition","psx",""))
        self.assertEqual(catalogue.calls,[])

    def test_generic_or_untrusted_header_title_does_not_rename(self):
        source=self.media("disc.iso",saturn_fixture())
        base=MediaIdentity("World Series Baseball 2","saturn","MK-81113","Saturn","header")
        cases=(replace(base,confidence="filesystem"),replace(base,system=None),
               replace(base,system="unknown"),replace(base,title="disc"),
               replace(base,title="Saturn disc"),replace(base,title=""),replace(base,title="Broken\nTitle"))
        for index,identity in enumerate(cases):
            library=Library(self.root/f"untrusted-{index}")
            with self.subTest(identity=identity),patch("redux.importing.identify_media",return_value=identity):
                game,_,_=import_identified(library,source)
            self.assertEqual(game["title"],"disc")

    def test_adopted_header_region_disambiguates_catalogue_without_changing_display_title(self):
        source=self.media("disc.iso",saturn_fixture())
        identity=MediaIdentity("World Series Baseball 2","saturn","MK-81113","Saturn","header",details={"region":"U"})
        catalogue=RegionCatalogueFixture()
        with patch("redux.importing.identify_media",return_value=identity):
            game,_,_=import_identified(self.library,source,catalogue=catalogue)
        self.assertEqual(game["title"],"World Series Baseball 2")
        self.assertEqual(game["region"],"North America")
        self.assertEqual(catalogue.calls,[("World Series Baseball 2 (USA)","saturn")])

    def test_unknown_multiregion_or_conflicting_header_regions_keep_existing_matching(self):
        source=self.media("disc.iso",saturn_fixture())
        for index,details in enumerate(({}, {"area_symbols":"JUE"}, {"region":"unknown"},
                                        {"region":"USA","area_symbols":"J"})):
            library=Library(self.root/f"region-{index}")
            identity=MediaIdentity("World Series Baseball 2","saturn","MK-81113","Saturn","header",details=details)
            catalogue=RegionCatalogueFixture()
            with self.subTest(details=details),patch("redux.importing.identify_media",return_value=identity):
                game,_,_=import_identified(library,source,catalogue=catalogue)
            self.assertEqual(catalogue.calls,[("World Series Baseball 2","saturn")])
            self.assertEqual(game["region"],"")


if __name__ == "__main__":
    unittest.main()
