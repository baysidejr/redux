"""Loose identity packages preserve the portable app and user-data boundary."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from PIL import Image

from redux.windows_identity import build_layout, package_version, prepare_identity_layout


class XboxIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "portable"
        (self.source / "_internal/assets").mkdir(parents=True)
        (self.source / "Redux.exe").write_bytes(b"test front-end executable")
        (self.source / "_internal/python.dll").write_bytes(b"runtime")
        Image.new("RGBA", (256, 256), "#19151e").save(self.source / "_internal/assets/redux.ico")
        self.templates = Path(__file__).resolve().parents[1] / "tools/windows_xbox"

    def test_staged_identity_preserves_exact_windows_contract_and_user_data_boundary(self):
        for relative in ("library/media/game.bin", "bios/firmware.bin", "tools/cores/core.dll", "analysis/vendor.dat"):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"user data not package payload")
        destination = self.root / "home/0.8.0.0"
        result = build_layout(self.source, self.source, destination, "0.8.0")
        self.assertEqual(result["version"], "0.8.0.0")
        manifest = ET.parse(destination / "AppxManifest.xml").getroot()
        ns = {"p": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
              "a": "http://schemas.microsoft.com/appx/manifest/uap/windows10/3",
              "c": "http://schemas.microsoft.com/appx/manifest/uap/windows10/4"}
        self.assertEqual(manifest.find("p:Identity", ns).get("Name"), "Redux.MediaLibrary")
        app = manifest.find("p:Applications/p:Application", ns)
        self.assertEqual((app.get("Id"), app.get("Executable"), app.get("EntryPoint")),
                         ("App", "Redux.exe", "Windows.FullTrustApplication"))
        self.assertEqual(app.find("p:Extensions/a:Extension/a:AppExtension", ns).get("Name"), "windows.gamingApp")
        capability = manifest.find("p:Capabilities/c:CustomCapability", ns).get("Name")
        self.assertEqual(capability, "Microsoft.appCategory.gamingHome_8wekyb3d8bbwe")
        desktop6 = "http://schemas.microsoft.com/appx/manifest/desktop/windows10/6"
        for property_name in ("RegistryWriteVirtualization", "FileSystemWriteVirtualization"):
            self.assertEqual(manifest.find(f"p:Properties/{{{desktop6}}}{property_name}", ns).text, "disabled")
        capabilities = {element.get("Name") for element in manifest.find("p:Capabilities", ns)}
        self.assertIn("unvirtualizedResources", capabilities)
        sccd = ET.parse(destination / "CustomCapability.SCCD").getroot()
        self.assertEqual(sccd.find("{*}CustomCapabilities/{*}CustomCapability").get("Name"), capability)
        self.assertEqual(sccd.find("{*}Catalog").text, "FFFF")
        self.assertTrue((destination / "Public/README.txt").is_file())
        self.assertEqual(json.loads((destination / "redux-launch.json").read_text())["portable_root"], str(self.source.resolve()))
        for folder in ("library", "bios", "analysis"):
            self.assertFalse((destination / folder).exists())
        self.assertFalse((destination / "tools/cores").exists())
        self.assertEqual((destination / "_internal/python.dll").read_bytes(), b"runtime")
        for size, name in ((150, "Square150x150Logo.png"), (44, "Square44x44Logo.png"), (50, "StoreLogo.png")):
            with Image.open(destination / "Assets" / name) as opened:
                self.assertEqual(opened.size, (size, size))

    def test_existing_layout_is_never_replaced(self):
        destination = self.root / "identity"
        destination.mkdir()
        sentinel = destination / "running-state"
        sentinel.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            build_layout(self.source, self.source, destination, "0.8.0")
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_prepare_reuses_matching_layout_and_changes_path_for_new_executable(self):
        first = prepare_identity_layout(self.source, self.source, "0.8.0")
        executable = Path(first["layout"]) / "Redux.exe"
        timestamp = executable.stat().st_mtime_ns
        second = prepare_identity_layout(self.source, self.source, "0.8.0")
        self.assertEqual(second["layout"], first["layout"])
        self.assertEqual(executable.stat().st_mtime_ns, timestamp)
        for name in ("register_script", "unregister_script", "enable_picker_script", "restore_device_form_script", "device_form_wrapper", "xbox_app_wrapper"):
            self.assertTrue(Path(second[name]).is_file(), name)
        (self.source / "Redux.exe").write_bytes(b"next executable")
        third = prepare_identity_layout(self.source, self.source, "0.8.0")
        self.assertNotEqual(third["layout"], first["layout"])
        self.assertEqual(executable.read_bytes(), b"test front-end executable")

    def test_prepare_rejects_mismatched_existing_metadata(self):
        first = prepare_identity_layout(self.source, self.source, "0.8.0")
        metadata = Path(first["layout"]) / "redux-package.json"
        content = json.loads(metadata.read_text())
        content["portable_root"] = "some other application"
        metadata.write_text(json.dumps(content))
        with self.assertRaisesRegex(ValueError, "does not match"):
            prepare_identity_layout(self.source, self.source, "0.8.0")

    def test_runtime_and_template_changes_stage_fresh_layout_without_touching_old_one(self):
        import shutil
        shutil.copytree(self.templates, self.source / "tools/windows_xbox")
        first = prepare_identity_layout(self.source, self.source, "0.8.0")
        manifest = Path(first["layout"]) / "AppxManifest.xml"
        original_manifest = manifest.read_bytes()
        template = self.source / "tools/windows_xbox/AppxManifest.xml.template"
        template.write_text(template.read_text() + "\n<!-- identity revision -->\n")
        second = prepare_identity_layout(self.source, self.source, "0.8.0")
        self.assertNotEqual(first["layout"], second["layout"])
        self.assertEqual(manifest.read_bytes(), original_manifest)
        (self.source / "_internal/python.dll").write_bytes(b"updated native runtime")
        third = prepare_identity_layout(self.source, self.source, "0.8.0")
        self.assertNotEqual(second["layout"], third["layout"])
        self.assertEqual((Path(first["layout"]) / "_internal/python.dll").read_bytes(), b"runtime")

    def test_source_packaged_templates_work_without_a_development_checkout(self):
        import shutil
        shutil.copytree(self.templates, self.source / "tools/windows_xbox")
        with patch("redux.windows_identity.TEMPLATES", self.root / "missing-checkout"):
            result = prepare_identity_layout(self.source, self.source, "0.8.0")
        self.assertTrue(Path(result["register_script"]).is_file())

    def test_invalid_build_does_not_leave_layout(self):
        (self.source / "Redux.exe").unlink()
        destination = self.root / "identity"
        with self.assertRaisesRegex(ValueError, "complete"):
            build_layout(self.source, self.source, destination, "0.8.0")
        self.assertFalse(destination.exists())

    def test_destination_cannot_replace_application_or_nested_runtime(self):
        for destination in (self.source, self.source / "_internal", self.source / "_internal/identity"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                build_layout(self.source, self.source, destination, "0.8.0")

    def test_windows_version_components_are_validated(self):
        self.assertEqual(package_version("0.8.0"), "0.8.0.0")
        self.assertEqual(package_version("1.2.3.4"), "1.2.3.4")
        for value in ("0.8", "1.2.3-beta", "1.2.3.4.5", "1.2.65536", "../x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                package_version(value)


if __name__ == "__main__":
    unittest.main()
