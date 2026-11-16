"""Controller focus on the System bar, its sections and the menus opened from them."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from redux.console_ui import ConsoleApp


def system_bar_app():
    app = object.__new__(ConsoleApp)
    app.overlay = None
    app.targets = []
    app.callbacks = {}
    for name in ("panel", "display", "icon", "rect", "text"):
        setattr(app, name, lambda *a, **kw: None)
    app.target = lambda key, bounds, callback, label=None: app.callbacks.__setitem__(key, callback)
    return app


class SystemMenuFocusTests(unittest.TestCase):
    def test_a_menu_opened_from_the_bar_keeps_its_focus_while_the_bar_draws_behind_it(self):
        # Exit opened from the bar: the bar used to reset focus to Home on every frame, the
        # overlay put it back on its first row, and the two redrew forever; Down did nothing.
        app = system_bar_app()
        app.focus = "overlay:2"
        app.overlay = {"title": "Exit Redux?", "items": [], "previous_focus": "system:exit"}
        app._system_menu()
        self.assertEqual(app.focus, "overlay:2")

    def test_the_bar_still_lands_on_home_when_nothing_is_focused(self):
        app = system_bar_app()
        app.focus = "nav:home"
        app._system_menu()
        self.assertEqual(app.focus, "system:home")
        self.assertIn("system:exit", app.callbacks)

    def test_back_from_a_menu_returns_to_the_row_that_opened_it(self):
        app = object.__new__(ConsoleApp)
        app.busy = False
        app.embedded = None
        app.schedule_draw = Mock()
        app.overlay = {"title": "Set up drive D:", "items": [], "previous_focus": "storage:D:"}
        app.focus = "overlay:1"
        app.back()
        self.assertIsNone(app.overlay)
        self.assertEqual(app.focus, "storage:D:")

    def test_saves_page_starts_on_its_own_button_not_the_side_menu(self):
        app = object.__new__(ConsoleApp)
        app.embedded = None
        app.history = SimpleNamespace(push=lambda state: None)
        app.screen = "settings"; app.view = "all"; app.system_filter = None; app.collection_id = None
        app.search_text = ""; app.facet_filters = {}; app.facet_tab = "system"; app.sort = "title"
        app.details_tab = "play"; app.keyboard_visible = False; app.pages = {}; app.overlay = None
        app.focus = "settings:savegame"; app.refresh = lambda: None
        for selected, expected in ((None, "saves:library"), ({"id": 1}, "saves:create")):
            with self.subTest(selected=bool(selected)):
                app.selected_id = None
                app.selected = lambda value=selected: value
                app.navigate("saves")
                self.assertEqual(app.focus, expected)


if __name__ == "__main__":
    unittest.main()
