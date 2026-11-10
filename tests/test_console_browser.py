"""Controller browser focus/async tests with injected listings and scheduler."""
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from redux.console_browser import ConsoleFileBrowser
from redux.file_browser import BrowserEntry, BrowserListing


class BrowserHarness(ConsoleFileBrowser):
    def __init__(self):
        self.screen, self.focus, self.overlay = "library", "game:7", None
        self.busy, self.closing = False, False
        self.library = SimpleNamespace(root=Path("C:/library"))
        self.root = SimpleNamespace(after=Mock())
        self.schedule_draw = Mock()
        self.workers = []
        self._browser_start_worker = self.workers.append
        self._browser_list_locations = Mock(return_value=(BrowserEntry(Path("C:/"), "C: drive", True, kind="drive"),))
        self._browser_list_directory = Mock(side_effect=lambda path, **kwargs:
            BrowserListing(path, (), path.parent if path.parent != path else None))
        self.targets, self.callbacks, self.drawn = [], {}, []

    def display(self, x, y, value, *args, **kwargs):
        self.drawn.append(("text", x, y, str(value)))

    def panel(self, *args, **kwargs):
        self.drawn.append(("panel", *args))

    def rect(self, *args, **kwargs):
        self.drawn.append(("rect", *args))

    def fit_text(self, value, *args, **kwargs):
        return str(value)

    def target(self, key, bounds, callback, label):
        self.targets.append(SimpleNamespace(key=key, bounds=bounds, label=label))
        self.callbacks[key] = callback

    def _controller_hint(self, key, label, *args, **kwargs):
        self.drawn.append(("hint", key, label))

    def load(self, worker=0):
        self.workers.pop(worker)()
        self._browser_poll_results()

    def choose(self, index, action="confirm"):
        state = self.overlay["browser"]
        state.focus_index = index
        self.focus = f"browser:row:{index}"
        return self.browser_input(action)


