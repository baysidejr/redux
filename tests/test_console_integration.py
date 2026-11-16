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


class PowerAndHoverTests(unittest.TestCase):
    def bare(self):
        app = object.__new__(ConsoleApp)
        app.busy, app.embedded, app.playback = False, None, None
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items, "message": message})
        return app

    def choose(self, app, label):
        next(action for name, action in app.overlay["items"] if name == label)()

    def test_exit_offers_restart_and_shut_down_behind_a_confirmation(self):
        app = self.bare()
        closed = []
        app._close_now = lambda power=None: closed.append(power)
        app.exit_app_menu()
        self.assertEqual([label for label, _ in app.overlay["items"]], ["Keep browsing", "Exit app", "Restart PC", "Shut down PC"])
        self.choose(app, "Restart PC")
        self.assertEqual((app.overlay["title"], closed), ("Restart this PC?", []))
        self.choose(app, "Cancel")
        self.assertEqual(closed, [])
        app.exit_app_menu(); self.choose(app, "Shut down PC"); self.choose(app, "Shut down now")
        self.assertEqual(closed, ["shutdown"])

    def test_a_running_game_must_finish_before_the_pc_restarts(self):
        app = self.bare()
        app.embedded = SimpleNamespace()
        app._close_now = lambda power=None: self.fail("A running game must not be cut off by a restart")
        app.notice = lambda title, message: setattr(app, "overlay", {"title": title})
        app.exit_app_menu(); self.choose(app, "Restart PC")
        self.assertEqual(app.overlay["title"], "Finish first")

    def test_redux_closes_its_work_then_asks_windows(self):
        import redux.app
        app = self.bare()
        calls = []
        app.windows_registration, app.stage_sounds, app.controller = None, None, None
        app._stop_media_watch = app._stop_card_watch = lambda: None
        app._harvest_sessions = lambda: calls.append("harvest")
        app._save_preferences = lambda: calls.append("save")
        with patch("redux.power.power_off", lambda action: calls.append(("power", action))), \
             patch.object(redux.app.App, "close", lambda self: calls.append("closed")):
            app._close_now(power="restart")
        self.assertEqual(calls, ["harvest", "save", ("power", "restart"), "closed"])

    def test_the_windows_command_never_forces_other_apps_closed(self):
        from redux import power
        with patch("redux.windows_paths.windows_system_directory", lambda: __import__("pathlib").Path(r"C:\Windows\System32")):
            self.assertEqual(power.command("restart")[1:], ["/r", "/t", "0"])
            self.assertEqual(power.command("shutdown")[1:], ["/s", "/t", "0"])
            self.assertTrue(power.command("shutdown")[0].endswith("shutdown.exe"))
            refused = lambda *a, **k: SimpleNamespace(returncode=1190, stderr="A system shutdown has already been scheduled.", stdout="")
            with self.assertRaisesRegex(OSError, "already been scheduled"):
                power.power_off("restart", run=refused)

    def test_the_mouse_moves_the_highlight_in_menus_too(self):
        from redux.ui_model import FocusTarget
        app = self.bare()
        app.controller_navigation, app.scale, app.offset_x, app.offset_y = False, 1.0, 0, 0
        app.overlay = {"title": "Exit Redux?", "items": []}
        app.focus = "overlay:0"
        app.targets = [FocusTarget("overlay:0", (812, 178, 1248, 230)), FocusTarget("overlay:1", (812, 240, 1248, 292))]
        drawn = []
        app.schedule_draw = lambda: drawn.append(True)
        app._motion(SimpleNamespace(x=900, y=260, x_root=900, y_root=260))
        self.assertEqual(app.focus, "overlay:1")
        self.assertTrue(drawn)
