"""Read-only game identities for disc leases.

A buffered disc is prepared into a game record without importing a database row;
its bundle hash shares the ordinary import namespace, so an already installed disc
is recognised after capture. Playback happens only from the installed copy.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
