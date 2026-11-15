"""The console banner carousel: cruising, braking onto a console, aliases, seamless wrap."""
from pathlib import Path
import json
import unittest

from redux.banner_carousel import BannerCarousel, load_manifest
from redux.ui_assets import Artwork


class BannerCarouselTests(unittest.TestCase):
    def setUp(self):
        manifest = Artwork.bundled("banners", "banners.json")
        self.assertIsNotNone(manifest, "the banner atlas ships with the app")
        self.manifest = load_manifest(manifest)
        self.carousel = BannerCarousel(self.manifest, Artwork.bundled("banners", "console_banners.png"))

    def test_the_pack_is_complete_and_every_redux_console_has_a_banner(self):
        from redux.console_stage import HOME_ORDER
        self.assertEqual(self.carousel.count, 52);self.assertTrue(self.carousel.atlas.is_file())
        for system in HOME_ORDER:
            if system == "pc":
                continue
            self.assertIsNotNone(self.carousel.resolve(system), f"{system} has a banner")
        self.assertEqual(self.carousel.resolve("playstation"), "psx");self.assertEqual(self.carousel.resolve("tg16"), "pce")
        self.assertIsNone(self.carousel.resolve("pc"));self.assertIsNone(self.carousel.resolve("nothing"))
        x, y, w, h = self.manifest["systems"]["sega_cd"]["rect"]
        self.assertEqual(self.carousel.rect("sega_cd"), (x, y, x + w, y + h), "the manifest's x, y, width, height becomes a crop box")
        self.assertEqual((w, h), (180, 540))

    def test_cruise_advances_with_time_and_a_stop_eases_onto_the_target_without_reversing(self):
        c = self.carousel
        c.start(10.0)
        self.assertEqual(c.status(10.5), "scrolling");self.assertAlmostEqual(c.sample(11.0), 32.0)
        duration = c.stop_at("sega_cd", 11.0)
        start_phase = c.sample(11.0);target = c.order.index("sega_cd")
        self.assertGreaterEqual(c.end - start_phase, c.minimum_stop_travel, "at least the minimum travel")
        self.assertEqual(round(c.end) % c.count, target, "the brake ends on the target index")
        self.assertAlmostEqual(c.velocity(11.0), 32.0, places=6, msg="the initial slope matches the cruise speed")
        previous = start_phase
        for step in range(1, 201):
            now = 11.0 + duration * step / 200
            phase = c.sample(now)
            self.assertGreaterEqual(phase, previous - 1e-9, "the strip never reverses");previous = phase
            self.assertGreaterEqual(c.velocity(now), -1e-9)
        self.assertEqual(c.status(11.0 + duration / 2), "stopping")
        self.assertEqual(c.status(11.0 + duration), "stopped");self.assertAlmostEqual(c.velocity(11.0 + duration), 0.0)
        self.assertEqual(c.centered_id(11.0 + duration + 5), "sega_cd")
        self.assertAlmostEqual(c.sample(11.0 + duration + 60), c.end, msg="it holds the target afterwards")
        # Asking again for the same target while braking changes nothing; a new target re-plans from here.
        c.start(10.0);planned = c.stop_at("sega_cd", 11.0);remaining = c.stop_at("sega_cd", 11.5)
        self.assertAlmostEqual(remaining, planned - 0.5, places=6)
        c.stop_at("nes", 11.5);self.assertEqual(c.target, "nes");self.assertEqual(round(c.end) % c.count, c.order.index("nes"))
        with self.assertRaises(KeyError):c.stop_at("pc", 12.0)

    def test_a_long_cruise_keeps_wrapping_and_placements_tile_the_viewport_without_gaps(self):
        c = self.carousel
        c.start(0.0, 1.6)
        for now in (0.0, 0.37, 61.0, 600.0, 3600.0):
            items = c.placements(933, 291, now)
            self.assertTrue(items)
            self.assertLessEqual(items[0][2][0], 0);self.assertGreaterEqual(items[-1][2][0] + items[-1][2][2], 933, "the viewport is covered")
            for first, second in zip(items, items[1:]):
                self.assertEqual(first[2][0] + first[2][2], second[2][0], "neighbours touch")
            for banner, source, destination in items:
                self.assertEqual(source, c.rect(banner));self.assertEqual(destination[3], 291)
        centred = [c.centered_id(now) for now in (0.0, 1.0 / 1.6, 52 / 1.6)]
        self.assertEqual(centred, [c.order[0], c.order[1], c.order[0]], "one banner per 1/speed seconds, wrapping after 52")
        c.start(0.0);c.stop_at("jaguar_cd", 1.0)
        settled = c.epoch + c.duration + 1
        middle = next(item for item in c.placements(1280, 180, settled) if item[0] == "jaguar_cd")
        self.assertEqual(middle[2][0] + middle[2][2] / 2, 640, "the target banner is centred exactly")
        with self.assertRaises(ValueError):c.placements(0, 10, 0)


if __name__ == "__main__":
    unittest.main()
