"""Typed input ABI tests with an injected core; no N64/PSX game is loaded."""
import ctypes as C
from pathlib import Path
import tempfile
import unittest

from redux.controllers import GamepadState, GameplayInputMapper
from redux.libretro_host import EmbeddedSession
from tests.test_libretro_host import FakeCore


class AnalogCore(FakeCore):
    def __init__(self):
        super().__init__()
        self.readings = []
        self.during_frame = None
        self.port_devices = []

    def retro_init(self):
        super().retro_init()
        environment = self.callbacks["environment"]
        # Sentinels catch incorrectly writing a pointer-sized or 32-bit result.
        capabilities = (C.c_uint64 * 2)(0xffffffffffffffff, 0x1122334455667788)
        assert environment(24, C.addressof(capabilities))
        assert capabilities[0] == (1 << 1) | (1 << 5)
        assert capabilities[1] == 0x1122334455667788
        self.capabilities = capabilities[0]
        users = (C.c_uint * 2)(0xffffffff, 0x12345678)
        assert environment(61, C.addressof(users))
        assert users[0] == 4 and users[1] == 0x12345678
        self.max_users = users[0]

    def retro_set_controller_port_device(self, port, device):
        super().retro_set_controller_port_device(port, device)
        self.port_devices.append((port, device))

    def _axes(self):
        # These are real CFUNCTYPE callbacks, including int16_t return conversion.
        read = self.callbacks["input"]
        return tuple(read(port, 5, stick, axis)
                     for port in range(8) for stick in (0, 1) for axis in (0, 1))

    def retro_run(self):
        before = self._axes()
        if self.during_frame:
            callback, self.during_frame = self.during_frame, None
            callback()
        after = self._axes()
        self.readings.append((before, after))
        super().retro_run()


class AnalogHostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.core = self.root / "fake.dll"
        self.game = self.root / "original-fixture.bin"
        self.core.write_bytes(b"injected core")
        self.game.write_bytes(b"original fixture")
        self.fake = AnalogCore()
        self.session = EmbeddedSession(self.core, self.game, self.root / "system", self.root / "saves",
            audio=False, _loader=lambda path: self.fake)

    def start(self):
        self.session.start()
        self.addCleanup(self.session.stop)
        self.session.pause()
        return self.session

    def test_signed_axes_all_ports_both_sticks_and_joypad_remain_independent(self):
        session = self.start()
        session.set_analog(0, -32768, 32767, -1234, 5678)
        session.set_analog(7, 2, -3, 4, -5)
        session.set_input(0, (1 << 8) | (1 << 12))
        session.step(1)
        before, after = self.fake.readings[-1]
        self.assertEqual(before[:4], (-32768, 32767, -1234, 5678))
        self.assertEqual(before[-4:], (2, -3, 4, -5))
        self.assertEqual(before[4:-4], (0,) * 24)
        self.assertEqual(before, after)
        self.assertEqual(self.fake.input_value, (1 << 8) | (1 << 12))
        self.assertEqual(self.fake.port_devices, [(port, 1) for port in range(4)])

    def test_axes_take_one_coherent_snapshot_for_each_retro_run(self):
        session = self.start()
        session.set_analog(0, 1000, -2000, 3000, -4000)
        self.fake.during_frame = lambda: session.set_analog(0, 5000, 6000, -7000, -8000)
        session.step(1)
        self.assertEqual(self.fake.readings[-1][0][:4], (1000, -2000, 3000, -4000))
        self.assertEqual(self.fake.readings[-1][0], self.fake.readings[-1][1])
        session.step(1)
        self.assertEqual(self.fake.readings[-1][0][:4], (5000, 6000, -7000, -8000))

    def test_return_to_neutral_does_not_latch_a_transient_axis(self):
        session = self.start()
        session.set_analog(0, 30000, -30000)
        session.set_analog(0, 0, 0)
        session.step(1)
        self.assertEqual(self.fake.readings[-1][0], (0,) * 32)

    def test_pause_clears_sticks_and_buttons_before_resume_or_frame_step(self):
        session = self.start()
        session.set_analog(0, 30000, -30000, 100, -100)
        session.set_input(0, 0xffff)
        session.step(1)
        session.pause()
        session.step(1)
        self.assertEqual(self.fake.readings[-1][0], (0,) * 32)
        self.assertEqual(self.fake.input_value, 0)

    def test_invalid_ports_axes_and_device_queries_are_rejected_or_neutral(self):
        for port in (-1, 8, True, 1.5):
            with self.subTest(port=port), self.assertRaises(ValueError):
                self.session.set_analog(port, 0, 0)
        for value in (-32769, 32768, True, 0.5, "0", None):
            for axis in range(4):
                axes = [0, 0, 0, 0]
                axes[axis] = value
                with self.subTest(value=value, axis=axis), self.assertRaises(ValueError):
                    self.session.set_analog(0, *axes)
        read = self.session._callbacks[5]
        for arguments in ((8, 5, 0, 0), (0, 5, 2, 0), (0, 5, 0, 2),
                          (0, 3, 0, 0), (0, 1, 1, 0)):
            self.assertEqual(read(*arguments), 0)
        self.assertEqual(self.session._input_state(-1, 5, 0, 0), 0)
        self.assertEqual(self.session._input_state(0, 1, 0, -1), 0)

    def test_capabilities_and_max_users_use_the_correct_c_types(self):
        self.start()
        self.assertEqual(self.fake.capabilities, (1 << 1) | (1 << 5))
        self.assertEqual(self.fake.max_users, 4)
        self.assertFalse(self.session._callbacks[0](24, None))
        self.assertFalse(self.session._callbacks[0](61, None))


class TriggerMappingTests(unittest.TestCase):
    def test_left_and_right_triggers_use_independent_press_release_hysteresis(self):
        mapper = GameplayInputMapper()
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=127), 0), 0)
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=128), .1), 1 << 12)
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=100, right_trigger=128), .2),
                         (1 << 12) | (1 << 13))
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=86, right_trigger=100), .3),
                         (1 << 12) | (1 << 13))
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=85, right_trigger=86), .4), 1 << 13)
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=100, right_trigger=85), .5), 0)
        self.assertEqual(mapper.update(1, GamepadState(left_trigger=100), .6), 0)

    def test_guide_and_quit_suppress_all_buttons_including_held_triggers(self):
        for buttons in (0x1400, 0x1730):
            mapper = GameplayInputMapper()
            self.assertEqual(mapper.update(0, GamepadState(left_trigger=255, right_trigger=255), 0),
                             (1 << 12) | (1 << 13))
            self.assertEqual(mapper.update(0, GamepadState(buttons, left_trigger=255, right_trigger=255), .1), 0)

    def test_resume_requires_trigger_and_button_release_and_does_not_replay_taps(self):
        mapper = GameplayInputMapper()
        mapper.update(0, GamepadState(0x10, left_trigger=255), 0)
        mapper.block_until_release()
        for index, state in enumerate((GamepadState(0x1000, left_trigger=255),
                                       GamepadState(left_trigger=100), GamepadState(left_trigger=32))):
            self.assertEqual(mapper.update(0, state, index + 1), 0)
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=31), 4), 0)
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=100), 5), 0)
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=128), 6), 1 << 12)
        self.assertEqual(mapper.update(0, None, 7), 0)
        self.assertEqual(mapper.update(0, GamepadState(left_trigger=100), 8), 0)


if __name__ == "__main__":
    unittest.main()
