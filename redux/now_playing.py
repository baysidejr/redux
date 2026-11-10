"""Pure selection and enrichment state for currently inserted optical media.

The watcher owns IO and the UI owns rendering. This module only applies events,
keeps physical media ahead of virtual images, and rejects stale enrichment.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import math
import re


def _field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _drive(value):
    if not isinstance(value, str):
        return None
    value = value.strip().upper().rstrip("\\/")
    if re.fullmatch(r"[A-Z]", value):
        return value + ":"
    return value if re.fullmatch(r"[A-Z]:", value) else None


def _title(metadata, identity):
    canonical = metadata.get("title")
    if isinstance(canonical, str) and canonical.strip():
        return canonical.strip()
    observed = _field(identity, "title")
    return observed.strip() if isinstance(observed, str) and observed.strip() else "Inserted disc"


@dataclass(frozen=True)
class DiscSnapshot:
    drive: str
    title: str
    system: str | None
    game_id: str | None
    fingerprint: str
    identity: object
    is_virtual: bool | None
    metadata: dict = field(default_factory=dict)
    cover_path: str | None = None
    hero_path: str | None = None


@dataclass
class _Entry:
    snapshot: DiscSnapshot
    order: int
    observed: float | None


class DiscRegistry:
    """Maintain a Now Playing selection without reading drives or artwork.

    ``apply`` and ``update`` return whether the visible active snapshot changed.
    Non-active discs and their metadata remain stored even when False is returned.
    Explicitly physical drives win, followed by unknown drive types, then virtual
    images. Within each group the newest inserted/changed medium wins. Duplicate
    observations of the same fingerprint do not change insertion order.
    """
    def __init__(self):
        self._entries: dict[str, _Entry] = {}
        self._order = 0

    def _selected(self):
        if not self._entries:
            return None
        def priority(entry):
            virtual = entry.snapshot.is_virtual
            return (2 if virtual is False else 0 if virtual is True else 1, entry.order)
        return max(self._entries.values(), key=priority).snapshot

    @property
    def active(self):
        """A snapshot copy; callers cannot mutate the registry's metadata dict."""
        snapshot = self._selected()
        return replace(snapshot, metadata=deepcopy(snapshot.metadata)) if snapshot else None

    def apply(self, event):
        kind = _field(event, "kind")
        if kind in {"error", "pending", "unknown"}:
            return False
        if kind not in {"inserted", "changed", "removed", "insert", "change", "remove"}:
            return False
        drive = _drive(_field(event, "drive"))
        if drive is None:
            return False
        before = self._selected()
        current = self._entries.get(drive)
        observed = _field(event, "monotonic_at")
        if isinstance(observed, bool) or not isinstance(observed, (int, float)) or not math.isfinite(observed):
            observed = None
        if current and observed is not None and current.observed is not None and observed < current.observed:
            return False
        fingerprint = _field(event, "fingerprint")
        if kind in {"removed", "remove"}:
            # A queued removal for the previous disc must not remove its successor.
            if current and (fingerprint is None or fingerprint == current.snapshot.fingerprint):
                del self._entries[drive]
            return before != self._selected()
        identity = _field(event, "identity")
        if identity is None:
            return False
        fingerprint = fingerprint or _field(identity, "fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            return False
        virtual = _field(event, "is_virtual", _field(identity, "is_virtual"))
        if virtual is not None and not isinstance(virtual, bool):
            virtual = None
        if current and current.snapshot.fingerprint == fingerprint:
            snapshot = replace(current.snapshot, identity=identity,
                title=_title(current.snapshot.metadata, identity),
                system=_field(identity, "system"), game_id=_field(identity, "game_id"),
                is_virtual=virtual)
            self._entries[drive] = _Entry(snapshot, current.order, observed if observed is not None else current.observed)
        else:
            self._order += 1
            snapshot = DiscSnapshot(drive, _title({}, identity), _field(identity, "system"),
                _field(identity, "game_id"), fingerprint, identity, virtual)
            self._entries[drive] = _Entry(snapshot, self._order, observed)
        return before != self._selected()

    def update(self, drive, fingerprint, metadata=None, artwork=None):
        """Merge enrichment only while this exact drive/fingerprint is present.

        Missing/None artwork values preserve already selected art. A replacement
        disc starts with empty metadata and art, so delayed workers cannot attach
        the previous disc's title or images to the replacement.
        """
        drive = _drive(drive)
        current = self._entries.get(drive) if drive else None
        if current is None or current.snapshot.fingerprint != fingerprint:
            return False
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("Disc metadata must be a dictionary or None")
        if artwork is not None and not isinstance(artwork, dict):
            raise TypeError("Disc artwork must be a dictionary or None")
        before = self._selected()
        snapshot = current.snapshot
        merged = deepcopy(snapshot.metadata)
        if metadata is not None:
            merged.update(deepcopy(metadata))
        cover = snapshot.cover_path
        hero = snapshot.hero_path
        if metadata:
            candidate = metadata.get("cover_path") or metadata.get("artwork_path")
            if isinstance(candidate, str) and candidate:
                cover = candidate
            candidate = metadata.get("hero_path")
            if isinstance(candidate, str) and candidate:
                hero = candidate
        if artwork:
            for key in ("cover_path", "hero_path"):
                candidate = artwork.get(key)
                if isinstance(candidate, str) and candidate:
                    if key == "cover_path":
                        cover = candidate
                    else:
                        hero = candidate
        current.snapshot = replace(snapshot, metadata=merged, title=_title(merged, snapshot.identity),
                                   cover_path=cover, hero_path=hero)
        return before != self._selected()
