"""Windows Xbox-mode information and ABI-checked gaming experience calls.

Signatures are taken from the installed Microsoft Windows SDK
``10.0.26100.0/um/gamingexperience.h``. The API set is optional: older Windows
builds keep working without it. Registry queries never alter Windows settings.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

try:
    import winreg as _registry
except ImportError:
    _registry = None

GAMING_CONFIGURATION_KEY = r"Software\Microsoft\Windows\CurrentVersion\GamingConfiguration"
DEVICE_FORM_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\OEM"
DEVELOPER_MODE_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock"
XBOX_HOME_AUMID = "Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox.App"


@dataclass(frozen=True)
class GamingConfiguration:
    device_form: int | None = None
    developer_mode: bool = False
    home_app: str | None = None
    startup_to_gaming_home: bool = False
    error: str | None = None

    @property
    def handheld(self) -> bool:
        return self.device_form == 46


def gaming_configuration() -> GamingConfiguration:
    if _registry is None:
        return GamingConfiguration(error="Xbox mode is available on Windows only.")
    errors: list[str] = []

    def read(hive, path, name, kind, default=None):
        try:
            with _registry.OpenKey(hive, path, 0, _registry.KEY_READ | _registry.KEY_WOW64_64KEY) as key:
                value, actual = _registry.QueryValueEx(key, name)
            return value if actual == kind else default
        except FileNotFoundError:
            return default
        except OSError as error:
            errors.append(str(error))
            return default

    machine, user = _registry.HKEY_LOCAL_MACHINE, _registry.HKEY_CURRENT_USER
    form = read(machine, DEVICE_FORM_KEY, "DeviceForm", _registry.REG_DWORD)
    developer = read(machine, DEVELOPER_MODE_KEY, "AllowDevelopmentWithoutDevLicense", _registry.REG_DWORD, 0)
    home = read(user, GAMING_CONFIGURATION_KEY, "GamingHomeApp", _registry.REG_SZ)
    startup = read(user, GAMING_CONFIGURATION_KEY, "StartupToGamingHome", _registry.REG_DWORD, 0)
    return GamingConfiguration(form, developer == 1, home, startup == 1, "; ".join(errors) or None)


def _package_string(function_name: str) -> str | None:
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        function = getattr(kernel, function_name)
        function.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
        function.restype = wintypes.LONG
        length = wintypes.UINT(0)
        # ERROR_INSUFFICIENT_BUFFER is the documented length-query response.
        if function(ctypes.byref(length), None) != 122 or not 0 < length.value <= 32768:
            return None
        result = ctypes.create_unicode_buffer(length.value)
        return result.value if function(ctypes.byref(length), result) == 0 else None
    except (OSError, AttributeError):
        return None


def current_package_full_name() -> str | None:
    return _package_string("GetCurrentPackageFullName")


def current_package_family_name() -> str | None:
    return _package_string("GetCurrentPackageFamilyName")


@dataclass(frozen=True)
class GamingExperienceState:
    available: bool = False
    supported: bool = False
    active: bool = False
    can_set: bool = False
    error: str | None = None


class GamingExperienceRegistration:
    """Keeps the native callback alive until Windows has unregistered it."""
    def __init__(self, owner, callback, handle):
        self._owner = owner
        self._callback = callback
        self._handle = handle

    def close(self) -> None:
        if self._handle is not None:
            self._owner._dll.UnregisterGamingFullScreenExperienceChangeNotification(self._handle)
            self._handle = None
            self._callback = None
            self._owner._registrations.discard(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class WindowsGamingExperience:
    def __init__(self):
        self._dll = None
        self._error = None
        self._registrations: set[GamingExperienceRegistration] = set()
        try:
            dll = ctypes.WinDLL("api-ms-win-gaming-experience-l1-1-0.dll", use_last_error=True)
            for name in ("IsGamingFullScreenExperienceSupported", "IsGamingFullScreenExperienceActive",
                         "CanSetGamingFullScreenExperience"):
                function = getattr(dll, name)
                function.argtypes = []
                function.restype = wintypes.BOOL
            dll.SetGamingFullScreenExperience.argtypes = [wintypes.BOOL]
            dll.SetGamingFullScreenExperience.restype = ctypes.c_int32  # HRESULT is always 32 bits.
            self._callback_type = ctypes.WINFUNCTYPE(None, ctypes.c_void_p)
            dll.RegisterGamingFullScreenExperienceChangeNotification.argtypes = [
                self._callback_type, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            dll.RegisterGamingFullScreenExperienceChangeNotification.restype = ctypes.c_int32
            dll.UnregisterGamingFullScreenExperienceChangeNotification.argtypes = [ctypes.c_void_p]
            dll.UnregisterGamingFullScreenExperienceChangeNotification.restype = None
            self._dll = dll
        except (OSError, AttributeError) as error:
            self._error = str(error)

    def query(self) -> GamingExperienceState:
        if self._dll is None:
            return GamingExperienceState(error=self._error)
        try:
            return GamingExperienceState(True, bool(self._dll.IsGamingFullScreenExperienceSupported()),
                                         bool(self._dll.IsGamingFullScreenExperienceActive()),
                                         bool(self._dll.CanSetGamingFullScreenExperience()))
        except OSError as error:
            return GamingExperienceState(error=str(error))

    def set_active(self, active: bool) -> GamingExperienceState:
        state = self.query()
        if not state.available or not state.supported:
            raise OSError("Xbox mode is not available on this Windows installation.")
        if not state.can_set:
            raise OSError("Windows cannot switch Xbox mode right now. Check Windows Gaming settings.")
        result = self._dll.SetGamingFullScreenExperience(bool(active))
        if result < 0:
            raise OSError(f"Windows could not switch Xbox mode (HRESULT 0x{result & 0xffffffff:08X}).")
        return self.query()

    def subscribe(self, callback: Callable[[GamingExperienceState], None]) -> GamingExperienceRegistration | None:
        """Notify outside the UI thread; callers must queue work for their UI loop."""
        if self._dll is None:
            return None

        def changed(_context):
            try:
                callback(self.query())
            except Exception:
                # Never allow a Python exception to cross a Windows callback ABI.
                pass

        native_callback = self._callback_type(changed)
        handle = ctypes.c_void_p()
        result = self._dll.RegisterGamingFullScreenExperienceChangeNotification(
            native_callback, None, ctypes.byref(handle))
        if result < 0:
            raise OSError(f"Xbox mode notifications failed (HRESULT 0x{result & 0xffffffff:08X}).")
        if not handle.value:
            raise OSError("Windows did not return an Xbox mode notification registration.")
        registration = GamingExperienceRegistration(self, native_callback, handle)
        self._registrations.add(registration)
        return registration

    def close(self) -> None:
        for registration in tuple(self._registrations):
            registration.close()
