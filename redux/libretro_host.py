"""An original software-video libretro frontend, using the public C ABI.

Native core operations run on one worker thread. Callbacks and ROM buffers remain
alive until retro_unload_game/retro_deinit finish. GPU callbacks are not supported.
"""
from __future__ import annotations

from array import array
import binascii
import ctypes as C
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import struct
import tempfile
import threading
import time
import zlib

from .audio_output import WaveOutAudio


class EmbeddedError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoFrame:
    width: int
    height: int
    rgb: bytes
    sequence: int


class _SystemInfo(C.Structure):
    _fields_ = [("library_name", C.c_char_p), ("library_version", C.c_char_p),
                ("valid_extensions", C.c_char_p), ("need_fullpath", C.c_bool),
                ("block_extract", C.c_bool)]


class _GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p),
                ("size", C.c_size_t), ("meta", C.c_char_p)]


class _Geometry(C.Structure):
    _fields_ = [("base_width", C.c_uint), ("base_height", C.c_uint),
                ("max_width", C.c_uint), ("max_height", C.c_uint), ("aspect_ratio", C.c_float)]


class _Timing(C.Structure):
    _fields_ = [("fps", C.c_double), ("sample_rate", C.c_double)]


class _AVInfo(C.Structure):
    _fields_ = [("geometry", _Geometry), ("timing", _Timing)]


class _Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


_LogFunction=C.CFUNCTYPE(None,C.c_int,C.c_char_p)
class _LogCallback(C.Structure):
    _fields_=[("log",_LogFunction)]


class _Message(C.Structure):
    _fields_ = [("msg", C.c_char_p), ("frames", C.c_uint)]


