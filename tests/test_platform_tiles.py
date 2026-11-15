"""The owner-made system tiles ship inside the frontend and win over optional reference art."""
from pathlib import Path
import tempfile
import unittest

from redux.ui_assets import Artwork

BUNDLED = {"genesis", "sega_cd", "nes", "snes", "pce", "pce_cd", "supergrafx", "neo_geo_cd", "psx", "32x", "saturn", "n64", "jaguar", "jaguar_cd", "3do", "neo_geo"}


class PlatformTileTests(unittest.TestCase):
    def test_every_bundled_system_tile_is_present_and_used_for_tile_and_hero(self):
        with tempfile.TemporaryDirectory() as temporary:
            art = Artwork(None, reference_root=temporary)  # no reference pack at all
            for system in BUNDLED:
                for kind in ("tile", "hero"):
                    path = art.path(system, kind)
                    self.assertIsNotNone(path, f"{system} {kind}")
                    self.assertEqual(path.name, f"{system}.png")
                    self.assertTrue(path.is_file())
            self.assertEqual(art.path("pc", "tile").name, "pc-games.png")
            self.assertIsNone(art.path("vectrex", "tile"), "no tile for an unknown system, and no reference pack")

    def test_tiles_are_real_images_of_one_shape(self):
        from PIL import Image
        root = Path(Artwork.bundled("platforms", "genesis.png")).parent
        sizes = set()
        for system in BUNDLED:
            with Image.open(root / f"{system}.png") as image:
                sizes.add(round(image.width / image.height, 2))
        self.assertEqual(len(sizes), 1, f"tiles differ in aspect: {sizes}")


if __name__ == "__main__":
    unittest.main()
