import binascii
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest

from redux.libretro_host import (
    EmbeddedError, EmbeddedSession, _AVInfo, _GameInfo, _Geometry, _SystemInfo,
    _Timing, _Variable, _convert_video,
)


class FakeCore:
    def __init__(self):
        self.callbacks = {}
        self.threads = set()
        self.counter = 0
        self.loaded = self.initialized = False
        self.memory = C.create_string_buffer(b"SRAM", 4)
        self.rejected_hardware = False
        self.bad_video = False

    def touch(self):
        self.threads.add(threading.get_ident())

    def retro_api_version(self):
        self.touch()
        return 1

    def retro_set_environment(self, cb):
        self.touch()
        self.callbacks["environment"] = cb

    def retro_init(self):
        self.touch()
        self.initialized = True
        cb = self.callbacks["environment"]
        pixel = C.c_int(1)
        assert cb(10, C.addressof(pixel))
        if self.rejected_hardware:
            assert not cb(14, None)
        variables = (_Variable * 2)(_Variable(b"test_option", b"Test; Enabled|Disabled"), _Variable())
        assert cb(16, C.addressof(variables))
        query = _Variable(b"test_option", None)
        assert cb(15, C.addressof(query))
        assert query.value == b"Enabled"
        self.paths = []
        for command in (9, 19, 30, 31):
            path = C.c_char_p()
            assert cb(command, C.addressof(path))
            self.paths.append(path.value.decode())

    def setter(self, name, callback):
        self.touch()
        assert self.initialized, "Renderer callbacks must be set after core initialization."
        self.callbacks[name] = callback

    def retro_set_video_refresh(self, cb): self.setter("video", cb)
    def retro_set_audio_sample(self, cb): self.setter("audio", cb)
    def retro_set_audio_sample_batch(self, cb): self.setter("audio_batch", cb)
    def retro_set_input_poll(self, cb): self.setter("poll", cb)
    def retro_set_input_state(self, cb): self.setter("input", cb)

    def retro_get_system_info(self, pointer):
        self.touch()
        info = C.cast(pointer, C.POINTER(_SystemInfo)).contents
        info.library_name, info.library_version, info.valid_extensions = b"Fake", b"1", b"nes"
        info.need_fullpath = False

    def retro_load_game(self, pointer):
        self.touch()
        info = C.cast(pointer, C.POINTER(_GameInfo)).contents
        self.loaded_bytes = C.string_at(info.data, info.size)
        self.loaded = True
        return True

    def retro_get_system_av_info(self, pointer):
        self.touch()
        info = C.cast(pointer, C.POINTER(_AVInfo)).contents
        info.geometry = _Geometry(2, 1, 2, 1, 2)
        info.timing = _Timing(60, 48000)

    def retro_set_controller_port_device(self, port, device): self.touch()
    def retro_get_memory_size(self, memory_id): self.touch(); return 4
    def retro_get_memory_data(self, memory_id): self.touch(); return C.addressof(self.memory)
    def retro_serialize_size(self): self.touch(); return 4

    def retro_serialize(self, pointer, size):
        self.touch()
        C.memmove(pointer, struct.pack("<I", self.counter), 4)
        return size == 4

    def retro_unserialize(self, pointer, size):
        self.touch()
        self.counter = struct.unpack("<I", C.string_at(pointer, 4))[0]
        return size == 4

    def retro_reset(self): self.touch(); self.counter = 0

    def retro_run(self):
        self.touch()
        self.counter += 1
        self.callbacks["poll"]()
        self.input_value = self.callbacks["input"](0, 1, 0, 256)
        pixels = C.create_string_buffer(bytes((3, 2, self.counter % 256, 0, 6, 5, 4, 0)))
        self.callbacks["video"](C.addressof(pixels), 2, 1, 7 if self.bad_video else 8)
        audio = (C.c_int16 * 4)(-123, 456, 789, -1000)
        assert self.callbacks["audio_batch"](audio, 2) == 2

    def retro_unload_game(self): self.touch(); self.loaded = False
    def retro_deinit(self): self.touch(); self.initialized = False


