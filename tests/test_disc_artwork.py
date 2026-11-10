"""Exact physical-disc artwork matching, failure isolation and cache integrity."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from redux import disc_artwork
from redux.media_identity import MediaIdentity


def png(color="navy", size=(128, 96), format="PNG"):
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, format=format)
    return stream.getvalue()


def identity(title="World Series BASEBALL2", area="U", serial="MK-81113"):
    return MediaIdentity(title, "saturn", serial, "physical disc", "header", details={"area_symbols": area})


class DiscArtworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_sega_cd_uses_its_own_artwork_repository_and_verified_cache(self):
        disc=MediaIdentity("Eternal Champions - Challenge from the Dark Side","sega_cd","MK-4427",
            "Sega CD","header",details={"region":"U"})
        with patch.object(disc_artwork,"_download",return_value=(png(),128,96)) as download:
            result=disc_artwork.fetch_disc_artwork(self.root,disc)
        self.assertTrue(result["cover_path"])
        self.assertTrue(result["hero_path"])
        self.assertTrue(all("Sega_-_Mega-CD_-_Sega_CD/master/" in call.args[0] for call in download.call_args_list))
        with patch.object(disc_artwork,"_download") as download:
            self.assertEqual(disc_artwork.fetch_disc_artwork(self.root,disc),result)
        download.assert_not_called()

    def test_generic_sega_cd_identity_does_not_request_placeholder_art(self):
        disc=MediaIdentity("Sega CD disc","sega_cd",None,"Sega CD","header",details={"region":"U"})
        with patch.object(disc_artwork,"_download") as download:
            result=disc_artwork.fetch_disc_artwork(self.root,disc)
        download.assert_not_called()
        self.assertIsNone(result["hero_path"])

    def test_known_serial_normalizes_header_and_roman_numeral_then_reuses_verified_cache(self):
        data = png()
        with patch.object(disc_artwork, "_download", return_value=(data, 128, 96)) as download:
            result = disc_artwork.fetch_disc_artwork(self.root, identity(), {"title": "World Series Baseball II", "region": "North America"})
        self.assertEqual(download.call_count, 2)
        self.assertTrue(all("World%20Series%20Baseball%20II%20%28USA%29.png" in call.args[0] for call in download.call_args_list))
        self.assertEqual(Path(result["cover_path"]).read_bytes(), data)
        self.assertEqual(Path(result["hero_path"]).read_bytes(), data)
        metadata = json.loads(next(self.root.glob("*/provenance.json")).read_text())
        self.assertEqual(metadata["identity"]["serial"], "MK-81113")
        self.assertEqual(len(metadata["entries"]["cover"]["sha256"]), 64)
        with patch.object(disc_artwork, "_download", side_effect=AssertionError("cache should avoid network")) as download:
            again = disc_artwork.fetch_disc_artwork(self.root, identity(), {"title": "World Series Baseball II", "region": "North America"})
        download.assert_not_called()
        self.assertEqual(again, result)

    def test_ambiguous_region_wrong_game_or_disagreeing_catalogue_never_fetches(self):
        for disc, catalogue in ((identity(area=""), None), (identity(area="JU"), None),
                                (identity(title="World Series Baseball"), None),
                                (identity(), {"title": "World Series Baseball 98", "region": "USA"}),
                                (identity(), {"title": "World Series Baseball II", "region": "Japan"})):
            with self.subTest(disc=disc, catalogue=catalogue), patch.object(disc_artwork, "_download") as download:
                result = disc_artwork.fetch_disc_artwork(self.root, disc, catalogue)
            download.assert_not_called()
            self.assertIsNone(result["cover_path"])
            self.assertIsNone(result["hero_path"])
            self.assertTrue(result["notes"])

    def test_user_jpeg_artwork_wins_without_network_or_modification(self):
        cover, hero = self.root / "user-cover.jpg", self.root / "user-hero.png"
        cover.write_bytes(png(format="JPEG"))
        hero.write_bytes(png("green"))
        before = (cover.read_bytes(), hero.read_bytes())
        with patch.object(disc_artwork, "_download") as download:
            result = disc_artwork.fetch_disc_artwork(self.root / "cache", identity(),
                {"title": "World Series Baseball II", "region": "USA", "artwork_path": str(cover), "hero_path": str(hero)})
        download.assert_not_called()
        self.assertEqual((result["cover_path"], result["hero_path"]), (str(cover.resolve()), str(hero.resolve())))
        self.assertEqual((cover.read_bytes(), hero.read_bytes()), before)

    def test_network_failure_is_optional_and_leaves_identification_untouched(self):
        disc = identity()
        with patch.object(disc_artwork, "_download", side_effect=TimeoutError("offline")):
            result = disc_artwork.fetch_disc_artwork(self.root, disc)
        self.assertIsNone(result["cover_path"])
        self.assertIsNone(result["hero_path"])
        self.assertTrue(result["notes"])
        self.assertEqual(disc.game_id, "MK-81113")
        self.assertFalse(list(self.root.rglob("*.png")))

    def test_corrupted_cache_is_not_trusted_or_overwritten(self):
        data = png()
        with patch.object(disc_artwork, "_download", return_value=(data, 128, 96)):
            first = disc_artwork.fetch_disc_artwork(self.root, identity())
        Path(first["cover_path"]).write_bytes(b"not artwork")
        with patch.object(disc_artwork, "_download", return_value=(data, 128, 96)):
            second = disc_artwork.fetch_disc_artwork(self.root, identity())
        self.assertIsNone(second["cover_path"])
        self.assertEqual(Path(first["cover_path"]).read_bytes(), b"not artwork")
        self.assertEqual(second["hero_path"], first["hero_path"])

    def test_download_validates_https_redirect_bytes_and_dimensions(self):
        class Response:
            def __init__(self, url, data, length=None):
                self.url, self.data = url, data
                self.headers = {} if length is None else {"Content-Length": str(length)}
            def geturl(self): return self.url
            def read(self, size): return self.data[:size]
            def __enter__(self): return self
            def __exit__(self, *args): pass
        url = disc_artwork._BASE + "Named_Boxarts/fixture.png"
        for response in (Response("http://raw.githubusercontent.com/test.png", png()),
                         Response("https://other.invalid/test.png", png()),
                         Response(url, b"<html>error</html>"),
                         Response(url, png(size=(1, 1))),
                         Response(url, png(), disc_artwork._MAX_BYTES + 1)):
            with self.subTest(url=response.url), patch.object(disc_artwork, "urlopen", return_value=response), self.assertRaises((ValueError, OSError)):
                disc_artwork._download(url)
        with patch.object(disc_artwork, "urlopen", return_value=Response(url, png())) as opened:
            self.assertEqual(disc_artwork._download(url)[1:], (128, 96))
            self.assertEqual(opened.call_args.kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
