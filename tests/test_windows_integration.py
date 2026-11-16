"""Windows setup boundaries: no host settings are changed by these tests."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from redux import windows_integration as integration
from redux.windows_gaming import GamingConfiguration


class FullscreenExperienceTests(unittest.TestCase):
    """The one System > Windows action and the update refresh, with every Windows call faked."""
    def setUp(self):
        self.calls=[]
        self.config=GamingConfiguration()
        def config():return self.config
        def device_form(enabled,application_dir):
            self.calls.append(("device_form",enabled));self.config=GamingConfiguration(device_form=46,developer_mode=True)
        def register(application_dir,portable_root,force_shutdown=False):
            self.calls.append(("register",force_shutdown))
            return {"aumid":"Redux.MediaLibrary_x!App","install_location":str(Path(portable_root)/"windows-home/new")}
        def select(aumid):
            self.calls.append(("select",aumid))
            self.config=GamingConfiguration(46,True,aumid,True)
        self.real_layout_is_current=integration.registered_layout_is_current
        self.patches=[patch.object(integration,"gaming_configuration",side_effect=config),
                      patch.object(integration,"change_device_form",side_effect=device_form),
                      patch.object(integration,"register_identity",side_effect=register),
                      patch.object(integration,"select_home_app",side_effect=select),
                      patch.object(integration,"identity_status",side_effect=lambda:{"aumid":"Redux.MediaLibrary_x!App"} if any(c[0]=="register" for c in self.calls) else None),
                      patch.object(integration,"registered_layout_is_current",side_effect=lambda package,application_dir:self.current)]
        self.current=True
        for item in self.patches:item.start()
        self.addCleanup(lambda:[item.stop() for item in self.patches])

    def test_one_action_sets_device_type_registers_and_selects_home_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            status=integration.set_up_fullscreen_experience(Path(directory),Path(directory))
        self.assertEqual(self.calls,[("device_form",True),("register",False),("select","Redux.MediaLibrary_x!App")])
        self.assertTrue(status.ready)

    def test_no_administrator_prompt_when_windows_is_already_prepared(self):
        self.config=GamingConfiguration(device_form=46,developer_mode=True)
        with tempfile.TemporaryDirectory() as directory:
            integration.set_up_fullscreen_experience(Path(directory),Path(directory))
        self.assertNotIn(("device_form",True),self.calls)

    def test_a_declined_administrator_prompt_stops_before_registration(self):
        integration.change_device_form.side_effect=OSError("The operation was canceled by the user.")
        with self.assertRaisesRegex(OSError,"canceled"):
            integration.set_up_fullscreen_experience(Path("C:/Redux"),Path("C:/Redux"))
        self.assertEqual(self.calls,[])

    def test_update_refresh_registers_the_new_copy_and_keeps_redux_home(self):
        self.config=GamingConfiguration(46,True,"Redux.MediaLibrary_x!App",True)
        self.calls.append(("register",None))  # already registered before the update
        self.current=False
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(integration.refresh_registration(Path(directory),Path(directory),force_shutdown=True),"refreshed")
        self.assertIn(("register",True),self.calls)

    def test_update_refresh_does_nothing_when_current_unregistered_or_developer_mode_is_off(self):
        self.config=GamingConfiguration(46,True)
        refresh=lambda:integration.refresh_registration(Path("C:/Redux"),Path("C:/Redux"))
        self.assertEqual(refresh(),"not-registered")
        self.calls.append(("register",None))
        self.assertEqual(refresh(),"current")
        self.config=GamingConfiguration(46,False);self.current=False
        self.assertEqual(refresh(),"developer-mode-off")  # the Full screen experience row turns it back on
        self.assertEqual([call for call in self.calls if call[1] is not None],[])

    def test_registered_copy_is_current_only_for_this_version_and_executable(self):
        import hashlib
        from redux import __version__
        from redux.windows_identity import package_version
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory)/"app";layout=Path(directory)/"layout"
            app.mkdir();layout.mkdir()
            (app/"Redux.exe").write_bytes(b"new build")
            def record(version,data):
                (layout/"redux-package.json").write_text(json.dumps({"version":version,"executable_sha256":hashlib.sha256(data).hexdigest()}))
            package={"install_location":str(layout)}
            record(package_version(__version__),b"new build")
            self.assertTrue(self.real_layout_is_current(package,app))
            record("0.1.0.0",b"new build")
            self.assertFalse(self.real_layout_is_current(package,app))
            record(package_version(__version__),b"old build")
            self.assertFalse(self.real_layout_is_current(package,app))
            self.assertFalse(self.real_layout_is_current({"install_location":str(Path(directory)/"missing")},app))

    def test_only_the_registered_copy_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ("0.29.0.0-old","0.30.0.0-old","0.31.0.0-new"):
                (root/"windows-home"/name).mkdir(parents=True)
                (root/"windows-home"/name/"redux-package.json").write_text("{}")
            (root/"windows-home"/"not-a-layout").mkdir()
            self.assertEqual(integration.remove_old_layouts(root,root/"windows-home"/"0.31.0.0-new"),2)
            self.assertEqual(sorted(p.name for p in (root/"windows-home").iterdir()),["0.31.0.0-new","not-a-layout"])


class OutdatedCopyTests(unittest.TestCase):
    def test_an_outdated_registered_copy_is_not_ready_and_says_so(self):
        aumid="Redux.MediaLibrary_x!App"
        with patch.object(integration,"gaming_configuration",return_value=GamingConfiguration(46,True,aumid,True)), \
             patch.object(integration,"identity_status",return_value={"aumid":aumid,"install_location":"C:/old"}), \
             patch.object(integration,"registered_layout_is_current",return_value=False):
            status=integration.fullscreen_status(Path("C:/Redux"))
        self.assertFalse(status.ready)
        self.assertTrue(status.outdated)

    def test_installer_registers_the_new_copy_after_copying_files(self):
        iss=(Path(__file__).resolve().parents[1]/"tools/installer/Redux.iss").read_text(encoding="utf-8")
        run=iss[iss.index("[Run]"):iss.index("[Code]")]
        line=next(entry for entry in run.splitlines() if "refresh-home" in entry)
        self.assertIn("waituntilterminated",line)
        self.assertIn("runhidden",line)
        self.assertNotIn("postinstall",line)  # runs on silent updates too


class RegistryHelperScriptTests(unittest.TestCase):
    def test_helpers_never_recreate_an_existing_registry_key(self):
        # New-Item -Force on an existing registry key deletes every value in it (the OEM key
        # holds the PC maker's details), so keys are created only when missing.
        folder=Path(__file__).resolve().parents[1]/"tools/windows_xbox"
        for script in folder.glob("*.ps1"):
            with self.subTest(script=script.name):
                self.assertNotRegex(script.read_text(encoding="utf-8"),r"New-Item\s[^\n]*-Force")

    def test_device_type_helper_also_turns_on_developer_mode(self):
        folder=Path(__file__).resolve().parents[1]/"tools/windows_xbox"
        self.assertIn("'-Enable', '-DeveloperMode'",(folder/"Invoke-DeviceForm.ps1").read_text(encoding="utf-8"))
        helper=(folder/"Enable-HomePicker.ps1").read_text(encoding="utf-8")
        self.assertIn("AllowDevelopmentWithoutDevLicense",helper)
        self.assertLess(helper.index("Assert-ReduxAdministrator"),helper.index("AllowDevelopmentWithoutDevLicense"))


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
