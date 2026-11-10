"""Automatic installation/controller flows without UI, optical or file IO."""
from pathlib import Path
from queue import Queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from redux.buffer_install import BufferInstallError
from redux.console_ui import ConsoleApp
from redux.controllers import InputEvent
from redux.disc_identity import DiscIdentity
from redux.library import ImportCancelled
from redux.now_playing import DiscSnapshot


class ConsoleInstallTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(ConsoleApp)
        app = self.app
        app.busy = False
        app.task_cancellable = True
        app.cancelled = threading.Event()
        app.events = Queue()
        app.task = Mock()
        app.overlay = None
        app.screen, app.focus = "home", "hero:play"
        app.held_keys = set()
        app.input_release_gate = False
        app.callbacks, app.targets = {}, []
        app.embedded = None
        app.embedded_record = None
        app.playback = SimpleNamespace(active=Mock(return_value=[]))
        app.library = SimpleNamespace(root=Path("C:/fixtures/portable-library"), start_session=Mock(return_value=42))
        app.catalogue = None
        app.preferences = {}
        app.selected_id = 9
        app.status = SimpleNamespace(set=Mock())
        app.schedule_draw = Mock()
        app.refresh = Mock()
        app.notice = Mock()
        self.identity = DiscIdentity("G:", "Saturn fixture", "saturn", "MK-TEST", "Saturn CD", "header", (),
                                     {"area_symbols": "U"}, "a" * 64)
        self.disc = DiscSnapshot("G:", "Saturn fixture", "saturn", "MK-TEST", "a" * 64,
                                 self.identity, False, {"year": 1996})
        app.discs = SimpleNamespace(active=self.disc)
        self.lease = SimpleNamespace(entry_path=Path("C:/fixtures/buffer/disc.cue"), identity=self.identity,
                                     closed=False)
        def close():
            self.lease.closed = True
            return True
        self.lease.close = Mock(side_effect=close)
        self.buffered_game = {"id": 1 << 62, "title": "Saturn fixture", "system": "saturn",
                              "bundle_hash": "b" * 64, "transient_disc": True, "size": 256}
        self.managed_game = dict(self.buffered_game, id=71, transient_disc=False, storage="managed")
        self.buffer = patch("redux.disc_buffer.buffer_disc", return_value=self.lease).start()
        self.prepare = patch("redux.disc_session.prepare_disc_game", return_value=self.buffered_game).start()
        self.install = patch("redux.buffer_install.install_buffered_disc", return_value=self.managed_game).start()
        self.capture = patch("redux.raw_acquisition.capture_raw_cd",
            side_effect=AssertionError("UI must acquire through buffer_disc only")).start()
        self.plan = patch("redux.raw_acquisition.plan_raw_capture",
            side_effect=AssertionError("Direct installation must not run a separate planning flow")).start()
        self.save_picker = patch("redux.console_ui.filedialog.asksaveasfilename",
            side_effect=AssertionError("Installation destination is automatic")).start()
        self.folder_picker = patch("redux.console_ui.filedialog.askdirectory",
            side_effect=AssertionError("Installation destination is automatic")).start()
        self.addCleanup(patch.stopall)

    def scheduled(self):
        self.app.task.assert_called_once()
        return self.app.task.call_args.args[:2]

    def no_manual_acquisition(self):
        self.capture.assert_not_called()
        self.plan.assert_not_called()
        self.save_picker.assert_not_called()
        self.folder_picker.assert_not_called()

    def choose_overlay_action(self, label):
        overlay = self.app.overlay
        index = next(index for index, (text, _) in enumerate(overlay["items"]) if text == label)
        callback = overlay["items"][index][1]
        self.app.focus = f"overlay:{index}"
        self.app.callbacks = {self.app.focus: self.app._overlay_run(overlay, callback)}
        self.app._route_controller_events([InputEvent("confirm")])

    def buffered_session(self, *, transient=True, installed=False):
        session = SimpleNamespace(game=dict(self.buffered_game, transient_disc=transient),
            media_lease=self.lease if transient else None,
            installed_game=self.managed_game if installed else None,
            play_seconds=12, paused=False, resume=Mock(), stop=Mock(), install_to_library=Mock())
        session.pause = Mock(side_effect=lambda: setattr(session, "paused", True))
        self.app.embedded = session
        self.app.embedded_game = session.game
        self.app.screen, self.app.focus = "gameplay", "gameplay:menu"
        return session

    def test_inserted_disc_a_action_installs_directly_into_portable_library_without_picker_or_bios(self):
        self.app.inserted_disc_options()
        self.choose_overlay_action("Install disc to library")
        work, done = self.scheduled()
        self.buffer.assert_not_called()
        result = work()
        self.buffer.assert_called_once()
        self.assertEqual(self.buffer.call_args.args, ("G:", self.app.library.root / "cache" / "disc-install"))
        self.assertEqual(self.buffer.call_args.kwargs["expected_fingerprint"], self.disc.fingerprint)
        self.assertIs(self.buffer.call_args.kwargs["cancelled"], self.app.cancelled)
        self.prepare.assert_called_once_with(self.app.library, self.lease,
            metadata=self.disc.metadata, cancelled=self.app.cancelled)
        self.install.assert_called_once()
        self.assertEqual(self.install.call_args.args, (self.app.library, self.lease, self.buffered_game))
        self.assertIs(self.install.call_args.kwargs["cancelled"], self.app.cancelled)
        self.lease.close.assert_called_once_with()
        done(result)
        self.assertEqual(self.app.selected_id, 71)
        self.app.refresh.assert_called_once_with()
        self.app.library.start_session.assert_not_called()
        self.app.status.set.assert_called_once_with("Installed to library • ready to play")
        self.no_manual_acquisition()

    def test_direct_install_progress_reports_read_and_managed_copy_phases(self):
        def buffered(*args, **kwargs):
            kwargs["progress"](10, 20, "verify_source")
            return self.lease
        def installed(*args, **kwargs):
            kwargs["progress"](1024 * 1024)
            return self.managed_game
        self.buffer.side_effect, self.install.side_effect = buffered, installed
        self.app.install_inserted_disc()
        work, _ = self.scheduled()
        work()
        messages = [self.app.events.get_nowait()[1] for _ in range(2)]
        self.assertIn("Reading and checking • 50%", messages[0])
        self.assertIn("Saving to library • 1 MiB", messages[1])
        self.no_manual_acquisition()

    def test_direct_install_cancelled_during_copy_closes_unhanded_buffer_and_keeps_selection(self):
        self.install.side_effect = ImportCancelled("Stopped by controller")
        self.app.install_inserted_disc()
        work, done = self.scheduled()
        self.assertIsNone(result := work())
        done(result)
        self.assertEqual(self.app.selected_id, 9)
        self.lease.close.assert_called_once_with()
        self.app.refresh.assert_not_called()
        self.app.library.start_session.assert_not_called()
        self.app.status.set.assert_called_once_with("Disc installation cancelled")
        self.no_manual_acquisition()

    def test_legacy_destination_action_delegates_to_automatic_install_without_file_dialog(self):
        self.app.install_disc_from_drive = Mock()
        self.app._raw_destination("G:")
        self.app.install_disc_from_drive.assert_called_once_with("G:")
        self.no_manual_acquisition()

    def test_busy_or_active_game_prevents_starting_a_second_direct_install(self):
        self.app.busy = True
        self.app.install_inserted_disc()
        self.app.task.assert_not_called()
        self.app.busy = False
        self.app.game_menu = Mock()
        self.app.embedded = object()
        self.app.install_inserted_disc()
        self.app.embedded = None
        self.app.playback.active.return_value = [SimpleNamespace(session_id="owned")]
        self.app.install_inserted_disc()
        self.assertEqual(self.app.game_menu.call_count, 2)
        self.app.task.assert_not_called()
        self.buffer.assert_not_called()

    def test_paused_install_action_uses_existing_session_buffer_and_starts_history_only_after_success(self):
        session = self.buffered_session()
        def install_owned_buffer(*, cancelled, progress):
            self.assertIs(cancelled, self.app.cancelled)
            progress(128)
            session.installed_game = self.managed_game
            return self.managed_game
        session.install_to_library.side_effect = install_owned_buffer
        self.app.game_menu()
        self.choose_overlay_action("Install to library")
        work, done = self.scheduled()
        self.app.library.start_session.assert_not_called()
        game = work()
        self.app.library.start_session.assert_not_called()
        done(game)
        session.install_to_library.assert_called_once()
        self.buffer.assert_not_called()
        self.prepare.assert_not_called()
        self.install.assert_not_called()
        self.lease.close.assert_not_called()
        self.app.library.start_session.assert_called_once_with(71)
        self.assertEqual(self.app.embedded_record, 42)
        self.assertIs(self.app.embedded, session)
        self.assertTrue(session.paused)
        session.resume.assert_not_called()
        session.stop.assert_not_called()
        self.app.refresh.assert_called_once_with()
        self.assertEqual(self.app.overlay["title"], "Game paused")
        self.assertNotIn("Install to library", dict(self.app.overlay["items"]))
        self.assertIn("Installed to library", self.app.overlay["message"])
        self.app.status.set.assert_called_once_with("Installed to library • the current game is ready to resume")
        self.no_manual_acquisition()

    def test_controller_b_during_active_buffer_copy_cancels_and_keeps_game_paused(self):
        session = self.buffered_session()
        def active_copy(*, cancelled, progress):
            progress(64)
            self.app.busy = True
            self.app._route_controller_events([InputEvent("back")])
            self.assertTrue(cancelled.is_set())
            raise ImportCancelled("Controller cancelled copy")
        session.install_to_library.side_effect = active_copy
        self.app._install_buffered_game()
        work, done = self.scheduled()
        result = work()
        self.app.busy = False
        done(result)
        self.assertIsNone(result)
        self.assertIs(self.app.embedded, session)
        self.assertTrue(session.paused)
        session.resume.assert_not_called()
        session.stop.assert_not_called()
        self.lease.close.assert_not_called()
        self.app.library.start_session.assert_not_called()
        self.app.refresh.assert_not_called()
        self.assertIn("Install to library", dict(self.app.overlay["items"]))
        self.assertIn("buffered game is ready to resume", self.app.status.set.call_args.args[0])
        self.buffer.assert_not_called()
        self.no_manual_acquisition()

    def test_busy_input_accepts_pressed_b_only_when_task_is_cancellable(self):
        self.app.busy = True
        self.app._route_controller_events([InputEvent("back", False), InputEvent("confirm"), InputEvent("guide")])
        self.assertFalse(self.app.cancelled.is_set())
        self.app.task_cancellable = False
        self.app.input_action("back")
        self.assertFalse(self.app.cancelled.is_set())
        self.app.task_cancellable = True
        self.app.input_action("b")
        self.assertTrue(self.app.cancelled.is_set())

    def test_install_row_absent_for_ordinary_or_already_installed_running_session(self):
        for transient, installed in ((False, False), (True, True)):
            with self.subTest(transient=transient, installed=installed):
                session = self.buffered_session(transient=transient, installed=installed)
                self.app.game_menu()
                self.assertNotIn("Install to library", dict(self.app.overlay["items"]))
                self.app._install_buffered_game()
                session.install_to_library.assert_not_called()
                self.app.task.assert_not_called()
        self.buffer.assert_not_called()
        self.no_manual_acquisition()

    def test_background_error_opens_controller_notice_without_native_messagebox_and_keeps_polling(self):
        del self.app.notice  # Exercise the real in-app notice/menu presentation.
        self.app.busy, self.app.closing = True, False
        self.app.progress = SimpleNamespace(stop=Mock(), configure=Mock())
        self.app.cancel_button = SimpleNamespace(configure=Mock())
        self.app.action_buttons = []
        self.app.root = SimpleNamespace(after=Mock(), destroy=Mock())
        self.app.events.put(("error", "Copy failed: storage removed"))
        with patch("redux.app.messagebox.showerror",
                   side_effect=AssertionError("Console errors must use the controller UI")) as native_notice:
            self.app._poll()
        native_notice.assert_not_called()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.overlay["title"], "Task could not complete")
        self.assertIn("storage removed", self.app.overlay["message"])
        self.assertEqual(self.app.focus, "overlay:0")
        self.assertEqual([label for label, _ in self.app.overlay["items"]], ["OK"])
        self.app.root.after.assert_called_once_with(100, self.app._poll)

    def test_stopped_disc_handoff_failure_offers_retry_or_keep_files_instead_of_resume(self):
        session = self.buffered_session()
        session.finished_seconds = 12
        self.app._stop_embedded = Mock()
        self.app._task_error("Managed save changed independently")
        self.assertEqual(self.app.overlay["title"], "Save transfer needs attention")
        labels = [label for label, _ in self.app.overlay["items"]]
        self.assertEqual(labels, ["Retry finishing save transfer", "Keep files and return to library"])
        self.assertFalse(any("resume" in label.casefold() for label in labels))
        self.assertEqual(self.app.overlay["back_action"], self.app._return_after_retained_disc)
        self.choose_overlay_action("Retry finishing save transfer")
        self.app._stop_embedded.assert_called_once_with()
        session.resume.assert_not_called()
        self.lease.close.assert_not_called()

    def test_return_after_failed_save_handoff_retains_owner_finishes_error_history_and_selects_install(self):
        session = self.buffered_session(installed=True)
        session.finished_seconds = 12
        self.app.embedded_record = 42
        self.app.library.finish_session = Mock()
        self.app.game_photo = object()
        self.app.held_keys = {"up", "x"}
        previous = object()
        self.app.retained_disc_sessions = [previous]
        self.app._task_error("Save transfer needs retry")
        self.choose_overlay_action("Keep files and return to library")
        self.assertEqual(self.app.retained_disc_sessions, [previous, session])
        self.app.library.finish_session.assert_called_once_with(42, duration_seconds=12, exit_code=1)
        self.assertEqual(self.app.selected_id, 71)
        self.assertIs(self.app.embedded_game, self.managed_game)
        self.assertIsNone(self.app.embedded)
        self.assertIsNone(self.app.embedded_record)
        self.assertIsNone(self.app.game_photo)
        self.assertEqual(self.app.held_keys, set())
        self.assertIsNone(self.app.overlay)
        self.assertEqual((self.app.screen, self.app.focus), ("details", "details:play"))
        self.app.refresh.assert_called_once_with()
        self.assertIn("retained for recovery", self.app.status.set.call_args.args[0])
        session.resume.assert_not_called()
        session.stop.assert_not_called()
        self.lease.close.assert_not_called()

    def test_direct_install_post_publication_save_error_shows_installed_game_and_retains_recovery_lease(self):
        del self.app.notice
        self.lease._redux_installed_game = self.managed_game
        self.install.side_effect = BufferInstallError("User saves conflict; both copies retained",
                                                     installed_game=self.managed_game)
        self.app.install_inserted_disc()
        work, done = self.scheduled()
        result = work()
        self.assertEqual(result["id"], 71)
        self.assertIn("both copies retained", result["_install_warning"])
        self.assertEqual(self.app.retained_disc_leases, [self.lease])
        self.lease.close.assert_not_called()
        done(result)
        self.assertEqual(self.app.selected_id, 71)
        self.app.refresh.assert_called_once_with()
        self.assertEqual(self.app.overlay["title"], "Game installed • saves need attention")
        self.assertIn("both copies retained", self.app.overlay["message"])
        self.app.status.set.assert_called_once_with("Installed to library • ready to play")
        self.app.library.start_session.assert_not_called()
        self.no_manual_acquisition()

    def test_paused_install_post_publication_save_warning_keeps_game_paused_and_returns_to_menu(self):
        del self.app.notice
        session = self.buffered_session()
        def published_with_warning(**kwargs):
            session.installed_game = self.managed_game
            raise BufferInstallError("Save snapshot needs reconciliation", installed_game=self.managed_game)
        session.install_to_library.side_effect = published_with_warning
        self.app._install_buffered_game()
        work, done = self.scheduled()
        result = work()
        self.assertEqual(result["id"], 71)
        done(result)
        self.assertIs(self.app.embedded, session)
        self.assertTrue(session.paused)
        self.app.library.start_session.assert_called_once_with(71)
        self.assertEqual(self.app.embedded_record, 42)
        self.assertEqual(self.app.overlay["title"], "Game installed • saves need attention")
        self.assertIn("reconciliation", self.app.overlay["message"])
        self.assertEqual(self.app.overlay["back_action"], self.app.game_menu)
        self.app.input_action("back")
        self.assertEqual(self.app.overlay["title"], "Game paused")
        self.assertNotIn("Install to library", dict(self.app.overlay["items"]))
        session.resume.assert_not_called()
        self.lease.close.assert_not_called()
        self.buffer.assert_not_called()
        self.no_manual_acquisition()


if __name__ == "__main__":
    unittest.main()
