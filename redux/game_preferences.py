"""Portable user choices for one game bundle, independent of catalogue metadata."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import uuid


_MAX_BYTES = 64 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _rating(value):
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5):
        raise ValueError("Rating must be an integer from 1 to 5, or None to clear it")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Game preferences contain a duplicate field")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Game preferences contain a non-finite JSON value")


def _is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _check_chain(path):
    """Do not silently canonicalise an existing symlink/junction in a path."""
    for part in (*reversed(path.parents), path):
        if _is_link(part):
            raise ValueError("Game preferences cannot use symlinks or junctions")


class GamePreferences:
    """The rating and future local choices for an ID plus immutable bundle hash.

    Reading does not create a file. Explicitly setting ``None`` stores an unrated
    choice. Existing unrelated JSON fields survive an update unchanged in value.
    An invalid file is reported instead of being silently discarded or replaced.
    """

    def __init__(self, library_root, game_id, bundle_hash):
        if isinstance(game_id, bool) or not isinstance(game_id, int) or game_id < 1:
            raise ValueError("Game ID must be a positive integer")
        if not isinstance(bundle_hash, str) or not _HASH.fullmatch(bundle_hash):
            raise ValueError("Game identity must be a lowercase SHA-256 bundle hash")
        original = Path(library_root).expanduser().absolute()
        _check_chain(original)
        self.root = original.resolve()
        self.game_id = game_id
        self.bundle_hash = bundle_hash
        self.directory = self.root / "game_data" / str(game_id) / bundle_hash
        self.path = self.directory / "preferences.json"
        self._lock = threading.RLock()
        self._guard()

    def _guard(self, extra=None):
        _check_chain(self.root)
        for candidate in (self.directory, self.path, extra):
            if candidate is None:
                continue
            _check_chain(candidate)
            if not candidate.resolve().is_relative_to(self.root):
                raise ValueError("Game preferences resolve outside the library")
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("The library root is not a directory")
        if self.directory.exists() and not self.directory.is_dir():
            raise ValueError("The game preferences directory is not a directory")
        if self.path.exists() and not self.path.is_file():
            raise ValueError("Game preferences are not a regular file")

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
            raise ValueError("Game preferences exceed the 64 KiB size limit")
        return data

    @staticmethod
    def _decode(data):
        if data is None:
            return {}
        try:
            result = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (UnicodeError, RecursionError) as error:
            raise ValueError("Game preferences are not valid bounded JSON") from error
        if not isinstance(result, dict):
            raise ValueError("Game preferences must be a JSON object")
        return result

    def rating(self):
        with self._lock:
            return _rating(self._decode(self._read_bytes()).get("rating"))

    def set_rating(self, value):
        value = _rating(value)
        self._set("rating",value)

    def video_filter(self):
        from .video_filters import filter_name
        with self._lock:
            value=self._decode(self._read_bytes()).get("video_filter","sharp")
            filter_name(value)
            return value

    def set_video_filter(self,value):
        from .video_filters import filter_name
        filter_name(value)
        self._set("video_filter",value)

    def _set(self,key,value):
        with self._lock:
            original = self._read_bytes()
            document = self._decode(original)
            document[key] = value
            try:
                data = (json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
            except (UnicodeError, RecursionError) as error:
                raise ValueError("Game preferences cannot be encoded as bounded JSON") from error
            if len(data) > _MAX_BYTES:
                raise ValueError("Updated game preferences exceed the 64 KiB size limit")
            self._guard()
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / (".preferences-" + uuid.uuid4().hex + ".tmp")
            self._guard(temporary)
            try:
                with temporary.open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                self._guard(temporary)
                if self._read_bytes() != original:
                    raise RuntimeError("Game preferences changed during this update; try again")
                os.replace(temporary, self.path)
                self._guard()
            finally:
                temporary.unlink(missing_ok=True)
