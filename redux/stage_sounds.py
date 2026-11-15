"""Sounds for the loading stage: a disc tray opening and spinning up for CD consoles, a cartridge going in for the rest.

Menu sounds go through winsound, which plays one WAV at a time and cuts the previous one
off. The stage sound plays through Windows' media control interface (MCI) instead, which
decodes MP3 itself and mixes alongside winsound, so a menu click never cuts the disc off.
Every call is short and made on the UI thread; nothing here blocks.
"""
from __future__ import annotations

import itertools
from pathlib import Path

from .ui_sounds import sounds_root

DISC_LOAD = "disc-load.mp3"
CARTRIDGE_LOAD = "cartridge-load.mp3"
_aliases = itertools.count(1)


def _mci():
    try:
        import ctypes
        return ctypes.windll.winmm.mciSendStringW
    except (ImportError, AttributeError, OSError):
        return None


class StageSounds:
    """One stage sound at a time; ``play`` replaces whatever was playing."""

    def __init__(self, root=None, sender=None):
        self.root = Path(root) if root else sounds_root()
        # None picks Windows MCI; False means no audio device (tests, other platforms).
        self.send = _mci() if sender is None else (sender or None)
        self.alias = None
        self.played = []

    def path(self, name):
        candidate = self.root / name
        return candidate if candidate.is_file() else None

    def _command(self, text):
        if self.send is None:
            return 1
        try:
            return self.send(text, None, 0, None)
        except (OSError, ValueError, TypeError):
            return 1

    def play(self, name=DISC_LOAD):
        """Start a sound from the beginning. False when it is missing or Windows cannot play it."""
        path = self.path(name)
        if path is None or self.send is None:
            return False
        self.stop()
        alias = f"reduxstage{next(_aliases)}"
        if self._command(f'open "{path}" type mpegvideo alias {alias}') != 0:
            return False
        if self._command(f"play {alias}") != 0:
            self._command(f"close {alias}")
            return False
        self.alias = alias
        self.played.append(name)
        del self.played[:-8]
        return True

    def stop(self):
        if self.alias:
            self._command(f"stop {self.alias}")
            self._command(f"close {self.alias}")
            self.alias = None
