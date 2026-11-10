"""Transient disc playback leases; no permanent library import is performed.

Discoverable mounted images are played from their original bundle. Supported
physical CDs use a verified BIN/CUE in a unique temporary *disk* directory. The
caller must retain the lease until the emulator has closed the media, and then
call ``close()``. This service does not implement a RAM disk or a full-disc /
subchannel-preserving dump; the raw acquisition service's limits still apply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable
import uuid

from .disc_identity import DiscIdentity, identify_drive
from .library import inspect_bundle
from .mounted_images import mounted_image_source
from .optical import OpticalError, normalize_drive
from .raw_acquisition import capture_raw_cd


_PREFIX = ".redux-disc-buffer-"
_MARKER = ".redux-disc-buffer.json"
_CAPTURE_FILES = frozenset({"disc.bin", "disc.cue", "manifest.json"})
Progress = Callable[[int, int, str], None]


class DiscBufferError(OpticalError):
    """A transient image could not safely be prepared or validated."""


class DiscBufferCancelled(DiscBufferError):
    def __init__(self):
        super().__init__("Disc playback preparation cancelled.", reason="cancelled")


def _cancel(event):
    if event is not None and event.is_set():
        raise DiscBufferCancelled()


def _error(message, reason="invalid_buffer"):
    return DiscBufferError(message, reason=reason)


def _plain_chain(path: Path):
    """Refuse symlinks and Windows reparse points before following a path."""
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise _error("Disc playback storage cannot use symlinks, junctions or reparse points.", "unsafe_path")


def _absolute(path):
    return Path(os.path.abspath(Path(path).expanduser()))


def _source_path(identity: DiscIdentity, drive: str):
    """The identifier already resolves sources; re-query to detect remounts."""
    discovered = mounted_image_source(drive)
    declared = identity.source_image
    if declared is not None:
        declared = _absolute(declared)
        _plain_chain(declared)
        declared = declared.resolve(strict=True)
    if discovered is not None:
        discovered = _absolute(discovered)
        _plain_chain(discovered)
        discovered = discovered.resolve(strict=True)
    if declared != discovered:
        raise _error("The mounted image changed while preparing playback.", "media_changed")
    if identity.is_virtual and declared is None:
        raise _error("This mounted drive does not expose a playable original image.", "unsupported_mounted_source")
    return declared


def _bundle_snapshot(source):
    _plain_chain(source)
    bundle = inspect_bundle(source)
    snapshot = []
    for path in bundle.files:
        _plain_chain(path)
        value = path.stat()
        snapshot.append((path, value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns))
    return bundle, tuple(snapshot)


def _same_disc(before, after):
    if (not before.fingerprint or before.fingerprint != after.fingerprint or
            before.drive != after.drive):
        raise _error("The disc changed while preparing playback. Try again with the current disc.", "media_changed")


@dataclass
class DiscBuffer:
    """Media lease kept alive until playback ends.

    ``entry_path`` (also ``path``) is the media path given to the core. A mounted
    source is never removed. ``close()`` returns False and leaves the directory
    for review when ownership or its exact contents cannot be established.
    Emulator saves must be stored outside this directory.
    """
    entry_path: Path
    identity: DiscIdentity
    storage: str
    _directory: Path | None = field(default=None, repr=False)
    _cache_root: Path | None = field(default=None, repr=False)
    _marker_bytes: bytes | None = field(default=None, repr=False)
    closed: bool = field(default=False, init=False)
    cleanup_error: str | None = field(default=None, init=False)

    @property
    def path(self):
        return self.entry_path

    @property
    def temporary(self):
        return self.storage == "temporary_disk"

    @property
    def directory(self):
        return self._directory

    def _owned_contents(self):
        root, cache = self._directory, self._cache_root
        if root is None or cache is None or self._marker_bytes is None:
            raise _error("Disc buffer ownership is unavailable.", "unsafe_cleanup")
        _plain_chain(root)
        if (root.parent != cache or root.resolve() != root or cache.resolve() != cache or
                not re.fullmatch(re.escape(_PREFIX) + r"[0-9a-f]{32}", root.name)):
            raise _error("Disc buffer storage moved outside its owned location.", "unsafe_cleanup")
        if not root.exists():
            return ()
        if not root.is_dir():
            raise _error("Disc buffer storage is no longer a directory.", "unsafe_cleanup")
        marker = root / _MARKER
        _plain_chain(marker)
        if (not marker.is_file() or marker.stat().st_size != len(self._marker_bytes) or
                marker.read_bytes() != self._marker_bytes):
            raise _error("Disc buffer ownership marker changed; files were retained.", "unsafe_cleanup")
        entries = {item.name: item for item in root.iterdir()}
        if set(entries) - {_MARKER, "capture"}:
            raise _error("Disc buffer contains unowned files; files were retained.", "unsafe_cleanup")
        files = []
        capture = root / "capture"
        if "capture" in entries:
            _plain_chain(capture)
            if not capture.is_dir():
                raise _error("Disc buffer capture is no longer a directory.", "unsafe_cleanup")
            children = list(capture.iterdir())
            if any(child.name not in _CAPTURE_FILES for child in children):
                raise _error("Disc buffer contains unowned files; files were retained.", "unsafe_cleanup")
            for child in children:
                _plain_chain(child)
                if not child.is_file():
                    raise _error("Disc buffer contains an unexpected directory; files were retained.", "unsafe_cleanup")
                files.append(child)
        return tuple(files)

    def close(self):
        """Remove only our exact owned files; never recursively remove a tree."""
        if self.closed:
            return True
        if not self.temporary:
            self.closed = True
            return True
        try:
            files = self._owned_contents()  # Preflight all contents before removing any.
            root = self._directory
            if root.exists():
                for path in files:
                    self._owned_contents()  # Refuse a redirected or newly unowned tree.
                    path.unlink()
                capture = root / "capture"
                self._owned_contents()
                if capture.exists():
                    capture.rmdir()
                self._owned_contents()
                (root / _MARKER).unlink()
                root.rmdir()
            self.closed = True
            self.cleanup_error = None
            return True
        except (OSError, ValueError, OpticalError) as exc:
            self.cleanup_error = str(exc)
            return False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _new_lease(cache_root, identity):
    cache = _absolute(cache_root)
    _plain_chain(cache)
    if cache.drive.casefold() == identity.drive.casefold():
        raise _error("Temporary disc storage cannot be on the source optical drive.", "invalid_output")
    cache.mkdir(parents=True, exist_ok=True)
    _plain_chain(cache)
    cache = cache.resolve(strict=True)
    token = uuid.uuid4().hex
    root = cache / (_PREFIX + token)
    root.mkdir()
    marker = json.dumps({"kind": "redux-transient-disc-playback", "version": 1,
        "token": token, "drive": identity.drive, "fingerprint": identity.fingerprint,
        "created_at": datetime.now(timezone.utc).isoformat()}, sort_keys=True).encode("utf-8")
    lease = DiscBuffer(root / "capture" / "disc.cue", identity, "temporary_disk", root, cache, marker)
    try:
        with (root / _MARKER).open("xb") as output:
            output.write(marker)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        # An empty, freshly made directory needs no recursive cleanup.
        if not list(root.iterdir()):
            root.rmdir()
        raise
    return lease


def _validate_capture(result, lease):
    target = lease.directory / "capture"
    lease._owned_contents()
    expected = {"output_directory": target, "cue_path": target / "disc.cue",
                "bin_path": target / "disc.bin", "manifest_path": target / "manifest.json"}
    if any(_absolute(getattr(result, name)) != path for name, path in expected.items()):
        raise _error("The capture did not publish into its owned playback directory.", "verification_failed")
    if (result.verified_output is not True or result.verified_source_passes < 2 or
            result.image_kind != "cd_main_channel_bin_cue_toc_index01"):
        raise _error("The disc capture did not complete repeated-source and output verification.", "verification_failed")
    if not re.fullmatch(r"[0-9a-f]{64}", result.sha256) or result.sha256 != result.source_sha256:
        raise _error("The disc capture checksums do not match.", "verification_failed")
    bundle, _ = _bundle_snapshot(lease.entry_path)
    if set(bundle.files) != {expected["cue_path"], expected["bin_path"]}:
        raise _error("The captured CUE does not reference exactly its owned BIN.", "verification_failed")
    manifest = json.loads(expected["manifest_path"].read_text(encoding="utf-8"))
    if (manifest.get("format") != "redux-cd-main-channel" or manifest.get("version") != 1 or
            manifest.get("sha256") != result.sha256 or manifest.get("bytes_written") != result.bytes_written or
            manifest.get("cue_sha256") != hashlib.sha256(expected["cue_path"].read_bytes()).hexdigest() or
            manifest.get("verified_output") is not True or manifest.get("verified_source_passes", 0) < 2 or
            result.bytes_written <= 0 or result.bytes_written % 2352 or
            result.bytes_written != expected["bin_path"].stat().st_size):
        raise _error("The captured disc manifest did not pass playback validation.", "verification_failed")


def buffer_disc(drive, cache_root, cancelled=None, progress: Progress | None = None,
                expected_fingerprint=None) -> DiscBuffer:
    """Prepare media without adding a game or copying it into the user library.

    Progress is the raw reader's ``(done_bytes, total_bytes, phase)`` callback:
    the total covers capture, output verification and the second source pass.
    Physical captures deliberately have no cooked ISO/mixed-track fallback.
    ``expected_fingerprint`` binds preparation to the disc displayed in the UI.
    """
    letter = normalize_drive(drive)
    _cancel(cancelled)
    before = identify_drive(letter)
    if expected_fingerprint is not None and before.fingerprint != expected_fingerprint:
        raise _error("The inserted disc differs from the selected disc.", "media_changed")
    source = _source_path(before, letter)
    _cancel(cancelled)
    if source is not None:
        bundle, snapshot = _bundle_snapshot(source)
        after = identify_drive(letter)
        _same_disc(before, after)
        if _source_path(after, letter) != source or _bundle_snapshot(source)[1] != snapshot:
            raise _error("The mounted image changed while preparing playback.", "media_changed")
        _cancel(cancelled)
        return DiscBuffer(bundle.entry, before, "mounted_source")

    lease = _new_lease(cache_root, before)
    try:
        _cancel(cancelled)
        result = capture_raw_cd(letter, lease.directory / "capture", cancelled=cancelled, progress=progress)
        _cancel(cancelled)
        _validate_capture(result, lease)
        after = identify_drive(letter)
        _same_disc(before, after)
        if _source_path(after, letter) is not None:
            raise _error("The drive changed from a physical disc to a mounted image.", "media_changed")
        _cancel(cancelled)
        return lease
    except BaseException as exc:
        if not lease.close() and hasattr(exc, "add_note"):
            exc.add_note(f"Temporary disc files were retained at {lease.directory}: {lease.cleanup_error}")
        raise
