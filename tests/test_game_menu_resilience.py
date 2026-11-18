"""The game menu opens over every core, even when the GPU will not hand back the paused frame."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from redux.console_ui import ConsoleApp
from redux.libretro_host import EmbeddedSession


class ReadbackFailureTests(unittest.TestCase):
    def session(self, gl):
        session = object.__new__(EmbeddedSession)
        session._readback_requested = True
        session._gl = gl
        session._hw_frame = (12, 640, 480)
        session._hw = {"bottom_left": True}
        session._rotation = 0
        session._lock = threading.Lock()
        session._frame = None
        session.messages = []
        return session

    def test_a_refused_gpu_read_is_recorded_and_does_not_raise(self):
        # Nintendo 64 (an OpenGL core) reads the frame when it pauses; a driver error there
        # used to fail the pause, so the menu never opened over a frozen game.
        session = self.session(SimpleNamespace(read_framebuffer=Mock(side_effect=OSError("glReadPixels failed"))))
        session._readback()
        session._readback()
        self.assertIsNone(session._frame)
        self.assertFalse(session._readback_requested)
        self.assertEqual(len(session.messages), 1, "the same failure is recorded once")
        self.assertIn("glReadPixels failed", session.messages[0])

    def test_a_good_read_still_stores_the_frame(self):
        session = self.session(SimpleNamespace(read_framebuffer=Mock(return_value=b"\0" * 640 * 480 * 3)))
        session._readback()
        self.assertEqual((session._frame.width, session._frame.height, session._frame.sequence), (640, 480, 12))
        self.assertEqual(session.messages, [])


class GameMenuOpensAnywayTests(unittest.TestCase):
    def test_menu_opens_with_its_stop_option_when_pause_fails(self):
        app = object.__new__(ConsoleApp)
        app.busy = False
        app.held_keys = {"x"}
        app.embedded = SimpleNamespace(pause=Mock(side_effect=RuntimeError("The core did not complete pause in time.")), play_seconds=42)
        app.embedded_game = {"title": "Super Mario 64", "system": "n64"}
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items, "message": message})
        with patch("redux.console_ui.log_ui_error") as log:
            app.game_menu()
        self.assertEqual(app.overlay["title"], "Game paused")
        self.assertIn("Stop game and return to library", [label for label, _ in app.overlay["items"]])
        self.assertIn("did not pause cleanly", app.overlay["message"])
        log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
