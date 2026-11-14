"""Menu sounds: cue mapping, the player, silence while a game runs, and the About switch."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from redux.console_ui import ConsoleApp
from redux.console_secondary import SecondaryScreens
from redux.ui_sounds import MenuSounds, cue_for_action, CUES


def sound_files(root):
    for cue in CUES:
        (Path(root) / f"{cue}.wav").write_bytes(b"RIFF")


class CueTests(unittest.TestCase):
    def test_actions_map_to_cues(self):
        self.assertEqual([cue_for_action(a) for a in ("left", "a", "back", "menu", "x")], ["move", "select", "back", None, None])

    def test_player_honours_the_switch_and_missing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            played = []
            sounds = MenuSounds(root=temporary, player=played.append)
            self.assertFalse(sounds.available());self.assertFalse(sounds.play("move"))
            sound_files(temporary)
            sounds = MenuSounds(root=temporary, player=played.append)
            self.assertTrue(sounds.available());self.assertTrue(sounds.play("move"))
            self.assertEqual(played[-1], Path(temporary) / "move.wav")
            self.assertFalse(sounds.play("move", silent=True))
            sounds.enabled = False
            self.assertFalse(sounds.play("select"));self.assertEqual(len(played), 1)
            def broken(path):
                raise OSError("device busy")
            self.assertFalse(MenuSounds(root=temporary, player=broken).play("back"))


class AppCueTests(unittest.TestCase):
    def app(self, root):
        app = object.__new__(ConsoleApp)
        app.played = []
        app.sounds = MenuSounds(root=root, player=lambda path: app.played.append(path.stem))
        app.embedded = None;app.playback = SimpleNamespace(active=lambda: []);app.overlay = None;app.focus = "welcome:card"
        app.preferences = {};app._save_preferences = lambda: None;app.schedule_draw = lambda: None
        return app

    def test_cues_follow_actions_open_for_system_and_stay_quiet_in_game(self):
        with tempfile.TemporaryDirectory() as temporary:
            sound_files(temporary);app = self.app(temporary)
            app.play_cue("right");app.play_cue("a");app.play_cue("b");app.play_cue("menu")
            self.assertEqual(app.played, ["move", "select", "back"])
            app.focus = "nav:settings";app.play_cue("a")
            self.assertEqual(app.played[-1], "open")
            app.embedded = object();app.play_cue("left")
            self.assertEqual(len(app.played), 4)
            app.embedded = None;app.toggle_menu_sounds()
            self.assertFalse(app.sounds.enabled);self.assertEqual(app.preferences["menu_sounds"], False)
            app.play_cue("left");self.assertEqual(len(app.played), 4)
            app.toggle_menu_sounds();self.assertEqual(app.played[-1], "select")

    def test_about_has_the_switch_between_updates_and_fullscreen(self):
        with tempfile.TemporaryDirectory() as temporary:
            sound_files(temporary);app = self.app(temporary)
            app.screen = "settings";app.settings_menu = False;app.settings_section = "about";app.targets = [];app.callbacks = {}
            app.all_games = [];app.busy = False;app.library = SimpleNamespace(root=Path(temporary));app.firmware_directory = lambda: Path("bios")
            app.root = SimpleNamespace(attributes=lambda *a: False);app.texts = []
            app.display = app.text = lambda x, y, value, *a, **k: app.texts.append(str(value))
            app.panel = app.rect = app.icon = lambda *a, **k: None;app.fit_text = lambda value, *a, **k: str(value)
            app.target = lambda key, bounds, callback, label="": (app.targets.append(key), app.callbacks.__setitem__(key, callback))
            app.fullscreen = lambda: None;app.updates_menu = lambda: None;app.updates_row_detail = lambda: "x"
            SecondaryScreens._settings(app)
            self.assertEqual(app.targets, ["settings:updates", "settings:sounds", "settings:fullscreen"])
            self.assertIn("ON · soft cues when you move, select and go back", app.texts)
            app.callbacks["settings:sounds"]()
            self.assertFalse(app.sounds.enabled)


if __name__ == "__main__":
    unittest.main()
