"""Updating from GitHub releases: version checks, verified installer downloads and the About → Updates row."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import io
import json
import tempfile
import threading
import unittest

from redux import __version__
from redux.console_ui import ConsoleApp
from redux.console_secondary import SecondaryScreens
from redux.updates import (parse_version, is_newer, latest_release, expected_digest, download_installer,
                           helper_script, UpdateError, LATEST_URL)


def release_json(version="9.9.9", with_sums=True):
    assets = [{"name": f"ReduxSetup-{version}-win-x64.exe", "size": 5, "browser_download_url": f"https://dl/ReduxSetup-{version}-win-x64.exe"},
              {"name": f"ReduxMediaLibrary-{version}-win-x64.zip", "size": 9, "browser_download_url": "https://dl/zip"}]
    if with_sums:
        assets.append({"name": f"SHA256SUMS-{version}.txt", "browser_download_url": "https://dl/sums"})
    return {"tag_name": f"v{version}", "name": f"Redux {version} — test", "body": "Notes here.", "html_url": "https://github.com/x/releases/tag/v" + version, "assets": assets}


class Opener:
    def __init__(self, pages):
        self.pages = pages;self.urls = []
    def __call__(self, url):
        self.urls.append(url)
        body = self.pages[url]
        return io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))


class VersionTests(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(parse_version("v0.11.2"), (0, 11, 2));self.assertIsNone(parse_version("latest"))
        self.assertTrue(is_newer("v0.11.0", "0.10.10"));self.assertFalse(is_newer("0.10.9", "0.10.10"));self.assertFalse(is_newer("0.10.10", "0.10.10"))
        self.assertTrue(is_newer("1.0.0", __version__))

    def test_latest_release_reads_the_installer_and_checksum_assets(self):
        opener = Opener({LATEST_URL: release_json()})
        release = latest_release(opener)
        self.assertEqual((release["version"], release["installer_name"], release["installer_bytes"]), ("9.9.9", "ReduxSetup-9.9.9-win-x64.exe", 5))
        self.assertEqual(release["sums_url"], "https://dl/sums");self.assertEqual(release["notes"], "Notes here.")
        with self.assertRaises(UpdateError):
            latest_release(Opener({LATEST_URL: {"tag_name": "v1.0.0", "assets": []}}))

    def test_expected_digest_accepts_the_published_format(self):
        text = "a" * 64 + " *ReduxSetup-9.9.9-win-x64.exe\n" + "b" * 64 + "  other.zip\n"
        self.assertEqual(expected_digest(text, "ReduxSetup-9.9.9-win-x64.exe"), "a" * 64)
        self.assertIsNone(expected_digest(text, "missing.exe"))


class DownloadTests(unittest.TestCase):
    def test_download_verifies_against_the_checksum_file(self):
        payload = b"setup"
        sums = (hashlib.sha256(payload).hexdigest() + " *ReduxSetup-9.9.9-win-x64.exe\n").encode()
        release = latest_release(Opener({LATEST_URL: release_json()}))
        with tempfile.TemporaryDirectory() as temporary:
            opener = Opener({"https://dl/sums": sums, release["installer_url"]: payload})
            seen = []
            path = download_installer(release, temporary, progress=lambda d, t: seen.append((d, t)), opener=opener)
            self.assertEqual(path, Path(temporary) / "ReduxSetup-9.9.9-win-x64.exe");self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(seen[-1], (5, 5));self.assertEqual(opener.urls[0], "https://dl/sums")
            # Tampered payload: refused and removed.
            with self.assertRaises(UpdateError):
                download_installer(release, temporary, opener=Opener({"https://dl/sums": sums, release["installer_url"]: b"setup!"}))
            self.assertEqual(sorted(p.name for p in Path(temporary).iterdir()), ["ReduxSetup-9.9.9-win-x64.exe"])
            # A checksum file that does not list the installer is an error, not a pass.
            with self.assertRaises(UpdateError):
                download_installer(release, temporary, opener=Opener({"https://dl/sums": b"zz other\n", release["installer_url"]: payload}))
            stop = threading.Event();stop.set()
            with self.assertRaises(UpdateError):
                download_installer(release, temporary, cancelled=stop, opener=Opener({"https://dl/sums": sums, release["installer_url"]: payload}))

    def test_helper_waits_for_redux_installs_silently_and_restarts(self):
        script = helper_script(Path(r"C:\d\ReduxSetup-9.9.9-win-x64.exe"), Path(r"C:\p\Redux.exe"), 4321)
        self.assertIn('"PID eq 4321"', script);self.assertIn("/VERYSILENT /SUPPRESSMSGBOXES /NORESTART", script)
        self.assertIn('start "" "C:\\p\\Redux.exe"', script);self.assertIn("Redux Controller Receiver", script)
        self.assertLess(script.index("/VERYSILENT"), script.index('start "" "C:\\p\\Redux.exe"'))


class UpdatesRowTests(unittest.TestCase):
    def app(self):
        app = object.__new__(ConsoleApp)
        app.screen = "settings";app.settings_menu = False;app.settings_section = "about";app.focus = "settings:updates"
        app.overlay = None;app.targets = [];app.callbacks = {};app.all_games = [];app.preferences = {};app.busy = False
        app.library = SimpleNamespace(root=Path(tempfile.gettempdir()));app.firmware_directory = lambda: Path("bios")
        app.root = SimpleNamespace(attributes=lambda *a: False)
        app.texts = [];app.calls = []
        app.display = app.text = lambda x, y, value, *a, **k: app.texts.append(str(value))
        app.panel = app.rect = app.icon = lambda *a, **k: None
        app.fit_text = lambda value, *a, **k: str(value)
        app.target = lambda key, bounds, callback, label="": (app.targets.append(key), app.callbacks.__setitem__(key, callback))
        app.button_canvas = lambda *a, **k: None;app.schedule_draw = lambda: None;app.fullscreen = lambda: None
        app.notice = lambda title, message: app.calls.append(("notice", title))
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items, "message": message})
        app.task = lambda work, done, description, **k: (app.calls.append(("task", description)), done(work()))
        return app

    def test_about_offers_the_updates_row_before_fullscreen(self):
        app = self.app()
        SecondaryScreens._settings(app)
        self.assertEqual(app.targets, ["settings:updates", "settings:sounds", "settings:fullscreen"])
        self.assertIn(f"Redux {__version__} · A checks GitHub for a newer release", app.texts)

    def test_checking_finds_the_latest_or_offers_the_newer_one(self):
        import redux.console_updates as module
        import redux.updates as updates
        app = self.app()
        original = updates.latest_release
        try:
            updates.latest_release = lambda opener=None: dict(version=__version__, installer_bytes=0, notes="", name="", installer_name="x", installer_url="u", sums_url=None)
            app.updates_menu()
            self.assertEqual(app.calls[-1], ("notice", "You have the latest Redux"));self.assertTrue(app.update_checked)
            updates.latest_release = lambda opener=None: dict(version="9.9.9", installer_bytes=90 * 2**20, notes="Big news", name="Redux 9.9.9", installer_name="ReduxSetup-9.9.9-win-x64.exe", installer_url="u", sums_url="s")
            app.update_checked = False;app.update_release = None
            app.updates_menu()
            self.assertEqual(app.overlay["title"], "Redux 9.9.9 is available")
            self.assertEqual([label for label, _ in app.overlay["items"]], ["Download and install · 90 MB", "Not now"])
            self.assertEqual(app.overlay["message"], "Big news")
            app.texts = [];SecondaryScreens._settings(app)
            self.assertIn("Redux 9.9.9 is available · A installs it", app.texts)
        finally:
            updates.latest_release = original


if __name__ == "__main__":
    unittest.main()
