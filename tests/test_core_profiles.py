"""Regression checks for consequential original-to-public module choices."""
import unittest

from redux.core_profiles import PROFILES, default_options, get_profile


class CoreProfileTests(unittest.TestCase):
    def test_snes_is_the_observed_faust_module(self):
        profile = get_profile("snes")
        self.assertEqual(profile.filename, "mednafen_supafaust_libretro.dll")
        self.assertEqual(profile.original_system_ids, (2,))
        self.assertEqual(default_options("snes"), {"supafaust_h_filter": "phr256blend_512"})

    def test_pce_family_uses_full_module_and_does_not_round_unrepresentable_volume(self):
        for system in ("pce", "pce_cd", "supergrafx"):
            self.assertEqual(get_profile(system).filename, "mednafen_pce_libretro.dll")
        self.assertEqual(default_options("pce_cd"), {"pce_cddavolume": "150"})
        self.assertTrue(any("125" in item for item in get_profile("pce_cd").blockers))
        self.assertEqual(default_options("pce"), {})

    def test_psx_hides_horizontal_borders_and_preserves_pal_crop_without_forcing_bios(self):
        options = default_options("psx")
        self.assertEqual(options["beetle_psx_crop_overscan"], "static")
        self.assertEqual(options["beetle_psx_initial_scanline_pal"], "11")
        self.assertEqual(options["beetle_psx_last_scanline_pal"], "280")
        self.assertNotIn("beetle_psx_override_bios", options)
        self.assertFalse(any("multitap" in key for key in options))

    def test_saturn_shows_horizontal_overscan_without_forcing_a_four_mb_cart(self):
        self.assertEqual(default_options("saturn"), {"beetle_saturn_horizontal_overscan": "0"})

    def test_n64_requires_optional_software_plugins_instead_of_gpu_default(self):
        self.assertEqual(default_options("n64"), {
            "mupen64plus-rdp-plugin": "angrylion", "mupen64plus-rsp-plugin": "cxd4"})
        self.assertTrue(any("conditional" in item for item in get_profile("n64").blockers))

    def test_32x_replacement_is_not_mislabeled_as_the_original_engine(self):
        profile = get_profile("32x")
        self.assertEqual(profile.filename, "picodrive_libretro.dll")
        self.assertEqual(profile.original_hook, "kega")
        self.assertEqual(profile.match_level, "replacement")
        self.assertEqual(get_profile("genesis").match_level, "qualified_replacement")

    def test_unknown_catalogue_ids_are_not_invented(self):
        for system in ("sms", "game_gear", "gb", "gbc"):
            self.assertEqual(get_profile(system).original_system_ids, ())
        known_ids = {item for profile in PROFILES.values() for item in profile.original_system_ids}
        self.assertTrue(known_ids.isdisjoint({4, 8, 12, 18, 19, 20, 24, 26, 33, 34}))
        self.assertIsNone(get_profile("31"))
        self.assertIsNone(get_profile(None))

    def test_profile_and_session_options_cannot_mutate_other_sessions(self):
        options = default_options("PSX")
        options["beetle_psx_crop_overscan"] = "disabled"
        self.assertEqual(default_options(" psx ")["beetle_psx_crop_overscan"], "static")
        with self.assertRaises(TypeError):
            get_profile("psx").default_options["beetle_psx_crop_overscan"] = "disabled"
        with self.assertRaises(TypeError):
            PROFILES["new"] = get_profile("psx")
        self.assertEqual(default_options("unsupported"), {})


if __name__ == "__main__":
    unittest.main()
