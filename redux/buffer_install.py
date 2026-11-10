"""Install an existing playback buffer and preserve its durable user data.

Only the leased files are read: no optical discovery, capture or drive command
is performed. The active core keeps its original media and save paths. A paused
session snapshots user data now and synchronizes its final files after native
shutdown, before releasing its media lease.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from .library import _hash_file


class BufferInstallError(RuntimeError):
    def __init__(self, message, *, installed_game=None):
        super().__init__(message)
        self.installed_game = installed_game


class BufferSaveConflict(BufferInstallError):
    """Managed user data changed independently; both copies are retained."""


def _plain(path):
    for candidate in (path, *path.parents):
        try:
            value = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(value.st_mode) or
                getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise BufferInstallError("User-data snapshots cannot traverse symlinks or reparse points")


def _save_directory(library, game):
    identity = game.get("bundle_hash")
    number = game.get("id")
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("Invalid buffered game identity")
    if isinstance(number, bool) or not isinstance(number, int) or not 0 < number < 1 << 63:
        raise ValueError("Invalid buffered game ID")
    root = Path(library.root).resolve()
    path = root / "game_data" / str(number) / identity
    _plain(path)
    if not path.resolve().is_relative_to(root / "game_data"):
        raise BufferInstallError("User-data snapshot resolves outside the library")
    return path


def _record(path):
    _plain(path)
    if not path.is_file():
        raise BufferInstallError("User-data snapshot contains a non-file entry")
    return _hash_file(path)


def _snapshot(root):
    _plain(root)
    if not root.exists():
        return {}
    if not root.is_dir():
        raise BufferInstallError("The user-data namespace is not a directory")
    result = {}
    for entry in sorted(root.rglob("*")):
        _plain(entry)
        if entry.is_dir():
            continue
        result[entry.relative_to(root).as_posix()] = _record(entry)
    return result


def _copy_verified(source, destination, expected):
    _plain(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _plain(destination.parent)
    with destination.open("xb") as output:
        observed = _hash_file(source, sink=output)
        output.flush()
        os.fsync(output.fileno())
    if observed != expected:
        raise BufferInstallError("User data changed while its snapshot was copied")


def _move_file_no_replace(source, target):
    if os.name == "nt":
        os.rename(source, target)  # Windows rename refuses an existing target.
    else:
        os.link(source, target)  # Exclusive publication on the same filesystem.
        source.unlink()


def _sync_file(source, target, expected_source, previous):
    """Publish a complete file, retaining an independently changed target."""
    _plain(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / (".redux-save-sync-" + uuid.uuid4().hex)
    backup = target.parent / (".redux-save-backup-" + uuid.uuid4().hex)
    published = False
    try:
        _copy_verified(source, temporary, expected_source)
        if target.exists():
            if previous is None or _record(target) != previous:
                raise BufferSaveConflict("Managed user-data conflict; both save namespaces were retained")
            _move_file_no_replace(target, backup)
            if _record(backup) != previous:
                raise BufferSaveConflict("Managed user data changed during synchronization; its backup was retained")
        _move_file_no_replace(temporary, target)
        published = True
        if backup.exists():
            backup.unlink()
    except BaseException:
        # Restore only into an empty name; never replace a competing writer.
        if backup.exists() and not target.exists():
            _move_file_no_replace(backup, target)
        raise
    finally:
        if temporary.exists():
            temporary.unlink()
        # If publication raced another writer, its name is untouched and the
        # old content remains in the explicitly named backup for recovery.
        if published and backup.exists():
            backup.unlink()


class BufferSaveHandoff:
    """Copy-only continuity from a transient ID to an installed library ID.

    ``sync()`` is called while paused after SRAM flush, then after host.stop().
    Existing conflicting files are never overwritten. Final updates may replace
    only files still matching this handoff's previous snapshot. Source files are
    never moved or deleted, including after successful synchronization.
    """

    def __init__(self, library, source_game, installed_game):
        if source_game["bundle_hash"] != installed_game["bundle_hash"]:
            raise ValueError("User data cannot migrate to a different game identity")
        self.source = _save_directory(library, source_game)
        self.target = _save_directory(library, installed_game)
        self.installed_game = dict(installed_game)
        self._published = {}

    def sync(self):
        try:
            return self._sync()
        except Exception as error:
            error_type = BufferSaveConflict if isinstance(error, (BufferSaveConflict, FileExistsError)) else BufferInstallError
            raise error_type(f"{error}. Source user data retained at {self.source}; "
                             f"managed destination: {self.target}", installed_game=self.installed_game) from error

    def _sync(self):
        if self.source == self.target:
            return self.target
        records = _snapshot(self.source)
        if not records:
            return self.target
        existing = _snapshot(self.target)
        # Check the entire set before publishing any changes. Identical files
        # need no write, and unrelated managed files remain untouched.
        for relative, record in records.items():
            if relative in existing and existing[relative] != record and existing[relative] != self._published.get(relative):
                raise BufferSaveConflict("Managed user-data conflict; both save namespaces were retained",
                                         installed_game=self.installed_game)
        ordered = sorted(records, key=lambda relative: (Path(relative).suffix.casefold() == ".json", relative))
        if not self.target.exists():
            self.target.parent.mkdir(parents=True, exist_ok=True)
            _plain(self.target.parent)
            stage = self.target.parent / (".redux-save-snapshot-" + uuid.uuid4().hex)
            stage.mkdir()
            try:
                for relative in ordered:
                    _copy_verified(self.source / relative, stage / relative, records[relative])
                from .raw_acquisition import _publish_directory
                _publish_directory(stage, self.target)
            finally:
                if stage.exists():
                    if stage.resolve().parent != self.target.parent.resolve() or not stage.name.startswith(".redux-save-snapshot-"):
                        raise BufferInstallError("Refusing cleanup outside the private user-data snapshot")
                    _plain(stage)
                    shutil.rmtree(stage)
            self._published.update(records)
            return self.target
        # Content blobs/thumbnails are published before JSON slot manifests,
        # so a manifest never intentionally refers to a not-yet-copied blob.
        for relative in ordered:
            record = records[relative]
            if existing.get(relative) != record:
                try:
                    _sync_file(self.source / relative, self.target / relative, record,
                               self._published.get(relative))
                except BufferInstallError as error:
                    error.installed_game = self.installed_game
                    raise
            self._published[relative] = record
        return self.target


def install_buffered_disc(library, lease, game, cancelled=None, progress=None):
    """Copy an existing leased bundle to managed storage, retaining the lease.

    ``progress(delta_bytes)`` follows Library.import_game. Cancellation remains
    supported through media publication; after commit, the small durable save
    snapshot completes before returning. Repeated installs preserve existing
    library metadata. Source identity is rechecked and the importer guards its
    copied manifest before publication, closing the source-change race.

    The normal serializable installed game is returned. An internal handoff is
    retained on the lease for DiscEmbeddedGame's final post-stop synchronization.
    If a save conflict follows a successful media install, the exception exposes
    ``installed_game`` and both source/destination user-data copies are retained.
    """
    if lease is None or getattr(lease, "closed", False):
        raise ValueError("Installing a buffered disc requires an open media lease")
    _save_directory(library, game)  # Validate identity before copying media.
    entry = Path(lease.entry_path).resolve()
    if Path(library.path_for(game)).resolve() != entry:
        raise ValueError("The buffered game path does not belong to its media lease")
    from .disc_session import prepare_disc_game
    current = prepare_disc_game(library, lease, cancelled=cancelled)
    if current["bundle_hash"] != game["bundle_hash"]:
        raise ValueError("The buffered disc changed from the expected game identity")
    installed, created = library.import_game(entry, system=game["system"], linked=False,
        title=game["title"], cancelled=cancelled, progress=progress,
        expected_bundle_hash=game["bundle_hash"])
    if installed["bundle_hash"] != game["bundle_hash"] or installed["storage"] != "managed":
        raise BufferInstallError("The importer did not publish the expected managed game")
    lease._redux_installed_game = dict(installed)
    try:
        handoff = getattr(lease, "_redux_save_handoff", None)
        if handoff is None:
            handoff = BufferSaveHandoff(library, game, installed)
            lease._redux_save_handoff = handoff
        elif handoff.source != _save_directory(library, game) or handoff.target != _save_directory(library, installed):
            raise BufferInstallError("This media lease already owns a different save-data handoff", installed_game=installed)
        handoff.installed_game = dict(installed)
        if created:
            fields = {key: game[key] for key in ("description", "year", "publisher", "developer", "region", "genre")
                      if game.get(key) not in (None, "")}
            if fields:
                installed = library.update_metadata(installed["id"], **fields)
                lease._redux_installed_game = dict(installed)
                handoff.installed_game = dict(installed)
        handoff.sync()
    except Exception as error:
        if isinstance(error, BufferInstallError):
            error.installed_game = dict(installed)
            raise
        source = Path(library.root) / "game_data" / str(game["id"]) / game["bundle_hash"]
        target = Path(library.root) / "game_data" / str(installed["id"]) / installed["bundle_hash"]
        raise BufferInstallError(f"Media was installed, but its user-data handoff did not complete: {error}. "
            f"Source user data retained at {source}; managed destination: {target}",
            installed_game=dict(installed)) from error
    return installed
