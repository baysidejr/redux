"""Game cards: removable drives that carry a ``Redux Games`` folder.

Games are never added by hand. A card, SD card or SSD caddy opts in with one marker folder at
its root, holding one folder per system. Redux links every supported game it finds there and
plays it in place; nothing is copied off the card. Discs still arrive through the optical
drive, where copying into the library remains the player's choice.

Layout on the drive::

    Redux Games/
        PlayStation/        disc images (.cue, .chd, .iso, .m3u, ...)
        Saturn/  Sega CD/  PC Engine CD/  Neo Geo CD/  3DO/  Jaguar CD/
        Jaguar/             cartridge files for the other supported systems work the same way
        PC/
            Expedition 33/  one folder per installed PC game, written by Redux:
                game.json       the launch settings, prefilled with the game's EXE
                art/            header.*, boxart.*, gameimage.* (png, jpg or webp)

When a drive is connected while Redux runs, Redux creates the marker folder and its system
folders, looks for installed PC games on the drive (a GOG folder with ``goggame-*.info`` or a
folder with one EXE, at the drive root or under ``Games`` / ``GOG Games``) and writes a
``game.json`` and empty ``art`` folder for each one. The game's own folder is never touched.
The launcher then adds whatever ``game.json`` files exist; without one, nothing happens.
System folders take the system's display name or its id, in any letter case. Unknown folders
and unsupported files are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import string

from .library import SYSTEMS, ImportCancelled, discover_media

MARKER = "Redux Games"
CONFIG_NAME = "game.json"
ART_FOLDER = "art"
ART_ROLES = (("header", "hero"), ("boxart", "cover"), ("gameimage", "screenshot"))
ART_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
CARD_SYSTEM_FOLDERS = ("PlayStation", "Saturn", "Sega CD", "PC Engine CD", "Neo Geo CD", "3DO", "Jaguar CD", "PC")
SYSTEM_FOLDER_NAMES = {"psx": "PlayStation", "saturn": "Saturn", "sega_cd": "Sega CD", "pce_cd": "PC Engine CD",
                       "neo_geo_cd": "Neo Geo CD", "3do": "3DO", "jaguar_cd": "Jaguar CD", "pc": "PC"}
MAX_PC_GAMES_PER_CARD = 500
_SKIP_FOLDERS = {"$recycle.bin", "system volume information", "windows", "program files", "program files (x86)",
                 "programdata", "users", "recovery", "perflogs", "msocache", "redux games", "steam", "steamlibrary",
                 "epic games", "xboxgames", "windowsapps", "documents and settings", "config.msi", "boot", "efi"}
_GAME_ROOTS = {"games", "gog games", "gog", "my games", "pc games"}


def system_folders():
    """Folder names (casefolded) that select each library system."""
    names = {}
    for system, label in SYSTEMS.items():
        if system == "unknown":
            continue
        names[system.casefold()] = system
        names[label.casefold()] = system
        for part in label.split(" / "):
            names[part.strip().casefold()] = system
    names["pc games"] = "pc"
    names["playstation 1"] = "psx"; names["ps1"] = "psx"; names["psx"] = "psx"
    names["mega cd"] = "sega_cd"; names["megacd"] = "sega_cd"; names["segacd"] = "sega_cd"
    names["turbografx cd"] = "pce_cd"; names["turbografx-cd"] = "pce_cd"; names["pc engine cd"] = "pce_cd"
    names["neogeo cd"] = "neo_geo_cd"; names["neo-geo cd"] = "neo_geo_cd"
    names["atari jaguar cd"] = "jaguar_cd"; names["jaguarcd"] = "jaguar_cd"
    return names


def list_drives():
    """Drive roots on this PC, in letter order."""
    if hasattr(os, "listdrives"):
        try:
            return [Path(drive) for drive in os.listdrives()]
        except OSError:
            pass
    if os.name != "nt":
        return [Path("/")]
    return [Path(f"{letter}:\\") for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]


def drive_kind(drive):
    """'removable', 'fixed', 'optical', 'remote' or 'other' for a drive root (Windows)."""
    if os.name != "nt":
        return "fixed"
    try:
        import ctypes
        kinds = {2: "removable", 3: "fixed", 4: "remote", 5: "optical", 6: "ramdisk"}
        return kinds.get(ctypes.windll.kernel32.GetDriveTypeW(str(drive)), "other")
    except (AttributeError, OSError, ValueError):
        return "other"


def system_folder_name(system):
    """The card folder a library system's games live in."""
    return SYSTEM_FOLDER_NAMES.get(system) or SYSTEMS.get(system, str(system)).split(" / ")[0]


