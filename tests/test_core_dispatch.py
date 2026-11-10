"""Cases tied to the recovered catalogue and the native switch's boundaries."""
import unittest

from redux.core_dispatch import CoreBackend, core_for_system


class CoreDispatchTests(unittest.TestCase):
    def test_catalogued_systems_use_their_observed_native_backends(self):
        # Catalogue IDs are independently decoded from catalogue.rbxhdb.
        cases = [
            ("NES", 1, CoreBackend.MESEN),
            ("SNES", 2, CoreBackend.MEDNAFEN),
            ("N64", 3, CoreBackend.MUPEN64PLUS),
            ("Genesis", 5, CoreBackend.KEGA),
            ("32X", 6, CoreBackend.KEGA),
            ("Sega CD", 7, CoreBackend.KEGA),
            ("Saturn", 9, CoreBackend.MEDNAFEN),
            ("PSX", 11, CoreBackend.MEDNAFEN),
            ("Neo Geo CD", 13, CoreBackend.MAME),
            ("PC Engine", 14, CoreBackend.MEDNAFEN),
            ("PC Engine CD", 15, CoreBackend.MEDNAFEN),
            ("Atari 2600", 16, CoreBackend.MAME),
            ("SuperGrafx", 17, CoreBackend.MEDNAFEN),
            ("Atari 7800", 23, CoreBackend.MAME),
        ]
        for label, system_id, expected in cases:
            with self.subTest(system=label):
                self.assertIs(core_for_system(system_id), expected)

    def test_uncatalogued_explicit_cases_are_preserved(self):
        for system_id in [4, 8, 19, 20]:
            self.assertIs(core_for_system(system_id), CoreBackend.KEGA)
        for system_id in [12, 26, 33, 34]:
            self.assertIs(core_for_system(system_id), CoreBackend.MAME)
        self.assertIs(core_for_system(18), CoreBackend.MESEN)

    def test_fake_branch_is_distinct_from_absent_selection(self):
        self.assertIs(core_for_system(24), CoreBackend.FAKE)
        self.assertIsNone(core_for_system(25))

    def test_switch_holes_and_uint32_end_do_not_get_a_fallback(self):
        for system_id in [0, 10, 21, 22, 25, 27, 28, 29, 30, 31, 32, 35, 0xFFFFFFFF]:
            with self.subTest(system_id=system_id):
                self.assertIsNone(core_for_system(system_id))

    def test_python_api_does_not_silently_wrap_or_coerce_ids(self):
        for system_id in [-1, 0x100000001]:
            with self.subTest(system_id=system_id), self.assertRaises(ValueError):
                core_for_system(system_id)
        for system_id in [True, "1", 1.0, None]:
            with self.subTest(system_id=system_id), self.assertRaises(TypeError):
                core_for_system(system_id)


if __name__ == "__main__":
    unittest.main()
