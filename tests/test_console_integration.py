"""Pure shell integration regressions. No Tk window or native core is opened."""
from queue import Queue
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux.console_ui import ConsoleApp
from redux.controllers import GameplayInputMapper


class ConsoleIntegrationTests(unittest.TestCase):
    def bare(self):
        app = object.__new__(ConsoleApp)
        app.busy = False
        return app

    def test_menu_chord_is_not_forwarded_as_game_start_and_select(self):
        app = self.bare()
        masks = {}
        app.embedded = SimpleNamespace(host=SimpleNamespace(set_input=lambda port, mask: masks.update({port: mask})))
        app.overlay, app.held_keys = None, set()
        app.gameplay_input = GameplayInputMapper()
        app.controller = SimpleNamespace(api=SimpleNamespace(state=lambda index:
            SimpleNamespace(buttons=0x30, thumb_lx=0, thumb_ly=0) if index == 0 else None))
        app._embedded_input()
        self.assertEqual(masks[0] & ((1 << 2) | (1 << 3)), 0)

    def test_exit_confirmation_pauses_embedded_game_and_releases_keyboard(self):
        app = self.bare()
        pauses = []
        app.embedded = SimpleNamespace(pause=lambda: pauses.append(True))
        app.held_keys = {"x", "up"}
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items})
        app.close()
        self.assertEqual(pauses, [True])
        self.assertEqual(app.held_keys, set())
        self.assertEqual(app.overlay["title"], "A game is still running")

    def test_explicit_system_exit_keeps_running_game_shutdown_guard_in_xbox_home(self):
        app = self.bare()
        pauses = []
        app.home_activation = True
        app.windows_experience = SimpleNamespace(query=lambda:SimpleNamespace(active=True))
        app.embedded = SimpleNamespace(pause=lambda:pauses.append(True))
        app.held_keys = {"x"}
        app.menu = lambda title,items,message="":setattr(app,"overlay",{"title":title,"items":items})
        app._close_now = lambda:self.fail("An active game must finish its guarded shutdown first")
        app.exit_app_menu()
        next(action for label,action in app.overlay["items"] if label=="Exit app")()
        self.assertEqual(pauses,[True])
        self.assertEqual(app.held_keys,set())
        self.assertEqual(app.overlay["title"],"A game is still running")

    def test_back_at_xbox_home_still_opens_home_choices_without_closing(self):
        app = self.bare()
        app.home_activation = True
        app.windows_experience = SimpleNamespace(query=lambda:SimpleNamespace(active=True))
        app.open_xbox_settings = lambda:None
        app.close = lambda:self.fail("Back must not close the selected Xbox home")
        app.menu = lambda title,items,message="":setattr(app,"overlay",{"title":title,"items":items})
        app.exit_menu()
        self.assertEqual(app.overlay["title"],"Xbox home")
        self.assertNotIn("Exit app",[label for label,_ in app.overlay["items"]])

    def test_corrupt_slot_does_not_hide_other_valid_embedded_states(self):
        app = self.bare()
        class Backend:
            def slot(self, number):
                if number == 0:
                    raise RuntimeError("Slot 1 has a damaged manifest")
                return SimpleNamespace(exists=number == 2, saved_at="2026-11-08T12:00:00Z", size=1234)
            def slots(self):
                return [self.slot(number) for number in range(10)]
        app.embedded, app.bridges = Backend(), {}
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"items": items})
        app.state_menu("load")
        labels = [label for label, callback in app.overlay["items"]]
        self.assertTrue(any(label.startswith("Slot 3") and "2026-11-08" in label for label in labels), labels)

    def test_failed_done_callback_keeps_worker_event_polling_alive(self):
        app = self.bare()
        scheduled, reported = [], []
        app.busy, app.closing = True, False
        app.events = Queue()
        def fail_done(value):
            raise OSError("Library session commit failed")
        app.events.put(("done", fail_done, None))
        app.progress = SimpleNamespace(stop=lambda: None, configure=lambda **kwargs: None)
        app.cancel_button = SimpleNamespace(configure=lambda **kwargs: None)
        app.action_buttons = []
        app.status = SimpleNamespace(set=lambda value: None)
        app.root = SimpleNamespace(after=lambda delay, callback: scheduled.append((delay, callback)),
                                   destroy=lambda: None,
                                   report_callback_exception=lambda *args: reported.append(args))
        app._callback_error = lambda *args: reported.append(args)
        with patch("redux.app.messagebox.showerror", side_effect=lambda *a, **k: reported.append(a)):
            app._poll()
        self.assertTrue(scheduled, "A failed task completion must not permanently stop event delivery")
        self.assertFalse(app.busy)

    def test_close_during_noncancellable_start_does_not_abandon_worker_result(self):
        app = self.bare()
        app.busy, app.task_cancellable = True, False
        app.embedded = None
        app.playback = SimpleNamespace(active=lambda: [])
        app.status = SimpleNamespace(set=lambda value: None)
        closed = []
        app._close_now = lambda: closed.append(True)
        app.menu = lambda *args, **kwargs: None
        app.close()
        self.assertEqual(closed, [], "A pending native launch must finish before its wrapper can be stopped safely")


if __name__ == "__main__":
    unittest.main()
