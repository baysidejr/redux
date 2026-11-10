"""Resolve the selected firmware folder without searching or changing files."""
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path


def firmware_directory(preferences: Mapping | None, project_root: str | os.PathLike[str]) -> Path:
    """Return the explicit firmware folder, or ``project_root / 'bios'``.

    Relative custom folders are anchored to the supplied project root, so the
    result does not depend on the application's working directory. An explicit
    ``~`` is expanded; no personal folders, drive roots or emulator folders are
    searched. This function does not create, inspect or copy firmware files and
    does not modify preferences. Folder creation belongs to application startup.

    Paths are normalized lexically, leaving symlink resolution and existence
    checks to the eventual reader. An unavailable custom folder remains selected
    rather than silently falling back to a different firmware location.
    """
    if preferences is not None and not isinstance(preferences, Mapping):
        raise TypeError("Firmware preferences must be a mapping or None")
    root = Path(os.path.abspath(Path(project_root).expanduser()))
    configured = preferences.get("firmware_dir") if preferences is not None else None
    if configured is None:
        return root / "bios"
    if not isinstance(configured, (str, os.PathLike)):
        raise TypeError("The configured firmware directory must be a path")
    value = os.fspath(configured)
    if not isinstance(value, str):
        raise TypeError("The configured firmware directory must be a text path")
    value = value.strip()
    if not value:
        return root / "bios"
    selected = Path(value).expanduser()
    if not selected.is_absolute():
        selected = root / selected
    return Path(os.path.abspath(selected))
