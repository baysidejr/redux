"""Menu sounds: four short cues that answer the controller before the screen redraws.

Playback is Windows' own sound API, asynchronous, so a cue never waits for
the frame. Nothing plays while a game runs, and the About page can switch the
cues off altogether.
"""
from __future__ import annotations

from pathlib import Path
import sys

CUES = ("move", "select", "back", "open")
MOVE_ACTIONS = frozenset({"left", "right", "up", "down"})
SELECT_ACTIONS = frozenset({"a", "select", "confirm"})
BACK_ACTIONS = frozenset({"b", "back"})


def cue_for_action(action):
    if action in MOVE_ACTIONS:
        return "move"
    if action in SELECT_ACTIONS:
        return "select"
    if action in BACK_ACTIONS:
        return "back"
    return None


def sounds_root():
    bundle = getattr(sys, "_MEIPASS", None)
    roots = [Path(bundle) / "assets" / "sounds", Path(bundle) / "redux" / "assets" / "sounds"] if bundle else []
    roots.append(Path(__file__).resolve().parent / "assets" / "sounds")
    for root in roots:
        if root.is_dir():
            return root
    return roots[-1]


class MenuSounds:
    def __init__(self, enabled=True, root=None, player=None):
        self.enabled = bool(enabled)
        self.root = Path(root) if root else sounds_root()
        self.paths = {cue: self.root / f"{cue}.wav" for cue in CUES}
        self.paths = {cue: path for cue, path in self.paths.items() if path.is_file()}
        self.player = player if player is not None else self._winsound_player()
        self.played = []

    @staticmethod
    def _winsound_player():
        try:
            import winsound
        except ImportError:
            return None
        flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        def play(path):
            winsound.PlaySound(str(path), flags)
        return play

    def available(self):
        return bool(self.paths) and self.player is not None

    def play(self, cue, *, silent=False):
        """Play a cue unless sounds are off, a game owns the audio, or the file is missing."""
        if not self.enabled or silent or cue not in self.paths or self.player is None:
            return False
        try:
            self.player(self.paths[cue])
        except (OSError, RuntimeError):
            return False
        self.played.append(cue)
        del self.played[:-8]
        return True
