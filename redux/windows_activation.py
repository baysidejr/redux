"""Single-instance shell activation without system hooks or registry changes.

The mutex is session-local. A user-protected Windows named pipe works across
desktop/package filesystem redirection. Only bounded JSON strings cross the
connection; the listener never deserializes Python objects.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time


HOME_ACTIVATION = "windows.gaming:///home"
_MAX_MESSAGE = 32768


@dataclass(frozen=True)
class Activation:
    argv: tuple[str, ...] = ()
    home: bool = False
    startup: bool = False
    fullscreen: bool = False
    remaining: tuple[str, ...] = ()
    # Diagnostics belong to the process that received the shell activation,
    # which may differ from the running process that owns the UI.
    source_pid: int | None = field(default=None, compare=False)
    source_parent_pid: int | None = field(default=None, compare=False)
    source_parent_executable: str | None = field(default=None, compare=False)


def parse_activation(argv) -> Activation:
    """Remove shell launch markers while preserving the existing CLI arguments."""
    values = tuple(str(value) for value in argv)
    home = any(value.casefold() == HOME_ACTIVATION for value in values)
    startup = "--startup" in values
    fullscreen = home or "--fullscreen" in values
    remaining = tuple(value for value in values
                      if value.casefold() != HOME_ACTIVATION
                      and value not in {"--startup", "--fullscreen"})
    return Activation(values, home, startup, fullscreen, remaining)


class ActivationError(RuntimeError):
    pass


def _runtime_root() -> Path:
    from .windows_paths import redux_user_data
    return redux_user_data()


def _windows_library(name):
    return ctypes.WinDLL(name, use_last_error=True)


def _user_sid() -> str:
    """Return the current token's user SID for the pipe's explicit DACL."""
    security = _windows_library("advapi32")
    kernel = _windows_library("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        security.GetTokenInformation.restype = wintypes.BOOL
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        result = wintypes.LPWSTR()
        security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        security.ConvertSidToStringSidW.restype = wintypes.BOOL
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return result.value
        finally:
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree.restype = ctypes.c_void_p
            kernel.LocalFree(ctypes.cast(result, ctypes.c_void_p))
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(token)


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p),
                ("inherit", wintypes.BOOL)]


def _create_pipe(address: str, *, first=False) -> int:
    kernel = _windows_library("kernel32")
    security = _windows_library("advapi32")
    descriptor = ctypes.c_void_p()
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    # No Everyone/Authenticated Users grant: only this user, SYSTEM and admins.
    sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{_user_sid()})"
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.DWORD, ctypes.POINTER(_SecurityAttributes)]
        kernel.CreateNamedPipeW.restype = wintypes.HANDLE
        # DUPLEX | OVERLAPPED | optional FIRST_PIPE_INSTANCE. Message-mode,
        # READMODE_MESSAGE and REJECT_REMOTE_CLIENTS are all explicit.
        flags = 3 | 0x40000000 | (0x00080000 if first else 0)
        handle = kernel.CreateNamedPipeW(address, flags, 4 | 2 | 8, 255,
                                         65536, 65536, 1000, ctypes.byref(attributes))
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(descriptor)


def _user_session() -> str:
    session = wintypes.DWORD()
    kernel = _windows_library("kernel32")
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", str(Path.home()))
    return f"{hashlib.sha256(user.casefold().encode()).hexdigest()[:24]}-{session.value}"


def _receive(connection) -> dict:
    if not connection.poll(1.0):
        raise TimeoutError("Activation connection timed out")
    value = json.loads(connection.recv_bytes(_MAX_MESSAGE).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid activation")
    return value


def _pipe_client(address: str):
    import _winapi
    from multiprocessing.connection import PipeConnection
    _winapi.WaitNamedPipe(address, 250)
    handle = _winapi.CreateFile(address, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                               0, 0, _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, 0)
    try:
        _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
        return PipeConnection(handle)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise


def _pipe_server_pid(connection) -> int | None:
    kernel = _windows_library("kernel32")
    kernel.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    kernel.GetNamedPipeServerProcessId.restype = wintypes.BOOL
    result = wintypes.ULONG()
    return result.value if kernel.GetNamedPipeServerProcessId(connection.fileno(), ctypes.byref(result)) else None


def _allow_foreground(process_id: int) -> bool:
    """Pass the second launch's foreground permission to the existing window.

    Windows still decides whether the caller may grant permission. Refusal is
    harmless: the activation must reach Redux even when focus cannot be moved.
    """
    if not isinstance(process_id, int) or not 0 < process_id < 0xFFFFFFFF:
        return False
    try:
        user = _windows_library("user32")
        user.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        user.AllowSetForegroundWindow.restype = wintypes.BOOL
        return bool(user.AllowSetForegroundWindow(process_id))
    except (OSError, AttributeError):
        return False


def _sender_details() -> dict:
    try:
        parent = parent_process_details()
    except Exception:
        parent = {"pid": os.getppid(), "executable": None}
    path = parent.get("executable")
    return {"pid": os.getpid(), "parent_pid": parent.get("pid", os.getppid()),
            "parent_executable": path[:4096] if isinstance(path, str) else None}


def _sender_fields(payload: dict) -> dict:
    sender = payload.get("sender")
    if not isinstance(sender, dict):
        return {}
    pid, parent_pid = sender.get("pid"), sender.get("parent_pid")
    path = sender.get("parent_executable")
    if (type(pid) is not int or not 0 < pid < 0xFFFFFFFF
            or type(parent_pid) is not int or not 0 <= parent_pid < 0xFFFFFFFF
            or not (path is None or isinstance(path, str) and len(path) <= 4096)):
        return {}
    return {"source_pid": pid, "source_parent_pid": parent_pid,
            "source_parent_executable": path}


class SingleInstance:
    """Hold one frontend per signed-in session and queue repeat activations.

    Call acquire before creating Tk. If False, activation reached the existing
    frontend and this process should return. Poll activations on Tk's own thread.
    The owner must close this object only after its Tk mainloop has ended.
    """
    def __init__(self, namespace="ReduxMediaLibrary", runtime_dir=None):
        self.namespace = namespace
        self.runtime_dir = Path(runtime_dir) if runtime_dir else _runtime_root() / "runtime"
        self.activations: queue.Queue[Activation] = queue.Queue()
        self._mutex = None
        self._listener = None
        self._thread = None
        self._closed = threading.Event()
        self._address = None
        self._connection = None
        self._connection_lock = threading.Lock()
        self._owner = False

    def acquire(self, activation: Activation, *, timeout=4.0) -> bool:
        if self._mutex is not None or self._owner or self._closed.is_set():
            raise ActivationError("This instance coordinator was already acquired")
        if sys.platform != "win32":
            self._owner = True
            return True
        kernel = _windows_library("kernel32")
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        identity = hashlib.sha256(self.namespace.encode()).hexdigest()[:24] + "-" + _user_session()
        ctypes.set_last_error(0)
        handle = kernel.CreateMutexW(None, False, "Local\\Redux.Activation." + identity)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        self._mutex = handle
        self._address = r"\\.\pipe\Redux.Activation." + identity
        if error == 183:  # ERROR_ALREADY_EXISTS
            try:
                self._forward(activation, timeout)
            finally:
                self._release_mutex()
            return False
        self._owner = True
        try:
            self._listener = _create_pipe(self._address, first=True)
            self._thread = threading.Thread(target=self._serve, name="Redux activation", daemon=True)
            self._thread.start()
            return True
        except BaseException:
            self.close()
            raise

    def _forward(self, activation, timeout):
        end = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < end:
            try:
                payload = json.dumps({"argv": list(activation.argv),
                                      "sender": _sender_details()}, ensure_ascii=True).encode()
                if len(payload) > _MAX_MESSAGE:
                    raise ActivationError("Activation arguments are too large")
                with _pipe_client(self._address) as connection:
                    # Ask Windows for the actual server PID; no file or client
                    # claim determines who receives foreground permission.
                    _allow_foreground(_pipe_server_pid(connection))
                    connection.send_bytes(payload)
                    reply = _receive(connection)
                    if reply.get("accepted") is True:
                        return
                    raise ValueError("Activation was not accepted")
            except (OSError, EOFError, ValueError, KeyError, TypeError) as error:
                last_error = error
                time.sleep(0.05)
        raise ActivationError("Redux is already running but could not receive this activation") from last_error

    def _serve(self):
        import _winapi
        from multiprocessing.connection import PipeConnection
        while not self._closed.is_set():
            handle = self._listener
            connection = None
            try:
                if handle is None:
                    handle = self._listener = _create_pipe(self._address)
                pending = _winapi.ConnectNamedPipe(handle, overlapped=True)
                while _winapi.WaitForMultipleObjects([pending.event], False, 250) == 258:
                    if self._closed.is_set():
                        pending.cancel()
                        pending.GetOverlappedResult(True)
                        return
                _, error = pending.GetOverlappedResult(True)
                if error:
                    raise OSError(error, "Named pipe connection failed")
                connection = PipeConnection(handle)
                with self._connection_lock:
                    self._connection = connection
                    self._listener = None
                try:
                    if self._closed.is_set():
                        return
                    payload = _receive(connection)
                    argv = payload.get("argv")
                    valid = (isinstance(argv, list) and len(argv) <= 64
                             and all(isinstance(value, str) and len(value) <= 4096 for value in argv))
                    if valid and self.activations.qsize() < 32:
                        self.activations.put(replace(parse_activation(argv), **_sender_fields(payload)))
                        connection.send_bytes(b'{"accepted":true}')
                    else:
                        connection.send_bytes(b'{"accepted":false}')
                finally:
                    self._close_connection(connection)
            except (OSError, EOFError, ValueError, UnicodeError):
                pass
            finally:
                if connection is None and handle is not None:
                    _winapi.CloseHandle(handle)
                    if self._listener == handle:self._listener = None

    def _close_connection(self, expected=None):
        # PipeConnection.close is not safe when two threads close the same
        # native handle concurrently. Both shutdown paths use this lock.
        with self._connection_lock:
            connection = self._connection
            if connection is not None and (expected is None or connection is expected):
                self._connection = None
                connection.close()

    def _release_mutex(self):
        if self._mutex is not None:
            kernel = _windows_library("kernel32")
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            kernel.CloseHandle(self._mutex)
            self._mutex = None

    def close(self):
        self._closed.set()
        self._close_connection()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        if self._listener is not None:
            import _winapi
            _winapi.CloseHandle(self._listener)
            self._listener = None
        self._release_mutex()
        self._owner = False

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def parent_process_details() -> dict:
    result = {"pid": os.getppid(), "executable": None}
    if sys.platform != "win32":
        return result
    kernel = _windows_library("kernel32")
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000, False, result["pid"])
    if not handle:
        return result
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            result["executable"] = buffer.value
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.CloseHandle(handle)
    return result


def append_activation_log(activation: Activation, *, package=None, fse_active=None,
                          outcome="received", path=None) -> Path | None:
    """Best-effort JSON-lines diagnostics; a full/read-only disk never blocks UI."""
    target = Path(path) if path else _runtime_root() / "logs" / "activations.jsonl"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Keep useful launch history bounded on machines that reactivate often.
        if target.exists() and target.stat().st_size > 2 * 1024 * 1024:
            target.replace(target.with_suffix(".previous.jsonl"))
        parent = ({"pid": activation.source_parent_pid,
                   "executable": activation.source_parent_executable}
                  if activation.source_pid is not None else parent_process_details())
        record = {"time_utc": datetime.now(timezone.utc).isoformat(),
                  "pid": activation.source_pid if activation.source_pid is not None else os.getpid(),
                  "receiver_pid": os.getpid(), "forwarded": activation.source_pid is not None,
                  "parent": parent, "argv": list(activation.argv),
                  "home": activation.home, "startup": activation.startup,
                  "fullscreen": activation.fullscreen, "package_full_name": package,
                  "fse_active": fse_active, "outcome": outcome}
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
        return target
    except Exception:
        # Diagnostics are deliberately isolated from the application lifecycle.
        return None