def drive_label(drive):
    """The volume label Windows shows for a drive, or an empty string."""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(str(drive), buffer, 261, None, None, None, None, 0):
            return buffer.value
    except (AttributeError, OSError, ValueError):
        pass
    return ""


def drive_usage(drive):
    """(free, total) bytes, or None when the drive cannot be queried."""
    import shutil
    try:
        usage = shutil.disk_usage(str(drive))
        return usage.free, usage.total
    except OSError:
        return None


def can_prepare(drive):
    """Only a removable or fixed data drive that is not Windows' own gets the marker folder."""
    root = Path(drive)
    system_drive = os.environ.get("SystemDrive", "C:") + "\\"
    if os.name == "nt" and os.path.normcase(str(root)) == os.path.normcase(system_drive):
        return False
    return drive_kind(root) in {"removable", "fixed"}


def find_cards(drives=None):
    """Drive roots that carry the marker folder; a missing or unreadable drive is skipped."""
    cards = []
    for drive in (list_drives() if drives is None else drives):
        try:
            marker = Path(drive) / MARKER
            if marker.is_dir() and not marker.is_symlink():
                cards.append(marker)
        except OSError:
            continue
    return cards


@dataclass(frozen=True)
class CardGame:
    system: str
    path: Path
    title: str | None = None
    folder: Path | None = None
    arguments: tuple = ()
    working_directory: str = ""


@dataclass
class CardSync:
    card: Path
    added: list = field(default_factory=list)
    existing: int = 0
    errors: list = field(default_factory=list)
    prepared: list = field(default_factory=list)
    cancelled: bool = False

    @property
    def summary(self):
        label = _card_label(self.card)
        if self.cancelled:
            return f"{label}: adding games cancelled"
        parts = [f"{len(self.added)} added"]
        if self.existing:
            parts.append(f"{self.existing} already in the library")
        if self.errors:
            parts.append(f"{len(self.errors)} skipped")
        if self.prepared:
            parts.append(f"{len(self.prepared)} PC game folder{'' if len(self.prepared) == 1 else 's'} written")
        return f"{label}: " + " • ".join(parts)


def _card_label(card):
    drive = Path(card).anchor.rstrip("\\/") or str(card)
    return f"Game card {drive}"


def pc_game_executable(folder):
    """The EXE that starts the game in ``folder``: GOG's play task, else the only EXE there."""
    folder = Path(folder)
    for info in sorted(folder.glob("goggame-*.info")):
        try:
            document = json.loads(info.read_text(encoding="utf-8-sig"))
            tasks = document.get("playTasks", []) if isinstance(document, dict) else []
            ordered = sorted((task for task in tasks if isinstance(task, dict) and task.get("path")),
                             key=lambda task: (not task.get("isPrimary"), task.get("category") != "game"))
            for task in ordered:
                candidate = (folder / str(task["path"]).replace("\\", "/")).resolve()
                if candidate.is_file() and candidate.suffix.casefold() == ".exe" and candidate.is_relative_to(folder.resolve()):
                    return candidate
        except (OSError, ValueError, TypeError):
            continue
    executables = [path for path in folder.glob("*.exe")
                   if path.is_file() and not any(word in path.name.casefold() for word in ("unins", "setup", "redist", "vcredist", "dxsetup", "crash"))]
    return executables[0] if len(executables) == 1 else None


