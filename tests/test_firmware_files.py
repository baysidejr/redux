"""Firmware files: what each system needs, finding them on a drive, copying them in, and the prompts."""
from pathlib import Path
from types import SimpleNamespace
import queue
import tempfile
import threading
import unittest

from redux import firmware_files as fw
from redux.console_ui import ConsoleApp


class FirmwareTableTests(unittest.TestCase):
    def test_every_known_file_names_a_real_system_and_names_are_unique(self):
        from redux.library import SYSTEMS
        names = [entry.name.casefold() for entry in fw.KNOWN]
        self.assertEqual(len(names), len(set(names)))
        for entry in fw.KNOWN:
            self.assertIn(entry.system, SYSTEMS, entry.name)
        self.assertEqual([e.name for e in fw.files_for("sega_cd")], ["bios_CD_U.bin", "bios_CD_E.bin", "bios_CD_J.bin"])
        self.assertEqual(fw.BY_NAME["neogeo.zip"].relative, "fbneo/neogeo.zip")

    def test_missing_firmware_follows_the_game_region_and_accepts_any_when_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            names, message = fw.missing_firmware("sega_cd", folder, region="U")
            self.assertEqual(names, ["bios_CD_U.bin"]);self.assertIn(str(folder), message);self.assertIn("retry Play", message)
            self.assertEqual(fw.missing_firmware("sega_cd", folder, region="Europe")[0], ["bios_CD_E.bin"])
            self.assertEqual(fw.missing_firmware("sega_cd", folder)[0], ["bios_CD_U.bin", "bios_CD_E.bin", "bios_CD_J.bin"])
            (folder / "bios_CD_E.bin").write_bytes(b"x")
            self.assertIsNone(fw.missing_firmware("sega_cd", folder), "any region's file satisfies an unknown region")
            self.assertEqual(fw.missing_firmware("sega_cd", folder, region="U")[0], ["bios_CD_U.bin"], "a known region wants its own file")
            self.assertEqual(fw.missing_firmware("saturn", folder, region="Europe")[0], ["mpr-17933.bin"])
            self.assertEqual(fw.missing_firmware("saturn", folder, region="J")[0], ["sega_101.bin"])
            self.assertEqual(fw.missing_firmware("psx", folder, region="USA")[0], ["scph5501.bin"])
            self.assertEqual(fw.missing_firmware("pce_cd", folder)[0], ["syscard3.pce"], "optional system cards do not count as required")
            for system in ("nes", "snes", "genesis", "3do", "neo_geo_cd", "pc", "jaguar"):
                self.assertIsNone(fw.missing_firmware(system, folder), system)

    def test_neo_geo_accepts_the_bios_set_beside_the_game(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary);games = folder / "games";games.mkdir()
            self.assertEqual(fw.missing_firmware("neo_geo", folder / "bios", game_path=games / "mslug.zip")[0], ["fbneo/neogeo.zip"])
            self.assertIn("next to the game", fw.missing_firmware("neo_geo", folder / "bios", game_path=games / "mslug.zip")[1])
            (games / "neogeo.zip").write_bytes(b"x")
            self.assertIsNone(fw.missing_firmware("neo_geo", folder / "bios", game_path=games / "mslug.zip"))
            (games / "neogeo.zip").unlink();(folder / "bios" / "fbneo").mkdir(parents=True);(folder / "bios" / "fbneo" / "neogeo.zip").write_bytes(b"x")
            self.assertIsNone(fw.missing_firmware("neo_geo", folder / "bios", game_path=games / "mslug.zip"))


class DriveFirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.drive = Path(self.temporary.name) / "stick";self.drive.mkdir()

    def test_known_files_are_found_in_a_bios_folder_at_the_root_or_inside_redux_games(self):
        self.assertIsNone(fw.find_firmware_folder(self.drive))
        bios = self.drive / "BIOS";bios.mkdir();(bios / "readme.txt").write_text("not firmware")
        self.assertIsNone(fw.find_firmware_folder(self.drive), "a bios folder without known files is not one")
        (bios / "Bios_CD_U.bin").write_bytes(b"u");(bios / "neogeo.zip").write_bytes(b"n");(bios / "psx").mkdir();(bios / "psx" / "scph5501.bin").write_bytes(b"p")
        (bios / "empty.bin").write_bytes(b"")
        found = fw.known_files_in(bios)
        self.assertEqual([(p.name, e.system) for p, e in found], [("Bios_CD_U.bin", "sega_cd"), ("neogeo.zip", "neo_geo"), ("scph5501.bin", "psx")])
        self.assertEqual(fw.find_firmware_folder(self.drive), bios)
        self.assertEqual(fw.describe(found), "Sega CD, PlayStation and Neo Geo")
        other = Path(self.temporary.name) / "card";(other / "Redux Games" / "bios").mkdir(parents=True);(other / "Redux Games" / "bios" / "syscard3.pce").write_bytes(b"s")
        self.assertEqual(fw.find_firmware_folder(other), other / "Redux Games" / "bios")
        self.assertEqual(fw.describe(fw.known_files_in(other / "Redux Games" / "bios")), "PC Engine CD")

    def test_copy_puts_files_where_the_emulators_look_and_never_replaces_a_different_file(self):
        bios = self.drive / "bios";bios.mkdir()
        (bios / "bios_CD_U.bin").write_bytes(b"sega");(bios / "neogeo.zip").write_bytes(b"neo");(bios / "neocd").mkdir();(bios / "neocd" / "neocd_f.rom").write_bytes(b"cd")
        (bios / "scph5501.bin").write_bytes(b"new psx")
        destination = Path(self.temporary.name) / "redux-bios";destination.mkdir()
        (destination / "scph5501.bin").write_bytes(b"old psx");(destination / "bios_CD_U.bin").write_bytes(b"sega")
        result = fw.copy_firmware(bios, destination)
        self.assertEqual((sorted(result.copied), result.identical, result.kept, result.failed),
                         (["fbneo/neogeo.zip", "neocd/neocd_f.rom"], ["bios_CD_U.bin"], ["scph5501.bin"], []))
        self.assertEqual((destination / "fbneo" / "neogeo.zip").read_bytes(), b"neo")
        self.assertEqual((destination / "neocd" / "neocd_f.rom").read_bytes(), b"cd")
        self.assertEqual((destination / "scph5501.bin").read_bytes(), b"old psx", "a different file already there is kept")
        self.assertIn("2 copied", result.summary);self.assertIn("1 kept", result.summary)
        self.assertFalse(list(destination.glob("**/*.part")))
        again = fw.copy_firmware(bios, destination)
        self.assertEqual((again.copied, len(again.identical)), ([], 3))
        cancelled = threading.Event();cancelled.set()
        self.assertEqual(fw.copy_firmware(bios, Path(self.temporary.name) / "never", cancelled=cancelled).copied, [])


