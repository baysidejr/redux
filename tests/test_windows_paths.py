"""Known-folder ABI and fallbacks without file writes or profile changes."""
import ctypes
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from redux import windows_paths as paths


class Function:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def __call__(self, *arguments):
        self.calls.append(arguments)
        return self.callback(*arguments)


class WindowsPathsTests(unittest.TestCase):
    def test_windows_uses_known_folder_over_package_environment_path(self):
        expected = Path.home() / "AppData" / "Local"
        with patch.object(paths.sys, "platform", "win32"), \
             patch.object(paths, "_known_local_app_data", return_value=expected), \
             patch.dict(os.environ, {"LOCALAPPDATA": str(expected / "Packages" / "SomePackage")}):
            self.assertEqual(paths.local_app_data(), expected)
            self.assertEqual(paths.redux_user_data(), expected / "ReduxMediaLibrary")

    def test_failure_falls_back_to_environment_and_missing_environment_to_home(self):
        expected = Path.home() / "selected-data"
        with patch.object(paths.sys, "platform", "win32"), \
             patch.object(paths, "_known_local_app_data", side_effect=OSError("Missing API")), \
             patch.dict(os.environ, {"LOCALAPPDATA": str(expected)}):
            self.assertEqual(paths.local_app_data(), expected)
        with patch.object(paths.sys, "platform", "win32"), \
             patch.object(paths, "_known_local_app_data", side_effect=AttributeError("Missing entrypoint")), \
             patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            self.assertEqual(paths.local_app_data(), Path.home() / ".config")

    def test_non_windows_keeps_portable_fallback_without_native_calls(self):
        expected = Path.home() / "custom"
        with patch.object(paths.sys, "platform", "linux"), \
             patch.object(paths, "_known_local_app_data", side_effect=AssertionError("No Windows API")), \
             patch.dict(os.environ, {"LOCALAPPDATA": str(expected)}):
            self.assertEqual(paths.local_app_data(), expected)

    def test_known_folder_uses_exact_guid_no_redirect_flags_and_frees_memory(self):
        expected = Path.home() / "AppData" / "Local"
        buffer = ctypes.create_unicode_buffer(str(expected))
        def query(folder, flags, token, result):
            self.assertEqual(bytes(folder._obj), uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le)
            self.assertEqual(flags, 0x14000)
            self.assertIsNone(token)
            result._obj.value = ctypes.addressof(buffer)
            return 0
        shell = type("Shell", (), {"SHGetKnownFolderPath": Function(query)})()
        ole = type("Ole", (), {"CoTaskMemFree": Function(lambda _: None)})()
        with patch.object(ctypes, "WinDLL", side_effect=[shell, ole], create=True):
            self.assertEqual(paths._known_local_app_data(), expected)
        self.assertEqual(len(ole.CoTaskMemFree.calls), 1)
        self.assertIs(shell.SHGetKnownFolderPath.restype, ctypes.c_int32)
        self.assertIsNone(ole.CoTaskMemFree.restype)

    def test_failed_hresult_frees_any_allocated_pointer(self):
        buffer = ctypes.create_unicode_buffer("allocated")
        def query(_folder, _flags, _token, result):
            result._obj.value = ctypes.addressof(buffer)
            return -2147024891
        shell = type("Shell", (), {"SHGetKnownFolderPath": Function(query)})()
        ole = type("Ole", (), {"CoTaskMemFree": Function(lambda _: None)})()
        with patch.object(ctypes, "WinDLL", side_effect=[shell, ole], create=True), \
             self.assertRaisesRegex(OSError, "0x80070005"):
            paths._known_local_app_data()
        self.assertEqual(len(ole.CoTaskMemFree.calls), 1)

    def test_empty_null_or_relative_native_path_does_not_become_current_directory(self):
        for value in (None, "", "relative-path"):
            buffer = ctypes.create_unicode_buffer(value or "")
            def query(_folder, _flags, _token, result):
                if value is not None:
                    result._obj.value = ctypes.addressof(buffer)
                return 0
            shell = type("Shell", (), {"SHGetKnownFolderPath": Function(query)})()
            ole = type("Ole", (), {"CoTaskMemFree": Function(lambda _: None)})()
            with self.subTest(value=value), patch.object(ctypes, "WinDLL", side_effect=[shell, ole], create=True), \
                 self.assertRaises(OSError):
                paths._known_local_app_data()
            self.assertEqual(len(ole.CoTaskMemFree.calls), 0 if value is None else 1)


if __name__ == "__main__":
    unittest.main()
