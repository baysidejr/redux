"""Living-room shell behavior without opening Tk or changing Windows settings."""
import contextlib
import io
from pathlib import Path
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

from redux.__main__ import main
from redux.console_ui import ConsoleApp
from redux.windows_activation import HOME_ACTIVATION, parse_activation
from redux.windows_gaming import GamingExperienceState
from redux.windows_startup import StartupStatus
from redux.ui_assets import project_root
import tempfile
import json


class ConsoleWindowsTests(unittest.TestCase):
    def test_failed_repeat_activation_never_opens_another_window_or_traceback(self):
        from redux.app import main as gui
        from redux.windows_activation import ActivationError
        instance=MagicMock()
        instance.acquire.side_effect=ActivationError("Receiver unavailable")
        with patch("redux.windows_activation.SingleInstance",return_value=instance), \
             patch("redux.app.tk.Tk") as window, \
             patch("redux.windows_activation.append_activation_log") as log:
            gui(activation=parse_activation([]))
        window.assert_not_called()
        instance.close.assert_called_once_with()
        self.assertIn("activation failed",log.call_args.kwargs["outcome"])

    def test_hidden_startup_canvas_waits_for_a_real_size_before_drawing(self):
        app=object.__new__(ConsoleApp)
        app.closing=False;app.redraw_id="pending"
        app.canvas=SimpleNamespace(winfo_width=lambda:1,winfo_height=lambda:1,delete=Mock())
        app.render()
        self.assertIsNone(app.redraw_id)
        app.canvas.delete.assert_not_called()

    def bare(self):
        app = object.__new__(ConsoleApp)
        app.busy = False
        app.embedded = None
        app.playback = None
        app.overlay = None
        app.home_activation = False
        app.held_keys = {"x"}
        app.preferences = {"fullscreen": True}
        app.root = SimpleNamespace(attributes=Mock(), deiconify=Mock(), lift=Mock(), focus_force=Mock())
        app.canvas = SimpleNamespace(focus_set=Mock())
        app.schedule_draw = Mock()
        app.navigate = Mock()
        app.status = SimpleNamespace(set=Mock())
        app.notice = Mock()
        app.windows_events = queue.Queue()
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items, "message": message})
        return app

    def test_home_activation_pauses_current_game_without_discarding_it(self):
        app = self.bare()
        game = SimpleNamespace(pause=Mock(), play_seconds=3, game={})
        app.embedded = game
        app.embedded_game = {"title": "Redux self test", "system": "nes"}
        app.handle_activation(parse_activation([HOME_ACTIVATION]))
        self.assertIs(app.embedded, game)
        game.pause.assert_called_once_with()
        self.assertEqual(app.overlay["title"], "Game paused")
        self.assertEqual(app.held_keys, set())
        self.assertTrue(app.input_release_gate)
        self.assertTrue(app.home_activation)
        app.navigate.assert_not_called()
        app.root.attributes.assert_called_once_with("-fullscreen", True)
        app.canvas.focus_set.assert_called_once_with()

    def test_repeat_home_activation_keeps_existing_pause_panel(self):
        app = self.bare()
        app.embedded = SimpleNamespace(pause=Mock())
        panel = {"title": "Save state", "slot": 2}
        app.overlay = panel
        app.handle_activation(parse_activation([HOME_ACTIVATION]))
        self.assertIs(app.overlay, panel)
        app.embedded.pause.assert_not_called()
        app.navigate.assert_not_called()

    def test_home_activation_does_not_interrupt_disc_install(self):
        app = self.bare()
        app.busy = True
        panel = {"kind": "task", "title": "Installing disc"}
        app.overlay = panel
        app.game_menu = Mock()
        app.handle_activation(parse_activation([HOME_ACTIVATION]))
        self.assertIs(app.overlay, panel)
        app.game_menu.assert_not_called()
        app.navigate.assert_not_called()
        app.schedule_draw.assert_called_once_with()

    def test_home_activation_without_a_game_returns_home(self):
        app = self.bare()
        app.handle_activation(parse_activation([HOME_ACTIVATION]))
        app.navigate.assert_called_once_with("home", remember=False)

    def test_plain_repeat_activation_raises_window_without_resetting_navigation(self):
        app = self.bare()
        app.handle_activation(parse_activation([]))
        app.root.deiconify.assert_called_once_with()
        app.root.lift.assert_called_once_with()
        app.navigate.assert_not_called()
        app.root.attributes.assert_not_called()

    def test_fse_changes_wait_in_queue_until_ui_polls_and_latest_state_wins(self):
        app = self.bare()
        app.windows_events.put(GamingExperienceState(True, True, False))
        app.windows_events.put(GamingExperienceState(True, True, True))
        app.root.attributes.assert_not_called()
        app.poll_windows_experience()
        app.root.attributes.assert_called_once_with("-fullscreen", True)
        app.schedule_draw.assert_called_once_with()
        self.assertTrue(app.windows_events.empty())

    def test_leaving_xbox_mode_allows_normal_startup_app_exit_behavior(self):
        app = self.bare()
        app.home_activation = True
        app.windows_events.put(GamingExperienceState(True, True, False))
        app.poll_windows_experience()
        self.assertFalse(app.home_activation)
        app.root.attributes.assert_not_called()

    def test_startup_toggle_does_not_change_selected_home_or_visual_preferences(self):
        app = self.bare()
        app.home_activation = True
        before = dict(app.preferences)
        with patch("redux.console_windows.application_directory", return_value=Path("C:/Redux")), \
             patch("redux.console_windows.startup_status", return_value=StartupStatus()), \
             patch("redux.console_windows.set_start_with_windows", return_value=StartupStatus(enabled=True)) as setting, \
             patch("redux.console_windows.gaming_configuration", side_effect=AssertionError("startup must not select home")):
            app.toggle_windows_startup()
        setting.assert_called_once_with(True, Path("C:/Redux/Redux.exe"))
        self.assertTrue(app.home_activation)
        self.assertEqual(app.preferences, before)
        app.schedule_draw.assert_called_once_with()

    def test_alongside_xbox_requires_startup_enabled(self):
        app = self.bare()
        with patch("redux.console_windows.startup_status", return_value=StartupStatus()), \
             patch("redux.console_windows.set_start_with_windows") as setting:
            app.toggle_xbox_startup()
        setting.assert_not_called()
        app.notice.assert_called_once()


