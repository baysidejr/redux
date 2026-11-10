"""Read the three OEM app tiles supported by the installed Windows Xbox app.

This is separate from the gaming-home identity: an ordinary packaged Redux EXE
can be an Xbox My Apps tile without changing the selected home app. Modifications
are delegated to the isolated, elevated Add/Remove-XboxApp PowerShell helpers.
The read path and value names were verified in Microsoft's XboxPcAppFT.exe.
"""
from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os

try:
    import winreg as _registry
except ImportError:
    _registry = None

OEM_APPS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\GamingConfiguration\OEMApps"
SLOT_NAMES = tuple(f"OEMAppPathsAndName{index}" for index in range(1, 4))


def _same_executable(left: str, right: str) -> bool:
    return bool(left and ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(ntpath.normpath(right)))


@dataclass(frozen=True)
class XboxAppSlot:
    slot: str
    exists: bool = False
    name: str | None = None
    executable: str | None = None
    icon: str | None = None
    available: bool = False


@dataclass(frozen=True)
class XboxAppStatus:
    registered: bool = False
    slot: str | None = None
    free_slot: str | None = None
    available_slots: int = 0
    slots: tuple[XboxAppSlot, ...] = ()
    error: str | None = None


def xbox_app_status(executable: str | os.PathLike[str]) -> XboxAppStatus:
    """Report the matching tile and first unused slot without registry writes.

    Windows creates empty OEM slot keys by default. A missing key or a key with
    zero values and zero subkeys is free. Any populated key is preserved,
    including malformed or unrecognized settings.
    """
    if _registry is None:
        return XboxAppStatus(error="Xbox app integration is available on Windows only.")
    slots: list[XboxAppSlot] = []
    errors: list[str] = []
    for slot in SLOT_NAMES:
        try:
            with _registry.OpenKey(_registry.HKEY_LOCAL_MACHINE, OEM_APPS_KEY + "\\" + slot, 0,
                                   _registry.KEY_READ | _registry.KEY_WOW64_64KEY) as key:
                subkeys, values, _ = _registry.QueryInfoKey(key)
                def read(name):
                    try:
                        value, kind = _registry.QueryValueEx(key, name)
                        return value if kind == _registry.REG_SZ and isinstance(value, str) else None
                    except FileNotFoundError:
                        return None
                slots.append(XboxAppSlot(slot, True, read("OemAppName"),
                                         read("OemAppExePath"), read("OemAppIconPath"),
                                         subkeys == 0 and values == 0))
        except FileNotFoundError:
            slots.append(XboxAppSlot(slot, available=True))
        except OSError as error:
            errors.append(f"{slot}: {error}")
            slots.append(XboxAppSlot(slot, True))
    match = next((item for item in slots if _same_executable(item.executable or "", str(executable))), None)
    free = [item.slot for item in slots if item.available]
    return XboxAppStatus(bool(match), match.slot if match else None, free[0] if free else None,
                         len(free), tuple(slots), "; ".join(errors) or None)
