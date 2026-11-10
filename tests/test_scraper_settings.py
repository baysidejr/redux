"""Account portability, protected storage, and interrupted-update behavior."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from redux.scraper_settings import (
    DEFAULTS, PROVIDER_FIELDS, ROLES, ScraperSettings, WindowsDataProtector,
)


class TestProtector:
    """Deterministic, explicitly injected test double; never production storage."""
    def protect(self, value):
        return b"test-only:" + base64.b64encode(bytes(byte ^ 0xA5 for byte in value))

    def unprotect(self, value):
        if not value.startswith(b"test-only:"):
            raise OSError("wrong test envelope")
        return bytes(byte ^ 0xA5 for byte in base64.b64decode(value[10:], validate=True))


class ScraperSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = self.root / "library"
        self.user = self.root / "local-user"
        self.protector = TestProtector()
        self.store = ScraperSettings(self.library, self.user, protector=self.protector)

    def test_defaults_and_account_reads_do_not_create_directories(self):
        self.assertEqual(self.store.load(), DEFAULTS)
        self.assertEqual(self.store.credentials("screenscraper"), {})
        self.assertFalse(self.store.has_credentials("screenscraper"))
        self.assertEqual(self.store.status("screenscraper"),
                         {field: "Not set" for field in PROVIDER_FIELDS["screenscraper"]})
        self.store.clear_credentials("screenscraper")
        self.assertFalse(self.library.exists())
        self.assertFalse(self.user.exists())
        # Returned mutable role lists are independent of module defaults.
        self.store.load()["roles"].clear()
        self.assertEqual(self.store.load()["roles"], list(ROLES))

    def test_partial_options_round_trip_and_keep_accounts_outside_library(self):
        saved = self.store.save({"provider": "libretro", "language": "fr", "roles": ["cover", "hero"]})
        self.assertEqual(saved["region"], "us")
        self.assertFalse(saved["overwrite"])
        self.store.save({"overwrite": True})
        other = ScraperSettings(self.library, self.user, protector=self.protector)
        self.assertEqual(other.load(), dict(saved, overwrite=True))
        self.assertEqual(json.loads(self.store.path.read_text())["schema"], 1)
        self.assertFalse(self.user.exists())

    def test_copying_library_moves_options_but_no_accounts(self):
        import shutil
        self.store.save({"provider": "igdb", "region": "jp"})
        self.store.set_credential("igdb", "client_secret", "private-app-secret")
        moved = self.root / "another-pc" / "library"
        shutil.copytree(self.library, moved)
        destination = ScraperSettings(moved, self.root / "another-user", protector=self.protector)
        self.assertEqual(destination.load()["provider"], "igdb")
        self.assertEqual(destination.load()["region"], "jp")
        self.assertEqual(destination.credentials("igdb"), {})
        self.assertFalse(any(b"private-app-secret" in p.read_bytes() for p in moved.rglob("*") if p.is_file()))

    def test_plaintext_account_fields_cannot_enter_portable_options(self):
        for field in ("username", "password", "api_key", "client_secret", "accounts", "credentials"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.save({field: "keep-private"})
        self.assertFalse(self.library.exists())

    def test_invalid_options_are_rejected_before_writing(self):
        bad = ({"provider": "../outside"}, {"provider": True}, {"region": "USA"},
               {"region": "us&password=x"}, {"language": ""}, {"overwrite": 1},
               {"automatic": "yes"}, {"roles": []}, {"roles": ["cover", "cover"]},
               {"roles": ["unknown"]}, {"roles": "cover"}, {"roles": [True]},
               {"tile_style": "cropped"}, {"tile_style": True})
        for values in bad:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.store.save(values)
        self.assertFalse(self.library.exists())

    def test_tile_style_is_portable_and_validated(self):
        self.assertEqual(self.store.load()["tile_style"], "mixed")
        for style in ("mixed", "screenshot", "cover"):
            self.assertEqual(self.store.save({"tile_style": style})["tile_style"], style)
            self.assertEqual(self.store.load()["tile_style"], style)

    def test_accounts_round_trip_preserve_other_providers_and_mask_values(self):
        self.store.set_credential("screenscraper", "username", "my-name")
        self.store.set_credential("screenscraper", "password", "  p@ss words 🌙  ")
        self.store.set_credential("thegamesdb", "api_key", "my-own-api-key")
        self.assertEqual(self.store.credentials("screenscraper"),
                         {"username": "my-name", "password": "  p@ss words 🌙  "})
        self.assertEqual(self.store.credentials("thegamesdb"), {"api_key": "my-own-api-key"})
        self.assertTrue(self.store.has_credentials("screenscraper"))
        self.assertEqual(self.store.status("screenscraper")["password"], "Saved")
        self.assertNotIn("my-name", repr(self.store.status("screenscraper")))
        self.assertNotIn("p@ss", repr(self.store.status("screenscraper")))
        self.store.set_credential("screenscraper", "password", "")
        self.assertEqual(self.store.credentials("screenscraper"), {"username": "my-name"})
        self.store.clear_credentials("screenscraper")
        self.assertEqual(self.store.credentials("screenscraper"), {})
        self.assertEqual(self.store.credentials("thegamesdb"), {"api_key": "my-own-api-key"})

    def test_account_file_has_only_protected_bytes_and_no_portable_file(self):
        self.store.set_credential("screenscraper", "username", "unmistakable-account")
        self.store.set_credential("screenscraper", "password", "unmistakable-password")
        raw = self.store.credentials_path.read_bytes()
        self.assertNotIn(b"unmistakable-account", raw)
        self.assertNotIn(b"unmistakable-password", raw)
        self.assertFalse(self.library.exists())
        value = self.store.credentials("screenscraper")
        value["password"] = "caller changed copy"
        self.assertEqual(self.store.credentials("screenscraper")["password"], "unmistakable-password")

    def test_unknown_provider_fields_and_bad_text_do_not_write(self):
        for provider, field, value in (("unknown", "password", "x"),
                ("screenscraper", "token", "x"), ("libretro", "password", "x"),
                ("igdb", "client_secret", "x" * 4097), ("screenscraper", "password", "x\nsecret"),
                ("screenscraper", "password", "\ud800"), ("screenscraper", "password", 42)):
            with self.subTest(provider=provider, field=field), self.assertRaises(ValueError):
                self.store.set_credential(provider, field, value)
        self.assertFalse(self.user.exists())
        for provider in (None, True, "../../file"):
            with self.assertRaises(ValueError):self.store.credentials(provider)

    def test_no_plaintext_fallback_when_protection_fails_and_errors_hide_secrets(self):
        self.store.set_credential("igdb", "client_secret", "old-secret")
        original = self.store.credentials_path.read_bytes()
        with patch.object(self.protector, "protect", side_effect=OSError("private-new-secret")):
            with self.assertRaises(OSError) as raised:
                self.store.set_credential("igdb", "client_secret", "private-new-secret")
        self.assertNotIn("private-new-secret", str(raised.exception))
        self.assertEqual(self.store.credentials_path.read_bytes(), original)
        self.assertEqual(list(self.user.glob("*.tmp")), [])

    def test_unreadable_account_blob_is_preserved_and_explicit_clear_all_recovers(self):
        self.store.set_credential("igdb", "client_secret", "old-secret")
        original = self.store.credentials_path.read_bytes()
        with patch.object(self.protector, "unprotect", side_effect=OSError("private-account-data")):
            with self.assertRaises(OSError) as raised:self.store.credentials("igdb")
            self.assertNotIn("private-account-data", str(raised.exception))
            with self.assertRaises(OSError):self.store.set_credential("igdb", "client_id", "new-id")
            self.assertEqual(self.store.credentials_path.read_bytes(), original)
            self.store.clear_all_credentials()
        self.assertEqual(self.store.credentials("igdb"), {})

    def test_invalid_existing_portable_documents_are_not_replaced(self):
        self.store.path.parent.mkdir(parents=True)
        documents = (b"[]", b"not-json", b'{"schema":1,"schema":1,"options":{}}',
                     b'{"schema":true,"options":{}}', b'{"schema":2,"options":{}}',
                     b'{"schema":1,"options":{"overwrite":NaN}}', b"x" * 16385)
        for raw in documents:
            self.store.path.write_bytes(raw)
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):self.store.load()
            with self.assertRaises(ValueError):self.store.save({"overwrite": True})
            self.assertEqual(self.store.path.read_bytes(), raw)

    def test_malformed_credential_file_is_not_discarded_on_save(self):
        self.user.mkdir()
        for raw in (b"plaintext-wrong-format", b"x" * 131073):
            self.store.credentials_path.write_bytes(raw)
            with self.assertRaises(ValueError):self.store.credentials("screenscraper")
            with self.assertRaises(ValueError):self.store.set_credential("screenscraper", "username", "x")
            self.assertEqual(self.store.credentials_path.read_bytes(), raw)

    def test_atomic_write_failure_preserves_original_and_removes_temporary_files(self):
        self.store.save({"language": "de"})
        self.store.set_credential("igdb", "client_secret", "old-secret")
        original_config = self.store.path.read_bytes()
        original_secret = self.store.credentials_path.read_bytes()
        for operation in ("os.replace", "os.fsync"):
            with self.subTest(operation=operation), \
                 patch("redux.scraper_settings." + operation, side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):self.store.save({"language": "en"})
                with self.assertRaises(OSError):self.store.set_credential("igdb", "client_secret", "new-secret")
            self.assertEqual(self.store.path.read_bytes(), original_config)
            self.assertEqual(self.store.credentials_path.read_bytes(), original_secret)
            self.assertEqual(list(self.store.path.parent.glob("*.tmp")), [])
            self.assertEqual(list(self.user.glob("*.tmp")), [])

    def test_concurrent_option_change_is_not_overwritten(self):
        self.store.save({"language": "de"})
        original_read = self.store._read
        reads = 0
        def changed(path, limit):
            nonlocal reads
            reads += 1
            if reads == 2:
                document = {"schema": 1, "options": dict(DEFAULTS, language="fr")}
                self.store.path.write_text(json.dumps(document))
            return original_read(path, limit)
        with patch.object(self.store, "_read", side_effect=changed), self.assertRaisesRegex(RuntimeError, "changed"):
            self.store.save({"language": "en"})
        self.assertEqual(self.store.load()["language"], "fr")

    def test_accounts_cannot_be_written_inside_portable_library(self):
        for user in (self.library, self.library / "local-user"):
            with self.assertRaises(ValueError):ScraperSettings(self.library, user, protector=self.protector)

    def test_symlinked_options_or_account_storage_are_rejected(self):
        self.store.path.parent.mkdir(parents=True)
        outside = self.root / "outside.json"
        outside.write_text("untouched")
        try:self.store.path.symlink_to(outside)
        except (OSError, NotImplementedError) as error:self.skipTest(str(error))
        with self.assertRaises(ValueError):self.store.load()
        with self.assertRaises(ValueError):self.store.save({"language": "fr"})
        self.assertEqual(outside.read_text(), "untouched")
        self.user.mkdir()
        self.store.credentials_path.symlink_to(outside)
        with self.assertRaises(ValueError):self.store.credentials("igdb")
        with self.assertRaises(ValueError):self.store.set_credential("igdb", "client_id", "x")

    def test_default_local_path_uses_windows_known_folder_adapter(self):
        with patch("redux.scraper_settings.redux_user_data", return_value=self.user):
            settings = ScraperSettings(self.library, protector=self.protector)
        self.assertEqual(settings.credentials_path.parent, self.user)

    def test_non_windows_storage_never_silently_saves_plaintext(self):
        settings = ScraperSettings(self.library, self.user)
        with patch("redux.scraper_settings.os.name", "posix"):
            with self.assertRaisesRegex(OSError, "protected account storage"):
                settings.set_credential("igdb", "client_id", "do-not-store-plain")
        self.assertFalse(self.user.exists())


@unittest.skipUnless(os.name == "nt", "Windows DPAPI is required")
class NativeProtectedAccountTests(unittest.TestCase):
    def test_real_user_bound_dpapi_round_trip_and_protected_local_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = ScraperSettings(root / "library", root / "local-user")
            synthetic = "Redux isolated synthetic account test 🔒"
            settings.set_credential("screenscraper", "password", synthetic)
            self.assertEqual(settings.credentials("screenscraper"), {"password": synthetic})
            self.assertNotIn(synthetic.encode(), settings.credentials_path.read_bytes())
            self.assertFalse((root / "library").exists())
            settings.clear_credentials("screenscraper")
            self.assertEqual(settings.credentials("screenscraper"), {})


if __name__ == "__main__":
    unittest.main()
