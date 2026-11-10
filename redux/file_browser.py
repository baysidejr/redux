"""Bounded, read-only folder listings for the controller-operated browser.

No Tk dialogs, recursive searches, drive media queries or filesystem mutations
occur here. Native Windows discovery enumerates assigned logical drive letters,
including fixed/removable/network/optical drives; opening a location is separate.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from .library import MEDIA_EXTENSIONS, _markdown_document


MAX_DIRECTORY_ENTRIES = 10000
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)


@dataclass(frozen=True, slots=True)
class BrowserEntry:
    path: Path
    label: str
    is_directory: bool
    size: int | None = None
    kind: str = "folder"

    @property
    def name(self):
        return self.label

    @property
    def is_dir(self):
        return self.is_directory


@dataclass(frozen=True, slots=True)
class BrowserListing:
    path: Path
    entries: tuple[BrowserEntry, ...]
    parent: Path | None = None
    error: str | None = None
    truncated: bool = False


def _absolute(path):
    # Normalize spelling without silently following junctions or symlinks.
    return Path(os.path.abspath(Path(path).expanduser()))


def _drives_from_mask(mask):
    return tuple(Path(chr(ord("A") + number) + ":\\") for number in range(26) if mask & (1 << number))


def _logical_drives():
    if os.name != "nt":
        return (Path("/"),)
    import ctypes
    # GetLogicalDrives is a bitmask only: do not open volumes, request labels,
    # spin optical media or probe each removable/network drive for availability.
    # https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-getlogicaldrives
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetLogicalDrives
    function.argtypes = []
    function.restype = ctypes.c_uint32
    mask = function()
    return _drives_from_mask(mask)


def _plain_directory(path):
    try:
        value = path.lstat()
        return stat.S_ISDIR(value.st_mode) and not (stat.S_ISLNK(value.st_mode) or
                getattr(value, "st_file_attributes", 0) & (_REPARSE | _HIDDEN | _SYSTEM))
    except OSError:
        return False


def list_locations(library_root, project_root):
    """Return explicit roots and logical drives without scanning their contents.

    Application, library and home remain selectable even if currently missing;
    list_directory reports the resulting error in-app. Only the three named
    optional home folders receive bounded existence checks, never a home scan.
    Duplicate paths are omitted. No volume or media readiness query is issued.
    """
    result = []
    seen = set()

    def add(path, label, kind="location"):
        absolute = _absolute(path)
        key = os.path.normcase(str(absolute))
        if key not in seen:
            seen.add(key)
            result.append(BrowserEntry(absolute, label, True, kind=kind))

    add(project_root, "Application")
    add(library_root, "Game library")
    try:
        home = _absolute(Path.home())
    except (OSError, RuntimeError):
        home = None
    if home is not None:
        for name in ("Downloads", "Documents", "Videos"):
            folder = home / name
            if _plain_directory(folder):
                add(folder, name)
        add(home, "Home")
    try:
        drives = _logical_drives()
    except (OSError, AttributeError):
        drives = ()
    for drive in drives:
        add(drive, f"{drive.drive} Drive" if drive.drive else "File system", "drive")
    return tuple(result)


def _extensions(values):
    if values is None:
        return frozenset(MEDIA_EXTENSIONS)
    if isinstance(values, str):
        values = (values,)
    result = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("Browser extensions must be text")
        value = value.strip().casefold()
        if value.startswith("*."):
            value = value[1:]
        if value and not value.startswith("."):
            value = "." + value
        if not value or any(character in value for character in ("/", "\\", ":", "*", "?")):
            raise ValueError("Browser extensions must be simple file suffixes")
        result.add(value)
    return frozenset(result)


def list_directory(path, extensions=None, folders_only=False):
    """List one folder with supported files, folders first and stable ordering.

    At most 10,000 directory entries receive metadata checks; one extra name may
    be observed to flag truncation. Hidden/dot entries, symlinks, junctions and
    other reparse entries are omitted. Explicit reparse roots are refused rather
    than silently changing location. This service never descends recursively.

    Removed drives, unavailable folders and permission errors are returned in
    ``listing.error`` for display, with no fatal filesystem exception. Children
    disappearing or becoming inaccessible during enumeration are simply skipped.
    ``extensions=None`` means supported media; an empty set means folders only.
    """
    root = _absolute(path)
    parent = root.parent if root.parent != root else None
    allowed = _extensions(extensions)
    entries = []
    truncated = False
    try:
        root_info = root.lstat()
        if stat.S_ISLNK(root_info.st_mode) or getattr(root_info, "st_file_attributes", 0) & _REPARSE:
            return BrowserListing(root, (), parent, "This location is a link or junction. Choose its actual folder.")
        with os.scandir(root) as directory:
            for number, entry in enumerate(directory):
                if number >= MAX_DIRECTORY_ENTRIES:
                    truncated = True
                    break
                if entry.name.startswith("."):
                    continue
                try:
                    value = entry.stat(follow_symlinks=False)
                    attributes = getattr(value, "st_file_attributes", 0)
                    if stat.S_ISLNK(value.st_mode) or attributes & (_REPARSE | _HIDDEN | _SYSTEM):
                        continue
                    directory_entry = stat.S_ISDIR(value.st_mode)
                    if directory_entry:
                        entries.append(BrowserEntry(root / entry.name, entry.name, True))
                    elif (not folders_only and stat.S_ISREG(value.st_mode) and
                          Path(entry.name).suffix.casefold() in allowed):
                        if extensions is None and _markdown_document(root / entry.name):
                            continue
                        entries.append(BrowserEntry(root / entry.name, entry.name, False, value.st_size, "file"))
                except OSError:
                    continue
    except OSError as error:
        message = error.strerror or str(error) or type(error).__name__
        return BrowserListing(root, (), parent, "Cannot open this folder: " + message)
    entries.sort(key=lambda entry: (not entry.is_directory, entry.label.casefold(), entry.label))
    return BrowserListing(root, tuple(entries), parent, truncated=truncated)
