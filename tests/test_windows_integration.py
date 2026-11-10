"""Windows setup boundaries: no host settings are changed by these tests."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from redux import windows_integration as integration
from redux.windows_gaming import GamingConfiguration


class WindowsIntegrationTests(unittest.TestCase):
    def test_home_registration_checks_developer_mode_before_staging(self):
        with patch.object(integration,"gaming_configuration",return_value=GamingConfiguration()), \
             patch("redux.windows_identity.prepare_identity_layout") as stage:
            with self.assertRaisesRegex(OSError,"Developer Mode"):
                integration.register_identity(Path("C:/Redux"),Path("C:/Redux"))
            stage.assert_not_called()

    def test_home_selection_rejects_desktop_before_registry_write(self):
        registry=MagicMock()
        with patch.object(integration,"gaming_configuration",return_value=GamingConfiguration()), \
             patch.object(integration,"identity_status",return_value={"aumid":"Redux.MediaLibrary_test!App"}), \
             patch.object(integration,"winreg",registry):
            with self.assertRaisesRegex(OSError,"desktop"):
                integration.select_home_app("Redux.MediaLibrary_test!App")
            registry.CreateKeyEx.assert_not_called()

    def test_arbitrary_aumid_cannot_be_written_as_redux_home(self):
        registry=MagicMock()
        with patch.object(integration,"gaming_configuration",return_value=GamingConfiguration(device_form=46)), \
             patch.object(integration,"identity_status",return_value={"aumid":"Redux.MediaLibrary_test!App"}), \
             patch.object(integration,"winreg",registry):
            with self.assertRaisesRegex(ValueError,"Register Redux"):
                integration.select_home_app("SomeoneElse!App")
            registry.CreateKeyEx.assert_not_called()

    def test_previous_home_is_backed_up_before_owned_home_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            aumid="Redux.MediaLibrary_test!App"
            before=GamingConfiguration(device_form=46,home_app="Existing!Home")
            after=GamingConfiguration(device_form=46,home_app=aumid,startup_to_gaming_home=True)
            registry=MagicMock()
            def write(*args):
                saved=json.loads((folder/"windows-home-selection.json").read_text())
                self.assertEqual(saved["home_app"],"Existing!Home")
            registry.SetValueEx.side_effect=write
            with patch.object(integration,"config_path",return_value=folder/"settings.json"), \
                 patch.object(integration,"gaming_configuration",side_effect=[before,after]), \
                 patch.object(integration,"identity_status",return_value={"aumid":aumid}), \
                 patch.object(integration,"winreg",registry):
                self.assertEqual(integration.select_home_app(aumid),after)
            self.assertEqual(registry.SetValueEx.call_count,2)

    def test_shell_reverted_selection_is_reported_without_retry_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            state=GamingConfiguration(device_form=46,home_app="Xbox!App")
            registry=MagicMock()
            with patch.object(integration,"config_path",return_value=Path(directory)/"settings.json"), \
                 patch.object(integration,"gaming_configuration",return_value=state), \
                 patch.object(integration,"identity_status",return_value={"aumid":"Redux!App"}), \
                 patch.object(integration,"winreg",registry):
                with self.assertRaisesRegex(OSError,"did not retain"):
                    integration.select_home_app("Redux!App")
            self.assertEqual(registry.SetValueEx.call_count,2)

    def test_failed_helper_json_cannot_be_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/"tools/windows_xbox/Invoke-XboxApp.ps1"
            script.parent.mkdir(parents=True);script.write_text("# test helper")
            with patch.object(integration,"_powershell",return_value={"ok":False,"error":"Slot is occupied"}):
                with self.assertRaisesRegex(OSError,"occupied"):
                    integration._script(directory,script.name)

    def test_powershell_arguments_use_file_mode_and_structured_argv(self):
        output=SimpleNamespace(returncode=0,stdout='\ufeff{"ok":true}',stderr="")
        with patch.object(integration.subprocess,"run",return_value=output) as run:
            self.assertTrue(integration._powershell(["-File","C:/Redux & Games/setup.ps1","-Executable","C:/Redux & Games/Redux.exe"])["ok"])
        args=run.call_args.args[0]
        self.assertTrue(Path(args[0]).is_absolute())
        self.assertTrue(args[0].endswith("powershell.exe"))
        self.assertIn("C:/Redux & Games/Redux.exe",args)
        self.assertNotIn("-Command",args)
        self.assertFalse(run.call_args.kwargs.get("shell",False))


if __name__=="__main__":unittest.main()
