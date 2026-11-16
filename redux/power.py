"""Restart or shut down Windows from the Exit menu, after Redux has closed its own work.

shutdown.exe runs with /t 0 and without /f: Windows still asks any app with unsaved work,
exactly as when the Start menu's power button is used. (/t above 0 would imply /f.)
"""
from __future__ import annotations

import subprocess

ACTIONS = {
    "restart": ("Restart this PC?", "Restart now", "/r"),
    "shutdown": ("Shut down this PC?", "Shut down now", "/s"),
}


def command(action):
    from .windows_paths import windows_system_directory
    return [str(windows_system_directory() / "shutdown.exe"), ACTIONS[action][2], "/t", "0"]


def power_off(action, run=subprocess.run):
    """Ask Windows to restart or shut down; raise OSError if Windows refuses."""
    result = run(command(action), capture_output=True, text=True, encoding="utf-8", errors="replace",
                 timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise OSError((result.stderr or result.stdout or "").strip()[-600:] or f"shutdown.exe returned {result.returncode}")
