"""Controller-operated fullscreen presentation without a window or core."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from redux.console_ui import ConsoleApp
from redux.display_preferences import DisplayProfile


class WindowState:
    def __init__(self):
        self.fullscreen = False

    def attributes(self, name, *values):
        assert name == "-fullscreen"
        if values:
            self.fullscreen = bool(values[0])
        return self.fullscreen


class ControllerDisplayTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(ConsoleApp)
        app = self.app
        app.root = WindowState()
        app.busy = False
        app.overlay = None
        app.embedded = None
        app.screen = "settings"
        app.settings_section = "about"
        app.focus = "settings:fullscreen"
        app.settings_menu = False  # the About page itself, not the System menu
        app.all_games = []
        app.controller = None
        app.firmware_directory = lambda: Path("C:/fixtures/bios")
        app.held_keys = set()
        app.preferences = {}
        app.library = SimpleNamespace(root=Path("C:/fixtures/library"))
        app.targets, app.callbacks = [], {}
        app.status = SimpleNamespace(get=lambda: "Ready")
        app.schedule_draw = Mock()
        app.notice = Mock()
        app._save_preferences = Mock(side_effect=lambda:
            app.preferences.update(fullscreen=app.root.attributes("-fullscreen")))
        self.drawn = []
        app.text = app.display = lambda x, y, value, *args, **kwargs: self.drawn.append(str(value))
        app.panel = app.rect = app.icon = Mock()
        app.fit_text = lambda value, *args, **kwargs: str(value)
        self.preferences = SimpleNamespace(get=Mock(return_value=DisplayProfile()), set=Mock())
        patch("redux.display_preferences.DisplayPreferences", return_value=self.preferences).start()
        self.addCleanup(patch.stopall)

    def render_settings(self):
        self.app.targets, self.app.callbacks = [], {}
        self.drawn.clear()
        self.app._settings()

    def paused_display(self):
        self.app.screen = "gameplay"
        self.app.embedded = SimpleNamespace(pause=Mock(), resume=Mock())
        self.app.embedded_game = {"system": "saturn", "title": "Fixture game"}
        self.app.focus = "gameplay:menu"
        self.app.video_filter_menu()
        self.render_display()

    def render_display(self):
        self.app.targets, self.app.callbacks = [], {}
        self.app._display_panel(self.app.overlay)

    def test_system_display_fullscreen_is_reachable_by_dpad_and_a_and_shows_live_state(self):
        self.render_settings()
        self.assertIn("OFF · A fills the display", self.drawn)
        self.assertEqual(self.app.focus, "settings:fullscreen")
        self.app.input_action("confirm")
        self.assertTrue(self.app.root.fullscreen)
        self.assertTrue(self.app.preferences["fullscreen"])
        self.assertEqual(self.app.focus, "settings:fullscreen")
        self.render_settings()
        self.assertIn("ON · A switches to windowed mode", self.drawn)
        self.app.input_action("confirm")
        self.assertFalse(self.app.root.fullscreen)
        self.assertFalse(self.app.preferences["fullscreen"])
        self.app.notice.assert_not_called()

    def test_paused_display_fullscreen_uses_a_and_retains_panel_focus_without_resuming_game(self):
        self.paused_display()
        index = next(index for index, (label, _) in enumerate(self.app.overlay["items"])
                     if label.startswith("FULLSCREEN"))
        self.assertIn("OFF", self.app.overlay["items"][index][0])
        for _ in range(index):
            self.app.input_action("down")
        self.assertEqual(self.app.focus, f"overlay:{index}")
        self.assertIn(self.app.focus, self.app.callbacks)
        self.app.input_action("confirm")
        self.assertTrue(self.app.root.fullscreen)
        self.assertEqual(self.app.overlay["kind"], "display_panel")
        self.assertEqual(self.app.focus, f"overlay:{index}")
        self.assertIn("ON", self.app.overlay["items"][index][0])
        self.app.embedded.pause.assert_not_called()
        self.app.embedded.resume.assert_not_called()
        self.preferences.set.assert_not_called()
        self.app._save_preferences.assert_called_once_with()

    def test_display_dpad_adjustment_and_direct_shortcut_refresh_fullscreen_state_and_keep_focus(self):
        self.paused_display()
        index = self.app.overlay["fields"].index("fullscreen")
        self.app.focus = f"overlay:{index}"
        self.app.input_action("right")
        self.assertTrue(self.app.root.fullscreen)
        self.assertEqual(self.app.focus, f"overlay:{index}")
        self.app.input_action("left")
        self.assertFalse(self.app.root.fullscreen)
        self.assertEqual(self.app.focus, f"overlay:{index}")
        self.app.fullscreen()  # The existing F11 binding invokes this same action.
        self.assertTrue(self.app.root.fullscreen)
        self.assertIn("ON", self.app.overlay["items"][index][0])
        self.assertEqual(self.app.focus, f"overlay:{index}")

    def test_display_back_returns_to_pause_menu_and_all_ten_actions_fit_one_panel(self):
        self.paused_display()
        self.assertEqual(len(self.app.overlay["items"]), 10)
        self.assertEqual(len(self.app.targets), 10)
        self.assertTrue(all(0 <= t.bounds[0] < t.bounds[2] <= 1280 and
                            292 <= t.bounds[1] < t.bounds[3] <= 720 for t in self.app.targets))
        self.app.game_menu = Mock()
        self.app.input_action("back")
        self.app.game_menu.assert_called_once_with()
        self.app.embedded.resume.assert_not_called()

    def test_visible_footer_uses_controller_menu_paths_without_keyboard_shortcut_hints(self):
        for screen, overlay in (("home", None), ("settings", None), ("library", None),
                                ("gameplay", None), ("gameplay", {"title": "Display"})):
            with self.subTest(screen=screen, overlay=overlay):
                self.drawn.clear()
                self.app.screen, self.app.overlay = screen, overlay
                self.app._footer()
                text = " ".join(self.drawn)
                self.assertNotIn("F11", text)
                self.assertNotIn("F10", text)
                self.assertNotIn("Esc", text)
                if screen == "gameplay" and overlay is None:
                    self.assertIn("Home / Guide", text)
                    self.assertIn("Select + Start", text)
                    self.assertIn("Pause → Display", text)
                elif screen != "home":
                    self.assertIn("Select", text)
                    self.assertIn("Back", text)


if __name__ == "__main__":
    unittest.main()
