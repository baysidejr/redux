import unittest

from redux.controllers import GamepadState, GameplayInputMapper


class GameplayInputTests(unittest.TestCase):
    def test_face_buttons_dpad_shoulders_and_thumb_clicks_use_libretro_ids(self):
        mapper = GameplayInputMapper()
        for source, target in ((1, 4), (2, 5), (4, 6), (8, 7), (64, 14), (128, 15),
                               (256, 10), (512, 11), (0x1000, 8), (0x2000, 0),
                               (0x4000, 1), (0x8000, 9)):
            with self.subTest(source=source):
                self.assertEqual(mapper.update(0, GamepadState(buttons=source), 1), 1 << target)

    def test_staggered_menu_chord_never_leaks_initial_edge_inside_window(self):
        for first, second in ((0x10, 0x20), (0x20, 0x10)):
            mapper = GameplayInputMapper()
            self.assertEqual(mapper.update(0, GamepadState(first), 0), 0)
            self.assertEqual(mapper.update(0, GamepadState(first), .04), 0)
            self.assertEqual(mapper.update(0, GamepadState(first | second), .08), 0)
            self.assertEqual(mapper.update(0, GamepadState(first | second), 2), 0)
            self.assertEqual(mapper.update(0, GamepadState(second), 3), 0)
            self.assertEqual(mapper.update(0, GamepadState(), 4), 0)
            self.assertEqual(mapper.update(0, GamepadState(second), 5), 0)
            self.assertEqual(mapper.update(0, GamepadState(), 5.02), 1 << (2 if second == 0x20 else 3))

    def test_quick_single_taps_produce_exactly_one_poll_pulse(self):
        for source, target in ((0x10, 3), (0x20, 2)):
            mapper = GameplayInputMapper()
            self.assertEqual(mapper.update(0, GamepadState(source), 0), 0)
            self.assertEqual(mapper.update(0, GamepadState(), .03), 1 << target)
            self.assertEqual(mapper.update(0, GamepadState(), .04), 0)
            self.assertEqual(mapper.update(0, GamepadState(), 1), 0)

    def test_held_single_becomes_held_button_without_new_pulses_on_release(self):
        mapper = GameplayInputMapper()
        self.assertEqual(mapper.update(0, GamepadState(0x10), 0), 0)
        self.assertEqual(mapper.update(0, GamepadState(0x10), .12), 1 << 3)
        self.assertEqual(mapper.update(0, GamepadState(0x10), 2), 1 << 3)
        self.assertEqual(mapper.update(0, GamepadState(), 3), 0)
        self.assertEqual(mapper.update(0, GamepadState(), 4), 0)

    def test_late_chord_suppresses_previously_held_single_and_release(self):
        mapper = GameplayInputMapper()
        mapper.update(0, GamepadState(0x10), 0)
        self.assertEqual(mapper.update(0, GamepadState(0x10), .2), 1 << 3)
        self.assertEqual(mapper.update(0, GamepadState(0x30), .3), 0)
        self.assertEqual(mapper.update(0, GamepadState(0x10), .4), 0)
        self.assertEqual(mapper.update(0, GamepadState(), .5), 0)

    def test_stick_hysteresis_combines_with_dpad(self):
        mapper = GameplayInputMapper()
        self.assertEqual(mapper.update(0, GamepadState(1, 20000, 0), 0), (1 << 4) | (1 << 7))
        self.assertEqual(mapper.update(0, GamepadState(0, 14000, 0), .1), 1 << 7)
        self.assertEqual(mapper.update(0, GamepadState(0, 10000, 0), .2), 0)
        self.assertEqual(mapper.update(0, GamepadState(0, -20000, -20000), .3), (1 << 6) | (1 << 5))

    def test_controllers_are_independent_and_disconnect_cancels_deferred_taps(self):
        mapper = GameplayInputMapper()
        mapper.update(0, GamepadState(0x10), 0)
        self.assertEqual(mapper.update(1, GamepadState(0x1000), .01), 1 << 8)
        self.assertEqual(mapper.update(0, None, .02), 0)
        self.assertEqual(mapper.update(0, GamepadState(), .03), 0)
        mapper.update(1, GamepadState(0x20), .04)
        mapper.reset()
        self.assertEqual(mapper.update(1, GamepadState(), .05), 0)
        for index in (-1, 4, True):
            with self.assertRaises(ValueError): mapper.update(index, None, 1)


if __name__ == "__main__":
    unittest.main()
