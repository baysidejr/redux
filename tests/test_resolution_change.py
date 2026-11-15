import tempfile
import unittest
from pathlib import Path

from redux.display_preferences import DisplayPreferences, DisplayProfile, effective_resolution
from redux.embedded import EmbeddedGame


class FakeHost:
    fail_at = set()

    def __init__(self, log, resolution):
        self.log, self.resolution, self.paused = log, resolution, False

    def start(self):
        self.log.append(("start", self.resolution))
        if self.resolution in FakeHost.fail_at:
            raise RuntimeError("core refused")

    def stop(self): self.log.append(("stop", self.resolution))
    def pause(self): self.paused = True; self.log.append(("pause", self.resolution))
    def step(self, frames): self.log.append(("step", frames))

    def save_state(self, path):
        Path(path).write_bytes(b"state"); self.log.append(("save", self.resolution))

    def load_state(self, path):
        assert Path(path).read_bytes() == b"state"
        self.log.append(("load", self.resolution))


def game(directory, system="n64", resolution=4, paused=True):
    session = object.__new__(EmbeddedGame)
    session.game = {"system": system}
    session.states = Path(directory)
    session.started, session.finished_seconds = 1.0, None
    import threading
    session._lock = threading.RLock()
    session.log = []
    session.resolution = resolution
    session._make_host = lambda: FakeHost(session.log, session.resolution)
    session.host = session._make_host()
    session.host.paused = paused
    return session


class ResolutionChangeTests(unittest.TestCase):
    def setUp(self):
        FakeHost.fail_at = set()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_the_emulator_restarts_at_the_new_resolution_where_the_game_was(self):
        session = game(self.directory.name)
        self.assertEqual(session.change_resolution(2), 2)
        self.assertEqual(session.log, [("save", 4), ("stop", 4), ("start", 2), ("pause", 2), ("load", 2), ("step", 3)])
        self.assertEqual(list(Path(self.directory.name).iterdir()), [], "the temporary state is removed")

    def test_an_unoffered_factor_or_the_same_one_changes_nothing(self):
        session = game(self.directory.name)
        self.assertEqual(session.change_resolution(4), 4)
        self.assertEqual(session.log, [])
        psx = game(self.directory.name, "psx", 1)
        self.assertEqual(psx.change_resolution(3), 1, "PlayStation offers 1x, 2x and 4x")
        self.assertEqual(psx.log, [])

    def test_a_refused_resolution_falls_back_to_the_previous_one_and_says_so(self):
        FakeHost.fail_at = {8}
        session = game(self.directory.name)
        with self.assertRaisesRegex(RuntimeError, "back at the previous one"):
            session.change_resolution(8)
        self.assertEqual(session.resolution, 4)
        self.assertIn(("load", 4), session.log)


class ResolutionSettingTests(unittest.TestCase):
    def test_the_choice_is_saved_per_system_and_zero_means_the_system_default(self):
        with tempfile.TemporaryDirectory() as root:
            preferences = DisplayPreferences(root)
            self.assertEqual(effective_resolution(preferences.get("n64"), "n64"), 4)
            self.assertEqual(effective_resolution(preferences.get("psx"), "psx"), 1)
            preferences.set("n64", resolution=2)
            self.assertEqual(effective_resolution(DisplayPreferences(root).get("n64"), "n64"), 2)
            self.assertEqual(effective_resolution(DisplayProfile(resolution=3), "psx"), 1, "PlayStation has no 3x")
            with self.assertRaises(ValueError):
                preferences.set("n64", resolution=17)


if __name__ == "__main__":
    unittest.main()
