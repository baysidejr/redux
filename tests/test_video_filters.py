"""Game-frame presentation contracts; no GUI, core, network, or vendor assets."""
import unittest

from redux.video_filters import FILTERS, display_filter_ids, filter_ids, filter_name, fit_size, render_frame


class VideoFilterTests(unittest.TestCase):
    def test_system_presets_keep_clean_equivalent_to_original_and_brightness_is_bounded(self):
        rgb = bytes((120, 80, 200)) * 4
        self.assertEqual(display_filter_ids(), ("clean", "scanlines", "crt", "composite"))
        clean = render_frame(rgb, 2, 2, 6, 6, "clean")
        self.assertEqual(clean.tobytes(), render_frame(rgb, 2, 2, 6, 6, "sharp").tobytes())
        self.assertEqual(render_frame(rgb, 2, 2, 6, 6, "clean", brightness=50).getpixel((0, 0)), (60, 40, 100))
        self.assertEqual(render_frame(rgb, 2, 2, 6, 6, "clean", brightness=200).getpixel((0, 0)), (240, 160, 255))

    def test_scanline_strength_zero_is_clean_and_strength_changes_do_not_reuse_wrong_mask(self):
        rgb = bytes((200, 100, 50)) * 4
        clean = render_frame(rgb, 2, 2, 6, 6, "clean")
        zero = render_frame(rgb, 2, 2, 6, 6, "scanlines", scanline_strength=0)
        forty = render_frame(rgb, 2, 2, 6, 6, "scanlines", scanline_strength=40)
        eighty = render_frame(rgb, 2, 2, 6, 6, "scanlines", scanline_strength=80)
        self.assertEqual(zero.tobytes(), clean.tobytes())
        self.assertGreater(forty.getpixel((0, 2))[0], eighty.getpixel((0, 2))[0])
        self.assertEqual(forty.getpixel((0, 0)), clean.getpixel((0, 0)))
        self.assertEqual(forty.tobytes(), render_frame(rgb, 2, 2, 6, 6, "scanlines").tobytes())

    def test_crt_has_equal_average_rgb_grille_and_blur(self):
        rgb = bytes((200, 200, 200)) * 9
        crt = render_frame(rgb, 3, 3, 9, 9, "crt", scanline_strength=0)
        self.assertEqual(crt.mode, "RGB")
        pixels = [crt.getpixel((x, 0)) for x in range(3)]
        self.assertTrue(pixels[0][0] > pixels[0][1])
        self.assertTrue(pixels[1][1] > pixels[1][0])
        self.assertTrue(pixels[2][2] > pixels[2][0])
        self.assertEqual(len({sum(pixel[channel] for pixel in pixels) for channel in range(3)}), 1)
        edge = bytes((255, 255, 255, 0, 0, 0))
        softened = render_frame(edge, 2, 1, 8, 4, "crt", scanline_strength=0)
        self.assertTrue(0 < softened.getpixel((3, 0))[0] < 255)

    def test_composite_softens_horizontal_edges_without_vertical_color_bleed(self):
        rgb = bytes((255, 0, 0, 0, 255, 0, 0, 0, 255)) + bytes(9)
        before = bytes(rgb)
        composite = render_frame(rgb, 3, 2, 12, 8, "composite")
        clean = render_frame(rgb, 3, 2, 12, 8, "clean")
        self.assertNotEqual(composite.getpixel((4, 0)), clean.getpixel((4, 0)))
        self.assertEqual({composite.getpixel((x, y)) for x in range(12) for y in range(4, 8)}, {(0, 0, 0)})
        self.assertEqual(rgb, before)

    def test_new_filter_parameters_reject_noninteger_and_out_of_range_values(self):
        for field, values in (("scanline_strength", (-1, 101, True, 40.0, "40")),
                              ("brightness", (24, 201, False, 100.0, "100"))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    render_frame(bytes(3), 1, 1, 1, 1, "clean", **{field: value})

    def test_sharp_preserves_source_bytes_and_exact_colors(self):
        pixels = bytearray((255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255))
        before = bytes(pixels)
        image = render_frame(pixels, 2, 2, 6, 6)
        self.assertEqual((image.mode, image.size), ("RGB", (6, 6)))
        self.assertEqual(set(image.get_flattened_data()), {(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)})
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(image.getpixel((5, 5)), (255, 255, 255))
        image.putpixel((0, 0), (0, 0, 0))
        self.assertEqual(bytes(pixels), before)
        second = render_frame(pixels, 2, 2, 6, 6)
        self.assertEqual(second.getpixel((0, 0)), (255, 0, 0))

    def test_smooth_blends_edges_without_crop_or_new_output_shape(self):
        rgb = bytes((255, 0, 0, 0, 0, 255))
        sharp = render_frame(rgb, 2, 1, 8, 4, "sharp")
        smooth = render_frame(rgb, 2, 1, 8, 4, "smooth")
        self.assertEqual(smooth.size, sharp.size)
        self.assertEqual(smooth.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(smooth.getpixel((7, 3)), (0, 0, 255))
        self.assertNotIn(smooth.getpixel((3, 1)), set(sharp.get_flattened_data()))

    def test_scanlines_are_neutral_and_do_not_brighten_or_shift_channels(self):
        rgb = bytes((200, 100, 50)) * 12
        image = render_frame(rgb, 4, 3, 12, 9, "scanlines")
        bright = image.getpixel((1, 0))
        dark = image.getpixel((1, 2))
        self.assertEqual(bright, (200, 100, 50))
        self.assertTrue(all(0 < dim < full for dim, full in zip(dark, bright)))
        self.assertEqual(image.getpixel((11, 2)), dark)
        self.assertLessEqual(abs(dark[0] - dark[1] * 2), 1)
        self.assertEqual(render_frame(bytes(36), 4, 3, 12, 9, "scanlines").getbbox(), None)

    def test_native_scanlines_and_cache_results_are_independent(self):
        rgb = bytes((180, 180, 180)) * 16
        first = render_frame(rgb, 4, 4, 4, 4, "scanlines")
        expected = first.tobytes()
        self.assertGreater(first.getpixel((0, 0))[0], first.getpixel((0, 1))[0])
        first.paste((255, 0, 0), (0, 0, 4, 4))
        self.assertEqual(render_frame(rgb, 4, 4, 4, 4, "scanlines").tobytes(), expected)

    def test_geometry_is_fitted_without_stretching_or_cropping(self):
        self.assertEqual(fit_size(4, 3, 1280, 720), (960, 720))
        self.assertEqual(fit_size(16, 9, 864, 648), (864, 486))
        self.assertEqual(fit_size(1, 8192, 1, 1), (1, 1))
        self.assertEqual(fit_size(8192, 1, 1, 1), (1, 1))

    def test_filter_ids_are_stable_and_invalid_parameters_allocate_no_frame(self):
        self.assertEqual(filter_ids(), ("sharp", "smooth", "scanlines"))
        self.assertEqual(filter_name("smooth"), "Smooth")
        with self.assertRaises(TypeError):
            FILTERS["other"] = "Other"
        for args in ((b"", 0, 1, 1, 1), (b"", True, 1, 1, 1),
                     (b"", 1.5, 1, 1, 1), (b"", 1, 1, 8193, 1),
                     (b"", 1, 1, 8192, 8192), (b"", 1, 1, 1, 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                render_frame(*args)
        with self.assertRaises(ValueError):
            render_frame(b"\0\0\0", 1, 1, 1, 1, "unknown")
        with self.assertRaises(TypeError):
            render_frame("abc", 1, 1, 1, 1)
        with self.assertRaises(ValueError):
            render_frame(memoryview(b"abcdef")[::2], 1, 1, 1, 1)


if __name__ == "__main__":
    unittest.main()
