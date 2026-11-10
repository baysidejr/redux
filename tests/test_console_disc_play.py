"""Play-disc UI/service integration with mocked media and native operations.

No window, firmware file, optical device or library database is opened here.
Disc lease lifetime inside the real wrapper is covered by test_disc_session.
"""
from dataclasses import replace
from pathlib import Path
from queue import Queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from redux.console_ui import ConsoleApp
from redux.disc_identity import DiscIdentity
from redux.library import ImportCancelled
from redux.now_playing import DiscSnapshot


class ConsoleDiscPlayTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(ConsoleApp)
        app = self.app
        app.busy = False
        app.embedded = None
        app.playback = SimpleNamespace(active=Mock(return_value=[]))
        app.preferences = {"firmware_dir": "C:/fixtures/firmware"}
        app.cancelled = threading.Event()
        app.close_after_task = False
        app.events = Queue()
        app.overlay = None
        app.library = SimpleNamespace(root=Path("C:/fixtures/library"),
            import_game=Mock(side_effect=AssertionError("Play must not import a library row")),
            start_session=Mock(side_effect=AssertionError("Transient games have no library session")))
        app.import_files = Mock(side_effect=AssertionError("Play must not import files"))
        app.task = Mock()
        app.notice = Mock()
        app.game_menu = Mock()
        app.choose_firmware = Mock()
        app.status = SimpleNamespace(set=Mock())
        app._raw_plan = Mock()
        app._inspect_console_drive = Mock()
        app._start_integrated_game = Mock()
        def menu(title, items, message=""):
            app.overlay = {"title": title, "items": items, "message": message}
        app.menu = Mock(side_effect=menu)
        self.identity = DiscIdentity("G:", "World Series Baseball 2", "saturn", "MK-81113",
            "Saturn CD", "header", (), {"area_symbols": "U"}, "a" * 64)
        self.disc = DiscSnapshot("G:", self.identity.title, "saturn", "MK-81113", self.identity.fingerprint,
            self.identity, False, {"year": 1996, "publisher": "Sega", "description": "Fixture metadata"})
        app.discs = SimpleNamespace(active=self.disc)
        self.core = Path("C:/fixtures/beetle_saturn_libretro.dll")
        self.find_core = patch("redux.embedded.find_core", return_value=self.core).start()
        patch("redux.ui_assets.project_root", return_value=Path("C:/fixtures/project")).start()
        self.firmware_exists = patch.object(Path, "is_file", return_value=True).start()
        self.lease = SimpleNamespace(entry_path=Path("C:/fixtures/cache/disc.cue"), identity=self.identity,
            temporary=True, storage="temporary_disk", closed=False)
        def close():
            self.lease.closed = True
            return True
        self.lease.close = Mock(side_effect=close)
        self.buffer = patch("redux.disc_buffer.buffer_disc", return_value=self.lease).start()
        self.game = {"id": 1 << 62, "title": self.disc.title, "system": "saturn", "bundle_hash": "b" * 64,
                     "transient_disc": True, "path": str(self.lease.entry_path)}
        self.prepare = patch("redux.disc_session.prepare_disc_game", return_value=self.game).start()
        self.addCleanup(patch.stopall)

    def scheduled(self):
        self.app.task.assert_called_once()
        work, done, *_ = self.app.task.call_args.args
        return work, done

    def assert_no_import(self):
        self.app.import_files.assert_not_called()
        self.app.library.import_game.assert_not_called()
        self.app.library.start_session.assert_not_called()
        self.app._raw_plan.assert_not_called()

    def test_disc_options_expose_play_and_install_as_independent_actions(self):
        self.app.play_inserted_disc = Mock()
        self.app.install_inserted_disc = Mock()
        self.app.inserted_disc_options()
        items = dict(self.app.overlay["items"])
        self.assertIn("Play disc", items)
        self.assertIn("Install disc to library", items)
        items["Play disc"]()
        self.app.play_inserted_disc.assert_called_once_with()
        self.app._raw_plan.assert_not_called()
        items["Install disc to library"]()
        self.app.install_inserted_disc.assert_called_once_with()
        self.app._raw_plan.assert_not_called()
        self.app.play_inserted_disc.assert_called_once_with()

    def test_old_disc_options_do_not_launch_the_replacement_disc(self):
        self.app.play_inserted_disc = Mock()
        self.app.inserted_disc_options()
        play = dict(self.app.overlay["items"])["Play disc"]
        self.app.discs.active = replace(self.disc, fingerprint="c" * 64)
        play()
        self.app.play_inserted_disc.assert_not_called()
        self.app.notice.assert_called_once()
        self.app.task.assert_not_called()

    def test_missing_saturn_firmware_blocks_before_capture_task_and_keeps_install_independent(self):
        self.firmware_exists.return_value = False
        self.app.play_inserted_disc()
        self.assertEqual(self.app.overlay["title"], "Saturn firmware needed")
        self.assertIn("mpr-17933.bin", self.app.overlay["message"])
        self.assertIn("Disc installation does not require firmware", self.app.overlay["message"])
        self.app.task.assert_not_called()
        self.buffer.assert_not_called()
        self.prepare.assert_not_called()
        dict(self.app.overlay["items"])["Choose firmware folder"]()
        self.app.choose_firmware.assert_called_once_with()

    def test_missing_default_firmware_blocks_before_capture_task(self):
        self.app.preferences = {}
        self.firmware_exists.return_value = False
        self.app.play_inserted_disc()
        self.assertEqual(self.app.overlay["title"], "Saturn firmware needed")
        self.assertIn(str(Path("C:/fixtures/project/bios")), self.app.overlay["message"])
        self.app.task.assert_not_called()

    def test_busy_guard_performs_no_core_or_capture_work(self):
        self.app.busy = True
        self.app.play_inserted_disc()
        self.app.inserted_disc_options()
        self.find_core.assert_not_called()
        self.app.task.assert_not_called()
        self.app.menu.assert_not_called()
        self.app.game_menu.assert_not_called()

    def test_existing_embedded_or_external_game_opens_its_menu_instead_of_starting_disc(self):
        for embedded in (True, False):
            with self.subTest(embedded=embedded):
                self.app.embedded = object() if embedded else None
                self.app.playback.active.return_value = [] if embedded else [SimpleNamespace(session_id="owned")]
                self.app.play_inserted_disc()
        self.assertEqual(self.app.game_menu.call_count, 2)
        self.find_core.assert_not_called()
        self.app.task.assert_not_called()
        self.buffer.assert_not_called()

    def test_missing_disc_or_core_does_not_schedule_buffering(self):
        self.app.discs.active = None
        self.app.play_inserted_disc()
        self.find_core.assert_not_called()
        self.app.discs.active = self.disc
        self.find_core.return_value = None
        with patch.object(self.app,'offer_core_download') as offer:
            self.app.play_inserted_disc()
        offer.assert_called_once_with(self.disc.system,self.app.play_inserted_disc)
        self.app.task.assert_not_called()

    def test_worker_forwards_selected_fingerprint_metadata_cancellation_and_progress(self):
        def buffered(*args, **kwargs):
            kwargs["progress"](15, 30, "verify_source")
            return self.lease
        self.buffer.side_effect = buffered
        self.app.play_inserted_disc()
        self.buffer.assert_not_called()  # Optical work is deferred to the worker.
        work, done = self.scheduled()
        result = work()
        self.assertEqual(result, (self.lease, self.game))
        self.assertEqual(self.buffer.call_args.args, ("G:", self.app.library.root / "cache" / "disc-play"))
        self.assertIs(self.buffer.call_args.kwargs["cancelled"], self.app.cancelled)
        self.assertEqual(self.buffer.call_args.kwargs["expected_fingerprint"], self.disc.fingerprint)
        self.prepare.assert_called_once_with(self.app.library, self.lease,
            metadata=self.disc.metadata, cancelled=self.app.cancelled)
        self.assertEqual(self.app.events.get_nowait(), ("status", "Preparing disc • verify_source • 50%"))
        self.lease.close.assert_not_called()
        done(result)
        self.app._start_integrated_game.assert_called_once_with(self.game, self.core, media_lease=self.lease)
        self.lease.close.assert_not_called()
        self.assert_no_import()

    def test_prepare_failure_closes_the_acquired_lease_and_never_launches_or_imports(self):
        self.prepare.side_effect = ValueError("Bundle changed")
        self.app.play_inserted_disc()
        work, _ = self.scheduled()
        with self.assertRaisesRegex(ValueError, "Bundle changed"):
            work()
        self.lease.close.assert_called_once_with()
        self.assertTrue(self.lease.closed)
        self.app._start_integrated_game.assert_not_called()
        self.assert_no_import()

    def test_cancellation_during_prepare_closes_lease(self):
        def cancelled(*args, **kwargs):
            self.app.cancelled.set()
            raise ImportCancelled("Cancelled while hashing")
        self.prepare.side_effect = cancelled
        self.app.play_inserted_disc()
        work, _ = self.scheduled()
        with self.assertRaises(ImportCancelled):
            work()
        self.lease.close.assert_called_once_with()
        self.app._start_integrated_game.assert_not_called()
        self.assert_no_import()

    def test_cancellation_or_close_request_after_worker_result_closes_unhanded_lease(self):
        for closing in (False, True):
            with self.subTest(closing=closing):
                self.app.task.reset_mock()
                self.app.cancelled.clear()
                self.app.close_after_task = False
                self.lease.closed = False
                self.lease.close.reset_mock()
                self.app.play_inserted_disc()
                work, done = self.scheduled()
                result = work()
                if closing:
                    self.app.close_after_task = True
                else:
                    self.app.cancelled.set()
                done(result)
                self.lease.close.assert_called_once_with()
                self.assertTrue(self.lease.closed)
        self.app._start_integrated_game.assert_not_called()
        self.assert_no_import()

    def test_successful_shared_launcher_handoff_retains_lease_until_owning_session_stops(self):
        session = SimpleNamespace(media_lease=self.lease, stop=Mock(side_effect=self.lease.close))
        def launch(game, core, *, media_lease):
            self.assertIs(media_lease, self.lease)
            self.assertFalse(media_lease.closed)
            self.app.embedded = session
        self.app._start_integrated_game.side_effect = launch
        self.app.play_inserted_disc()
        work, done = self.scheduled()
        done(work())
        self.assertIs(self.app.embedded, session)
        self.lease.close.assert_not_called()
        self.assertFalse(self.lease.closed)
        session.stop()
        self.lease.close.assert_called_once_with()
        self.assertTrue(self.lease.closed)
        self.assert_no_import()

    def stopped_session(self, *, transient=True, retained=False, record=None):
        session = SimpleNamespace(game={"transient_disc": transient}, media_lease=self.lease,
            lease_retained=retained, play_seconds=23, stop=Mock())
        self.app.embedded = session
        self.app.embedded_record = record
        self.app.held_keys = {"x", "up"}
        self.app.game_photo = object()
        self.app.refresh = Mock()
        self.app.library.finish_session = Mock()
        self.app._stop_embedded()
        work, done = self.scheduled()
        done(work())
        session.stop.assert_called_once_with()
        self.assertFalse(self.app.task.call_args.kwargs["cancellable"])
        return session

    def test_stopped_disc_with_retained_lease_keeps_retry_owner_and_shows_path_without_false_history(self):
        previous = object()
        self.app.retained_disc_sessions = [previous]
        session = self.stopped_session(retained=True)
        self.assertEqual(self.app.retained_disc_sessions, [previous, session])
        self.assertIsNone(self.app.embedded)
        self.assertIsNone(self.app.embedded_record)
        self.assertFalse(self.lease.closed)
        self.lease.close.assert_not_called()
        self.app.library.finish_session.assert_not_called()
        message = self.app.status.set.call_args.args[0]
        self.assertIn("temporary disc buffer retained", message)
        self.assertIn(str(self.lease.entry_path), message)
        self.assertNotIn("history", message)
        self.assertEqual((self.app.screen, self.app.focus), ("home", "hero:play"))

    def test_successful_transient_stop_returns_home_and_reports_saves_without_library_history(self):
        self.app.retained_disc_sessions = []
        self.stopped_session()
        self.assertIsNone(self.app.embedded)
        self.assertIsNone(self.app.game_photo)
        self.assertEqual(self.app.held_keys, set())
        self.assertEqual((self.app.screen, self.app.focus), ("home", "hero:play"))
        self.assertIsNone(self.app.overlay)
        self.assertEqual(self.app.retained_disc_sessions, [])
        self.app.library.finish_session.assert_not_called()
        self.app.status.set.assert_called_once_with("Game closed • saves stored; temporary disc buffer released")
        self.app.refresh.assert_called_once_with()

    def test_installed_session_stop_finishes_its_record_and_returns_to_game_details(self):
        self.stopped_session(transient=False, record=71)
        self.app.library.finish_session.assert_called_once_with(71, duration_seconds=23, exit_code=0)
        self.assertIsNone(self.app.embedded_record)
        self.assertEqual((self.app.screen, self.app.focus), ("details", "details:play"))
        self.app.status.set.assert_called_once_with("Game closed • battery saves and play history stored")

    def shared_start(self, game=None, media_lease=None):
        # Bypass the launcher Mock used by the Play Disc handoff tests. The
        # actual ConsoleApp method runs only its preflight and queues work.
        game = game if game is not None else dict(self.game, transient_disc=False, region="USA")
        return ConsoleApp._start_integrated_game(self.app, game, self.core, media_lease=media_lease)

    def test_installed_saturn_without_firmware_offers_folder_before_scheduling_native_load(self):
        self.app.preferences = {}
        self.firmware_exists.return_value = False
        self.shared_start()
        self.app.task.assert_not_called()
        self.assertEqual(self.app.overlay["title"], "Saturn firmware needed")
        self.assertIn("mpr-17933.bin", self.app.overlay["message"])
        self.assertIn(str(Path("C:/fixtures/project/bios")), self.app.overlay["message"])
        dict(self.app.overlay["items"])["Choose firmware folder"]()
        self.app.choose_firmware.assert_called_once_with()
        self.app.library.import_game.assert_not_called()

    def test_missing_shared_firmware_closes_a_supplied_unhanded_disc_lease(self):
        self.app.preferences = {}
        self.firmware_exists.return_value = False
        self.shared_start(self.game, media_lease=self.lease)
        self.lease.close.assert_called_once_with()
        self.assertTrue(self.lease.closed)
        self.assertIsNone(self.app.embedded)
        self.app.task.assert_not_called()
        self.assertEqual(self.app.overlay["title"], "Saturn firmware needed")

    def test_usa_and_europe_installed_saturn_require_mpr_instead_of_japanese_bios(self):
        for region in ("U", "USA", "North America", "E", "Europe"):
            with self.subTest(region=region):
                self.app.task.reset_mock()
                self.app.menu.reset_mock()
                probes = []
                def japanese_only(path):
                    probes.append(path.name)
                    return path.name == "sega_101.bin"
                with patch.object(Path, "is_file", japanese_only):
                    self.shared_start(dict(self.game, transient_disc=False, region=region))
                self.app.task.assert_not_called()
                self.assertEqual(probes, ["mpr-17933.bin"])
                self.assertEqual(self.app.overlay["title"], "Saturn firmware needed")
                self.assertNotIn("sega_101.bin", self.app.overlay["message"])
        self.lease.close.assert_not_called()

    def test_regional_saturn_firmware_permits_queuing_without_running_native_code(self):
        for region, available in (("Japan", "sega_101.bin"), ("J", "sega_101.bin"),
                                  ("USA", "mpr-17933.bin"), ("Europe", "mpr-17933.bin")):
            with self.subTest(region=region, firmware=available):
                self.app.task.reset_mock()
                self.app.menu.reset_mock()
                with patch.object(Path, "is_file", lambda path: path.name == available):
                    self.shared_start(dict(self.game, transient_disc=False, region=region))
                self.app.menu.assert_not_called()
                self.app.task.assert_called_once()
                self.assertFalse(self.app.task.call_args.kwargs["cancellable"])
                self.assertEqual(self.app.task.call_args.args[2], "Starting integrated playback…")
        self.assertIsNone(self.app.embedded)
        self.lease.close.assert_not_called()

    def test_nes_without_firmware_queues_normally_and_does_not_request_saturn_bios(self):
        self.app.preferences = {}
        with patch.object(Path, "is_file", side_effect=AssertionError("NES has no Saturn firmware preflight")):
            self.shared_start(dict(self.game, system="nes", transient_disc=False))
        self.app.task.assert_called_once()
        self.app.menu.assert_not_called()
        self.app.choose_firmware.assert_not_called()
        self.lease.close.assert_not_called()

    def test_firmware_preflight_retains_a_lease_when_cleanup_is_refused_or_not_confirmed(self):
        self.app.preferences = {}
        self.firmware_exists.return_value = False
        previous = object()
        for reported_closed in (False, True):
            with self.subTest(close_result=reported_closed):
                self.app.retained_disc_leases = [previous]
                self.lease.closed = False
                self.lease.close = Mock(return_value=reported_closed)
                self.shared_start(self.game, media_lease=self.lease)
                self.lease.close.assert_called_once_with()
                self.assertEqual(self.app.retained_disc_leases, [previous, self.lease])
                self.assertIn("Temporary disc buffer retained", self.app.overlay["message"])
                self.assertIn(str(self.lease.entry_path), self.app.overlay["message"])
                self.app.task.assert_not_called()

    def test_european_inserted_saturn_with_only_japanese_bios_blocks_before_expensive_buffer(self):
        european_identity = replace(self.identity, details={"area_symbols": "E"})
        self.app.discs.active = replace(self.disc, identity=european_identity)
        with patch.object(Path, "is_file", lambda path: path.name == "sega_101.bin"):
            self.app.play_inserted_disc()
        self.app.task.assert_not_called()
        self.buffer.assert_not_called()
        self.prepare.assert_not_called()
        self.assertEqual(self.app.overlay["title"], "Saturn firmware needed")
        self.assertIn("mpr-17933.bin", self.app.overlay["message"])
        self.assertNotIn("sega_101.bin", self.app.overlay["message"])

    def test_capture_plan_completion_keeps_the_inserted_disc_instead_of_selected_library_game(self):
        self.app.selected_id = 99  # An unrelated previously browsed library item.
        self.app.selected_game = {"id": 99, "title": "Diablo", "system": "psx"}
        ConsoleApp._raw_plan(self.app, "G:")
        _, done = self.scheduled()  # Never run the optical worker.
        done((None, SimpleNamespace(tracks=[object(), object()], bytes_to_capture=307502832)))
        self.assertEqual(self.app.overlay["title"], "Install disc")
        self.assertIs(self.app.overlay["disc"], self.disc)
        self.assertEqual(self.app.overlay["disc"].game_id, "MK-81113")
        self.assertIn("2 tracks", self.app.overlay["message"])
        self.app.install_disc_from_drive = Mock()
        dict(self.app.overlay["items"])["Install to library"]()
        self.app.install_disc_from_drive.assert_called_once_with("G:", disc=self.disc)
        self.app.notice.assert_not_called()
        self.buffer.assert_not_called()
        self.assert_no_import()

    def test_capture_plan_for_replaced_disc_is_refused_at_async_completion(self):
        ConsoleApp._raw_plan(self.app, "G:")
        _, done = self.scheduled()  # Never run the optical worker.
        self.app.discs.active = replace(self.disc, fingerprint="c" * 64)
        done((None, SimpleNamespace(tracks=[object(), object()], bytes_to_capture=307502832)))
        self.app.notice.assert_called_once()
        self.assertEqual(self.app.notice.call_args.args[0], "Disc changed")
        self.app.menu.assert_not_called()
        self.assertIsNone(self.app.overlay)
        self.buffer.assert_not_called()
        self.assert_no_import()

    def firmware_flow(self, source):
        self.app.task.reset_mock()
        self.app.menu.reset_mock()
        self.app.overlay = None
        if source == "inserted":
            self.app.play_inserted_disc()
        else:
            self.shared_start()

    def test_default_project_bios_is_used_for_inserted_and_installed_saturn_without_folder_selection(self):
        self.app.preferences = {}
        directory = Path("C:/fixtures/project/bios")
        for source in ("inserted", "installed"):
            with self.subTest(source=source):
                queried = []
                def default_bios(path):
                    queried.append(path)
                    return path == directory / "mpr-17933.bin"
                with patch.object(Path, "is_file", default_bios):
                    self.firmware_flow(source)
                self.assertEqual(queried, [directory / "mpr-17933.bin"])
                self.app.task.assert_called_once()
                self.app.menu.assert_not_called()
                self.assertEqual(self.app.preferences, {})
                self.app.choose_firmware.assert_not_called()

    def test_custom_firmware_directory_wins_even_when_default_bios_exists(self):
        directory = Path("D:/my-console-bios")
        self.app.preferences = {"firmware_dir": str(directory)}
        default = Path("C:/fixtures/project/bios/mpr-17933.bin")
        for source in ("inserted", "installed"):
            with self.subTest(source=source):
                queried = []
                def only_default_exists(path):
                    queried.append(path)
                    return path == default
                with patch.object(Path, "is_file", only_default_exists):
                    self.firmware_flow(source)
                self.app.task.assert_not_called()
                self.assertEqual(queried, [directory / "mpr-17933.bin"])
                self.assertEqual(self.app.overlay["title"], "Saturn firmware needed")
                self.assertIn(str(directory), self.app.overlay["message"])
                self.assertIn("mpr-17933.bin", self.app.overlay["message"])
                self.assertNotIn(str(default.parent), self.app.overlay["message"])

    def test_bios_added_while_app_remains_open_is_detected_on_next_play_for_both_entry_points(self):
        self.app.preferences = {}
        expected = Path("C:/fixtures/project/bios/mpr-17933.bin")
        files = set()
        with patch.object(Path, "is_file", lambda path: path in files):
            for source in ("inserted", "installed"):
                with self.subTest(source=source):
                    files.clear()
                    self.firmware_flow(source)
                    self.app.task.assert_not_called()
                    self.assertIn(str(expected.parent), self.app.overlay["message"])
                    self.assertIn(expected.name, self.app.overlay["message"])
                    files.add(expected)
                    self.firmware_flow(source)
                    self.app.task.assert_called_once()
                    self.app.menu.assert_not_called()
                    self.assertEqual(self.app.preferences, {})

    def test_resolved_firmware_directory_reaches_shared_session_constructor(self):
        for configured in (None, Path("D:/my-console-bios")):
            with self.subTest(configured=configured):
                self.app.preferences = {"firmware_dir": str(configured)} if configured else {}
                expected = configured or Path("C:/fixtures/project/bios")
                self.app.task.reset_mock()
                fake_session = SimpleNamespace(start=Mock())
                fake_session.start.return_value = fake_session
                with patch.object(Path, "is_file", lambda path: path == expected / "mpr-17933.bin"), \
                        patch("redux.embedded.EmbeddedGame", return_value=fake_session) as constructor:
                    self.shared_start()
                    work, _ = self.scheduled()
                    self.assertIs(work(), fake_session)
                self.assertEqual(Path(constructor.call_args.kwargs["firmware"]), expected)
                fake_session.start.assert_called_once_with()
                self.assertIsNone(self.app.embedded)


if __name__ == "__main__":
    unittest.main()
