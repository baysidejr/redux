"""Bounded, per-process PCSX-Redux Lua bridge for genuine emulator state.

The bridge is loaded explicitly with the documented ``-dofile`` option. It uses
PCSX-Redux's existing Lua IO and main-loop API; it never enables its web server,
changes script permissions, injects keys, or evaluates commands as source code.
Mesen does not expose this protocol and must not be assigned these capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import threading
import time
import uuid
import zlib


class BridgeError(RuntimeError):
    """The owned backend did not complete a supported bridge operation."""


@dataclass(frozen=True)
class StateSlot:
    slot: int
    exists: bool
    path: Path | None = None
    size: int = 0
    saved_at: str | None = None
    sha256: str | None = None


_MAX_STATE = 64 * 1024 * 1024
_MAX_FRAME = 2048 * 2048 * 3
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _lua_string(value: str, *, encoding: str = "utf-8") -> str:
    # Decimal byte escapes are Lua 5.1/LuaJIT compatible and cannot terminate
    # the quoted literal, including for non-ASCII Windows profile names.
    return '"' + ''.join(f"\\{byte:03d}" for byte in value.encode(encoding)) + '"'


def _lua_io_root(path: Path) -> str:
    # LuaJIT's Windows standard IO uses the active ANSI code page. Keep this
    # separate from the UTF-8 script source/PCSX API; never change the process
    # code page or a backend permission to make a path work.
    text = path.as_posix()
    if os.name != "nt":
        return _lua_string(text)
    try:
        return _lua_string(text, encoding="mbcs")
    except UnicodeEncodeError:
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        kernel.GetShortPathNameW.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.GetShortPathNameW(str(path), buffer, len(buffer))
        if 0 < length < len(buffer):
            try:
                return _lua_string(buffer.value.replace("\\", "/"), encoding="mbcs")
            except UnicodeEncodeError:
                pass
        raise BridgeError("Lua IO cannot address this library path; use a library folder supported by the Windows code page.")


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _bounded_read(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise BridgeError("The backend output is not a regular file.")
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise BridgeError("The backend output exceeds the supported size.")
    return data


def _slot_number(slot: int) -> int:
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= 9:
        raise ValueError("State slot must be an integer between 0 and 9.")
    return slot


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 120:
        raise ValueError("Bridge timeout must be greater than zero and at most 120 seconds.")
    return float(value)


def _png_frame(raw: bytes, width: int, height: int, bpp: int) -> bytes:
    """Encode the GPU API's native RGB888 or little-endian RGB555 frame."""
    if not (1 <= width <= 2048 and 1 <= height <= 2048) or bpp not in (0, 1):
        raise BridgeError("The backend returned an unsupported frame format.")
    pixel_size = 2 if bpp == 0 else 3
    if len(raw) != width * height * pixel_size or len(raw) > _MAX_FRAME:
        raise BridgeError("The backend frame length does not match its dimensions.")
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)  # PNG filter: none
        row = raw[y * width * pixel_size:(y + 1) * width * pixel_size]
        if bpp == 1:
            scanlines.extend(row)
        else:
            for (pixel,) in struct.iter_unpack("<H", row):
                for shift in (0, 5, 10):
                    channel = (pixel >> shift) & 31
                    scanlines.append((channel << 3) | (channel >> 2))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")


