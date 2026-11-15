"""Pixel art keeps its shape and enlarges by whole numbers; the carousel gets its own clipped viewport."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import time
import unittest

from PIL import Image

from redux.pixel_fit import contain, fit_factor, fit_height


class FitTests(unittest.TestCase):
    def test_whole_number_factors_when_they_fit_and_fractional_only_below_one(self):
        self.assertEqual(fit_factor(1920, 1080, 240, 135), 8)
        self.assertEqual(fit_factor(1000, 1000, 240, 135), 4, "width-limited, never stretched to the square")
        self.assertEqual(fit_factor(3700, 1200, 240, 135), 8, "height-limited")
        self.assertAlmostEqual(fit_factor(120, 100, 240, 135), 0.5)
        self.assertEqual(fit_factor(0, 100, 240, 135), 0)
        left, top, width, height, factor = contain(10, 20, 1000, 1000, 240, 135)
        self.assertEqual((width, height, factor), (960, 540, 4));self.assertEqual(width / height, 240 / 135)
        self.assertEqual((left, top), (10 + 20, 20 + 230), "centred in its space")
        self.assertEqual(fit_height(540, 270), 2);self.assertEqual(fit_height(800, 270), 2);self.assertAlmostEqual(fit_height(180, 270), 180 / 270)


class SpriteScalingTests(unittest.TestCase):
    def test_a_2x_sheet_is_reduced_to_its_art_pixels_then_enlarged_into_even_squares(self):
        from redux.ui_assets import Artwork
        import tkinter as tk
        try:
            root = tk.Tk();root.withdraw()
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as temporary:
            # A 4x2 logical cell exported at 2x: each art pixel is a 2x2 block.
            logical = Image.new("RGBA", (4, 2));logical.putdata([(i * 40, 0, 0, 255) for i in range(8)])
            sheet = Path(temporary) / "sheet.png";logical.resize((8, 4), Image.Resampling.NEAREST).save(sheet)
            art = Artwork(root)
            photo = art.sprite(sheet, (0, 0, 8, 4), 12, 6, pixel_scale=2)
            self.assertIsNotNone(photo)
            image = next(iter(art._sprite_images.values()))
            self.assertEqual(image.size, (12, 6))
            for x in range(12):
                for y in range(6):
                    self.assertEqual(image.getpixel((x, y)), logical.getpixel((x // 3, y // 3)), "every art pixel is a clean 3x3 square at 3x")
            clipped = art.sprite(sheet, (0, 0, 8, 4), 12, 6, pixel_scale=2, crop=(3, 0, 12, 6))
            self.assertEqual((clipped.width(), clipped.height()), (9, 6), "a clipped sprite keeps its scale and loses its overhang")


class ViewportTests(unittest.TestCase):
    def app(self, width, height):
        from redux.console_ui import ConsoleApp
        from redux.ui_assets import Artwork
        app = object.__new__(ConsoleApp)
        app.scale = min(width / 1280, height / 720);app.offset_x = (width - 1280 * app.scale) / 2;app.offset_y = (height - 720 * app.scale) / 2
        app.canvas = SimpleNamespace(winfo_width=lambda: width, winfo_height=lambda: height, create_rectangle=lambda *a, **k: None)
        app.art = SimpleNamespace(bundled=Artwork.bundled, hero=lambda: None)
        app.drawn = [];app.rects = []
        app.device_sprite = lambda sheet, box, x, y, w, h, pixel_scale=1, clip=None: app.drawn.append((Path(sheet).name, int(x), int(y), int(w), int(h), pixel_scale, clip)) or True
        app.device_rect = lambda x, y, w, h, color: app.rects.append((x, y, w, h, color))
        return app

    def test_the_carousel_shows_more_banners_on_a_wider_window_at_whole_number_sizes(self):
        from redux.console_stage import ConsoleStage
        counts = {}
        for width, height in ((1920, 1080), (2560, 1080), (3840, 2160), (1000, 1000), (1280, 720)):
            app = self.app(width, height)
            carousel = ConsoleStage.banner_carousel(app);carousel.hold(time.monotonic())
            hero = round(height * 0.25)
            self.assertTrue(ConsoleStage._draw_carousel_device(app, 0, 0, width, hero))
            banners = [d for d in app.drawn if d[0] == "console_banners.png"]
            sizes = {(d[3], d[4]) for d in banners}
            self.assertEqual(len(sizes), 1, "every banner the same size");w, h = sizes.pop()
            if h >= 270:
                self.assertEqual(h % 270, 0);self.assertEqual(w * 3, h, "the banner keeps its 1:3 shape")
            else:
                self.assertLessEqual(abs(w * 3 - h), 3, "below 1x the banner still keeps its shape")
            self.assertEqual(banners[0][5], 2, "the 2x export is reduced to its art pixels first")
            self.assertTrue(all(d[6] == (0, 0, width, hero) for d in banners), "clipped to the carousel viewport")
            self.assertEqual(app.rects[0], (0, 0, width, hero, "#0a0816"), "the viewport's spare space is the stage colour")
            counts[(width, height)] = len(banners)
        self.assertGreater(counts[(2560, 1080)], counts[(1920, 1080)], "a wider window shows more banners")

    def test_the_loader_is_contained_and_centred_at_a_whole_number_size_on_every_window(self):
        from redux.console_stage import ConsoleStage, SpriteSheetAnimation
        from redux.ui_assets import Artwork
        settings = json.loads(Path(Artwork.bundled("animations", "animations.json")).read_text(encoding="utf-8"))
        sheet = SpriteSheetAnimation(Artwork.bundled("animations", "sega_cd.png"), settings["systems"]["sega_cd"], settings)
        self.assertEqual((sheet.logical_width, sheet.logical_height, sheet.pixel_scale), (240, 135, 2))
        for width, height, box in ((1920, 1080, (24, 400, 1872, 600)), (1000, 1000, (0, 0, 1000, 1000)), (1280, 2000, (0, 300, 1280, 1500)), (200, 100, (0, 0, 200, 100))):
            app = self.app(width, height)
            left, top, w, h, factor = ConsoleStage._draw_loader_device(app, sheet, 30, *box)
            self.assertAlmostEqual(w / h, 240 / 135, places=2, msg="never stretched")
            if factor >= 1:
                self.assertEqual(factor, int(factor));self.assertEqual((w, h), (240 * factor, 135 * factor))
            self.assertLessEqual(w, box[2]);self.assertLessEqual(h, box[3])
            self.assertLessEqual(abs((left - box[0]) - (box[0] + box[2] - left - w)), 1, "centred across")
            self.assertLessEqual(abs((top - box[1]) - (box[1] + box[3] - top - h)), 1, "centred down")
            self.assertEqual(app.rects[0], (*box, "#0a0816"), "the spare space is the stage colour")
            self.assertEqual(app.drawn[0][5], 2)


if __name__ == "__main__":
    unittest.main()
