"""Process-private bundled typography; no font installation or registry writes.

Roboto Condensed is an independently obtained Google Fonts dependency under the
SIL Open Font License, not a recovered or identified vendor UI font. The Windows
GDI resource API returns a count rather than a font handle. Its DLL, filename
buffers and successful registrations are retained for the process lifetime.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import threading


DISPLAY_FAMILY = "Roboto Condensed"
FR_PRIVATE = 0x10
_FILES = ("RobotoCondensed[wght].ttf", "RobotoCondensed-Italic[wght].ttf")


@dataclass(frozen=True)
class _FontResource:
    path: Path
    filename_buffer: object
    registered_faces: int


_lock = threading.RLock()
_gdi = None
_resources: list[_FontResource] = []
_family: str | None = None
_failure: str | None = None


def font_directory() -> Path:
    """Find source assets or either conventional PyInstaller data destination.

    Recommended Analysis datas entry:
    ('redux/assets/fonts', 'assets/fonts').
    """
    source = Path(__file__).resolve().parent / "assets" / "fonts"
    bundle = getattr(sys, "_MEIPASS", None)
    candidates = ([Path(bundle) / "assets" / "fonts",
                   Path(bundle) / "redux" / "assets" / "fonts"]
                  if bundle else [])
    candidates.append(source)
    return next((path for path in candidates if (path / _FILES[0]).is_file()), candidates[0])


def _fallback() -> str:
    if os.name == "nt":
        windows = Path(os.environ.get("WINDIR", "C:/Windows"))
        if (windows / "Fonts" / "bahnschrift.ttf").is_file():
            return "Bahnschrift SemiCondensed"
    return "Segoe UI"


def register_fonts() -> str:
    """Register bundled TTFs for this process and return the display family.

    Call before creating Tk font objects. FR_PRIVATE keeps the fonts available
    only to this application; FR_NOT_ENUM is deliberately absent so Tk can find
    the family. Windows removes these private resources when the process exits.
    A missing/unsupported font cleanly falls back to an existing Windows family.
    Repeated calls do not add duplicate native registrations.
    """
    global _gdi, _family, _failure
    with _lock:
        if _family is not None:
            return _family
        _family = _fallback()
        if os.name != "nt":
            _failure = "Private GDI font registration is available on Windows."
            return _family
        try:
            _gdi = ctypes.WinDLL("gdi32", use_last_error=True)
            add = _gdi.AddFontResourceExW
            add.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
            add.restype = ctypes.c_int
            directory = font_directory()
            for filename in _FILES:
                path = directory / filename
                if not path.is_file():
                    if filename == _FILES[0]:
                        _failure = "The bundled regular typeface is unavailable."
                    continue
                buffer = ctypes.create_unicode_buffer(str(path.resolve()))
                count = int(add(buffer, FR_PRIVATE, None))
                if count > 0:
                    _resources.append(_FontResource(path.resolve(), buffer, count))
                    if filename == _FILES[0]:
                        _family = DISPLAY_FAMILY
                elif filename == _FILES[0]:
                    # The API explicitly does not provide extended error info.
                    _failure = "GDI did not accept the bundled regular typeface."
        except (OSError, AttributeError, TypeError) as error:
            _failure = str(error)
        return _family


def font_status() -> dict:
    """Return small diagnostics without opening a window or altering settings."""
    with _lock:
        return {"family": _family, "directory": str(font_directory()),
                "private": True, "registered_files": [str(item.path) for item in _resources],
                "registered_faces": sum(item.registered_faces for item in _resources),
                "failure": _failure}
