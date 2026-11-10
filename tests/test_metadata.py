"""Optional catalogue adapter: conservative matching and binary boundary cases."""
from pathlib import Path
import struct
import tempfile
import unittest

from redux.metadata import Catalogue,CatalogueError,normalize_title


def u32(value):return struct.pack("<I",value)
def text(value):
    raw=value.encode("utf-8")+b"\0"
    return u32(len(raw))+raw


def catalogue(rows):
    """Serialize independent fixtures using the observed counted-map grammar."""
    systems={}
    for system,region,index,fields in rows:
        systems.setdefault(system,{}).setdefault(region,[]).append((index,fields))
    raw=u32(0)+u32(len(systems))
    for system,regions in systems.items():
        raw+=u32(system)+u32(len(regions))
        for region,games in regions.items():
            raw+=u32(region)+u32(len(games))
            for index,fields in games:
                raw+=u32(index)+u32(len(fields))
                for name,value in fields:
                    raw+=text(name)+u32(1)+text(value)
    return raw


def fields(name,**extra):
    return [("name",name),("copy","Original synthetic description"),("year","1998"),
            ("publisher","Fixture Studio"),("developer","Independent Creator"),
            ("genre","Puzzle"),*[(key.replace("_"," "),value) for key,value in extra.items()]]


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.path=self.root/"catalogue.rbxhdb"
        self.assets=self.root/"assets"
        self.assets.mkdir()

    def tearDown(self):self.temp.cleanup()

    def load(self,rows,asset_root=True):
        self.path.write_bytes(catalogue(rows))
        return Catalogue(self.path,self.assets if asset_root else None)

    def test_country_tags_resolve_exact_title_without_fuzzy_matching(self):
        provider=self.load([(11,1,3,fields("Example Game")),(11,3,9,fields("Example Game")),
                            (1,1,4,fields("Example Game"))])
        self.assertIsNone(provider.match("Example Game","psx"))
        match=provider.match("Example Game (USA) (En,Fr,De,Sv).cue","psx")
        self.assertEqual(match["catalogue_index"],3)
        self.assertEqual(match["region"],"North America")
        self.assertEqual(match["year"],1998)
        self.assertEqual(match["description"],"Original synthetic description")
        self.assertIsNone(provider.match("Example Gam","psx"))
        self.assertIsNone(provider.match("Example Game (Disc 1)","psx"))
        self.assertEqual(provider.match("Example Game","nes")["catalogue_index"],4)
        self.assertIsNone(provider.match("Example Game","gba"))
        with self.assertRaises(ValueError):provider.match("Example Game (USA) (Europe)","psx")

    def test_alias_unicode_normalization_and_missing_values_are_preserved(self):
        provider=self.load([(11,1,2,fields("Café’s Quest",alt_name="Alternate Name")),
                            (11,1,3,[("name","No info"),("copy","[MISSING]"),("year","unknown")])])
        self.assertEqual(provider.match("Cafe\u0301's Quest","psx")["title"],"Café’s Quest")
        self.assertEqual(provider.match("alternate_name","psx")["title"],"Café’s Quest")
        unknown=provider.match("No info","psx")
        self.assertEqual(unknown["description"],"")
        self.assertIsNone(unknown["year"])

    def test_duplicate_titles_in_same_region_are_not_auto_matched(self):
        provider=self.load([(11,1,1,fields("Same")),(11,1,2,fields("Same"))])
        self.assertIsNone(provider.match("Same (USA)","psx"))
        self.assertEqual(len(provider.search("Same","psx")),2)

    def test_assets_require_existing_files_and_cannot_leave_explicit_root(self):
        (self.assets/"img").mkdir()
        (self.assets/"img"/"box.png").write_bytes(b"owned synthetic artwork")
        (self.assets/"img"/"screen_0001.png").write_bytes(b"owned synthetic screenshot")
        (self.root/"outside.png").write_bytes(b"outside the selected assets")
        provider=self.load([(11,1,1,fields("With art",box_image_front="/img/box.png",hero_banner="../../outside.png",
                                                   tile_image="C:\\private.png",screenshots="/img/screen"))])
        match=provider.match("With art","psx")
        self.assertEqual(match["artwork_path"],str((self.assets/"img/box.png").resolve()))
        self.assertIsNone(match["hero_path"])
        self.assertIsNone(match["tile_path"])
        self.assertEqual(match["screenshot_paths"],[str((self.assets/"img/screen_0001.png").resolve())])
        no_assets=Catalogue(self.path)
        self.assertEqual(no_assets.match("With art","psx")["artwork_path"],"")

    def test_search_is_bounded_system_scoped_and_returns_independent_dicts(self):
        provider=self.load([(11,1,index,fields(f"Game {index}")) for index in range(5)]+
                           [(1,1,9,fields("NES Game"))])
        result=provider.search("game","psx",limit=2)
        self.assertEqual(len(result),2)
        result[0]["title"]="mutated caller copy"
        self.assertEqual(provider.match("Game 0","psx")["title"],"Game 0")
        self.assertEqual(provider.stats()["record_count"],6)
        self.assertEqual(provider.stats()["system_counts"],{"psx":5,"nes":1})
        self.assertTrue(provider.statistics["all_bytes_consumed"])
        for limit in (0,1001,True,"3"):
            with self.subTest(limit=limit),self.assertRaises(ValueError):provider.search("",limit=limit)

    def test_malformed_truncated_duplicate_and_trailing_data_fail_closed(self):
        valid=catalogue([(11,1,1,fields("Game"))])
        duplicate_fields=catalogue([(11,1,1,[("name","A"),("name","B")])])
        duplicate_records=catalogue([(11,1,1,fields("A")),(11,1,1,fields("B"))])
        unterminated=bytearray(valid)
        pos=valid.index(b"name\0")+4
        unterminated[pos]=65
        for invalid in (valid[:-1],valid+b"extra",u32(1)+valid[4:],u32(0)+u32(9999),duplicate_fields,
                        duplicate_records,bytes(unterminated),u32(0)+u32(1)+u32(11)+u32(1)+u32(1)+u32(1)+u32(1)+u32(1)+u32(2_000_000)):
            self.path.write_bytes(invalid)
            with self.subTest(length=len(invalid)),self.assertRaises(CatalogueError):Catalogue(self.path)

    def test_explicit_path_is_required_and_unknown_systems_are_not_guessed(self):
        with self.assertRaises(FileNotFoundError):Catalogue(self.root/"missing.rbxhdb")
        provider=self.load([(11,1,1,fields("Game"))])
        self.assertIsNone(provider.match("Game",None))
        with self.assertRaises(ValueError):provider.match("Game","made_up_system")
        self.assertEqual(normalize_title("Real Game (Special Edition) (USA)")[0],"real game (special edition)")


class OptionalActualCatalogueTest(unittest.TestCase):
    @unittest.skipUnless(Path("analysis/formats/decoded/data/catalogue.rbxhdb").is_file(),"Local reference corpus not present")
    def test_known_local_catalogue_consumes_all_bytes_and_matches_diablo_without_bundling_it(self):
        provider=Catalogue("analysis/formats/decoded/data/catalogue.rbxhdb","analysis/formats/decoded")
        self.assertEqual(provider.stats()["record_count"],19272)
        self.assertEqual(provider.stats()["system_count"],14)
        record=provider.match("Diablo (USA) (En,Fr,De,Sv)","psx")
        self.assertIsNotNone(record)
        self.assertEqual((record["title"],record["year"],record["catalogue_region_id"]),("Diablo",1998,1))


if __name__=="__main__":unittest.main()
