"""SDK signatures, optional API behavior, and registry reads without OS changes."""
import ctypes
import unittest
from unittest.mock import patch

from redux import windows_gaming as gaming


class Function:
    def __init__(self, value=0):
        self.value = value
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.value(*args) if callable(self.value) else self.value


class DLL:
    def __init__(self):
        self.IsGamingFullScreenExperienceSupported = Function(1)
        self.IsGamingFullScreenExperienceActive = Function(0)
        self.CanSetGamingFullScreenExperience = Function(1)
        self.SetGamingFullScreenExperience = Function(0)
        self.RegisterGamingFullScreenExperienceChangeNotification = Function(self.register)
        self.UnregisterGamingFullScreenExperienceChangeNotification = Function(None)
        self.callback = None

    def register(self, callback, context, handle):
        self.callback = callback
        handle._obj.value = 47
        return 0


class Key:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class Registry:
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = 1, 2
    KEY_READ, KEY_WOW64_64KEY = 1, 256
    REG_SZ, REG_DWORD = 1, 4

    def __init__(self, values):
        self.values = values
        self.calls = []

    def OpenKey(self, hive, path, _, access):
        self.calls.append((hive, path, access))
        return Key(self.values)

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]


class WindowsGamingTests(unittest.TestCase):
    def make_api(self):
        dll = DLL()
        with patch.object(ctypes, "WinDLL", return_value=dll, create=True), \
             patch.object(ctypes, "WINFUNCTYPE", getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE), create=True):
            api = gaming.WindowsGamingExperience()
        return api, dll

    def test_sdk_query_and_set_signatures_and_no_state_changes_during_query(self):
        api, dll = self.make_api()
        state = api.query()
        self.assertTrue(state.available)
        self.assertTrue(state.supported)
        self.assertFalse(state.active)
        self.assertTrue(state.can_set)
        self.assertEqual(dll.IsGamingFullScreenExperienceActive.argtypes, [])
        self.assertIs(dll.SetGamingFullScreenExperience.restype, ctypes.c_int32)
        self.assertEqual(dll.SetGamingFullScreenExperience.argtypes, [gaming.wintypes.BOOL])
        self.assertEqual(dll.SetGamingFullScreenExperience.calls, [])

    def test_explicit_set_uses_bool_and_hresult_failure_is_readable(self):
        api, dll = self.make_api()
        api.set_active(True)
        self.assertEqual(dll.SetGamingFullScreenExperience.calls, [(True,)])
        dll.SetGamingFullScreenExperience.value = -2147024891
        with self.assertRaisesRegex(OSError, "0x80070005"):
            api.set_active(False)

    def test_unsupported_mode_does_not_call_set(self):
        api, dll = self.make_api()
        dll.IsGamingFullScreenExperienceSupported.value = 0
        with self.assertRaisesRegex(OSError, "not available"):
            api.set_active(True)
        dll.IsGamingFullScreenExperienceSupported.value = 1
        dll.CanSetGamingFullScreenExperience.value = 0
        with self.assertRaisesRegex(OSError, "cannot switch"):
            api.set_active(True)
        self.assertEqual(dll.SetGamingFullScreenExperience.calls, [])

    def test_old_windows_missing_api_is_graceful(self):
        for error in (OSError("missing DLL"), AttributeError("missing entry point")):
            with self.subTest(error=error), patch.object(ctypes, "WinDLL", side_effect=error, create=True):
                api = gaming.WindowsGamingExperience()
                self.assertFalse(api.query().available)
                self.assertIsNone(api.subscribe(lambda _: None))
                api.close()

    def test_notification_queries_fresh_state_and_unregisters_once(self):
        api, dll = self.make_api()
        states = []
        registration = api.subscribe(states.append)
        self.assertIn(registration, api._registrations)
        dll.IsGamingFullScreenExperienceActive.value = 1
        dll.callback(None)
        self.assertEqual(len(states), 1)
        self.assertTrue(states[0].active)
        api.close()
        registration.close()
        self.assertEqual(len(dll.UnregisterGamingFullScreenExperienceChangeNotification.calls), 1)
        self.assertFalse(api._registrations)

    def test_callback_exception_does_not_escape_into_windows(self):
        api, dll = self.make_api()
        def broken(_):
            raise RuntimeError("UI destroyed")
        api.subscribe(broken)
        dll.callback(None)
        api.close()

    def test_failed_notification_registration_does_not_leak(self):
        api, dll = self.make_api()
        dll.RegisterGamingFullScreenExperienceChangeNotification.value = -2147024891
        with self.assertRaisesRegex(OSError, "0x80070005"):
            api.subscribe(lambda _: None)
        self.assertFalse(api._registrations)

    def test_configuration_reads_correct_hives_and_types(self):
        registry = Registry({"DeviceForm": (46, 4), "AllowDevelopmentWithoutDevLicense": (1, 4),
                             "GamingHomeApp": ("OurPackage!App", 1), "StartupToGamingHome": (1, 4)})
        with patch.object(gaming, "_registry", registry):
            state = gaming.gaming_configuration()
        self.assertTrue(state.handheld)
        self.assertTrue(state.developer_mode)
        self.assertTrue(state.startup_to_gaming_home)
        self.assertEqual(state.home_app, "OurPackage!App")
        self.assertIsNone(state.error)
        self.assertEqual([item[0] for item in registry.calls], [2, 2, 1, 1])
        self.assertTrue(all(item[2] == 257 for item in registry.calls))

    def test_missing_configuration_is_normal_desktop_and_wrong_types_are_ignored(self):
        for values in ({}, {"DeviceForm": ("46", 1), "AllowDevelopmentWithoutDevLicense": ("1", 1)}):
            with patch.object(gaming, "_registry", Registry(values)):
                state = gaming.gaming_configuration()
            self.assertIsNone(state.device_form)
            self.assertFalse(state.handheld)
            self.assertFalse(state.developer_mode)
            self.assertIsNone(state.error)

    def test_unpacked_package_has_no_identity(self):
        kernel = type("Kernel", (), {})()
        kernel.GetCurrentPackageFullName = Function(15700)  # APPMODEL_ERROR_NO_PACKAGE
        kernel.GetCurrentPackageFamilyName = Function(15700)
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            self.assertIsNone(gaming.current_package_full_name())
            self.assertIsNone(gaming.current_package_family_name())

    def test_package_identity_uses_two_calls_and_bounded_length(self):
        def query(length, result):
            if result is None:
                length._obj.value = 32
                return 122
            result.value = "OurPackage_1.0_x64"
            return 0
        kernel = type("Kernel", (), {"GetCurrentPackageFullName": Function(query)})()
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            self.assertEqual(gaming.current_package_full_name(), "OurPackage_1.0_x64")
        self.assertEqual(len(kernel.GetCurrentPackageFullName.calls), 2)


if __name__ == "__main__":
    unittest.main()