class FakeAudio:
    def __init__(self, rate):
        self.rate, self.closed, self.data = rate, False, []
    def write(self, data): self.data.append(data)
    def pause(self): pass
    def resume(self): pass
    def flush(self): pass
    def close(self): self.closed = True


class EmbeddedHostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.core = self.root / "core.dll"
        self.game = self.root / "test.nes"
        self.core.write_bytes(b"fake library")
        self.game.write_bytes(b"original test game")
        self.fake = FakeCore()
        self.session = None

    def start(self, **kwargs):
        self.session = EmbeddedSession(self.core, self.game, self.root / "system", self.root / "saves",
                                       _loader=lambda path: self.fake, **kwargs)
        self.session.start()
        self.addCleanup(self.session.stop)
        self.session.pause()
        return self.session

    def test_worker_owns_abi_callbacks_inputs_video_audio_and_shutdown(self):
        sinks = []
        def factory(rate):
            sink = FakeAudio(rate); sinks.append(sink); return sink
        s = self.start(audio=True, _audio_factory=factory)
        s.set_input(0, (1 << 15) | (1 << 8))
        frame = s.step(2)
        self.assertEqual((frame.width, frame.height), (2, 1))
        self.assertEqual(frame.rgb[:3], bytes((self.fake.counter % 256, 2, 3)))
        self.assertEqual(self.fake.input_value, C.c_int16(0x8100).value)
        self.assertEqual(self.fake.loaded_bytes, self.game.read_bytes())
        self.assertTrue(all(Path(path).is_absolute() for path in self.fake.paths))
        self.assertEqual(sinks[0].data[-1], struct.pack("<hhhh", -123, 456, 789, -1000))
        self.assertEqual(len(self.fake.threads), 1)
        self.assertNotIn(threading.get_ident(), self.fake.threads)
        s.stop()
        self.assertFalse(self.fake.loaded or self.fake.initialized or s.running)
        self.assertTrue(sinks[0].closed)
        self.assertEqual(s.error, None)

    def test_exact_state_roundtrip_and_reject_foreign_or_malformed_state(self):
        s = self.start(audio=False)
        state = self.root / "slot.state"
        s.step(7); count = self.fake.counter
        s.save_state(state)
        expected = s.step(12).rgb
        s.load_state(state)
        self.assertEqual(self.fake.counter, count)
        self.assertEqual(s.step(12).rgb, expected)
        data = bytearray(state.read_bytes())
        position = data.find(hashlib.sha256(self.game.read_bytes()).hexdigest().encode())
        data[position] = ord("f") if data[position] != ord("f") else ord("e")
        foreign = self.root / "foreign.state"; foreign.write_bytes(data)
        with self.assertRaisesRegex(EmbeddedError, "different core build or game"):
            s.load_state(foreign)
        damaged = bytearray(state.read_bytes()); damaged[-1] ^= 1
        foreign.write_bytes(damaged)
        with self.assertRaisesRegex(EmbeddedError, "integrity check"):
            s.load_state(foreign)
        foreign.write_bytes(b"RDXSTATE1" + struct.pack("<I", 50000))
        with self.assertRaisesRegex(EmbeddedError, "header is malformed"):
            s.load_state(foreign)
        with self.assertRaisesRegex(EmbeddedError, "overwrite the game"):
            s.save_state(self.game)

    def test_screenshot_is_real_rgb_png_with_valid_crcs(self):
        s = self.start(audio=False)
        frame = s.step(1)
        path = s.save_screenshot(self.root / "shot.png")
        data = path.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        offset, kinds = 8, []
        import zlib
        while offset < len(data):
            length = struct.unpack_from(">I", data, offset)[0]
            kind = data[offset + 4:offset + 8]
            payload = data[offset + 8:offset + 8 + length]
            crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
            self.assertEqual(crc, binascii.crc32(kind + payload))
            if kind == b"IDAT": self.assertEqual(zlib.decompress(payload), b"\0" + frame.rgb)
            kinds.append(kind); offset += 12 + length
        self.assertEqual(kinds, [b"IHDR", b"IDAT", b"IEND"])

    def test_sram_restored_and_saved_while_mismatched_existing_file_is_preserved(self):
        s = self.start(audio=False)
        path = s._sram_path
        C.memmove(self.fake.memory, b"SAVE", 4)
        s.stop()
        self.assertEqual(path.read_bytes(), b"SAVE")
        self.fake = FakeCore()
        s = self.start(audio=False)
        self.assertEqual(self.fake.memory.raw, b"SAVE")
        s.stop()
        path.write_bytes(b"MISMATCH")
        self.fake = FakeCore()
        s = self.start(audio=False)
        self.assertTrue(any("different size" in msg for msg in s.messages))
        s.stop()
        self.assertEqual(path.read_bytes(), b"MISMATCH")

    def test_paused_battery_flush_persists_memory_without_stopping_or_changing_paths(self):
        session=self.start(audio=False)
        game_path,save_directory=session.game_path,session.save_directory
        C.memmove(self.fake.memory,b"LIVE",4)
        self.assertTrue(session.flush_saves())
        self.assertEqual(session._sram_path.read_bytes(),b"LIVE")
        self.assertTrue(session.running)
        self.assertTrue(session.paused)
        self.assertEqual((session.game_path,session.save_directory),(game_path,save_directory))

    def test_duplicate_core_ownership_and_bad_input(self):
        s = self.start(audio=False)
        other = EmbeddedSession(self.core, self.game, self.root / "s2", self.root / "s3", _loader=lambda p: FakeCore())
        with self.assertRaisesRegex(EmbeddedError, "already running"):
            other.start()
        for port, mask in ((8, 0), (0, -1), (0, 65536)):
            with self.assertRaises(ValueError): s.set_input(port, mask)
        s.resume()
        with self.assertRaisesRegex(EmbeddedError, "Pause the game"):
            s.step(1)

    def test_tap_between_frames_is_delivered_once_and_pause_discards_old_edges(self):
        s = self.start(audio=False)
        s.set_input(0, 1 << 3)
        s.set_input(0, 0)
        s.step(1)
        self.assertEqual(self.fake.input_value, 1 << 3)
        s.step(1)
        self.assertEqual(self.fake.input_value, 0)
        s.set_input(0, 1 << 8)
        s.set_input(0, 0)
        s.pause()
        s.step(1)
        self.assertEqual(self.fake.input_value, 0)

    def test_hardware_request_refused_explicitly(self):
        self.fake.rejected_hardware = True
        with self.assertRaisesRegex(EmbeddedError, "GPU context requests are unsupported"):
            self.start(audio=False)
        self.assertFalse(self.fake.loaded or self.fake.initialized)

    def test_bad_video_is_caught_without_escaping_callback(self):
        s = self.start(audio=False)
        self.fake.bad_video = True
        with self.assertRaisesRegex(EmbeddedError, "row pitch"):
            s.step(1)

    def test_1555_and_565_padded_pixel_conversion(self):
        for fmt, red, green, blue in ((0, 0x7C00, 0x03E0, 0x001F), (2, 0xF800, 0x07E0, 0x001F)):
            data = C.create_string_buffer(struct.pack("<HHHH", red, green, blue, 0))
            rgb = _convert_video(C.addressof(data), 3, 1, 8, fmt)
            self.assertEqual(rgb, b"\xff\0\0\0\xff\0\0\0\xff")
        with self.assertRaisesRegex(EmbeddedError, "dimensions"):
            _convert_video(1, 0, 1, 4, 1)
        with self.assertRaisesRegex(EmbeddedError, "pitch"):
            _convert_video(1, 2, 1, 4, 1)


if __name__ == "__main__":
    unittest.main()
