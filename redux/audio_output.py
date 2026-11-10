"""Small PCM16 stereo WaveOut sink. No audio device is opened until requested."""
from __future__ import annotations

import ctypes as C
import os
import threading


class AudioOutputError(RuntimeError):
    pass


class _WaveFormat(C.Structure):
    _pack_ = 1
    _fields_ = [("format", C.c_uint16), ("channels", C.c_uint16),
                ("sample_rate", C.c_uint32), ("bytes_per_second", C.c_uint32),
                ("block_align", C.c_uint16), ("bits", C.c_uint16), ("extra", C.c_uint16)]


class _WaveHeader(C.Structure):
    _fields_ = [("data", C.c_void_p), ("length", C.c_uint32),
                ("recorded", C.c_uint32), ("user", C.c_size_t),
                ("flags", C.c_uint32), ("loops", C.c_uint32),
                ("next", C.c_void_p), ("reserved", C.c_size_t)]


# Retain any buffers a broken driver fails to release rather than freeing live DMA data.
_unreleased = []


class WaveOutAudio:
    """Bounded non-blocking output; an overloaded audio queue drops newest audio."""

    def __init__(self, sample_rate: float, *, _api=None):
        rate = round(sample_rate)
        if not 8000 <= rate <= 192000:
            raise AudioOutputError("The core supplied an unsupported audio sample rate.")
        if _api is None:
            if os.name != "nt":
                raise AudioOutputError("WaveOut audio requires Windows.")
            _api = C.WinDLL("winmm")
        self._api = _api
        self._lock = threading.RLock()
        self._buffers = []
        self._handle = C.c_void_p()
        self._closed = False
        self.dropped_frames = 0
        signatures = {
            "waveOutOpen": ([C.POINTER(C.c_void_p), C.c_uint, C.POINTER(_WaveFormat),
                             C.c_size_t, C.c_size_t, C.c_uint], C.c_uint),
            "waveOutPrepareHeader": ([C.c_void_p, C.POINTER(_WaveHeader), C.c_uint], C.c_uint),
            "waveOutWrite": ([C.c_void_p, C.POINTER(_WaveHeader), C.c_uint], C.c_uint),
            "waveOutUnprepareHeader": ([C.c_void_p, C.POINTER(_WaveHeader), C.c_uint], C.c_uint),
        }
        for name in ("waveOutPause", "waveOutRestart", "waveOutReset", "waveOutClose"):
            signatures[name] = ([C.c_void_p], C.c_uint)
        for name, (args, result) in signatures.items():
            fn = getattr(_api, name)
            try:
                fn.argtypes, fn.restype = args, result
            except AttributeError:  # Python test doubles.
                pass
        fmt = _WaveFormat(1, 2, rate, rate * 4, 4, 16, 0)
        self._check(_api.waveOutOpen(C.byref(self._handle), 0xFFFFFFFF, C.byref(fmt), 0, 0, 0))

    @staticmethod
    def _check(result):
        if result:
            raise AudioOutputError(f"WaveOut returned error {result}.")

    def _reap(self):
        pending = []
        for data, header in self._buffers:
            if header.flags & 1 and not self._api.waveOutUnprepareHeader(
                    self._handle, C.byref(header), C.sizeof(header)):
                continue
            pending.append((data, header))
        self._buffers = pending

    def write(self, pcm: bytes):
        if len(pcm) % 4:
            raise AudioOutputError("Audio must contain complete PCM16 stereo frames.")
        if not pcm:
            return
        with self._lock:
            if self._closed:
                return
            self._reap()
            if len(self._buffers) >= 8:
                self.dropped_frames += len(pcm) // 4
                return
            data = C.create_string_buffer(pcm)
            header = _WaveHeader(C.addressof(data), len(pcm), 0, 0, 0, 0, None, 0)
            self._check(self._api.waveOutPrepareHeader(self._handle, C.byref(header), C.sizeof(header)))
            self._buffers.append((data, header))
            self._check(self._api.waveOutWrite(self._handle, C.byref(header), C.sizeof(header)))

    def pause(self):
        with self._lock:
            if not self._closed:
                self._check(self._api.waveOutPause(self._handle))

    def resume(self):
        with self._lock:
            if not self._closed:
                self._check(self._api.waveOutRestart(self._handle))

    def flush(self):
        with self._lock:
            if not self._closed:
                self._check(self._api.waveOutReset(self._handle))
                self._reap()

    def close(self):
        with self._lock:
            if self._closed:
                return
            try:
                self.flush()
                if self._buffers:
                    raise AudioOutputError("The audio driver did not release its output buffers.")
                self._check(self._api.waveOutClose(self._handle))
            except Exception:
                _unreleased.append(self)
                raise
            finally:
                self._closed = True