class ConsoleBrowserTests(unittest.TestCase):
    def setUp(self):
        self.app = BrowserHarness()
        self.selected = Mock()
        self.base = Path("C:/Games")
        self.child = self.base / "Saturn"
        self.entries = (BrowserEntry(self.child, "Saturn", True),
                        BrowserEntry(self.base / "one.cue", "one.cue", False, 1024, "file"),
                        BrowserEntry(self.base / "two.iso", "two.iso", False, 307502832, "file"))
        self.child_entries = (BrowserEntry(self.child / "three.chd", "three.chd", False, 4096, "file"),)
        def listing(path, **kwargs):
            entries = self.entries if path == self.base else self.child_entries if path == self.child else ()
            return BrowserListing(path, entries, path.parent if path.parent != path else None)
        self.app._browser_list_directory.side_effect = listing
        patch("redux.ui_assets.project_root", return_value=Path("C:/application")).start()
        self.addCleanup(patch.stopall)

    def open(self, **kwargs):
        self.app.open_file_browser("Add media", on_select=self.selected, initialdir=self.base, **kwargs)
        self.app.load()
        return self.app.overlay["browser"]

    def test_listings_are_deferred_and_native_worker_never_schedules_tk_or_selects(self):
        threads = []
        main = threading.get_ident()
        self.app._browser_list_directory.side_effect = lambda path, **kwargs: (
            threads.append(threading.get_ident()) or BrowserListing(path, self.entries, path.parent))
        self.app.open_file_browser("Add media", on_select=self.selected, initialdir=self.base)
        state = self.app.overlay["browser"]
        self.assertTrue(state.loading)
        self.app._browser_list_directory.assert_not_called()
        self.assertEqual(len(self.app.workers), 1)
        after_calls = self.app.root.after.call_count
        draw_calls = self.app.schedule_draw.call_count
        worker = threading.Thread(target=self.app.workers.pop())
        worker.start(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertNotEqual(threads, [main])
        self.assertTrue(state.loading)
        self.assertEqual(self.app.root.after.call_count, after_calls)
        self.assertEqual(self.app.schedule_draw.call_count, draw_calls)
        self.app._browser_poll_results()
        self.assertFalse(state.loading)
        self.assertEqual(state.entries, self.entries)
        self.selected.assert_not_called()

    def test_folder_a_parent_b_restores_focus_then_locations_b_restores_previous_overlay(self):
        previous = {"title": "Disc options", "items": [("Add", lambda: None)]}
        self.app.overlay = previous
        original_focus = self.app.focus
        state = self.open()
        self.assertTrue(self.app.choose(0))
        self.assertEqual(state.path, self.child)
        self.app.load()
        self.assertTrue(self.app.browser_input("back"))
        self.app.load()
        self.assertEqual(state.path, self.base)
        self.assertEqual(state.entries[state.focus_index].path, self.child)
        self.assertEqual(self.app.focus, "browser:row:0")
        self.app.focus = "browser:locations"
        self.app.browser_input("confirm"); self.app.load()
        self.assertIsNone(state.path)
        self.app.browser_input("back")
        self.assertIs(self.app.overlay, previous)
        self.assertEqual((self.app.screen, self.app.focus), ("library", original_focus))
        self.selected.assert_not_called()

    def test_multi_selection_survives_folders_and_y_submits_only_selected_paths(self):
        state = self.open(multiple=True)
        self.app.choose(1, "details")
        self.app.choose(2, "confirm")
        self.app.choose(2, "x")  # Toggle it back off.
        self.app.choose(0)
        self.app.load()
        self.app.choose(0, "favorite")
        self.assertEqual(list(state.selected.values()), [self.base / "one.cue", self.child / "three.chd"])
        self.app.browser_input("search")
        self.selected.assert_called_once_with([self.base / "one.cue", self.child / "three.chd"])
        self.assertIsNone(self.app.overlay)
        self.assertEqual(self.app.focus, "game:7")

    def test_single_file_a_and_folder_visible_use_button_return_paths(self):
        self.open()
        self.app.choose(1)
        self.selected.assert_called_once_with(self.base / "one.cue")
        self.selected.reset_mock()
        self.app._browser_list_directory.side_effect = lambda path, **kwargs: BrowserListing(path, (), path.parent)
        state = self.open(mode="folder")
        self.app._file_browser_panel()
        self.assertIn("browser:use", self.app.callbacks)
        self.assertTrue(self.app._browser_can_use(state))
        self.app.focus = "browser:use"
        self.app.browser_input("a")
        self.selected.assert_called_once_with(self.base)
        self.assertIsNone(self.app.overlay)

    def test_dpad_scroll_pages_and_shoulder_wheel_aliases_keep_focused_row_visible(self):
        entries = tuple(BrowserEntry(self.base / f"{index:02}.iso", f"{index:02}.iso", False, 100, "file")
                        for index in range(27))
        self.app._browser_list_directory.side_effect = lambda path, **kwargs: BrowserListing(path, entries, path.parent)
        state = self.open(multiple=True)
        for _ in range(12):
            self.app.browser_input("down")
        self.assertEqual((state.focus_index, self.app.focus), (12, "browser:row:12"))
        self.app._file_browser_panel()
        rows = [target for target in self.app.targets if target.key.startswith("browser:row:")]
        self.assertEqual([target.key for target in rows], [f"browser:row:{index}" for index in range(10, 20)])
        self.assertEqual(len(self.app.targets), 13)
        self.app.browser_input("next_tab")
        self.assertEqual(state.focus_index, 22)
        self.app.browser_input("lb")
        self.assertEqual(state.focus_index, 12)
        self.app.browser_input("page_up")
        self.assertEqual(state.focus_index, 2)
        self.app.browser_input("rb")
        self.assertEqual(state.focus_index, 12)
        self.app.browser_input("page_down")
        self.app.browser_input("page_down")
        self.assertEqual(state.focus_index, 26)
        self.app.browser_input("down")
        self.assertEqual(self.app.focus, "browser:use")
        self.app.browser_input("left")
        self.assertEqual(self.app.focus, "browser:cancel")
        self.app.browser_input("up")
        self.assertEqual(self.app.focus, "browser:row:26")

    def test_stale_completions_and_old_callbacks_cannot_replace_new_folder_or_closed_browser(self):
        self.app.open_file_browser("Add media", on_select=self.selected, initialdir=self.base)
        state = self.app.overlay["browser"]
        self.app._browser_request(state, self.child)
        self.app.load(worker=1)
        self.assertEqual(state.path, self.child)
        self.assertEqual(state.entries, self.child_entries)
        self.app.load(worker=0)
        self.assertEqual(state.entries, self.child_entries)
        self.app._file_browser_panel()
        old_location = self.app.callbacks["browser:locations"]
        self.app.browser_input("guide")
        old_location()
        self.assertEqual(self.app.workers, [])
        self.assertIsNone(self.app.overlay)
        self.selected.assert_not_called()

    def test_closed_browser_result_cannot_change_a_new_browser(self):
        self.app.open_file_browser("First", on_select=self.selected, initialdir=self.base)
        self.app.browser_input("home")
        self.app.open_file_browser("Second", on_select=self.selected, initialdir=self.child)
        current = self.app.overlay["browser"]
        self.app.load(worker=0)
        self.assertTrue(current.loading)
        self.assertEqual(current.path, self.child)
        self.app.load(worker=0)
        self.assertFalse(current.loading)
        self.assertEqual(current.entries, self.child_entries)

    def test_removed_folder_error_keeps_locations_cancel_and_parent_navigation_available(self):
        self.app._browser_list_directory.side_effect = OSError("Drive was disconnected")
        state = self.open()
        self.assertIn("disconnected", state.error)
        self.app._file_browser_panel()
        self.assertEqual({target.key for target in self.app.targets},
                         {"browser:locations", "browser:cancel", "browser:use"})
        self.assertFalse(self.app._browser_can_use(state))
        self.app.browser_input("back")
        self.assertEqual(state.path, self.base.parent)
        self.app.focus = "browser:locations"
        self.app.browser_input("confirm")
        self.app.load(worker=1)
        self.assertIsNone(state.path)
        self.assertFalse(state.error)
        self.app.load(worker=0)  # Error from the abandoned parent is ignored.
        self.assertFalse(state.error)
        self.app.browser_input("back")
        self.assertIsNone(self.app.overlay)

    def test_loading_does_not_steal_footer_focus_and_empty_file_listing_can_cancel(self):
        self.app._browser_list_directory.side_effect = lambda path, **kwargs: BrowserListing(path, (), path.parent)
        self.app.open_file_browser("Add media", on_select=self.selected, initialdir=self.base)
        self.app.browser_input("right")
        self.assertEqual(self.app.focus, "browser:use")
        self.app.load()
        self.assertEqual(self.app.focus, "browser:use")
        self.app.browser_input("y")
        self.selected.assert_not_called()
        self.app.browser_input("left")
        self.app.browser_input("confirm")
        self.assertIsNone(self.app.overlay)

    def test_initial_loading_placeholder_keeps_focus_valid_until_first_location_arrives(self):
        self.app.open_file_browser("Scan a folder", mode="folder", on_select=self.selected)
        state = self.app.overlay["browser"]
        self.assertTrue(state.loading)
        self.app._file_browser_panel()
        keys = {target.key for target in self.app.targets}
        self.assertEqual(self.app.focus, "browser:row:0")
        self.assertIn(self.app.focus, keys, "The shell must not fall back to the Locations button while listing")
        self.app.callbacks["browser:row:0"]()
        self.selected.assert_not_called()
        self.app.load()
        self.assertFalse(state.loading)
        self.assertEqual(self.app.focus, "browser:row:0")
        self.assertEqual(state.entries[0].label, "C: drive")
        self.app.browser_input("confirm")
        self.assertEqual(state.path, Path("C:/"))
        self.assertTrue(state.loading)

    def test_filters_folder_mode_and_locations_are_passed_only_to_injected_worker(self):
        self.open(mode="folder", extensions=".png", multiple=True)
        self.app._browser_list_directory.assert_called_once_with(self.base, extensions=(".png",), folders_only=True)
        self.assertFalse(self.app.overlay["browser"].multiple)
        self.app.browser_input("guide")
        self.app.open_file_browser("Choose storage", mode="folder", on_select=self.selected)
        self.app.load()
        self.app._browser_list_locations.assert_called_once_with(self.app.library.root, Path("C:/application"))

    def test_panel_is_full_screen_has_bounded_rows_sizes_and_controller_reachable_actions(self):
        self.open(multiple=True)
        self.app.choose(1, "details")
        self.app._file_browser_panel()
        self.assertIn(("rect", 0, 0, 1280, 720, "#0b0c10"), self.app.drawn)
        text = [value[3] for value in self.app.drawn if value[0] == "text"]
        self.assertIn(str(self.base), text)
        self.assertIn("1.0 KB", text)
        self.assertIn("293.3 MB", text)
        self.assertIn("Add 1 file", text)
        self.assertEqual(len({target.key for target in self.app.targets}), len(self.app.targets))
        self.assertTrue(all(0 <= t.bounds[0] < t.bounds[2] <= 1280 and
                            0 <= t.bounds[1] < t.bounds[3] <= 720 for t in self.app.targets))
        self.assertIn(("hint", "A", "Open / Select"), self.app.drawn)
        self.assertIn(("hint", "B", "Back"), self.app.drawn)
        self.assertIn(("hint", "X", "Select"), self.app.drawn)
        self.assertIn(("hint", "Y", "Add selected"), self.app.drawn)

    def test_invalid_options_and_busy_open_leave_underlying_view_unchanged(self):
        with self.assertRaises(ValueError):
            self.app.open_file_browser("Invalid", mode="save", on_select=self.selected)
        with self.assertRaises(ValueError):
            self.app.open_file_browser("Invalid", on_select=None)
        self.app.busy = True
        self.app.open_file_browser("Busy", on_select=self.selected)
        self.assertIsNone(self.app.overlay)
        self.assertEqual(self.app.workers, [])
        self.assertFalse(self.app.browser_input("down"))
        self.assertFalse(self.app.browser_back())


if __name__ == "__main__":
    unittest.main()
