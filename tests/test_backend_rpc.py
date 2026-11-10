"""Protocol and Lua-interpreter tests; these never launch an emulator or GUI."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
import zlib

from redux.backend_rpc import BridgeError, PcsxReduxBridge, _png_frame


class MailboxWorker:
    """A deliberately small protocol peer, not an emulation/state fixture."""
    def __init__(self, bridge, handler=None):
        self.bridge = bridge
        self.handler = handler or self.default_handler
        self.commands = []
        self.errors = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run)

    def __enter__(self):
        (self.bridge.session_directory / "ready").write_bytes(
            f"{self.bridge.session_token} pcsx_redux 1\n".encode("ascii"))
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        self.thread.join(2)
        if self.errors:
            raise self.errors[0]

    def reply(self, token, operation, *fields):
        text = " ".join([self.bridge.session_token, token, operation, *fields]) + "\n"
        path = self.bridge.session_directory / f"{token}.reply"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(text, encoding="ascii")
        os.replace(temporary, path)

    def default_handler(self, token, operation, slot):
        root = self.bridge.session_directory
        if operation == "save":
            data = b"test-protocol-state-" + str(slot).encode()
            (root / f"{token}.state").write_bytes(data)
            self.reply(token, operation, "ok", str(len(data)))
        elif operation == "load":
            self.loaded = (root / f"{token}.input").read_bytes()
            self.reply(token, operation, "ok")
        elif operation == "capture":
            (root / f"{token}.raw").write_bytes(b"\xff\0\0\0\xff\0")
            self.reply(token, operation, "ok", "2", "1", "1", "6")
        else:
            self.reply(token, operation, "ok")

    def run(self):
        while not self.stop_event.wait(.002):
            path = self.bridge.session_directory / "command"
            if not path.exists():
                continue
            try:
                session, token, operation, slot = path.read_text(encoding="ascii").split()
                path.unlink()
                assert session == self.bridge.session_token
                self.commands.append((operation, int(slot)))
                self.handler(token, operation, int(slot))
            except BaseException as error:
                self.errors.append(error)
                return


def decode_png(png):
    """Return header and unfiltered pixels after checking every PNG CRC."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("not PNG")
    cursor, data, header = 8, bytearray(), None
    while cursor < len(png):
        length = struct.unpack_from(">I", png, cursor)[0]
        kind = png[cursor + 4:cursor + 8]
        body = png[cursor + 8:cursor + 8 + length]
        crc = struct.unpack_from(">I", png, cursor + 8 + length)[0]
        assert zlib.crc32(kind + body) & 0xffffffff == crc
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            data.extend(body)
        cursor += 12 + length
    return header, zlib.decompress(data)


class BackendRpcTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bridge = PcsxReduxBridge(self.root, 7, timeout=.3)

    def tearDown(self):
        self.temporary.cleanup()

    def test_unique_launch_nonce_and_no_stale_ready(self):
        other = PcsxReduxBridge(self.root, 7)
        self.assertNotEqual(other.session_token, self.bridge.session_token)
        self.assertFalse(self.bridge.ready)
        (self.bridge.session_directory / "ready").write_text(f"{other.session_token} pcsx_redux 1\n")
        self.assertFalse(self.bridge.wait_ready(.03))
        with self.assertRaisesRegex(BridgeError, "not ready"):
            self.bridge.pause()
        self.assertEqual(self.bridge.launch_arguments, ("-dofile", str(self.bridge.script_path)))

    def test_states_survive_new_launch_and_hash_guard_restore(self):
        with MailboxWorker(self.bridge) as worker:
            saved = self.bridge.save(3)
            self.assertTrue(saved.exists)
            self.assertEqual(saved.sha256, hashlib.sha256(saved.path.read_bytes()).hexdigest())
            self.assertEqual(self.bridge.load(3), saved)
            self.assertEqual(worker.loaded, b"test-protocol-state-3")
            self.assertEqual([s.exists for s in self.bridge.slots()], [False, False, False, True] + [False] * 6)
        other = PcsxReduxBridge(self.root, 7)
        self.assertEqual(other.slots()[3], saved)
        saved.path.write_bytes(b"X" * saved.size)
        with MailboxWorker(other) as worker:
            with self.assertRaisesRegex(BridgeError, "changed"):
                other.load(3)
            self.assertEqual(worker.commands, [])

    def test_bundle_key_separates_reused_database_ids(self):
        first = PcsxReduxBridge(self.root, 5, game_key="a" * 64)
        with MailboxWorker(first):
            first.save()
        second = PcsxReduxBridge(self.root, 5, game_key="b" * 64)
        self.assertFalse(any(slot.exists for slot in second.slots()))
        same_game = PcsxReduxBridge(self.root, 5, game_key="a" * 64)
        self.assertTrue(same_game.slots()[0].exists)

    def test_failed_save_does_not_replace_previous_slot(self):
        with MailboxWorker(self.bridge):
            first = self.bridge.save(0)
        def fail(token, operation, slot):
            worker.reply(token, operation, "error", "backend_api_failed")
        with MailboxWorker(self.bridge, fail) as worker:
            with self.assertRaisesRegex(BridgeError, "backend_api_failed"):
                self.bridge.save()
        self.assertEqual(self.bridge.slots()[0], first)
        self.assertEqual(first.path.read_bytes(), b"test-protocol-state-0")

    def test_capture_is_real_raw_frame_conversion_and_durable_png(self):
        with MailboxWorker(self.bridge):
            path = self.bridge.capture()
            self.bridge.pause()
            self.bridge.resume()
        self.assertTrue(path.is_relative_to(self.bridge.screenshot_directory))
        header, pixels = decode_png(path.read_bytes())
        self.assertEqual(header, (2, 1, 8, 2, 0, 0, 0))
        self.assertEqual(pixels, b"\0\xff\0\0\0\xff\0")

    def test_rgb555_frame_uses_psx_channel_order(self):
        raw = struct.pack("<HHHH", 0x001f, 0x03e0, 0x7c00, 0xffff)
        _, pixels = decode_png(_png_frame(raw, 4, 1, 0))
        self.assertEqual(pixels, b"\0\xff\0\0\0\xff\0\0\0\xff\xff\xff\xff")
        for arguments in ((b"", 2, 1, 1), (b"", 99999, 1, 0), (b"", 1, 1, 2)):
            with self.assertRaises(BridgeError):
                _png_frame(*arguments)

    def test_wrong_nonce_reply_is_rejected_and_faults_bridge(self):
        def wrong(token, operation, slot):
            worker.reply("0" * 32, operation, "ok")
            wrong_path = self.bridge.session_directory / ("0" * 32 + ".reply")
            wrong_path.rename(self.bridge.session_directory / f"{token}.reply")
        with MailboxWorker(self.bridge, wrong) as worker:
            with self.assertRaisesRegex(BridgeError, "does not belong"):
                self.bridge.pause()
        self.assertFalse(self.bridge.ready)

    def test_timeout_never_becomes_success_and_disables_later_commands(self):
        self.bridge.timeout = .04
        with MailboxWorker(self.bridge, lambda *_: None) as worker:
            with self.assertRaisesRegex(BridgeError, "in time"):
                self.bridge.save()
            with self.assertRaisesRegex(BridgeError, "relaunch"):
                self.bridge.resume()
            self.assertEqual(worker.commands, [("save", 0)])
        self.assertFalse(any(s.exists for s in self.bridge.slots()))

    def test_slots_and_parameters_are_bounded(self):
        for value in (-1, 10, True, "1", 1.5):
            with self.assertRaises(ValueError):
                self.bridge.save(value)
        for value in (0, -1, True, "7"):
            with self.assertRaises(ValueError):
                PcsxReduxBridge(self.root, value)
        for value in (0, -1, float("inf"), float("nan"), True, 121):
            with self.assertRaises(ValueError):
                PcsxReduxBridge(self.root, 1, timeout=value)
        with self.assertRaisesRegex(BridgeError, "empty"):
            self.bridge.load()

    def test_manifest_cannot_supply_arbitrary_path(self):
        with MailboxWorker(self.bridge):
            self.bridge.save()
        path = self.bridge.state_directory / "slot-0.json"
        manifest = json.loads(path.read_text())
        manifest["sha256"] = "../../private"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(BridgeError, "incomplete"):
            self.bridge.load()
        self.assertFalse(self.bridge.slot(1).exists)
        with self.assertRaisesRegex(BridgeError, "incomplete"):
            self.bridge.slot(0)
        # An explicit save can replace the damaged manifest without requiring
        # that its old contents be trusted first.
        with MailboxWorker(self.bridge):
            self.assertTrue(self.bridge.save(0).exists)

    def test_backend_frame_mismatch_creates_no_screenshot(self):
        def bad(token, operation, slot):
            (self.bridge.session_directory / f"{token}.raw").write_bytes(b"\0")
            worker.reply(token, operation, "ok", "200000", "1", "1", "1")
        with MailboxWorker(self.bridge, bad) as worker:
            with self.assertRaises(BridgeError):
                self.bridge.capture()
        self.assertEqual(list(self.bridge.screenshot_directory.iterdir()), [])

    def test_close_is_idempotent_and_keeps_media_and_states(self):
        media = self.root / "source.iso"
        media.write_bytes(b"source bytes")
        with MailboxWorker(self.bridge):
            state = self.bridge.save()
            self.bridge.close()
            self.bridge.close()
            with self.assertRaisesRegex(BridgeError, "closed"):
                self.bridge.capture()
        self.assertEqual(media.read_bytes(), b"source bytes")
        self.assertTrue(state.path.is_file())


