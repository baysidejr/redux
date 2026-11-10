"""Resolve ordinary user data paths when invoked by a packaged launcher.

``KF_FLAG_NO_PACKAGE_REDIRECTION`` changes the known-folder query result. It
does not disable file/registry virtualization imposed by a process's package;
the owning package must also allow unvirtualized writes where those are needed.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import uuid


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


_LOCAL_APP_DATA = _GUID.from_buffer_copy(uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le)
KF_FLAG_NO_PACKAGE_REDIRECTION = 0x00010000
KF_FLAG_DONT_VERIFY = 0x00004000


def _known_local_app_data() -> Path:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    function = shell.SHGetKnownFolderPath
    function.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE,
                         ctypes.POINTER(ctypes.c_void_p)]
    function.restype = ctypes.c_int32  # HRESULT has a fixed 32-bit ABI.
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    pointer = ctypes.c_void_p()
    try:
        result = function(ctypes.byref(_LOCAL_APP_DATA),
                          KF_FLAG_NO_PACKAGE_REDIRECTION | KF_FLAG_DONT_VERIFY,
                          None, ctypes.byref(pointer))
        if result < 0:
            raise OSError(f"The Windows data folder could not be resolved (HRESULT 0x{result & 0xffffffff:08X}).")
        if not pointer.value:
            raise OSError("Windows did not return a user data folder.")
        value = ctypes.wstring_at(pointer.value)
        if not value:
            raise OSError("Windows returned an empty user data folder.")
        path = Path(value)
        if not path.is_absolute():
            raise OSError("Windows returned a relative user data folder.")
        return path
    finally:
        if pointer.value:
            ole.CoTaskMemFree(pointer)


def local_app_data() -> Path:
    """Return the user's ordinary LocalAppData, without creating any directories.

    Windows known folders are authoritative, including profiles whose data was
    moved by policy. The environment is the compatibility fallback if the API
    is unavailable; portable tools on other systems keep their existing path.
    """
    if sys.platform == "win32":
        try:
            return _known_local_app_data()
        except (AttributeError, OSError, ValueError):
            pass
    configured = os.environ.get("LOCALAPPDATA", "")
    return Path(configured) if configured.strip() else Path.home() / ".config"


def redux_user_data() -> Path:
    return local_app_data() / "ReduxMediaLibrary"


def windows_system_directory() -> Path:
    """Get the trusted OS binary directory without a PATH/current-directory search."""
    if sys.platform != 'win32':
        raise OSError('Windows integration is available on Windows only')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = kernel.GetSystemDirectoryW
    function.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    function.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError('Windows system directory could not be resolved')
    return Path(buffer.value)
