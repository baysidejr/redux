"""The firmware (BIOS) files Redux's emulators look for, where they go, and copying them from a drive.

Redux never downloads firmware. A living-room PC has no keyboard, so the way files
get in is a drive: any connected drive with a ``bios`` folder (at its root or inside
``Redux Games``) holding at least one known file is offered for copying, once, and
only on the player's say-so. Files already present and identical are skipped; a file
that differs is never overwritten, so a good BIOS cannot be replaced behind the
player's back. The same table tells the pre-launch check what a system needs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from .library import SYSTEMS

MAX_FIRMWARE_FILE = 64 * 1024 * 1024
FOLDER_NAMES = ("bios", "firmware", "BIOS")


@dataclass(frozen=True)
class FirmwareFile:
    name: str            # file name as the emulator expects it
    system: str          # Redux system id
    region: str = ""     # the region codes it serves, e.g. "U", "UE"; "" when it serves every region
    folder: str = ""     # subfolder under the firmware folder ("fbneo", "neocd"), "" for the root
    required: bool = True

    @property
    def relative(self):
        return f"{self.folder}/{self.name}" if self.folder else self.name


# One row per file an emulator Redux ships can use. ``required`` means the system does not
# start without it; optional files improve compatibility but the core has a fallback.
KNOWN = (
    FirmwareFile("bios_CD_U.bin", "sega_cd", "U"), FirmwareFile("bios_CD_E.bin", "sega_cd", "E"), FirmwareFile("bios_CD_J.bin", "sega_cd", "J"),
    FirmwareFile("scph5501.bin", "psx", "U"), FirmwareFile("scph5502.bin", "psx", "E"), FirmwareFile("scph5500.bin", "psx", "J"),
    FirmwareFile("mpr-17933.bin", "saturn", "UE"), FirmwareFile("sega_101.bin", "saturn", "J"),
    FirmwareFile("syscard3.pce", "pce_cd"), FirmwareFile("syscard2.pce", "pce_cd", required=False),
    FirmwareFile("syscard1.pce", "pce_cd", required=False), FirmwareFile("gexpress.pce", "pce_cd", required=False),
    FirmwareFile("neogeo.zip", "neo_geo", folder="fbneo"),
    FirmwareFile("neocd_f.rom", "neo_geo_cd", folder="neocd", required=False), FirmwareFile("neocd_z.rom", "neo_geo_cd", folder="neocd", required=False),
    FirmwareFile("neocd.bin", "neo_geo_cd", folder="neocd", required=False), FirmwareFile("000-lo.lo", "neo_geo_cd", folder="neocd", required=False),
    FirmwareFile("front-sp1.bin", "neo_geo_cd", folder="neocd", required=False), FirmwareFile("top-sp1.bin", "neo_geo_cd", folder="neocd", required=False),
    FirmwareFile("uni-bioscd.rom", "neo_geo_cd", folder="neocd", required=False),
    FirmwareFile("panafz1.bin", "3do", required=False), FirmwareFile("panafz10.bin", "3do", required=False),
    FirmwareFile("panafz10-norsa.bin", "3do", required=False), FirmwareFile("panafz10e-anvil.bin", "3do", required=False),
    FirmwareFile("panafz1j.bin", "3do", required=False), FirmwareFile("goldstar.bin", "3do", required=False),
    FirmwareFile("sanyotry.bin", "3do", required=False), FirmwareFile("3do_arcade_saot.bin", "3do", required=False),
    FirmwareFile("32X_G_BIOS.BIN", "32x", required=False), FirmwareFile("32X_M_BIOS.BIN", "32x", required=False), FirmwareFile("32X_S_BIOS.BIN", "32x", required=False),
    FirmwareFile("bios_MD.bin", "genesis", required=False),
    FirmwareFile("bios_U.sms", "sms", "U", required=False), FirmwareFile("bios_E.sms", "sms", "E", required=False), FirmwareFile("bios_J.sms", "sms", "J", required=False),
    FirmwareFile("bios.gg", "game_gear", required=False),
    FirmwareFile("gb_bios.bin", "gb", required=False), FirmwareFile("gbc_bios.bin", "gbc", required=False),
)
BY_NAME = {entry.name.casefold(): entry for entry in KNOWN}
_REGIONS = {"u": "U", "usa": "U", "us": "U", "north america": "U", "america": "U", "canada": "U",
            "e": "E", "europe": "E", "eu": "E", "pal": "E", "j": "J", "japan": "J", "jp": "J", "ntsc-j": "J"}


def region_code(value):
    """'U', 'E' or 'J' from a library region or disc header value, else ''."""
    text = str(value or "").strip().casefold()
    if text in _REGIONS:
        return _REGIONS[text]
    for word in text.replace(",", " ").split():
        if word in _REGIONS:
            return _REGIONS[word]
    return ""


def system_label(system):
    return SYSTEMS.get(system, str(system))


def files_for(system):
    return [entry for entry in KNOWN if entry.system == system]


def missing_firmware(system, folder, *, region=None, game_path=None):
    """What a system needs before its core can start, or None when it is satisfied or needs nothing.

    Returns (names, message): the acceptable file names for this game and one sentence to
    show. A regional system wants the file for the game's region when that is known; any
    region's file is accepted otherwise. Neo Geo also accepts neogeo.zip beside the game.
    """
    required = [entry for entry in files_for(system) if entry.required]
    if not required:
        return None
    code = region_code(region)
    wanted = [entry for entry in required if code in entry.region] if code else []
    if not wanted:
        wanted = required
    folder = Path(folder) if folder else None
    places = []
    if folder:
        places.extend(folder / entry.relative for entry in wanted)
    if game_path and system == "neo_geo":
        places.extend(Path(game_path).parent / entry.name for entry in wanted)
    for place in places:
        try:
            if place.is_file():
                return None
        except OSError:
            continue
    names = [entry.relative for entry in wanted]
    where = str(folder) if folder else "the BIOS folder"
    extra = " (or next to the game)" if system == "neo_geo" else ""
    message = f"Add {' or '.join(names)} to {where}{extra} and retry Play, or choose another firmware folder."
    return names, message


# --------------------------------------------------------------------------- drives

def known_files_in(folder, *, depth=2):
    """Known firmware files under a folder: [(source path, FirmwareFile)], never following links."""
    folder = Path(folder)
    found = []
    try:
        if not folder.is_dir() or folder.is_symlink():
            return found
    except OSError:
        return found
    pending = [(folder, 0)]
    while pending:
        current, level = pending.pop(0)
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_symlink():
                    continue
                if child.is_dir():
                    if level < depth:
                        pending.append((child, level + 1))
                    continue
                entry = BY_NAME.get(child.name.casefold())
                if entry and child.is_file() and 0 < child.stat().st_size <= MAX_FIRMWARE_FILE:
                    found.append((child, entry))
            except OSError:
                continue
    return found


def find_firmware_folder(drive):
    """The folder on a drive that holds known firmware files, or None: bios/firmware at the root or inside Redux Games."""
    root = Path(drive)
    from .game_cards import MARKER
    for base in (root, root / MARKER):
        for name in FOLDER_NAMES:
            candidate = base / name
            if known_files_in(candidate):
                return candidate
    return None


def describe(found):
    """'Sega CD, PlayStation and Neo Geo' for a list from known_files_in, in system order."""
    systems = []
    for _, entry in found:
        if entry.system not in systems:
            systems.append(entry.system)
    labels = [system_label(system) for system in sorted(systems, key=list(SYSTEMS).index)]
    if len(labels) > 1:
        return ", ".join(labels[:-1]) + " and " + labels[-1]
    return labels[0] if labels else ""


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class CopyResult:
    copied: list
    identical: list
    kept: list      # destination already had a different file; left alone
    failed: list

    @property
    def summary(self):
        parts = [f"{len(self.copied)} copied"]
        if self.identical:
            parts.append(f"{len(self.identical)} already present")
        if self.kept:
            parts.append(f"{len(self.kept)} kept as they were (a different file was already there)")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return " • ".join(parts)


def copy_firmware(source, destination, *, cancelled=None):
    """Copy the known firmware files under ``source`` into the firmware folder ``destination``.

    Each file lands where its emulator looks (``fbneo/neogeo.zip``, ``neocd/...``, the rest at the
    root) whatever subfolder it came from. Identical files are skipped, differing ones are kept.
    """
    destination = Path(destination)
    result = CopyResult([], [], [], [])
    for path, entry in known_files_in(source):
        if cancelled is not None and cancelled.is_set():
            break
        target = destination / entry.relative
        try:
            if target.is_file():
                if _digest(target) == _digest(path):
                    result.identical.append(entry.relative)
                else:
                    result.kept.append(entry.relative)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".part")
            with path.open("rb") as reader, temporary.open("wb") as writer:
                while True:
                    chunk = reader.read(1 << 20)
                    if not chunk:
                        break
                    writer.write(chunk)
            os.replace(temporary, target)
            result.copied.append(entry.relative)
        except OSError as error:
            result.failed.append(f"{entry.relative}: {error}")
    return result