class LuaRuntime:
    """Use the optional installed LuaJIT DLL as an interpreter, not emulator."""
    def __init__(self, dll):
        self.lib = ctypes.CDLL(str(dll))
        self.lib.luaL_newstate.restype = ctypes.c_void_p
        self.lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
        self.lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
        self.lib.luaL_loadbuffer.restype = ctypes.c_int
        self.lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.lib.lua_pcall.restype = ctypes.c_int
        self.lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
        self.lib.lua_tolstring.restype = ctypes.c_void_p
        self.lib.lua_close.argtypes = [ctypes.c_void_p]
        self.state = self.lib.luaL_newstate()
        if not self.state:
            raise RuntimeError("Lua state allocation failed")
        self.lib.luaL_openlibs(self.state)

    def run(self, code):
        encoded = code.encode("utf-8")
        status = self.lib.luaL_loadbuffer(self.state, encoded, len(encoded), b"test_adapter")
        if status == 0:
            status = self.lib.lua_pcall(self.state, 0, 0, 0)
        if status:
            length = ctypes.c_size_t()
            pointer = self.lib.lua_tolstring(self.state, -1, ctypes.byref(length))
            raise AssertionError(ctypes.string_at(pointer, length.value).decode("utf-8", "replace"))

    def close(self):
        self.lib.lua_close(self.state)


_LUA_DLL = Path(__file__).resolve().parents[1] / "tools/emulators/PCSXRedux/lua51.dll"


@unittest.skipUnless(os.name == "nt" and _LUA_DLL.is_file(), "optional local LuaJIT interpreter is unavailable")
class LuaAdapterTests(unittest.TestCase):
    def test_generated_script_handles_actual_mailbox_in_luajit(self):
        with tempfile.TemporaryDirectory(prefix="redux-quoted-é-") as folder:
            bridge = PcsxReduxBridge(Path(folder) / "apostrophe'", 12, timeout=.5)
            lua = LuaRuntime(_LUA_DLL)
            self.addCleanup(lua.close)
            lua.run(r'''
                local callbacks = {}
                local timer = {}
                function timer:start(_, _, callback) _tick = callback end
                function timer:stop() _timer_stopped = true end
                function timer:close() _timer_closed = true end
                luv = {new_timer = function() return timer end, now = function() return 1 end}
                PCSX = {
                    nextTick = function(f) f() end,
                    pauseEmulator = function() _paused = true end,
                    resumeEmulator = function() _paused = false end,
                    createSaveState = function() return 'lua-interpreter-test-state' end,
                    loadSaveState = function(file)
                        assert(file.bytes == 'lua-interpreter-test-state')
                        callbacks['ExecutionFlow::SaveStateLoaded']()
                    end,
                    GPU = {takeScreenShot = function()
                        return {data = string.char(31, 0), width = 1, height = 1, bpp = 0}
                    end},
                    Events = {createEventListener = function(name, callback)
                        callbacks[name] = callback
                        return {remove = function() callbacks[name] = nil end}
                    end}
                }
                Support = {File = {buffer = function()
                    local file = {close = function() end}
                    function file:write(bytes) self.bytes = bytes return #bytes end
                    return file
                end}}
            ''')
            lua.run(bridge.script_path.read_text(encoding="utf-8"))
            self.assertTrue(bridge.ready)

            def operation(function):
                results, errors = [], []
                def run():
                    try:
                        results.append(function())
                    except BaseException as error:
                        errors.append(error)
                thread = threading.Thread(target=run)
                thread.start()
                while thread.is_alive():
                    lua.run("_tick()")
                    time.sleep(.002)
                thread.join()
                if errors:
                    raise errors[0]
                return results[0]

            operation(bridge.pause)
            lua.run("assert(_paused == true)")
            state = operation(lambda: bridge.save(5))
            self.assertEqual(state.path.read_bytes(), b"lua-interpreter-test-state")
            self.assertEqual(operation(lambda: bridge.load(5)), state)
            path = operation(bridge.capture)
            self.assertEqual(decode_png(path.read_bytes())[1], b"\0\xff\0\0")
            operation(bridge.resume)
            lua.run("assert(_paused == false)")
            bridge.close()
            lua.run("_tick(); assert(_timer_stopped and _timer_closed)")


if __name__ == "__main__":
    unittest.main()