_Environment = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
_Video = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
_Audio = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
_AudioBatch = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
_InputPoll = C.CFUNCTYPE(None)
_InputState = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)
_owners: set[str] = set()
_owner_lock = threading.Lock()
_MAX_STATE = 512 * 1024 * 1024
_STATE_MAGIC = b"RDXSTATE1"
_RGB_TABLES: dict[int, tuple[bytes, ...]] = {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _png(frame: VideoFrame) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))
    stride = frame.width * 3
    pixels = b"".join(b"\0" + frame.rgb[i:i + stride] for i in range(0, len(frame.rgb), stride))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", frame.width, frame.height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def _convert_video(data: int, width: int, height: int, pitch: int, pixel_format: int) -> bytes:
    if not (0 < width <= 4096 and 0 < height <= 4096) or width * height > 16_777_216:
        raise EmbeddedError("The core supplied invalid software-video dimensions.")
    pixel_bytes = 4 if pixel_format == 1 else 2
    if pitch < width * pixel_bytes or pitch > 64 * 1024 * 1024:
        raise EmbeddedError("The core supplied an invalid video row pitch.")
    if pixel_format == 1:
        output = bytearray(width * height * 3)
        for row in range(height):
            source = C.string_at(data + row * pitch, width * 4)
            offset, end = row * width * 3, (row + 1) * width * 3
            output[offset:end:3], output[offset + 1:end:3], output[offset + 2:end:3] = (
                source[2::4], source[1::4], source[0::4])
        return bytes(output)
    if pixel_format not in (0, 2):
        raise EmbeddedError("The core requested an unsupported software pixel format.")
    if pixel_format not in _RGB_TABLES:
        if pixel_format == 2:
            _RGB_TABLES[2] = tuple(bytes((((v >> 11) & 31) * 255 // 31,
                                          ((v >> 5) & 63) * 255 // 63,
                                          (v & 31) * 255 // 31)) for v in range(65536))
        else:
            _RGB_TABLES[0] = tuple(bytes((((v >> 10) & 31) * 255 // 31,
                                          ((v >> 5) & 31) * 255 // 31,
                                          (v & 31) * 255 // 31)) for v in range(65536))
    table = _RGB_TABLES[pixel_format]
    rows = []
    for row in range(height):
        pixels = array("H", C.string_at(data + row * pitch, width * 2))
        if os.sys.byteorder != "little":
            pixels.byteswap()
        rows.append(b"".join(table[v] for v in pixels))
    return b"".join(rows)


class EmbeddedSession:
    """Run one content item with one software-rendered core.

    Joypad masks use libretro IDs: B=0,Y=1,Select=2,Start=3,Up=4,
    Down=5,Left=6,Right=7,A=8,X=9,L=10,R=11,L2=12,R2=13,L3=14,R3=15.
    Paths and returned state files belong to the independent frontend.
    """

    def __init__(self, core_path, game_path, system_directory, save_directory,
                 *, audio=True, core_options=None, _loader=None, _audio_factory=None):
        self.core_path = Path(core_path).resolve()
        self.game_path = Path(game_path).resolve()
        self.system_directory = Path(system_directory).resolve()
        self.save_directory = Path(save_directory).resolve()
        self.audio_enabled = bool(audio)
        self.error: str | None = None
        self.audio_error: str | None = None
        self.core_info: dict = {}
        self.fps = 60.0
        self.sample_rate = 48000.0
        self.display_aspect = 4 / 3
        self.messages: list[str] = []
        self._loader = _loader or C.CDLL
        self._audio_factory = _audio_factory or WaveOutAudio
        self._thread = None
        self._ready, self._stop, self._wake = threading.Event(), threading.Event(), threading.Event()
        self._commands = queue.Queue()
        self._lock = threading.RLock()
        self._paused = False
        self._frame = None
        self._sequence = 0
        self._inputs = [0] * 8
        self._pending_inputs = [0] * 8
        self._frame_inputs = [0] * 8
        self._analog_inputs = [(0, 0, 0, 0)] * 8
        self._frame_analog_inputs = [(0, 0, 0, 0)] * 8
        self._pixel_format = 0
        self._rotation = 0
        self._variables: dict[bytes, bytes] = {}
        if core_options is not None and (not isinstance(core_options,dict) or len(core_options)>256):raise ValueError("Core options must be a bounded dictionary")
        self._requested_variables={}
        for key,value in (core_options or {}).items():
            if not isinstance(key,str) or not isinstance(value,str) or not 1<=len(key)<=128 or len(value)>512 or "\0" in key+value:
                raise ValueError("Core option keys and values must be bounded text")
            self._requested_variables[key.encode("utf-8")]=value.encode("utf-8")
        self.declared_options={}
        self._log_callback=_LogFunction(self._core_log)
        self._strings = {9: os.fsencode(self.system_directory), 19: os.fsencode(self.core_path),
                         30: os.fsencode(self.system_directory), 31: os.fsencode(self.save_directory),
                         38: b"Redux"}
        self._callback_error = None
        self._audio_sink = None
        self._sample_buffer = bytearray()
        self._core = None
        self._sram_mismatch = False
        self._callbacks = (_Environment(self._environment), _Video(self._video),
                           _Audio(self._audio_sample), _AudioBatch(self._audio_batch),
                           _InputPoll(lambda: None), _InputState(self._input_state))

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive() and self._ready.is_set() and not self.error

    @property
    def paused(self):
        return self._paused

    @property
    def frame_count(self):
        return self._sequence

    def latest_frame(self) -> VideoFrame | None:
        with self._lock:
            return self._frame

    def set_input(self, port: int, button_mask: int):
        if not 0 <= port < 8 or not 0 <= button_mask <= 0xFFFF:
            raise ValueError("Joypad input requires port 0..7 and a 16-bit button mask.")
        with self._lock:
            # A quick GUI/controller tap can begin and end between core frames.
            # Retain its rising edge until one frame consumes a stable snapshot.
            self._pending_inputs[port] |= button_mask & ~self._inputs[port]
            self._inputs[port] = button_mask

    def set_analog(self, port: int, lx: int, ly: int, rx: int = 0, ry: int = 0):
        """Set signed 16-bit stick axes; libretro Y is positive down.

        Axes are snapshotted together with joypad buttons before each frame.
        Unlike button edges, transient axis positions are not latched. Callers
        using a positive-up input backend must reverse Y before this boundary.
        """
        axes = (lx, ly, rx, ry)
        if (isinstance(port, bool) or not isinstance(port, int) or not 0 <= port < 8 or
                any(isinstance(value, bool) or not isinstance(value, int) or
                    not -32768 <= value <= 32767 for value in axes)):
            raise ValueError("Analog input requires port 0..7 and signed 16-bit integer axes.")
        with self._lock:
            self._analog_inputs[port] = axes

    def start(self, timeout=20.0):
        if self._thread is not None:
            raise EmbeddedError("An embedded session can only be started once.")
        if not self.core_path.is_file() or not self.game_path.is_file():
            raise EmbeddedError("The selected core and game must both exist as files.")
        self.system_directory.mkdir(parents=True, exist_ok=True)
        self.save_directory.mkdir(parents=True, exist_ok=True)
        self._owner_key = os.path.normcase(str(self.core_path))
        with _owner_lock:
            if self._owner_key in _owners:
                raise EmbeddedError("This core is already running in another embedded session.")
            _owners.add(self._owner_key)
        self._thread = threading.Thread(target=self._worker, name="Redux libretro", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self._stop.set()
            raise EmbeddedError("The core did not finish starting within the allowed time.")
        if self.error:
            self._thread.join(timeout=2)
            raise EmbeddedError(self.error)
        return self

    def stop(self, timeout=15.0):
        if not self._thread:
            return
        self._stop.set()
        self._wake.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise EmbeddedError("The native core did not stop; its buffers remain retained.")

    def _request(self, operation, argument=None, timeout=15.0):
        if not self.running:
            raise EmbeddedError(self.error or "No embedded game is running.")
        event, result = threading.Event(), []
        self._commands.put((operation, argument, event, result))
        self._wake.set()
        if not event.wait(timeout):
            raise EmbeddedError(f"The core did not complete {operation} in time.")
        if isinstance(result[0], BaseException):
            raise result[0]
        return result[0]

    def pause(self):
        return self._request("pause", True)

    def resume(self):
        return self._request("pause", False)

    def reset(self):
        return self._request("reset")

    def step(self, frames=1):
        """Run exact frames while paused, for deterministic tools and validation."""
        if not isinstance(frames, int) or not 1 <= frames <= 6000:
            raise ValueError("Step requires 1..6000 frames.")
        return self._request("step", frames, timeout=max(15, frames / 20))

    def save_state(self, path):
        return self._request("save", Path(path).resolve())

    def flush_saves(self):
        """Flush battery memory on the core worker without changing media paths."""
        return self._request("flush_saves")

    def load_state(self, path):
        return self._request("load", Path(path).resolve())

    def save_screenshot(self, path):
        frame = self.latest_frame()
        if frame is None:
            raise EmbeddedError("The core has not produced a video frame yet.")
        target = Path(path).resolve()
        self._protect_content(target)
        _atomic_write(target, _png(frame))
        return target

    def _protect_content(self, path):
        if path in (self.core_path, self.game_path):
            raise EmbeddedError("Output cannot overwrite the game or the core.")

    def _bind(self):
        signatures = {
            "retro_api_version": ([], C.c_uint), "retro_init": ([], None), "retro_deinit": ([], None),
            "retro_get_system_info": ([C.POINTER(_SystemInfo)], None),
            "retro_get_system_av_info": ([C.POINTER(_AVInfo)], None),
            "retro_load_game": ([C.POINTER(_GameInfo)], C.c_bool), "retro_unload_game": ([], None),
            "retro_run": ([], None), "retro_reset": ([], None),
            "retro_serialize_size": ([], C.c_size_t),
            "retro_serialize": ([C.c_void_p, C.c_size_t], C.c_bool),
            "retro_unserialize": ([C.c_void_p, C.c_size_t], C.c_bool),
            "retro_get_memory_data": ([C.c_uint], C.c_void_p),
            "retro_get_memory_size": ([C.c_uint], C.c_size_t),
            "retro_set_controller_port_device": ([C.c_uint, C.c_uint], None),
        }
        for name, cb_type in zip(("environment", "video_refresh", "audio_sample", "audio_sample_batch",
                                  "input_poll", "input_state"),
                                 (_Environment, _Video, _Audio, _AudioBatch, _InputPoll, _InputState)):
            signatures["retro_set_" + name] = ([cb_type], None)
        for name, (args, result) in signatures.items():
            try:
                fn = getattr(self._core, name)
            except AttributeError as exc:
                raise EmbeddedError(f"The DLL is missing libretro function {name}.") from exc
            try:
                fn.argtypes, fn.restype = args, result
            except AttributeError:
                pass
        if self._core.retro_api_version() != 1:
            raise EmbeddedError("The core does not implement libretro API version 1.")

    def _worker(self):
        initialized = loaded = False
        stage = "hashing the core and content"
        try:
            self._core_hash, self._game_hash = _sha256(self.core_path), _sha256(self.game_path)
            self._core = self._loader(str(self.core_path))
            stage = "binding the public libretro ABI"
            self._bind()
            stage = "registering the environment callback"
            self._core.retro_set_environment(self._callbacks[0])
            stage = "initializing the core"
            self._core.retro_init()
            initialized = True
            for name, callback in zip(("environment", "video_refresh", "audio_sample", "audio_sample_batch",
                                       "input_poll", "input_state"), self._callbacks):
                if name == "environment":
                    continue
                stage = "registering the " + name + " callback"
                getattr(self._core, "retro_set_" + name)(callback)
            info = _SystemInfo()
            stage = "reading core system information"
            self._core.retro_get_system_info(C.byref(info))
            decode = lambda value: (value or b"").decode("utf-8", "replace")
            self.core_info = {"name": decode(info.library_name), "version": decode(info.library_version),
                              "extensions": decode(info.valid_extensions), "need_fullpath": info.need_fullpath}
            path = os.fsencode(self.game_path)
            if info.need_fullpath:
                self._rom_buffer = None
                game = _GameInfo(path, None, 0, None)
            else:
                if self.game_path.stat().st_size > 1024 * 1024 * 1024:
                    raise EmbeddedError("In-memory game content exceeds the 1 GiB host limit.")
                data = self.game_path.read_bytes()
                self._rom_buffer = C.create_string_buffer(data)
                game = _GameInfo(path, C.addressof(self._rom_buffer), len(data), None)
            self._game_info = game
            stage = "loading game content"
            if not self._core.retro_load_game(C.byref(game)):
                raise EmbeddedError(self._callback_error or "The libretro core refused this game.")
            loaded = True
            if self._callback_error:
                raise EmbeddedError(self._callback_error)
            missing=set(self._requested_variables)-set(key.encode("utf-8") for key in self.declared_options)
            if missing:
                raise EmbeddedError("The installed core did not declare required options: "+", ".join(sorted(key.decode("utf-8") for key in missing)))
            av = _AVInfo()
            stage = "reading core timing and geometry"
            self._core.retro_get_system_av_info(C.byref(av))
            self._apply_av(av)
            stage = "assigning game controllers"
            for port in range(4):
                self._core.retro_set_controller_port_device(port, 1)
            self._sram_path = self.save_directory / (self._core_hash[:12] + "-" + self._game_hash + ".srm")
            stage = "loading battery-save memory"
            self._load_sram()
            if self.audio_enabled:
                try:
                    self._audio_sink = self._audio_factory(self.sample_rate)
                except Exception as exc:
                    self.audio_error = str(exc)
            stage = "running emulation frames"
            self._ready.set()
            deadline = time.perf_counter()
            while not self._stop.is_set():
                self._drain_commands()
                if self._stop.is_set():
                    break
                if self._paused:
                    self._wake.wait(.05)
                    self._wake.clear()
                    deadline = time.perf_counter()
                    continue
                self._run_frame()
                deadline += 1 / self.fps
                now = time.perf_counter()
                if now - deadline > .25:
                    deadline = now
                self._wake.wait(max(0, deadline - now))
                self._wake.clear()
        except Exception as exc:
            self.error = f"While {stage}: {type(exc).__name__}: {exc}"
        finally:
            if loaded:
                try:
                    self._save_sram()
                except Exception as exc:
                    self.messages.append(f"SRAM was not saved: {exc}")
                try:
                    self._core.retro_unload_game()
                except Exception as exc:
                    self.error = self.error or str(exc)
            if initialized:
                try:
                    self._core.retro_deinit()
                except Exception as exc:
                    self.error = self.error or str(exc)
            if self._audio_sink:
                try:
                    self._audio_sink.close()
                except Exception as exc:
                    self.audio_error = str(exc)
            self._stop.set()
            self._ready.set()
            with _owner_lock:
                _owners.discard(self._owner_key)
            while not self._commands.empty():
                _, _, event, result = self._commands.get_nowait()
                result.append(EmbeddedError(self.error or "The embedded session has stopped."))
                event.set()

    def _run_frame(self):
        with self._lock:
            self._frame_inputs = [held | pending for held, pending in zip(self._inputs, self._pending_inputs)]
            self._pending_inputs = [0] * 8
            self._frame_analog_inputs = list(self._analog_inputs)
        self._core.retro_run()
        if self._callback_error:
            raise EmbeddedError(self._callback_error)
        if self._sample_buffer:
            self._write_audio(bytes(self._sample_buffer))
            self._sample_buffer.clear()

    def _drain_commands(self):
        while not self._commands.empty():
            operation, argument, event, result = self._commands.get_nowait()
            try:
                if operation == "pause":
                    self._paused = argument
                    if argument:
                        with self._lock:
                            self._inputs = [0] * 8
                            self._pending_inputs = [0] * 8
                            self._frame_inputs = [0] * 8
                            self._analog_inputs = [(0, 0, 0, 0)] * 8
                            self._frame_analog_inputs = [(0, 0, 0, 0)] * 8
                    if self._audio_sink:
                        try:
                            self._audio_sink.pause() if argument else self._audio_sink.resume()
                        except Exception as exc:
                            self.audio_error = str(exc)
                    value = self._paused
                elif operation == "step":
                    if not self._paused:
                        raise EmbeddedError("Pause the game before stepping exact frames.")
                    for _ in range(argument):
                        self._run_frame()
                    value = self.latest_frame()
                elif operation == "reset":
                    self._core.retro_reset()
                    self._flush_audio()
                    value = True
                elif operation == "save":
                    value = self._save_state(argument)
                elif operation == "load":
                    value = self._load_state(argument)
                elif operation == "flush_saves":
                    self._save_sram()
                    value = True
                else:
                    raise EmbeddedError("Unknown embedded session command.")
                result.append(value)
            except Exception as exc:
                result.append(exc)
            finally:
                event.set()

    def _apply_av(self, av):
        if not math.isfinite(av.timing.fps) or not 1 <= av.timing.fps <= 1000:
            raise EmbeddedError("The core supplied an invalid frame rate.")
        if not math.isfinite(av.timing.sample_rate) or not 8000 <= av.timing.sample_rate <= 192000:
            raise EmbeddedError("The core supplied an invalid audio sample rate.")
        self.fps, self.sample_rate = av.timing.fps, av.timing.sample_rate
        aspect=float(av.geometry.aspect_ratio)
        if math.isfinite(aspect) and .125<=aspect<=8:self.display_aspect=aspect

    def _environment(self, command, data):
        try:
            if command in self._strings:
                if not data:
                    return False
                C.cast(data, C.POINTER(C.c_char_p))[0] = self._strings[command]
                return True
            if command in (2, 3, 17, 0x10031):  # overscan, dupe, option update, fast-forward
                C.cast(data, C.POINTER(C.c_bool))[0] = command == 3
                return True
            if command == 10:
                value = C.cast(data, C.POINTER(C.c_int))[0]
                if value not in (0, 1, 2):
                    return False
                self._pixel_format = value
                return True
            if command == 14 or command == 0x1002B:
                self._callback_error = "This frontend supports software-rendered libretro cores; GPU context requests are unsupported."
                return False
            if command == 1:
                rotation = C.cast(data, C.POINTER(C.c_uint))[0]
                if rotation > 3:
                    return False
                self._rotation = rotation
                return True
            if command == 15:
                variable = C.cast(data, C.POINTER(_Variable)).contents
                variable.value = self._variables.get(variable.key)
                return True
            if command == 16:
                variables = C.cast(data, C.POINTER(_Variable))
                for i in range(4096):
                    entry = variables[i]
                    if not entry.key:
                        return True
                    options = (entry.value or b"").partition(b";")[2].strip().split(b"|")
                    if options and options[0]:
                        self.declared_options[entry.key.decode("utf-8","replace")]=tuple(value.decode("utf-8","replace") for value in options)
                        requested=self._requested_variables.get(entry.key)
                        if requested is not None and requested not in options:
                            self._callback_error=f"Unsupported core option: {entry.key.decode('utf-8','replace')}"
                            return False
                        self._variables[entry.key] = requested if requested is not None else options[0]
                return False
            if command == 27 and data:
                C.cast(data,C.POINTER(_LogCallback)).contents.log=self._log_callback
                return True
            if command == 24 and data:  # GET_INPUT_DEVICE_CAPABILITIES: uint64_t *
                C.cast(data, C.POINTER(C.c_uint64))[0] = (1 << 1) | (1 << 5)
                return True
            if command == 61 and data:  # GET_INPUT_MAX_USERS: unsigned *
                C.cast(data, C.POINTER(C.c_uint))[0] = 4
                return True
            if command == 32:
                av = C.cast(data, C.POINTER(_AVInfo)).contents
                old_rate = self.sample_rate
                self._apply_av(av)
                if self._audio_sink and old_rate != self.sample_rate:
                    try:
                        self._audio_sink.close()
                        self._audio_sink = self._audio_factory(self.sample_rate)
                    except Exception as exc:
                        self.audio_error = str(exc)
                        self._audio_sink = None
                return True
            if command in (39, 52, 59, 0x10048):
                C.cast(data, C.POINTER(C.c_uint))[0] = 0
                return True
            if command == 0x1002F:
                C.cast(data, C.POINTER(C.c_int))[0] = 3 if self.audio_enabled else 1
                return True
            if command == 0x10033:
                return True
            if command == 0x10032:
                C.cast(data, C.POINTER(C.c_float))[0] = 60.0
                return True
            if command == 6:
                msg = C.cast(data, C.POINTER(_Message)).contents.msg
                if msg:
                    self.messages.append(msg.decode("utf-8", "replace")[:2048])
                    del self.messages[:-100]
                return True
            if command == 7:
                self._stop.set()
                return True
            if command in (8, 11, 18, 35, 37):
                return True
            return False
        except Exception as exc:
            self._callback_error = f"Environment callback failed: {exc}"
            return False

    def _video(self, data, width, height, pitch):
        try:
            if data == C.c_void_p(-1).value:
                raise EmbeddedError("Hardware-rendered video frames are unsupported.")
            if not data:
                return
            rgb = _convert_video(data, width, height, pitch, self._pixel_format)
            if self._rotation:
                # Pillow is already a frontend dependency; keep the common no-rotation path stdlib-only.
                from PIL import Image
                image = Image.frombytes("RGB", (width, height), rgb)
                image = image.transpose({1: Image.Transpose.ROTATE_90, 2: Image.Transpose.ROTATE_180,
                                         3: Image.Transpose.ROTATE_270}[self._rotation])
                width, height = image.size
                rgb = image.tobytes()
            with self._lock:
                self._sequence += 1
                self._frame = VideoFrame(width, height, rgb, self._sequence)
        except Exception as exc:
            self._callback_error = str(exc)

    def _core_log(self,level,message):
        # A C variadic logger cannot be printf-formatted safely by a Python
        # callback. Keep its bounded format text; never interpret remote/native
        # formatting directives or claim substituted argument values.
        if message:
            self.messages.append(f"Core log {level}: "+message.decode("utf-8","replace")[:2048])
            del self.messages[:-100]

    def _write_audio(self, pcm):
        if self._audio_sink:
            try:
                self._audio_sink.write(pcm)
            except Exception as exc:
                self.audio_error = str(exc)

    def _audio_sample(self, left, right):
        try:
            if self.audio_enabled and len(self._sample_buffer) < 4 * 192000:
                self._sample_buffer.extend(struct.pack("<hh", left, right))
        except Exception as exc:
            self.audio_error = str(exc)

    def _audio_batch(self, data, frames):
        try:
            if frames > 192000:
                self.audio_error = "The core supplied an oversized audio batch."
                return 0
            if self.audio_enabled and data:
                self._write_audio(C.string_at(data, frames * 4))
            return frames
        except Exception as exc:
            self.audio_error = str(exc)
            return 0

    def _input_state(self, port, device, index, button):
        if not 0 <= port < len(self._inputs):
            return 0
        if device == 5:  # RETRO_DEVICE_ANALOG; left/right stick, X/Y axis.
            if index not in (0, 1) or button not in (0, 1):
                return 0
            with self._lock:
                return self._frame_analog_inputs[port][index * 2 + button]
        if device != 1 or index != 0:
            return 0
        with self._lock:
            mask = self._frame_inputs[port]
        if button == 256:
            return C.c_int16(mask).value
        return int(0 <= button < 16 and bool(mask & (1 << button)))

    def _flush_audio(self):
        self._sample_buffer.clear()
        if self._audio_sink:
            try:
                self._audio_sink.flush()
            except Exception as exc:
                self.audio_error = str(exc)

    def _memory(self):
        size = int(self._core.retro_get_memory_size(0))
        if not 0 <= size <= 64 * 1024 * 1024:
            raise EmbeddedError("The core supplied an invalid battery-save size.")
        pointer = self._core.retro_get_memory_data(0) if size else None
        if size and not pointer:
            raise EmbeddedError("The core battery-save memory is unavailable.")
        return pointer, size

    def _load_sram(self):
        pointer, size = self._memory()
        if not size or not self._sram_path.exists():
            return
        if self._sram_path.stat().st_size != size:
            self._sram_mismatch = True
            self.messages.append("Existing battery save has a different size; it was preserved without loading or overwriting.")
            return
        data = self._sram_path.read_bytes()
        C.memmove(pointer, data, size)

    def _save_sram(self):
        pointer, size = self._memory()
        if size and not self._sram_mismatch:
            _atomic_write(self._sram_path, C.string_at(pointer, size))

    def _save_state(self, path):
        self._protect_content(path)
        size = int(self._core.retro_serialize_size())
        if not 0 < size <= _MAX_STATE:
            raise EmbeddedError("This core does not offer a supported save-state size.")
        buffer = C.create_string_buffer(size)
        if not self._core.retro_serialize(buffer, size):
            raise EmbeddedError("The core could not serialize its current state.")
        header = json.dumps({"core_sha256": self._core_hash, "entry_sha256": self._game_hash,
                             "payload_size": size, "payload_sha256": hashlib.sha256(buffer.raw).hexdigest(),
                             "core": self.core_info, "frame_sequence": self._sequence},
                            separators=(",", ":")).encode("utf-8")
        _atomic_write(path, _STATE_MAGIC + struct.pack("<I", len(header)) + header + buffer.raw)
        self._save_sram()
        return path

    def _load_state(self, path):
        if path.stat().st_size > _MAX_STATE + 16384:
            raise EmbeddedError("The state file exceeds the size limit.")
        with path.open("rb") as stream:
            data = stream.read(_MAX_STATE + 16385)
        if len(data) > _MAX_STATE + 16384:
            raise EmbeddedError("The state file exceeds the size limit.")
        if len(data) < 13 or data[:9] != _STATE_MAGIC:
            raise EmbeddedError("This is not a Redux embedded state file.")
        length = struct.unpack_from("<I", data, 9)[0]
        if not 1 <= length <= 16384 or 13 + length > len(data):
            raise EmbeddedError("The state header is malformed.")
        try:
            header = json.loads(data[13:13 + length])
        except (ValueError, UnicodeDecodeError) as exc:
            raise EmbeddedError("The state header is malformed.") from exc
        payload = data[13 + length:]
        if not isinstance(header, dict) or header.get("core_sha256") != self._core_hash or header.get("entry_sha256") != self._game_hash:
            raise EmbeddedError("The state belongs to a different core build or game entry file.")
        if header.get("payload_size") != len(payload) or len(payload) != self._core.retro_serialize_size():
            raise EmbeddedError("The state payload has an incompatible size.")
        if header.get("payload_sha256") != hashlib.sha256(payload).hexdigest():
            raise EmbeddedError("The state payload failed its integrity check.")
        buffer = C.create_string_buffer(payload)
        if not self._core.retro_unserialize(buffer, len(payload)):
            raise EmbeddedError("The core rejected this save state.")
        self._flush_audio()
        return path
