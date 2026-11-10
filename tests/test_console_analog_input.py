"""Console-to-host input integration without a window, hardware or game."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from redux.console_ui import ConsoleApp
from redux.controllers import GamepadState, GameplayInputMapper


class ConsoleAnalogInputTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(ConsoleApp)
        self.app.overlay = None
        self.app.held_keys = set()
        self.app.input_release_gate = False
        self.app.gameplay_input = GameplayInputMapper()
        self.app.preferences = {}
        self.app.embedded_game = {"system": "n64"}
        self.masks, self.axes, self.states = {}, {}, {}
        self.app.embedded = SimpleNamespace(host=SimpleNamespace(
            set_input=lambda player, mask: self.masks.update({player: mask}),
            set_analog=lambda player, *axes: self.axes.update({player: axes})), resume=Mock())
        self.app.controller = SimpleNamespace(api=SimpleNamespace(state=lambda player: self.states.get(player)))
        self.app.schedule_draw = Mock()

    def send(self):
        self.app._embedded_input()

    def test_all_players_sticks_pass_to_host_with_y_inversion_and_limits(self):
        self.states.update({0: GamepadState(thumb_lx=-32768, thumb_ly=-32768, thumb_rx=32767, thumb_ry=32767),
                            2: GamepadState(thumb_lx=1234, thumb_ly=5678, thumb_rx=-9012, thumb_ry=-3456)})
        self.send()
        self.assertEqual(self.axes, {0: (-32768, 32767, 32767, -32767), 1: (0, 0, 0, 0),
                                     2: (1234, -5678, -9012, 3456), 3: (0, 0, 0, 0)})

    def test_neutral_disconnect_and_keyboard_do_not_reuse_old_axes(self):
        self.states[1] = GamepadState(thumb_lx=20000, thumb_ry=-20000)
        self.send()
        self.assertEqual(self.axes[1], (20000, 0, 0, 20000))
        self.states.clear()
        self.app.held_keys = {"up", "x"}
        self.send()
        self.assertEqual(self.axes, {player: (0, 0, 0, 0) for player in range(4)})
        self.assertEqual(self.masks, {0: (1 << 4) | (1 << 8), 1: 0, 2: 0, 3: 0})

    def test_pause_sends_neutral_axes_and_buttons_while_consuming_pending_taps(self):
        self.states[0] = GamepadState(0x10, thumb_lx=20000, left_trigger=255)
        self.app.overlay = {"title": "Paused"}
        self.app.held_keys = {"x"}
        self.send()
        self.assertEqual(self.axes, {player: (0, 0, 0, 0) for player in range(4)})
        self.assertEqual(self.masks, {player: 0 for player in range(4)})
        self.app._resume_embedded()
        self.assertTrue(self.app.input_release_gate)
        self.assertEqual(self.app.held_keys, set())
        self.states[0] = GamepadState(left_trigger=255)
        self.send()
        self.assertEqual(self.masks[0], 0)
        self.assertEqual(self.axes[0], (0, 0, 0, 0))
        self.states[0] = GamepadState()
        self.send()
        self.assertFalse(self.app.input_release_gate)
        self.assertEqual(self.masks[0], 0)

    def test_resume_waits_for_all_players_axes_and_trigger_neutral_then_reads_fresh(self):
        self.states[0] = GamepadState(0x1000)
        self.states[3] = GamepadState(thumb_rx=24000, right_trigger=255)
        self.app._resume_embedded()
        self.send()
        self.assertEqual(self.axes, {player: (0, 0, 0, 0) for player in range(4)})
        self.states[0] = GamepadState()
        self.states[3] = GamepadState(right_trigger=32)
        self.send()
        self.assertTrue(self.app.input_release_gate)
        self.assertEqual(self.masks, {player: 0 for player in range(4)})
        self.states[3] = GamepadState(right_trigger=31)
        self.send()
        self.assertFalse(self.app.input_release_gate)
        self.assertEqual(self.masks, {player: 0 for player in range(4)})
        self.states[0] = GamepadState(thumb_lx=1000, thumb_ly=2000, left_trigger=128)
        self.send()
        self.assertEqual(self.axes[0], (1000, -2000, 0, 0))
        self.assertEqual(self.masks[0], 1 << 12)

    def test_guide_and_select_start_reserve_axes_even_before_menu_routing(self):
        for buttons in (0x400, 0x30, 0x330):
            with self.subTest(buttons=buttons):
                self.app.gameplay_input.reset()
                self.states[0] = GamepadState(buttons, thumb_lx=24000, thumb_ry=24000,
                                               left_trigger=255, right_trigger=255)
                self.send()
                self.assertEqual(self.axes[0], (0, 0, 0, 0))
                self.assertEqual(self.masks[0], 0)

    def test_swap_ab_only_changes_face_buttons_and_preserves_trigger_axes(self):
        self.app.preferences = {"system_controls": {"n64": {"swap_ab": True}}}
        self.states[1] = GamepadState(0x1000, thumb_lx=1000, left_trigger=255)
        self.send()
        self.assertEqual(self.masks[1], 1 | (1 << 12))
        self.assertEqual(self.axes[1], (1000, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