class PcsxReduxBridge:
    """One trusted Lua mailbox belonging to one frontend-owned backend launch.

    Construct before launching, append ``launch_arguments`` to that process's
    arguments, then call ``wait_ready`` off the UI thread. Readiness is an actual
    Lua acknowledgment. Operations are synchronous and should run off the UI
    thread too. A native OS-suspended process cannot service any Lua request;
    use this bridge's emulation pause/resume instead while its menu is open.
    """

    backend_id = "pcsx_redux"

    def __init__(self, library_root: str | Path, game_id: int, *, game_key: str | None = None,
                 timeout: float = 8.0):
        if isinstance(game_id, bool) or not isinstance(game_id, int) or game_id < 1:
            raise ValueError("Game ID must be a positive integer.")
        self.game_id = game_id
        if game_key is not None and (not isinstance(game_key, str) or not _HEX.fullmatch(game_key)):
            raise ValueError("Game key must be the library bundle's SHA-256 digest.")
        self.game_key = game_key
        self.timeout = _timeout(timeout)
        self.root = Path(library_root).expanduser().resolve()
        self.game_directory = self.root / "game_data" / str(game_id)
        self.state_directory = self.game_directory / "states" / self.backend_id
        if game_key:
            self.state_directory /= game_key
        self.screenshot_directory = self.game_directory / "screenshots"
        if game_key:self.screenshot_directory = self.game_directory / game_key / "screenshots"
        self.session_token = uuid.uuid4().hex
        self.session_directory = self.game_directory / ".backend-rpc" / self.session_token
        for directory in (self.state_directory, self.screenshot_directory, self.session_directory):
            if not directory.resolve().is_relative_to(self.root):
                raise BridgeError("The game data directory resolves outside the library.")
            directory.mkdir(parents=True, exist_ok=True)
            if not directory.resolve().is_relative_to(self.root):
                raise BridgeError("The game data directory resolves outside the library.")
        self.script_path = self.session_directory / "bridge.lua"
        self._lock = threading.RLock()
        self._closed = False
        self._fault: str | None = None
        script = _PCSX_SCRIPT.replace("@@ROOT@@", _lua_io_root(self.session_directory))
        script = script.replace("@@SESSION@@", _lua_string(self.session_token))
        _atomic_write(self.script_path, script.encode("utf-8"))

    @property
    def launch_arguments(self) -> tuple[str, str]:
        return ("-dofile", str(self.script_path))

    @property
    def ready(self) -> bool:
        if self._closed or self._fault:
            return False
        try:
            expected = f"{self.session_token} pcsx_redux 1\n".encode("ascii")
            return _bounded_read(self.session_directory / "ready", 128) == expected
        except (OSError, BridgeError):
            return False

    @property
    def failure_reason(self) -> str | None:
        return self._fault or ("This bridge is closed." if self._closed else None)

    def wait_ready(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + _timeout(timeout)
        while time.monotonic() < deadline:
            if self.ready:
                return True
            if self._closed or self._fault:
                return False
            time.sleep(0.025)
        return self.ready

    def _command(self, operation: str, slot: int = 0, *, input_data: bytes | None = None) -> tuple[str, list[str]]:
        if operation not in {"save", "load", "capture", "pause", "resume"}:
            raise ValueError("Unsupported bridge operation.")
        _slot_number(slot)
        if self._closed or self._fault:
            raise BridgeError(self.failure_reason)
        if not self.ready:
            raise BridgeError("The PCSX-Redux bridge is not ready for this launch.")
        token = uuid.uuid4().hex
        if input_data is not None:
            _atomic_write(self.session_directory / f"{token}.input", input_data)
        command_path = self.session_directory / "command"
        if command_path.exists():
            self._fault = "A previous backend request is still pending; relaunch the game to reconnect."
            raise BridgeError(self._fault)
        message = f"{self.session_token} {token} {operation} {slot}\n".encode("ascii")
        _atomic_write(command_path, message)
        reply_path = self.session_directory / f"{token}.reply"
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                reply = _bounded_read(reply_path, 512).decode("ascii").strip().split()
            except FileNotFoundError:
                time.sleep(0.025)
                continue
            except (OSError, UnicodeError, BridgeError) as error:
                # A missing file is normal while the backend works. Other
                # malformed output is a protocol failure, not success.
                if not reply_path.exists():
                    time.sleep(0.025)
                    continue
                self._fault = "The backend returned an invalid bridge acknowledgment."
                raise BridgeError(self._fault) from error
            if len(reply) < 4 or reply[:3] != [self.session_token, token, operation]:
                self._fault = "The backend acknowledgment does not belong to this request."
                raise BridgeError(self._fault)
            if reply[3] == "error":
                reply_path.unlink(missing_ok=True)
                (self.session_directory / f"{token}.input").unlink(missing_ok=True)
                raise BridgeError("PCSX-Redux could not complete " + operation + ": " + " ".join(reply[4:]))
            if reply[3] != "ok":
                self._fault = "The backend returned an unknown acknowledgment status."
                raise BridgeError(self._fault)
            reply_path.unlink(missing_ok=True)
            (self.session_directory / f"{token}.input").unlink(missing_ok=True)
            return token, reply[4:]
        self._fault = "PCSX-Redux did not acknowledge the request in time; relaunch the game to reconnect."
        raise BridgeError(self._fault)

    def _get_slot(self, slot: int, *, verify: bool = False) -> StateSlot:
        slot = _slot_number(slot)
        manifest_path = self.state_directory / f"slot-{slot}.json"
        if not manifest_path.exists():
            return StateSlot(slot, False)
        try:
            manifest = json.loads(_bounded_read(manifest_path, 4096))
            digest, size = manifest["sha256"], manifest["size"]
            if (manifest["schema"] != 1 or manifest["backend"] != self.backend_id or
                manifest["game_id"] != self.game_id or manifest["slot"] != slot or
                manifest.get("game_key") != self.game_key or
                not isinstance(digest, str) or not _HEX.fullmatch(digest) or
                isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= _MAX_STATE or
                not isinstance(manifest["saved_at"], str)):
                raise ValueError("Invalid state manifest")
            state_path = self.state_directory / (digest + ".state")
            if state_path.is_symlink() or not state_path.is_file() or state_path.stat().st_size != size:
                raise ValueError("State file is unavailable")
            if verify and hashlib.sha256(_bounded_read(state_path, _MAX_STATE)).hexdigest() != digest:
                raise ValueError("State checksum changed")
            return StateSlot(slot, True, state_path, size, manifest["saved_at"], digest)
        except (OSError, ValueError, TypeError, KeyError, BridgeError) as error:
            raise BridgeError(f"State slot {slot} is incomplete or has changed.") from error

    def slots(self) -> list[StateSlot]:
        with self._lock:
            return [self._get_slot(slot) for slot in range(10)]

    def slot(self, number: int) -> StateSlot:
        """Inspect one slot, so another damaged slot cannot hide this one."""
        with self._lock:
            return self._get_slot(number)

    def save(self, slot: int = 0) -> StateSlot:
        with self._lock:
            slot = _slot_number(slot)
            token, fields = self._command("save", slot)
            payload_path = self.session_directory / f"{token}.state"
            try:
                data = _bounded_read(payload_path, _MAX_STATE)
            finally:
                payload_path.unlink(missing_ok=True)
            if not data or fields != [str(len(data))]:
                raise BridgeError("The saved state does not match the backend acknowledgment.")
            digest = hashlib.sha256(data).hexdigest()
            state_path = self.state_directory / (digest + ".state")
            if state_path.exists():
                if hashlib.sha256(_bounded_read(state_path, _MAX_STATE)).hexdigest() != digest:
                    raise BridgeError("An existing state file has changed.")
            else:
                _atomic_write(state_path, data)
            manifest = {"schema": 1, "backend": self.backend_id, "game_id": self.game_id,
                        "game_key": self.game_key,
                        "slot": slot, "sha256": digest, "size": len(data),
                        "saved_at": datetime.now(timezone.utc).isoformat()}
            _atomic_write(self.state_directory / f"slot-{slot}.json", json.dumps(manifest, sort_keys=True).encode("utf-8"))
            return self._get_slot(slot)

    def load(self, slot: int = 0) -> StateSlot:
        with self._lock:
            state = self._get_slot(slot, verify=True)
            if not state.exists:
                raise BridgeError(f"State slot {slot} is empty.")
            data = _bounded_read(state.path, _MAX_STATE)
            if hashlib.sha256(data).hexdigest() != state.sha256:
                raise BridgeError(f"State slot {slot} changed before it could be loaded.")
            _, fields = self._command("load", slot, input_data=data)
            if fields:
                raise BridgeError("The backend returned unexpected load acknowledgment data.")
            return state

    def capture(self) -> Path:
        with self._lock:
            token, fields = self._command("capture")
            try:
                width, height, bpp, size = (int(value) for value in fields)
            except (ValueError, TypeError) as error:
                raise BridgeError("The backend returned invalid screenshot dimensions.") from error
            payload_path = self.session_directory / f"{token}.raw"
            try:
                raw = _bounded_read(payload_path, _MAX_FRAME)
            finally:
                payload_path.unlink(missing_ok=True)
            if len(raw) != size:
                raise BridgeError("The captured frame does not match the backend acknowledgment.")
            png = _png_frame(raw, width, height, bpp)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            destination = self.screenshot_directory / f"{stamp}-{token[:8]}.png"
            _atomic_write(destination, png)
            return destination

    def pause(self) -> None:
        with self._lock:
            _, fields = self._command("pause")
            if fields:
                raise BridgeError("The backend returned unexpected pause acknowledgment data.")

    def resume(self) -> None:
        with self._lock:
            _, fields = self._command("resume")
            if fields:
                raise BridgeError("The backend returned unexpected resume acknowledgment data.")

    def close(self) -> None:
        """Stop this mailbox on its next poll; never stop or delete game media."""
        with self._lock:
            if not self._closed:
                _atomic_write(self.session_directory / "stop", self.session_token.encode("ascii"))
                self._closed = True


# This is our original adapter, not recovered/decompiled application code. The
# only substituted text consists of two escaped trusted constants. Commands
# contain fixed verbs, numeric slots and hex nonces, never paths or Lua source.
_PCSX_SCRIPT = r'''
local root = @@ROOT@@
local session = @@SESSION@@
local bridge = { stopped = false, queued = false, pending = nil, listeners = {} }
local maxState = 67108864
local maxFrame = 12582912

local function writeAtomic(path, bytes)
    local temporary = path .. '.tmp'
    local file = assert(io.open(temporary, 'wb'))
    assert(file:write(bytes))
    assert(file:flush())
    assert(file:close())
    assert(os.rename(temporary, path))
end

local function reply(token, operation, status, details)
    writeAtomic(root .. '/' .. token .. '.reply', session .. ' ' .. token .. ' ' ..
        operation .. ' ' .. status .. (details and (' ' .. details) or '') .. '\n')
end

local function stop()
    if bridge.stopped then return end
    bridge.stopped = true
    if bridge.timer then
        bridge.timer:stop()
        bridge.timer:close()
    end
end

bridge.listeners.loaded = PCSX.Events.createEventListener('ExecutionFlow::SaveStateLoaded', function()
    if bridge.pending then bridge.pending.loaded = true end
end)
bridge.listeners.quitting = PCSX.Events.createEventListener('Quitting', stop)

local function poll()
    if bridge.stopped then return end
    local stopFile = io.open(root .. '/stop', 'rb')
    if stopFile then
        local marker = stopFile:read(33)
        stopFile:close()
        if marker == session then stop() return end
    end
    if bridge.pending then
        local pending = bridge.pending
        if pending.loaded then
            bridge.pending = nil
            reply(pending.token, 'load', 'ok')
        elseif luv.now() > pending.deadline then
            bridge.pending = nil
            reply(pending.token, 'load', 'error', 'state_not_loaded')
        end
        return
    end
    local command = io.open(root .. '/command', 'rb')
    if not command then return end
    local line = command:read(257)
    command:close()
    os.remove(root .. '/command')
    if not line or #line > 256 then return end
    local suppliedSession, token, operation, slotText = line:match('^([0-9a-f]+) ([0-9a-f]+) ([a-z]+) ([0-9])\n$')
    if suppliedSession ~= session or not token or #token ~= 32 then return end
    local slot = tonumber(slotText)
    if not slot or slot < 0 or slot > 9 then return end
    local ok = pcall(function()
        if operation == 'save' then
            local bytes = tostring(PCSX.createSaveState())
            assert(#bytes > 0 and #bytes <= maxState)
            writeAtomic(root .. '/' .. token .. '.state', bytes)
            reply(token, operation, 'ok', tostring(#bytes))
        elseif operation == 'load' then
            local path = root .. '/' .. token .. '.input'
            local input = assert(io.open(path, 'rb'))
            local bytes = input:read(maxState + 1)
            input:close()
            assert(bytes and #bytes > 0 and #bytes <= maxState)
            local file = Support.File.buffer()
            assert(file:write(bytes) == #bytes)
            bridge.pending = {token = token, deadline = luv.now() + 5000, loaded = false}
            PCSX.loadSaveState(file)
            file:close()
        elseif operation == 'capture' then
            local frame = PCSX.GPU.takeScreenShot()
            local width, height, bpp = tonumber(frame.width), tonumber(frame.height), tonumber(frame.bpp)
            assert(width and height and width >= 1 and width <= 2048 and height >= 1 and height <= 2048)
            assert(bpp == 0 or bpp == 1)
            local bytes = tostring(frame.data)
            assert(#bytes == width * height * (bpp == 0 and 2 or 3) and #bytes <= maxFrame)
            writeAtomic(root .. '/' .. token .. '.raw', bytes)
            reply(token, operation, 'ok', width .. ' ' .. height .. ' ' .. bpp .. ' ' .. #bytes)
        elseif operation == 'pause' then
            PCSX.pauseEmulator()
            reply(token, operation, 'ok')
        elseif operation == 'resume' then
            PCSX.resumeEmulator()
            reply(token, operation, 'ok')
        else
            reply(token, operation, 'error', 'unsupported_command')
        end
    end)
    if not ok then
        bridge.pending = nil
        reply(token, operation, 'error', 'backend_api_failed')
    end
end

bridge.timer = luv.new_timer()
bridge.timer:start(25, 25, function()
    if bridge.queued or bridge.stopped then return end
    bridge.queued = true
    PCSX.nextTick(function()
        bridge.queued = false
        if not bridge.stopped then
            local ok = pcall(poll)
            if not ok then stop() end
        end
    end)
end)
_G.ReduxLibraryBridge = bridge
writeAtomic(root .. '/ready', session .. ' pcsx_redux 1\n')
'''
