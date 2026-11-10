"""Xbox app tile discovery is read only and never steals an OEM reservation."""
import unittest
from unittest.mock import patch

from redux import windows_xbox_apps as xbox


class Key:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class Registry:
    HKEY_LOCAL_MACHINE = 2
    KEY_READ, KEY_WOW64_64KEY = 1, 256
    REG_SZ = 1

    def __init__(self, values):
        self.values = values
        self.calls = []

    def OpenKey(self, hive, path, _, access):
        self.calls.append((hive, path, access))
        slot = path.rsplit("\\", 1)[-1]
        if slot not in self.values:
            raise FileNotFoundError(slot)
        if isinstance(self.values[slot], Exception):
            raise self.values[slot]
        return Key(self.values[slot])

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]

    def QueryInfoKey(self, key):
        return (0, len(key.values), 0)


class WindowsXboxAppTests(unittest.TestCase):
    def status(self, values, path=r"C:\TV\Redux.exe"):
        registry = Registry(values)
        with patch.object(xbox, "_registry", registry):
            result = xbox.xbox_app_status(path)
        self.assertEqual(len(registry.calls), 3)
        self.assertTrue(all(hive == 2 and access == 257 for hive, _, access in registry.calls))
        return result

    def test_no_slots_occupied_gives_first_slot_without_writing(self):
        state = self.status({})
        self.assertFalse(state.registered)
        self.assertEqual(state.available_slots, 3)
        self.assertEqual(state.free_slot, "OEMAppPathsAndName1")
        self.assertIsNone(state.error)

    def test_existing_other_app_and_unknown_settings_are_both_preserved(self):
        state = self.status({"OEMAppPathsAndName1": {"OemAppExePath": (r"C:\Other\Other.exe", 1)},
                             "OEMAppPathsAndName2": {"Reserved": (1, 4)}})
        self.assertEqual(state.free_slot, "OEMAppPathsAndName3")
        self.assertEqual(state.available_slots, 1)
        self.assertFalse(state.registered)

    def test_owned_path_matches_case_insensitively(self):
        state = self.status({"OEMAppPathsAndName2": {"OemAppName": ("Redux", 1),
                "OemAppExePath": (r"c:\TV\REDUX.exe", 1),
                "OemAppIconPath": (r"C:\TV\logo.png", 1)}})
        self.assertTrue(state.registered)
        self.assertEqual(state.slot, "OEMAppPathsAndName2")
        self.assertEqual(state.slots[1].name, "Redux")

    def test_full_slots_cannot_be_claimed(self):
        state = self.status({slot: {"Reserved": (1, 4)} for slot in xbox.SLOT_NAMES})
        self.assertIsNone(state.free_slot)
        self.assertEqual(state.available_slots, 0)

    def test_default_windows_empty_keys_are_free(self):
        state = self.status({slot: {} for slot in xbox.SLOT_NAMES})
        self.assertEqual(state.available_slots, 3)
        self.assertEqual(state.free_slot, "OEMAppPathsAndName1")
        self.assertTrue(state.slots[0].exists)
        self.assertTrue(state.slots[0].available)

    def test_access_denied_key_is_occupied_and_reported(self):
        state = self.status({"OEMAppPathsAndName1": PermissionError("Denied")})
        self.assertEqual(state.free_slot, "OEMAppPathsAndName2")
        self.assertIn("Denied", state.error)

    def test_wrong_value_type_is_not_a_registered_app(self):
        state = self.status({"OEMAppPathsAndName1": {"OemAppExePath": (r"C:\TV\Redux.exe", 3)}})
        self.assertFalse(state.registered)
        self.assertTrue(state.slots[0].exists)

    def test_non_windows_returns_explanation(self):
        with patch.object(xbox, "_registry", None):
            self.assertIn("Windows only", xbox.xbox_app_status("app").error)


if __name__ == "__main__":
    unittest.main()
