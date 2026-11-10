"""Own-value startup changes tested without changing the user's registry."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from redux import windows_startup as startup


class Key:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class Registry:
    HKEY_CURRENT_USER = 1
    KEY_READ, KEY_SET_VALUE = 1, 2
    REG_SZ, REG_BINARY, REG_DWORD = 1, 3, 4

    def __init__(self):
        self.store = {}
        self.calls = []

    def OpenKey(self, hive, path, _, access):
        assert hive == self.HKEY_CURRENT_USER
        self.calls.append(("open", path, access))
        if path not in self.store:
            raise FileNotFoundError(path)
        return Key(self.store, path)

    def CreateKeyEx(self, hive, path, _, access):
        assert hive == self.HKEY_CURRENT_USER
        self.calls.append(("create", path, access))
        self.store.setdefault(path, {})
        return Key(self.store, path)

    def QueryValueEx(self, key, name):
        try:
            return self.store[key.path][name]
        except KeyError:
            raise FileNotFoundError(name)

    def SetValueEx(self, key, name, _, kind, value):
        self.calls.append(("set", key.path, name, kind, value))
        self.store[key.path][name] = value, kind

    def DeleteValue(self, key, name):
        self.calls.append(("delete", key.path, name))
        try:
            del self.store[key.path][name]
        except KeyError:
            raise FileNotFoundError(name)


class WindowsStartupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.exe = Path(temp.name) / "TV App" / "Redux.exe"
        self.exe.parent.mkdir()
        self.exe.touch()
        self.registry = Registry()
        self.patch = patch.object(startup, "_registry", self.registry)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_command_quotes_executable_and_forces_startup_fullscreen(self):
        command = startup.startup_command(self.exe)
        self.assertEqual(command, f'"{self.exe}" --startup --fullscreen')
        self.assertNotIn("cmd", command)

    def test_invalid_relative_non_exe_and_oversized_paths_are_rejected(self):
        for path in ("Redux.exe", self.exe.with_suffix(".bat"), self.exe.parent / ("x" * 260 + ".exe")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                startup.startup_command(path)
        self.assertFalse(self.registry.calls)

    def test_query_is_read_only_and_reports_missing_registration(self):
        state = startup.startup_status(self.exe)
        self.assertFalse(state.enabled)
        self.assertFalse(state.with_xbox_mode)
        self.assertIsNone(state.error)
        self.assertEqual(state.expected_command, startup.startup_command(self.exe))
        self.assertTrue(all(call[0] == "open" for call in self.registry.calls))

    def test_enable_uses_own_per_user_run_and_xbox_login_policy_only(self):
        state = startup.set_start_with_windows(True, self.exe)
        self.assertTrue(state.enabled)
        self.assertTrue(state.with_xbox_mode)
        self.assertFalse(state.disabled_by_windows)
        self.assertFalse(state.needs_update)
        self.assertEqual(self.registry.store[startup.RUN_KEY][startup.VALUE_NAME],
                         (startup.startup_command(self.exe), self.registry.REG_SZ))
        self.assertEqual(self.registry.store[startup.GAMING_STARTUP_KEY][startup.VALUE_NAME],
                         (1, self.registry.REG_DWORD))
        self.assertEqual(set(self.registry.store), {startup.RUN_KEY, startup.GAMING_STARTUP_KEY})

    def test_explicit_enable_clears_only_own_windows_disabled_marker(self):
        self.registry.store[startup.STARTUP_APPROVED_KEY] = {
            startup.VALUE_NAME: (b"\x03\0\0\0" + b"\0" * 8, self.registry.REG_BINARY),
            "OtherApp": (b"\x03\0\0\0", self.registry.REG_BINARY),
        }
        startup.set_start_with_windows(True, self.exe)
        self.assertNotIn(startup.VALUE_NAME, self.registry.store[startup.STARTUP_APPROVED_KEY])
        self.assertIn("OtherApp", self.registry.store[startup.STARTUP_APPROVED_KEY])

    def test_disable_removes_only_owned_values_and_is_idempotent(self):
        startup.set_start_with_windows(True, self.exe)
        for path in (startup.RUN_KEY, startup.GAMING_STARTUP_KEY, startup.STARTUP_APPROVED_KEY):
            self.registry.store.setdefault(path, {})["OtherApp"] = ("untouched", self.registry.REG_SZ)
        self.assertFalse(startup.set_start_with_windows(False, self.exe).enabled)
        self.assertFalse(startup.set_start_with_windows(False, self.exe).enabled)
        for values in self.registry.store.values():
            self.assertEqual(values, {"OtherApp": ("untouched", self.registry.REG_SZ)})

    def test_deferred_until_desktop_policy_is_zero_not_xbox_login(self):
        state = startup.set_start_with_windows(True, self.exe, with_xbox_mode=False)
        self.assertTrue(state.enabled)
        self.assertFalse(state.with_xbox_mode)
        self.assertEqual(self.registry.store[startup.GAMING_STARTUP_KEY][startup.VALUE_NAME],
                         (0, self.registry.REG_DWORD))

    def test_status_reports_stale_path_and_windows_disable_without_mutation(self):
        self.registry.store[startup.RUN_KEY] = {startup.VALUE_NAME: ('"C:\\old\\Redux.exe"', self.registry.REG_SZ)}
        self.registry.store[startup.STARTUP_APPROVED_KEY] = {
            startup.VALUE_NAME: (b"\x03\0\0\0", self.registry.REG_BINARY)}
        state = startup.startup_status(self.exe)
        self.assertTrue(state.enabled)
        self.assertTrue(state.needs_update)
        self.assertTrue(state.disabled_by_windows)
        self.assertTrue(all(call[0] == "open" for call in self.registry.calls))

    def test_windows_disable_bit_preserves_other_status_bits(self):
        for value, expected in ((2, False), (3, True), (6, False), (7, True)):
            self.registry.store[startup.STARTUP_APPROVED_KEY] = {
                startup.VALUE_NAME: (value.to_bytes(4, "little"), self.registry.REG_BINARY)}
            self.assertEqual(startup.startup_status(self.exe).disabled_by_windows, expected)

    def test_missing_executable_cannot_register_startup(self):
        self.exe.unlink()
        with self.assertRaises(FileNotFoundError):
            startup.set_start_with_windows(True, self.exe)
        self.assertFalse(self.registry.calls)

    def test_unsupported_platform_and_registry_failure_are_explicit(self):
        with patch.object(startup, "_registry", None):
            self.assertIn("Windows only", startup.startup_status().error)
            with self.assertRaises(OSError):
                startup.set_start_with_windows(True, self.exe)
        with patch.object(self.registry, "OpenKey", side_effect=PermissionError("Denied")):
            self.assertEqual(startup.startup_status().error, "Denied")


if __name__ == "__main__":
    unittest.main()