def find_installed_pc_games(drive):
    """Installed PC games on a drive: (folder, exe) for root folders and common game roots."""
    root = Path(drive)
    found = []
    try:
        top = sorted((path for path in root.iterdir() if path.is_dir() and not path.is_symlink()), key=lambda path: path.name.casefold())
    except OSError:
        return found
    candidates = []
    for folder in top:
        name = folder.name.casefold()
        if name in _SKIP_FOLDERS or name.startswith(("$", ".")):
            continue
        if name in _GAME_ROOTS:
            try:
                candidates.extend(sorted((path for path in folder.iterdir() if path.is_dir() and not path.is_symlink()), key=lambda path: path.name.casefold()))
            except OSError:
                pass
        else:
            candidates.append(folder)
    for folder in candidates[:MAX_PC_GAMES_PER_CARD]:
        executable = pc_game_executable(folder)
        if executable:
            found.append((folder, executable))
    return found


def _safe_name(value):
    cleaned = "".join("_" if character in '<>:"/\\|?*' or ord(character) < 32 else character for character in str(value)).strip(" .")
    return cleaned[:120] or "Game"


def config_template(title, executable, drive):
    executable = Path(executable)
    try:
        stored = executable.relative_to(Path(drive)).as_posix()
    except ValueError:
        stored = str(executable)
    return {
        "title": title,
        "executable": stored,
        "arguments": [],
        "working_directory": "",
        "art": {"header": "art/header.png", "boxart": "art/boxart.png", "gameimage": "art/gameimage.png"},
        "notes": "Written by Redux. The executable path is relative to this drive's root. Drop header, boxart and "
                 "gameimage images (png, jpg or webp) into the art folder; Redux picks them up on the next card read.",
    }


def prepare_card(drive):
    """Create the marker folder, its system folders and one config folder per installed PC game.

    Existing files are never overwritten and the games' own folders are never touched.
    Returns the game folders written this time.
    """
    root = Path(drive)
    marker = root / MARKER
    written = []
    marker.mkdir(exist_ok=True)
    for name in CARD_SYSTEM_FOLDERS:
        (marker / name).mkdir(exist_ok=True)
    readme = marker / "README.txt"
    if not readme.exists():
        readme.write_text(
            "Redux game card\n\nPut each system's disc images or cartridge files in its folder: " + ", ".join(CARD_SYSTEM_FOLDERS[:-1]) +
            ", or any other supported system's name (Jaguar, NES, Genesis, ...). Redux plays them from here.\n\n"
            "PC: Redux writes one folder per installed PC game it finds on this drive, with a game.json and an art folder. "
            "Edit game.json to change the title, EXE or arguments; add header, boxart and gameimage images to art.\n",
            encoding="utf-8")
    pc = marker / "PC"
    for folder, executable in find_installed_pc_games(root):
        target = pc / _safe_name(folder.name)
        config = target / CONFIG_NAME
        if config.exists():
            continue
        try:
            (target / ART_FOLDER).mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps(config_template(folder.name, executable, root), indent=2) + "\n", encoding="utf-8")
            written.append(target)
        except OSError:
            continue
    return written


