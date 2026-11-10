"""Portable library location, with an explicit external storage override."""
from pathlib import Path
import os


def library_directory(preferences,project_root,override=None):
    root=Path(project_root).expanduser().absolute()
    selected=override if override is not None else preferences.get("library_root")
    if selected is None or not str(selected).strip():return root/"library"
    folder=Path(selected).expanduser()
    return Path(os.path.abspath(folder if folder.is_absolute() else root/folder))


def library_setting(directory,project_root):
    folder=Path(directory).expanduser().resolve()
    root=Path(project_root).expanduser().resolve()
    return folder.relative_to(root).as_posix() if folder.is_relative_to(root) else str(folder)
