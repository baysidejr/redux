"""Read-only game identities and host-lifetime ownership for disc leases.

Transient playback never imports a database row or copies media into the
library. Full bundle hashes share the ordinary import/save namespace, while
the linked playback path remains owned by a lease until the native core stops.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .embedded import EmbeddedGame
from .library import ImportCancelled, SYSTEMS, _hash_file, inspect_bundle


_METADATA_FIELDS = ("description", "year", "publisher", "developer", "region", "genre")


def _cancel(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise ImportCancelled("Disc playback preparation cancelled")


def _snapshot(bundle):
    result = []
    for path in bundle.files:
        value = path.stat()
        result.append((path, value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns))
    return tuple(result)


def prepare_disc_game(library, lease, metadata=None, cancelled=None, progress=None):
    """Return a linked transient game without importing or changing its source.

    ``progress(delta_bytes)`` has the same contract as ``Library.import_game``.
    The manifest is deliberately identical to its linked-import manifest, so
    existing installed media reuses its real ID and save namespace. Otherwise
    a stable positive signed-64-bit ID is derived from the complete bundle hash.
    Ownership of the lease remains with the caller if preparation fails.
    """
    _cancel(cancelled)
    if lease is None or getattr(lease, "closed", False):
        raise ValueError("Disc playback requires an open media lease")
    entry = Path(lease.entry_path)
    bundle = inspect_bundle(entry)
    before = _snapshot(bundle)
    records = []
    # inspect_bundle supplies precisely the same case-insensitive relative-path
    # ordering used by Library.import_game. Preserve that order in the manifest.
    for source in bundle.files:
        _cancel(cancelled)
        digest, size = _hash_file(source, cancelled, progress=progress)
        records.append({"relative_path": source.relative_to(bundle.root).as_posix(),
                        "sha256": digest, "size": size})
    observed = inspect_bundle(bundle.entry)
    expected_paths = {record["relative_path"] for record in records}
    observed_paths = {path.relative_to(observed.root).as_posix() for path in observed.files}
    if observed_paths != expected_paths:
        raise ValueError("Media descriptor references changed during disc preparation")
    if _snapshot(observed) != before:
        raise ValueError("Source changed during disc preparation")
    manifest = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    bundle_hash = hashlib.sha256(manifest).hexdigest()
    _cancel(cancelled)
    existing = next((game for game in library.games() if game["bundle_hash"] == bundle_hash), None)
    _cancel(cancelled)

    identity = lease.identity
    details = getattr(identity, "details", {}) or {}
    system = getattr(identity, "system", None) or "unknown"
    if system not in SYSTEMS:
        system = "unknown"
    game = {
        "id": existing["id"] if existing else (1 << 62) | int(bundle_hash[:15], 16),
        "bundle_hash": bundle_hash,
        "title": getattr(identity, "title", None) or bundle.entry.stem,
        "system": system,
        "game_id": getattr(identity, "game_id", None),
        "storage": "linked",
        "path": str(bundle.entry),
        "source_path": str(bundle.entry),
        "size": sum(record["size"] for record in records),
        "transient_disc": True,
        "available": True,
        "entry_available": True,
        "file_count": len(records),
        "available_file_count": len(records),
        "health": "available",
        "favorite": False,
        "wishlist": False,
        "completed": False,
        "play_seconds": 0,
        "playtime_seconds": 0,
        "launch_count": 0,
        "last_played": None,
        "description": "",
        "year": None,
        "publisher": "",
        "developer": "",
        "region": getattr(identity, "region", None) or details.get("region") or "",
        "genre": "",
        "artwork_path": "",
    }
    # Reusing an installed identity should also preserve its user-visible
    # metadata and flags; the current disc header still defines title/platform.
    for field in (*_METADATA_FIELDS, "favorite", "wishlist", "completed", "play_seconds",
                  "playtime_seconds", "launch_count", "last_played", "artwork_path"):
        if existing and existing.get(field) is not None:
            game[field] = existing[field]
    for field in _METADATA_FIELDS:
        if metadata is not None and metadata.get(field) is not None:
            game[field] = metadata[field]
    return game


class DiscEmbeddedGame(EmbeddedGame):
    """An embedded session that releases a disc lease after native shutdown.

    A failed ``stop`` retains its files. A cleanup refusal also retains ownership
    and is exposed by ``lease_retained`` / ``lease_cleanup_error``; calling stop
    again can retry. This class never recursively removes media itself.
    """

    def __init__(self, library, game, core, *, media_lease, **kwargs):
        if media_lease is None or getattr(media_lease, "closed", False):
            raise ValueError("Disc playback requires an open media lease")
        if not callable(getattr(media_lease, "close", None)):
            raise TypeError("A media lease must support close()")
        if Path(library.path_for(game)).resolve() != Path(media_lease.entry_path).resolve():
            raise ValueError("Disc playback path does not belong to its media lease")
        self.media_lease = media_lease
        self._lease_cleanup_error = None
        self._install_library = library
        self.installed_game = None
        self._save_handoff = None
        super().__init__(library, game, core, **kwargs)

    @property
    def lease_retained(self):
        return not bool(getattr(self.media_lease, "closed", False))

    @property
    def lease_cleanup_error(self):
        return self._lease_cleanup_error or getattr(self.media_lease, "cleanup_error", None)

    def _native_worker_alive(self):
        thread = getattr(self.host, "_thread", None)
        return thread is not None and thread.is_alive()

    def _release_lease(self):
        if self._native_worker_alive():
            raise RuntimeError("The native core worker is still alive; disc files remain retained")
        if not self.lease_retained:
            return
        if self.installed_game is not None:
            if self._save_handoff is None:
                from .buffer_install import BufferSaveHandoff
                self._save_handoff = BufferSaveHandoff(self._install_library, self.game, self.installed_game)
            self._save_handoff.sync()
        try:
            closed = self.media_lease.close()
            if closed is False or self.lease_retained:
                self._lease_cleanup_error = (getattr(self.media_lease, "cleanup_error", None) or
                    "The media lease declined cleanup; disc files remain retained")
            else:
                self._lease_cleanup_error = None
        except Exception as error:
            self._lease_cleanup_error = str(error) or type(error).__name__

    def install_to_library(self, cancelled=None, progress=None):
        """Install the existing buffer while paused, without retargeting the core.

        Native SRAM is flushed before the initial snapshot. Final user data is
        synchronized after successful host.stop(), then the lease may close.
        A post-publication snapshot failure still exposes ``installed_game``;
        its handoff and source namespace are retained for a safe retry.
        """
        with self._lock:
            if self.started is None or self.finished_seconds is not None or self.paused_at is None:
                raise RuntimeError("Pause the running disc game before installing it")
            flush = getattr(self.host, "flush_saves", None)
            if not callable(flush):
                raise RuntimeError("The embedded host cannot flush its current battery saves")
            flush()
            from .buffer_install import install_buffered_disc
            try:
                return install_buffered_disc(self._install_library, self.media_lease, self.game,
                                             cancelled=cancelled, progress=progress,
                                             destination=getattr(self, "install_destination", None))
            finally:
                installed = getattr(self.media_lease, "_redux_installed_game", None)
                if installed is not None:
                    self.installed_game = dict(installed)
                    self._save_handoff = getattr(self.media_lease, "_redux_save_handoff", None)

    def start(self):
        with self._lock:
            # Reject duplicate starts without stopping a currently running game.
            if self.started is not None or self.finished_seconds is not None:
                return super().start()
            try:
                return super().start()
            except BaseException as error:
                try:
                    self.host.stop()
                    if self._native_worker_alive():
                        raise RuntimeError("The native core worker is still alive; disc files remain retained")
                    self.finished_seconds = 0
                    self._release_lease()
                except Exception as stop_error:
                    self._lease_cleanup_error = str(stop_error) or type(stop_error).__name__
                if self.lease_retained and hasattr(error, "add_note"):
                    error.add_note("Disc media lease retained: " + (self.lease_cleanup_error or "cleanup unavailable"))
                raise

    def stop(self):
        with self._lock:
            try:
                if self.finished_seconds is None:
                    elapsed = self.play_seconds
                    self.host.stop()
                    if self._native_worker_alive():
                        raise RuntimeError("The native core worker is still alive; disc files remain retained")
                    self.finished_seconds = elapsed
                self._release_lease()
            except BaseException as error:
                self._lease_cleanup_error = str(error) or type(error).__name__
                raise
