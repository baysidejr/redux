"""Reversible, per-user Windows startup registration for the living-room UI.

Only values owned by Redux are changed. No service, scheduled task, machine-wide
registry key, Developer Mode setting, or Xbox home selection is involved.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

try:
    import winreg as _registry
except ImportError:  # Importable by tests and portable tooling on other systems.
    _registry = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
GAMING_STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\GamingConfiguration\Startup\Run"
VALUE_NAME = "ReduxMediaLibrary"


@dataclass(frozen=True)
class StartupStatus:
    enabled: bool = False
    command: str | None = None
    expected_command: str | None = None
    disabled_by_windows: bool = False
    with_xbox_mode: bool = False
    error: str | None = None

    @property
    def needs_update(self) -> bool:
        return bool(self.command and self.expected_command and self.command != self.expected_command)


def startup_command(executable: str | os.PathLike[str]) -> str:
    """Produce a Windows-quoted command independent of the logon working folder."""
    path = Path(executable)
    if not path.is_absolute():
        raise ValueError("The startup application must have an absolute path.")
    if path.suffix.lower() != ".exe":
        raise ValueError("Choose the packaged Redux application for Windows startup.")
    command = subprocess.list2cmdline([str(path), "--startup", "--fullscreen"])
    if len(command) > 260:
        raise ValueError("The application path is too long for Windows startup. Move Redux to a shorter path.")
    return command


def _read(path: str, name: str):
    with _registry.OpenKey(_registry.HKEY_CURRENT_USER, path, 0, _registry.KEY_READ) as key:
        return _registry.QueryValueEx(key, name)


def startup_status(executable: str | os.PathLike[str] | None = None) -> StartupStatus:
    expected = startup_command(executable) if executable is not None else None
    if _registry is None:
        return StartupStatus(expected_command=expected, error="Windows startup is available on Windows only.")
    try:
        try:
            value, kind = _read(RUN_KEY, VALUE_NAME)
            command = value if kind == _registry.REG_SZ and isinstance(value, str) and value else None
        except FileNotFoundError:
            command = None
        try:
            approval, kind = _read(STARTUP_APPROVED_KEY, VALUE_NAME)
            # Settings sets/clears bit 0 of the little-endian status DWORD.
            disabled = bool(kind == _registry.REG_BINARY and isinstance(approval, bytes)
                            and len(approval) >= 4 and int.from_bytes(approval[:4], "little") & 1)
        except FileNotFoundError:
            disabled = False
        try:
            mode, kind = _read(GAMING_STARTUP_KEY, VALUE_NAME)
            alongside = kind == _registry.REG_DWORD and mode == 1
        except FileNotFoundError:
            alongside = False
        return StartupStatus(bool(command), command, expected, disabled, alongside)
    except OSError as error:
        return StartupStatus(expected_command=expected, error=str(error))


def _delete_owned_value(path: str) -> None:
    try:
        with _registry.OpenKey(_registry.HKEY_CURRENT_USER, path, 0, _registry.KEY_SET_VALUE) as key:
            try:
                _registry.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def set_start_with_windows(enabled: bool, executable: str | os.PathLike[str], *,
                           with_xbox_mode: bool = True) -> StartupStatus:
    """Change only this user's Redux entry; failures propagate for in-app feedback.

    An explicit enable also clears a previous Windows Startup Apps disable flag
    for this same entry, so the controller toggle has the result it promises.
    """
    if _registry is None:
        raise OSError("Windows startup is available on Windows only.")
    command = startup_command(executable)
    if enabled:
        if not Path(executable).is_file():
            raise FileNotFoundError("The packaged Redux application could not be found.")
        with _registry.CreateKeyEx(_registry.HKEY_CURRENT_USER, RUN_KEY, 0, _registry.KEY_SET_VALUE) as key:
            _registry.SetValueEx(key, VALUE_NAME, 0, _registry.REG_SZ, command)
        # Windows' Xbox mode Startup Apps policy: 1 means Start at log in.
        # This is isolated to Redux; it never changes the selected home app.
        with _registry.CreateKeyEx(_registry.HKEY_CURRENT_USER, GAMING_STARTUP_KEY, 0,
                                   _registry.KEY_SET_VALUE) as key:
            _registry.SetValueEx(key, VALUE_NAME, 0, _registry.REG_DWORD, 1 if with_xbox_mode else 0)
        _delete_owned_value(STARTUP_APPROVED_KEY)
    else:
        for path in (RUN_KEY, GAMING_STARTUP_KEY, STARTUP_APPROVED_KEY):
            _delete_owned_value(path)
    status = startup_status(executable)
    if status.error:
        raise OSError(status.error)
    if status.enabled != bool(enabled):
        raise OSError("Windows did not retain the requested Redux startup setting.")
    return status
