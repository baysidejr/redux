import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from redux.redux_controller import (MAPPING, ReceiverClient, describe, profile_for_title,
                                    receiver_executable, start_receiver)
from redux.sdl_gamepads import validate_mapping


class _Receiver(BaseHTTPRequestHandler):
    profile = None
    disconnects = 0

    def _send(self, value, status=200):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/control/state":
            self._send({"version": "0.3.0", "pin": "123456", "outputReady": True, "phonePaired": True,
                        "phoneName": "Pixel 9", "packets": 42, "profile": _Receiver.profile,
                        "pairedPhones": 1, "addresses": ["192.168.4.23"]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")) or b"{}") or b"{}")
        if self.path == "/control/profile":
            _Receiver.profile = body.get("profile")
            self._send({"ok": True, "profile": _Receiver.profile})
        elif self.path == "/control/disconnect":
            _Receiver.disconnects += 1
            self._send({"ok": True})
        else:
            self._send({"error": "not found"}, 404)

    def log_message(self, *_):
        pass


class ProfileTests(unittest.TestCase):
    def test_jaguar_titles_map_to_shared_profile_ids(self):
        cases = {"Alien vs. Predator (1994)": "jaguar-avp", "Aliens vs Predator": "jaguar-avp",
                 "Hover Strike": "jaguar-hover-strike", "Hover Strike: Unconquered Lands": "jaguar-hover-strike-unconquered",
                 "DOOM": "jaguar-doom", "Dragon: The Bruce Lee Story": "jaguar-dragon",
                 "Double Dragon V - The Shadow Falls": "jaguar", "Trevor McFur in the Crescent Galaxy": "jaguar-trevor-mcfur",
                 "JagMIND: Bomb Squad": "jaguar-jagmind", "Wolfenstein 3D": "jaguar-wolfenstein",
                 "Tempest 2000": "jaguar", "": "jaguar"}
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(profile_for_title(title), expected)
        self.assertEqual(profile_for_title("DOOM", "jaguar_cd"), "jaguar-doom")
        self.assertIsNone(profile_for_title("DOOM", "snes"))

    def test_receiver_mapping_is_built_into_the_sdl_backend(self):
        from test_sdl_gamepads import FakeSDL
        from redux.sdl_gamepads import SDLGamepadAPI
        native = FakeSDL()
        with patch("redux.sdl_gamepads.native_directory", return_value=Path("Z:/missing-test-assets")):
            api = SDLGamepadAPI(native=native)
        self.addCleanup(api.close)
        self.assertIn(MAPPING.encode("utf-8"), native.maps)

    def test_mapping_is_a_valid_windows_sdl_line_for_the_receiver_device(self):
        self.assertEqual(validate_mapping(MAPPING), MAPPING)
        self.assertTrue(MAPPING.startswith("03000000091200004452000000000000,Redux Jaguar Controller,"))
        self.assertIn("guide:b10", MAPPING)
        self.assertTrue(MAPPING.endswith("platform:Windows,"))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _Receiver)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _Receiver.profile = None
        _Receiver.disconnects = 0

    def test_state_profile_and_disconnect_round_trip(self):
        client = ReceiverClient(port=self.server.server_address[1], timeout=2)
        state = client.state()
        self.assertEqual(state["phoneName"], "Pixel 9")
        self.assertEqual(client.set_profile("jaguar-avp")["profile"], "jaguar-avp")
        self.assertEqual(client.state()["profile"], "jaguar-avp")
        self.assertIsNone(client.set_profile(None)["profile"])
        client.disconnect()
        self.assertEqual(_Receiver.disconnects, 1)
        with self.assertRaises(ValueError):
            client.set_profile("not valid!")
        text = describe(state)
        self.assertIn("Pixel 9 connected", text)
        self.assertIn("PIN 123456", text)

    def test_missing_receiver_yields_none_without_raising(self):
        probe = HTTPServer(("127.0.0.1", 0), _Receiver)
        port = probe.server_address[1]
        probe.server_close()
        client = ReceiverClient(port=port, timeout=0.5)
        self.assertIsNone(client.state())
        with self.assertRaises(OSError):
            client.set_profile("jaguar")
        self.assertIn("not running", describe(None, Path("C:/x/ReduxReceiver.exe")))


class InstallTests(unittest.TestCase):
    def test_receiver_executable_prefers_the_chosen_path_then_the_tools_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertIsNone(receiver_executable(root, {}))
            tools = root / "tools" / "ReduxReceiver"
            tools.mkdir(parents=True)
            (tools / "ReduxReceiver.exe").write_bytes(b"")
            self.assertEqual(receiver_executable(root, {}), tools / "ReduxReceiver.exe")
            chosen = root / "elsewhere" / "ReduxReceiver.exe"
            chosen.parent.mkdir()
            chosen.write_bytes(b"")
            self.assertEqual(receiver_executable(root, {"redux_controller": {"receiver": str(chosen)}}), chosen)
            other = root / "elsewhere" / "Other.exe"
            other.write_bytes(b"")
            self.assertEqual(receiver_executable(root, {"redux_controller": {"receiver": str(other)}}), tools / "ReduxReceiver.exe")
            with self.assertRaises(OSError):
                start_receiver(other)
            if os.name == "nt":
                with patch("redux.redux_controller.os.startfile") as startfile:
                    start_receiver(chosen)
                    startfile.assert_called_once_with(str(chosen), arguments="", cwd=str(chosen.parent))
                with patch("redux.redux_controller.os.startfile") as startfile:
                    start_receiver(chosen, setup=True)
                    startfile.assert_called_once_with(str(chosen), arguments="--start-with-windows", cwd=str(chosen.parent))


if __name__ == "__main__":
    unittest.main()
