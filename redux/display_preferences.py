"""Portable per-system display choices, with bounded transactional JSON updates."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import re
import threading
import uuid

from .game_preferences import _check_chain, _unique_object, _invalid_constant
from .video_filters import DISPLAY_FILTERS

_MAX_BYTES = 128 * 1024
_SYSTEM = re.compile(r"[a-z0-9][a-z0-9_]{0,47}\Z")
_SHAPES = frozenset({"auto", "4:3", "pixel", "stretch"})
_COMPOSITE_DEFAULTS = frozenset({"saturn", "sega_cd", "32x_cd"})


@dataclass(frozen=True)
class DisplayProfile:
    filter_id: str = "scanlines"
    scanlines: int = 40
    shape: str = "auto"
    zoom: int = 100
    offset_x: int = 0
    offset_y: int = 0
    brightness: int = 100

    @property
    def scanline_strength(self):
        return self.scanlines


def _system(value):
    if not isinstance(value, str) or not _SYSTEM.fullmatch(value):
        raise ValueError("Display settings require a valid system identifier")
    return value


def default_profile(system):
    return DisplayProfile(filter_id="composite" if _system(system) in _COMPOSITE_DEFAULTS else "scanlines")


def _validate(profile):
    if not isinstance(profile.filter_id, str) or profile.filter_id not in DISPLAY_FILTERS:
        raise ValueError("Unknown display filter")
    if not isinstance(profile.shape, str) or profile.shape not in _SHAPES:
        raise ValueError("Display shape must be auto, 4:3, pixel, or stretch")
    for name, low, high in (("scanlines", 0, 100), ("zoom", 50, 150),
                           ("offset_x", -200, 200), ("offset_y", -200, 200), ("brightness", 25, 200)):
        value = getattr(profile, name)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"Display {name} must be an integer from {low} to {high}")
    if profile.zoom % 5:
        raise ValueError("Display zoom must use 5 percent steps")
    return profile


_FIELDS = frozenset(DisplayProfile.__dataclass_fields__)


class DisplayPreferences:
    """One settings file moves with the library; games never own these choices.

    Reads do not create files. A write merges only the selected system and
    compares the source bytes immediately before atomic publication. Invalid
    files or observed external edits are reported without replacing them.
    """
    def __init__(self, library_root):
        original = Path(library_root).expanduser().absolute()
        _check_chain(original)
        self.root = original.resolve()
        self.directory = self.root / "settings"
        self.path = self.directory / "display.json"
        self._lock = threading.RLock()
        self._guard()

    def _guard(self, extra=None):
        _check_chain(self.root)
        for candidate in (self.directory, self.path, extra):
            if candidate is None:
                continue
            _check_chain(candidate)
            if not candidate.resolve().is_relative_to(self.root):
                raise ValueError("Display settings resolve outside the library")
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("The library root is not a directory")
        if self.directory.exists() and not self.directory.is_dir():
            raise ValueError("Display settings are not in a directory")
        if self.path.exists() and not self.path.is_file():
            raise ValueError("Display settings are not a regular file")

    def _read_bytes(self):
        self._guard()
        try:
            with self.path.open("rb") as source:
                data = source.read(_MAX_BYTES + 1)
        except FileNotFoundError:
            self._guard()
            return None
        self._guard()
        if len(data) > _MAX_BYTES:
            raise ValueError("Display settings exceed the 128 KiB size limit")
        return data

    @staticmethod
    def _decode(data):
        if data is None:
            return {"version": 1, "systems": {}}
        try:
            document = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (UnicodeError, RecursionError) as error:
            raise ValueError("Display settings are not valid bounded JSON") from error
        if (not isinstance(document, dict) or set(document) != {"version", "systems"}
                or type(document.get("version")) is not int or document["version"] != 1
                or not isinstance(document.get("systems"), dict) or len(document["systems"]) > 256):
            raise ValueError("Display settings have an unsupported schema")
        for system, values in document["systems"].items():
            _system(system)
            if not isinstance(values, dict) or set(values) - _FIELDS:
                raise ValueError("Display settings contain invalid system fields")
            _validate(replace(default_profile(system), **values))
        return document

    def get(self, system):
        system = _system(system)
        with self._lock:
            values = self._decode(self._read_bytes())["systems"].get(system, {})
            return _validate(replace(default_profile(system), **values))

    def set(self, system, **changes):
        system = _system(system)
        if set(changes) - _FIELDS:
            raise ValueError("Unknown display setting")
        return self._update(system, changes)

    def reset(self, system):
        return self._update(_system(system), None)

    def _update(self, system, changes):
        with self._lock:
            original = self._read_bytes()
            document = self._decode(original)
            systems = document["systems"]
            if changes is None:
                if system not in systems:
                    return default_profile(system)
                systems.pop(system)
                profile = default_profile(system)
            else:
                current = _validate(replace(default_profile(system), **systems.get(system, {})))
                profile = _validate(replace(current, **changes))
                if not changes or profile == current and system in systems:
                    return profile
                systems[system] = asdict(profile)
                if len(systems) > 256:
                    raise ValueError("Display settings contain too many systems")
            try:
                data = (json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
            except (UnicodeError, RecursionError) as error:
                raise ValueError("Display settings cannot be encoded as bounded JSON") from error
            if len(data) > _MAX_BYTES:
                raise ValueError("Updated display settings exceed the size limit")
            self._guard()
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / (".display-" + uuid.uuid4().hex + ".tmp")
            self._guard(temporary)
            try:
                with temporary.open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                self._guard(temporary)
                if self._read_bytes() != original:
                    raise RuntimeError("Display settings changed during this update; try again")
                os.replace(temporary, self.path)
                self._guard()
            finally:
                temporary.unlink(missing_ok=True)
            return profile