def read_config(folder, drive):
    """A CardGame from a PC config folder, or None when the config or its EXE is missing."""
    folder = Path(folder)
    config = folder / CONFIG_NAME
    try:
        document = json.loads(config.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    raw = str(document.get("executable") or "")
    if not raw:
        return None
    candidate = Path(raw.replace("\\", "/")) if "/" in raw or ":" in raw else Path(raw)
    for base in (Path(drive), folder):
        path = candidate if candidate.is_absolute() else base / candidate
        try:
            if path.is_file() and path.suffix.casefold() == ".exe":
                arguments = document.get("arguments", [])
                arguments = tuple(str(value) for value in arguments) if isinstance(arguments, list) and all(isinstance(value, str) for value in arguments) else ()
                working = str(document.get("working_directory") or "")
                return CardGame("pc", path.resolve(), str(document.get("title") or folder.name).strip()[:500] or folder.name, folder, arguments, working)
        except OSError:
            continue
    return None


def card_art(folder):
    """{role: path} for the header/boxart/gameimage files present in a config folder's art folder."""
    art = {}
    base = Path(folder) / ART_FOLDER
    for name, role in ART_ROLES:
        for extension in ART_EXTENSIONS:
            candidate = base / (name + extension)
            if candidate.is_file():
                art[role] = candidate
                break
    return art


def scan_card(marker, *, cancelled=None):
    """Every game a card offers, as CardGame records; the card is only read."""
    marker = Path(marker)
    names = system_folders()
    found = []
    try:
        folders = sorted((path for path in marker.iterdir() if path.is_dir() and not path.is_symlink()), key=lambda path: path.name.casefold())
    except OSError:
        return found
    for folder in folders:
        if cancelled is not None and cancelled.is_set():
            raise ImportCancelled("Game card scan cancelled")
        system = names.get(folder.name.casefold())
        if system is None:
            continue
        if system == "pc":
            try:
                games = sorted((path for path in folder.iterdir() if path.is_dir() and not path.is_symlink()), key=lambda path: path.name.casefold())
            except OSError:
                continue
            for game_folder in games[:MAX_PC_GAMES_PER_CARD]:
                configured = read_config(game_folder, marker.parent)
                if configured:
                    found.append(configured)
                    continue
                if (game_folder / CONFIG_NAME).exists():
                    continue  # a config that points nowhere adds nothing
                executable = pc_game_executable(game_folder)
                if executable:
                    found.append(CardGame("pc", executable, game_folder.name, game_folder))
            continue
        try:
            entries = discover_media(folder, cancelled=cancelled)
        except (OSError, ValueError):
            continue
        found.extend(CardGame(system, entry) for entry in entries)
    return found


def _known_paths(library):
    known = set()
    for game in library.games():
        for key in ("source_path", "path"):
            value = game.get(key)
            if value:
                known.add(os.path.normcase(str(value)))
    return known


def _apply_card_art(library, game, folder):
    """Copy any new header/boxart/gameimage into the library's artwork for this game."""
    art = card_art(folder) if folder else {}
    missing = {role: path for role, path in art.items() if not library.artwork_for(game, role) or not Path(library.artwork_for(game, role)).is_file()}
    if not missing:
        return game
    provenance = {role: {"provider": "game card", "source": str(path)} for role, path in missing.items()}
    updated = library.set_artworks(game["id"], missing, provenance=provenance, overwrite=False)
    try:
        sources = {role: library.artwork_for(updated, role) for role in ("cover", "screenshot", "hero")}
        tile = library.artwork_for(updated, "tile")
        if (sources.get("cover") or sources.get("screenshot")) and not (tile and Path(tile).is_file()):
            import tempfile
            from .scraping import compose_tile
            with tempfile.TemporaryDirectory(prefix="card-tile-") as temporary:
                composed = Path(temporary) / "tile.png"
                compose_tile(composed, updated["title"], {role: path for role, path in sources.items() if path})
                updated = library.set_artworks(updated["id"], {"tile": composed}, provenance={"tile": {"provider": "game card", "attribution": "Landscape tile composed by Redux from the card's art"}}, overwrite=False)
    except (OSError, ValueError):
        pass
    return updated


def sync_card(library, marker, *, cancelled=None, catalogue=None, progress=None):
    """Link the card's games into the library without copying them. Known paths are left alone,
    except PC config folders, which are re-read so edited settings and new art arrive."""
    from .importing import import_identified
    result = CardSync(Path(marker))
    try:
        games = scan_card(marker, cancelled=cancelled)
    except ImportCancelled:
        result.cancelled = True
        return result
    known = _known_paths(library)
    for index, item in enumerate(games):
        if cancelled is not None and cancelled.is_set():
            result.cancelled = True
            break
        if progress:
            progress(index + 1, len(games), item.title or item.path.stem)
        already = os.path.normcase(str(item.path)) in known
        if already and item.system != "pc":
            result.existing += 1
            continue
        try:
            if item.system == "pc":
                game = library.add_pc_game(item.path, title=item.title)
                if item.arguments or item.working_directory:
                    library.configure_pc_game(game["id"], list(item.arguments), item.working_directory or None)
                _apply_card_art(library, game, item.folder)
                if already:
                    result.existing += 1
                else:
                    result.added.append(game["id"])
            else:
                game, created, _ = import_identified(library, item.path, system=item.system, linked=True, cancelled=cancelled, catalogue=catalogue)
                if created:
                    result.added.append(game["id"])
                else:
                    result.existing += 1
        except ImportCancelled:
            result.cancelled = True
            break
        except (OSError, ValueError) as error:
            result.errors.append(f"{item.path.name}: {error}")
    return result