class ActivationEntryPointTests(unittest.TestCase):
    def test_home_uri_accepts_future_shell_arguments_without_cli_parsing(self):
        argv = [HOME_ACTIVATION, "--future-shell-option", "opaque-value"]
        with patch("redux.app.main") as gui:
            main(argv)
        gui.assert_called_once()
        activation = gui.call_args.kwargs["activation"]
        self.assertEqual(activation.argv, tuple(argv))
        self.assertTrue(activation.home)

    def test_normal_gui_startup_preserves_library_and_fullscreen_flags(self):
        with patch("redux.app.main") as gui:
            main(["--library", "library", "--startup", "--fullscreen", "gui"])
        gui.assert_called_once()
        self.assertEqual(str(gui.call_args.args[0]), "library")
        self.assertTrue(gui.call_args.kwargs["activation"].startup)
        self.assertTrue(gui.call_args.kwargs["activation"].fullscreen)

    def test_actual_cli_rejects_invalid_flags_before_work(self):
        with contextlib.redirect_stderr(io.StringIO()), patch("redux.app.main") as gui:
            with self.assertRaises(SystemExit) as result:
                main(["--library", "library", "list", "--this-option-does-not-exist"])
        self.assertEqual(result.exception.code, 2)
        gui.assert_not_called()


class FrozenPortableRootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.layout = self.root / "xbox-home"
        self.layout.mkdir()
        self.executable = self.layout / "Redux.exe"
        self.executable.write_bytes(b"fixture")
        self.marker = self.layout / "redux-launch.json"

    def tearDown(self):
        self.temporary.cleanup()

    def resolve(self):
        with patch("redux.ui_assets.sys.frozen", True, create=True), \
             patch("redux.ui_assets.sys.executable", str(self.executable)):
            return project_root()

    def test_valid_existing_absolute_marker_uses_original_portable_library_root(self):
        portable = self.root / "portable-games"
        portable.mkdir()
        self.marker.write_text(json.dumps({"portable_root": str(portable)}))
        self.assertEqual(self.resolve(), portable.resolve())

    def test_invalid_markers_fall_back_to_application_folder(self):
        examples = ["not json", "[]", "{}", "null", '{"portable_root":null}',
                    json.dumps({"portable_root": "relative-library"}),
                    json.dumps({"portable_root": str(self.root / "missing")}),
                    json.dumps({"portable_root": str(self.executable)})]
        for value in examples:
            with self.subTest(marker=value):
                self.marker.write_text(value)
                self.assertEqual(self.resolve(), self.layout.resolve())

    def test_missing_marker_retains_existing_development_tree_fallback(self):
        (self.root / "tools" / "emulators").mkdir(parents=True)
        self.assertEqual(self.resolve(), self.root.resolve())


if __name__ == "__main__":
    unittest.main()