class FirmwarePromptTests(unittest.TestCase):
    def app(self):
        app = object.__new__(ConsoleApp)
        app.busy = False;app.embedded = None;app.playback = None;app.overlay = None;app.text_prompt = None
        app.preferences = {};app.calls = [];app.card_roots = set();app.card_pending = [];app.card_offers = [];app.firmware_offers = []
        app.card_drives = {"C:\\"};app.card_events = queue.Queue();app.cancelled = threading.Event()
        app._save_preferences = lambda: app.calls.append("save");app.schedule_draw = lambda: None;app.refresh = lambda: None
        app.status = SimpleNamespace(set=lambda text: app.calls.append(("status", text)))
        app.menu = lambda title, items, message="": setattr(app, "overlay", {"title": title, "items": items, "message": message})
        app.notice = lambda title, message: app.calls.append(("notice", title, message))
        app.task = lambda work, done, label, cancellable=False: done(work())
        return app

    def test_a_drive_with_bios_files_is_offered_once_and_copying_lands_in_the_firmware_folder(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);drive = root / "stick";(drive / "bios").mkdir(parents=True);(drive / "bios" / "bios_CD_U.bin").write_bytes(b"u")
            firmware = root / "redux" / "bios";app.firmware_directory = lambda: firmware
            import redux.game_cards as cards
            original = cards.can_prepare;cards.can_prepare = lambda d: True;self.addCleanup(setattr, cards, "can_prepare", original)
            (drive / "Redux Games").mkdir()  # a prepared card: no card question, only the BIOS one
            app.card_events.put((frozenset({"C:\\", str(drive)}), frozenset({str(drive / "Redux Games")})))
            started = [];app._sync_card = lambda card: started.append(card)
            ConsoleApp._poll_cards(app)
            self.assertEqual(started, [str(drive / "Redux Games")], "the card is read first")
            self.assertEqual(len(app.firmware_offers), 1);self.assertIsNone(app.overlay)
            ConsoleApp._poll_cards(app)
            self.assertEqual(app.overlay["title"], f"BIOS files found on {str(drive).rstrip(chr(92))}")
            self.assertEqual([label for label, _ in app.overlay["items"]], ["Copy 1 file", "Not now"])
            self.assertIn("Sega CD", app.overlay["message"])
            dict(app.overlay["items"])["Copy 1 file"]()
            self.assertEqual((firmware / "bios_CD_U.bin").read_bytes(), b"u")
            self.assertEqual(app.calls[-1][:2], ("notice", "BIOS files copied"))
            # Not now is remembered: the same drive arriving again asks nothing.
            app.overlay = None;app.firmware_offers = [];app.card_drives = {"C:\\"}
            app.card_events.put((frozenset({"C:\\", str(drive)}), frozenset({str(drive / "Redux Games")})))
            ConsoleApp._poll_cards(app);ConsoleApp._poll_cards(app)
            dict(app.overlay["items"])["Not now"]()
            self.assertTrue(ConsoleApp._firmware_drive_ignored(app, str(drive)))
            app.overlay = None;app.card_drives = {"C:\\"}
            app.card_events.put((frozenset({"C:\\", str(drive)}), frozenset({str(drive / "Redux Games")})))
            ConsoleApp._poll_cards(app);ConsoleApp._poll_cards(app)
            self.assertIsNone(app.overlay);self.assertEqual(app.firmware_offers, [])
            # The menu entry still finds it.
            import redux.game_cards as cards_module
            original_list = cards_module.list_drives;cards_module.list_drives = lambda: [drive];self.addCleanup(setattr, cards_module, "list_drives", original_list)
            ConsoleApp.copy_firmware_menu(app)
            self.assertEqual(app.overlay["title"], "Copy BIOS files from a drive");self.assertIn("Sega CD", app.overlay["items"][0][0])

    def test_the_launch_check_names_the_system_and_offers_the_drive_copy(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as temporary:
            app.firmware_directory = lambda: Path(temporary)
            app.library = SimpleNamespace(path_for=lambda game: Path(temporary) / "game.cue")
            app.choose_firmware = lambda: None;app.copy_firmware_menu = lambda: None
            game = {"id": 1, "system": "sega_cd", "region": "U", "bundle_hash": "a" * 64, "title": "Eternal Champions"}
            ConsoleApp._start_integrated_game(app, game, Path(temporary) / "core.dll")
            self.assertEqual(app.overlay["title"], "Sega CD firmware needed")
            self.assertEqual([label for label, _ in app.overlay["items"]], ["Choose firmware folder", "Copy BIOS files from a drive", "Back"])
            self.assertIn("bios_CD_U.bin", app.overlay["message"]);self.assertIn(temporary, app.overlay["message"])
            self.assertNotIn("task", [c if isinstance(c, str) else c[0] for c in app.calls])


class RefusalReasonTests(unittest.TestCase):
    def test_the_core_log_error_line_is_quoted_without_format_directives(self):
        from redux.libretro_host import EmbeddedSession
        host = object.__new__(EmbeddedSession);host.messages = []
        self.assertEqual(host._refusal_reason(), "The libretro core refused this game.")
        host.messages = ['Core log 0: Sega CD (NTSC-U) BIOS should be located at: "%s".\n', 'Core log 3: Unable to open CD BIOS: "%s".\n']
        self.assertEqual(host._refusal_reason(), "The libretro core refused this game: Unable to open CD BIOS.")
        host.messages = ["Core log 1: info only"]
        self.assertEqual(host._refusal_reason(), "The libretro core refused this game.")


if __name__ == "__main__":
    unittest.main()
