"""Fresh-edge controller setup and durable SDL mapping behavior, without Tk."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from redux.console_controllers import (
    ConsoleControllers, ControllerSetupSession, STEPS, save_controller_mapping,
)


GUID = "03000000111100002222000000000000"
OTHER_GUID = "03000000333300004444000000000000"


def device(**changes):
    value = {"instance_id": 1, "name": "NES USB pad", "guid": GUID, "mapped": False}
    value.update(changes)
    return value


def raw(*pressed, axes=(0, -32768), hats=(0,), **changes):
    value = {"instance_id": 1, "name": "NES USB pad", "guid": GUID,
             "buttons": tuple(index in pressed for index in range(16)), "axes": axes, "hats": hats}
    value.update(changes)
    return value


def completed_nes():
    session = ControllerSetupSession(device(), raw())
    button = 0
    while not session.done:
        if not session.step.required:
            session.skip()
        else:
            session.observe(raw())
            session.observe(raw(button))
            session.observe(raw())
            button += 1
    return session


class SetupSessionTests(unittest.TestCase):
    def test_held_controls_never_fill_multiple_assignments(self):
        session = ControllerSetupSession(device(), raw(0))
        session.observe(raw(0))
        session.observe(raw(0, 1))
        self.assertFalse(session.armed)
        self.assertEqual(session.bindings, {})
        session.observe(raw())
        session.observe(raw(0))
        self.assertEqual(session.bindings, {"dpup": "b0"})
        for _ in range(4):
            session.observe(raw(0))
        self.assertEqual(session.index, 1)
        session.observe(raw())
        session.observe(raw(1))
        self.assertEqual(session.bindings["dpdown"], "b1")

    def test_duplicate_and_multiple_buttons_need_release_before_retry(self):
        session = ControllerSetupSession(device(), raw())
        session.observe(raw())
        session.observe(raw(0, 1))
        self.assertEqual(session.bindings, {})
        session.observe(raw(0))
        self.assertEqual(session.bindings, {})
        session.observe(raw())
        session.observe(raw(0))
        session.observe(raw())
        session.observe(raw(0))
        self.assertEqual(session.index, 1)
        self.assertIn("already assigned", session.message)
        session.observe(raw(1))
        self.assertEqual(session.index, 1)
        session.observe(raw())
        session.observe(raw(1))
        self.assertEqual(session.index, 2)

    def test_hat_edges_support_dpad_and_diagonals_are_not_guessed(self):
        session = ControllerSetupSession(device(), raw())
        session.observe(raw())
        session.observe(raw(hats=(3,)))
        self.assertEqual(session.bindings, {})
        session.observe(raw())
        session.observe(raw(hats=(1,)))
        session.observe(raw())
        session.observe(raw(hats=(4,)))
        self.assertEqual(session.bindings, {"dpup": "h0.1", "dpdown": "h0.4"})

    def test_idle_trigger_is_ignored_and_centered_axis_directions_are_distinct(self):
        session = ControllerSetupSession(device(), raw())
        for _ in range(5):
            session.observe(raw())
        self.assertEqual(session.bindings, {})
        session.observe(raw(axes=(-27000, -32768)))
        session.observe(raw())
        session.observe(raw(axes=(27000, -32768)))
        self.assertEqual(session.bindings, {"dpup": "-a0", "dpdown": "+a0"})
        self.assertNotIn("a1", "".join(session.bindings.values()))

    def test_nes_controls_are_sufficient_and_extra_home_is_optional(self):
        session = completed_nes()
        mapping = session.mapping("Windows")
        self.assertEqual(len(session.bindings), 8)
        self.assertIn("back:b6,start:b7,platform:Windows,", mapping)
        self.assertNotIn("guide:", mapping)
        self.assertTrue(all(step.key in session.bindings for step in STEPS if step.required))
        self.assertEqual(session.skipped, {"x", "y", "leftshoulder", "rightshoulder", "guide"})
        self.assertTrue(session.previous_step())
        self.assertEqual(session.step.key, "guide")
        session.observe(raw())
        session.observe(raw(8))
        self.assertTrue(session.done)
        self.assertIn("guide:b8", session.mapping("Windows"))

    def test_required_controls_cannot_be_skipped_and_incomplete_mapping_cannot_save(self):
        session = ControllerSetupSession(device(), raw())
        self.assertFalse(session.skip())
        self.assertEqual(session.index, 0)
        with self.assertRaises(ValueError):
            session.mapping()
        with self.assertRaises(ValueError):
            ControllerSetupSession(device(guid="bad,identifier"), raw())
        with self.assertRaises(ValueError):
            session.observe(raw(axes=(100000,)))

    def test_mapping_names_cannot_inject_a_second_mapping_or_field(self):
        session = completed_nes()
        session.device["name"] = "Bad, name\n" + OTHER_GUID + ",Injected"
        mapping = session.mapping("Windows")
        self.assertNotIn("\n", mapping)
        self.assertEqual(mapping.split(",")[0], GUID)
        self.assertEqual(mapping.count("platform:"), 1)
        self.assertEqual(mapping.split(",")[1], "Bad name " + OTHER_GUID + " Injected")


class MappingStorageTests(unittest.TestCase):
    def test_round_trip_preserves_other_devices_comments_and_platforms(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "preferences" / "controller-mappings.txt"
            old = f"# Controller choices\n{OTHER_GUID},Other,a:b0,platform:Windows,\n{GUID},Linux,a:b9,platform:Linux,\n{GUID},Old,a:b1,platform:Windows,\n"
            path.parent.mkdir()
            path.write_text(old, encoding="utf-8")
            mapping = completed_nes().mapping("Windows")
            save_controller_mapping(path, mapping)
            result = path.read_text(encoding="utf-8")
            self.assertIn(f"{OTHER_GUID},Other,a:b0,platform:Windows,\n", result)
            self.assertIn(f"{GUID},Linux,a:b9,platform:Linux,\n", result)
            self.assertIn("# Controller choices\n", result)
            self.assertNotIn(f"{GUID},Old", result)
            self.assertIn(mapping + "\n", result)
            save_controller_mapping(path, mapping)
            self.assertEqual(path.read_text(encoding="utf-8"), result)

    def test_failed_atomic_publish_preserves_original_mapping_file(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "controller-mappings.txt"
            path.write_text("# Keep original\n", encoding="utf-8")
            with patch("redux.console_controllers.Path.replace", side_effect=PermissionError("Locked")):
                with self.assertRaises(PermissionError):
                    save_controller_mapping(path, completed_nes().mapping("Windows"))
            self.assertEqual(path.read_text(encoding="utf-8"), "# Keep original\n")
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


class SetupIntegrationTests(unittest.TestCase):
    def bare(self):
        app = ConsoleControllers()
        app.controller_setup_session = ControllerSetupSession(device(), raw())
        app.controller = SimpleNamespace(api=SimpleNamespace(raw_state=Mock(return_value=raw()),
            add_mapping=Mock(), refresh=Mock()), devices=Mock(return_value=[device()]))
        app.screen = "controller_setup"
        app.overlay = None
        app.busy = False
        app.embedded = None
        app.playback = SimpleNamespace(active=lambda: [])
        app.schedule_draw = Mock()
        app.back = Mock()
        app.notice = Mock()
        app.status = SimpleNamespace(set=Mock())
        return app

    def test_disconnection_keeps_assignments_and_ambiguous_reconnect_is_not_selected(self):
        app = self.bare()
        app.controller_setup_session.observe(raw())
        app.controller_setup_session.observe(raw(0))
        app.controller.api.raw_state.return_value = None
        app.controller.devices.return_value = [device(instance_id=2), device(instance_id=3)]
        app._poll_controller_setup()
        self.assertEqual(app.controller_setup_session.bindings, {"dpup": "b0"})
        self.assertIn("disconnected", app.controller_setup_session.message)
        self.assertEqual(app.controller_setup_session.device["instance_id"], 1)
        app.controller.devices.return_value = [device(instance_id=2)]
        app.controller.api.raw_state.side_effect = lambda instance: raw() if instance == 2 else None
        app._poll_controller_setup()
        self.assertEqual(app.controller_setup_session.device["instance_id"], 2)
        self.assertEqual(app.controller_setup_session.bindings, {"dpup": "b0"})

    def test_sdl_rejection_does_not_persist_mapping_or_leave_setup(self):
        app = self.bare()
        app.controller_setup_session = completed_nes()
        app.controller.api.add_mapping.side_effect = ValueError("SDL rejected mapping")
        with patch("redux.console_controllers.save_controller_mapping") as save:
            app._save_controller_setup()
        save.assert_not_called()
        app.back.assert_not_called()
        app.notice.assert_called_once()
        self.assertIsNotNone(app.controller_setup_session)

    def test_successful_mapping_is_persisted_refreshed_and_returns_to_settings(self):
        app = self.bare()
        app.controller_setup_session = completed_nes()
        with TemporaryDirectory() as temp:
            settings = Path(temp) / "settings.json"
            with patch("redux.console_controllers.config_path", return_value=settings):
                app._save_controller_setup()
            saved = (settings.parent / "controller-mappings.txt").read_text(encoding="utf-8")
        self.assertIn("back:b6,start:b7", saved)
        self.assertIsNone(app.controller_setup_session)
        app.controller.api.refresh.assert_called_once()
        app.back.assert_called_once()


if __name__ == "__main__":
    unittest.main()
