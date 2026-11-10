"""The controller keyboard can enter real passwords and API credentials."""
import string
import unittest

from redux.credential_keyboard import (
    CONTROL_KEYS, MAX_LENGTH, PAGES, current_page, edit_value, keyboard_rows,
    masked_value, next_page, previous_page,
)


class CredentialKeyboardTests(unittest.TestCase):
    def test_all_95_printable_ascii_characters_can_be_entered_from_controller(self):
        available = set()
        for page in PAGES:
            rows = keyboard_rows(page)
            self.assertIsInstance(rows, tuple)
            self.assertTrue(all(isinstance(row, str) and 1 <= len(row) <= 10 for row in rows))
            for row in rows:
                for key in row:
                    self.assertEqual(edit_value("", key), key)
                    available.add(key)
        self.assertIn("SPACE", CONTROL_KEYS)
        available.add(edit_value("", "SPACE"))
        self.assertEqual(available, {chr(code) for code in range(32, 127)})
        self.assertEqual(len(available), 95)

    def test_pages_cycle_in_both_directions_and_invalid_pages_clamp(self):
        self.assertEqual([current_page(page) for page in PAGES], list(PAGES))
        for page in (None, "", "UPPER", 1, True, [], {}):
            self.assertEqual(current_page(page), "lower")
            self.assertEqual(keyboard_rows(page), keyboard_rows("lower"))
        page = "lower"
        seen = []
        for _ in range(3):
            seen.append(page)
            page = next_page(page)
        self.assertEqual(seen, list(PAGES))
        self.assertEqual(page, "lower")
        self.assertEqual(previous_page("lower"), "symbols")
        self.assertEqual(next_page(previous_page("upper")), "upper")

    def test_case_and_every_punctuation_character_survive_unchanged(self):
        expected = "aAzZ019" + string.punctuation + " end "
        value = ""
        for key in expected:
            value = edit_value(value, "SPACE" if key == " " else key)
        self.assertEqual(value, expected)

    def test_edit_controls_work_at_empty_and_full_values(self):
        self.assertEqual(edit_value("", "BACKSPACE"), "")
        self.assertEqual(edit_value("abc", "BACKSPACE"), "ab")
        self.assertEqual(edit_value("abc", "CLEAR"), "")
        self.assertEqual(edit_value("", "CLEAR"), "")
        self.assertEqual(edit_value("ab", "SPACE", 3), "ab ")
        self.assertEqual(edit_value("abc", "d", 3), "abc")
        self.assertEqual(edit_value("x" * MAX_LENGTH, "a"), "x" * MAX_LENGTH)
        self.assertEqual(len(edit_value("x" * MAX_LENGTH, "BACKSPACE")), MAX_LENGTH - 1)

    def test_unicode_from_physical_typing_is_kept_and_masked(self):
        value = ""
        for key in "Café日本🌙":
            value = edit_value(value, key)
        self.assertEqual(value, "Café日本🌙")
        self.assertEqual(masked_value(value), "\u2022" * len(value))
        self.assertEqual(edit_value(value, "BACKSPACE"), value[:-1])

    def test_mask_never_contains_credential_characters(self):
        secret = "sensitive Username@Key!0123"
        self.assertEqual(set(masked_value(secret)), {"\u2022"})
        self.assertEqual(len(masked_value(secret)), len(secret))
        self.assertEqual(masked_value(""), "")
        self.assertEqual(len(masked_value("x" * MAX_LENGTH)), MAX_LENGTH)

    def test_bad_keys_and_limits_do_not_echo_secret_values(self):
        secret = "never-echo-this"
        for key in (None, 7, "ENTER", "PASSWORD=value", "\n", "\r", "\t", "\x7f", "\ud800"):
            with self.subTest(key_type=type(key).__name__), self.assertRaises(ValueError) as raised:
                edit_value(secret, key)
            self.assertNotIn(secret, str(raised.exception))
        for limit in (True, False, None, 0, -1, MAX_LENGTH + 1, 1.5, "4"):
            with self.assertRaises(ValueError) as raised:edit_value(secret, "a", limit)
            self.assertNotIn(secret, str(raised.exception))
        with self.assertRaises(ValueError) as raised:edit_value(secret, "a", 3)
        self.assertNotIn(secret, str(raised.exception))

    def test_invalid_values_are_rejected_without_logging_contents(self):
        for value in (None, [], 123, "not-printable\n", "x" * (MAX_LENGTH + 1)):
            with self.assertRaises(ValueError):edit_value(value, "a")
            with self.assertRaises(ValueError):masked_value(value)


if __name__ == "__main__":
    unittest.main()
