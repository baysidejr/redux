"""Original, portable SQLite library and lossless multi-file media importer.

No reference application code, metadata corpus, credentials, or hardware is required.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3
import threading
import tempfile
from typing import Callable
import uuid
from urllib.parse import urlsplit, urlunsplit

SYSTEMS = {
    "pc": "PC Games",
    "3do": "3DO",
    "jaguar": "Atari Jaguar", "jaguar_cd": "Atari Jaguar CD",
    "unknown": "Unassigned", "nes": "NES", "snes": "SNES", "n64": "Nintendo 64",
    "genesis": "Mega Drive / Genesis", "32x": "32X", "sega_cd": "Sega CD",
    "saturn": "Saturn", "psx": "PlayStation", "neo_geo_cd": "Neo Geo CD",
    "pce": "PC Engine / TurboGrafx-16", "pce_cd": "PC Engine CD",
    "supergrafx": "SuperGrafx", "atari2600": "Atari 2600", "atari7800": "Atari 7800",
    "gb": "Game Boy", "gbc": "Game Boy Color", "gba": "Game Boy Advance",
    "sms": "Master System", "game_gear": "Game Gear",
}
EXTENSION_SYSTEM = {
    '.j64':'jaguar', '.jag':'jaguar', '.bigpimg':'jaguar_cd',
    ".nes": "nes", ".sfc": "snes", ".smc": "snes", ".z64": "n64", ".n64": "n64",
    ".v64": "n64", ".md": "genesis", ".gen": "genesis", ".smd": "genesis",
    ".32x": "32x", ".pce": "pce", ".a26": "atari2600", ".a78": "atari7800",
    ".gb": "gb", ".gbc": "gbc", ".gba": "gba", ".sms": "sms", ".gg": "game_gear",
}
MEDIA_EXTENSIONS = set(EXTENSION_SYSTEM) | {".cue", ".m3u", ".iso", ".chd", ".ccd", ".bin",'.cdi','.cof','.abs','.rom'}
_CUE_FILE = re.compile(r'^\s*FILE\s+(?:"([^"\r\n]+)"|(\S+))\s+\S+\s*$', re.I)
ARTWORK_FIELDS = {"cover": "artwork_path", "tile": "tile_path", "hero": "hero_path",
                  "screenshot": "screenshot_path", "logo": "logo_path"}


class ImportCancelled(Exception):
    pass


@dataclass(frozen=True)
class Bundle:
    entry: Path
    root: Path
    files: tuple[Path, ...]


def _reference(root: Path, descriptor: Path, text: str) -> Path:
    text = text.strip().replace("\\", "/")
    if not text or Path(text).is_absolute() or PureWindowsPath(text).is_absolute() or ":" in text:
        raise ValueError(f"Media reference must be relative: {text!r}")
    lexical = Path(os.path.abspath(descriptor.parent / text))
    path = lexical.resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Media reference leaves the selected bundle: {text}")
    if not path.is_file():
        raise FileNotFoundError(f"Missing referenced media: {text}")
    if path != lexical:
        raise ValueError(f"Media references through symlinks or junctions are unsupported: {text}")
    return path


def inspect_bundle(entry: str | Path) -> Bundle:
    entry = Path(entry).expanduser().resolve(strict=True)
    if not entry.is_file() or entry.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("Select a supported media file (ROM, ISO, CUE, M3U, CCD, CHD or BIN)")
    root = entry.parent
    visited: set[Path] = set()
    active: set[Path] = set()

    def visit(path: Path, depth: int = 0):
        if path in active:
            raise ValueError("Cyclic media playlist")
        if path in visited:
            return
        if depth > 8 or len(visited) >= 4096:
            raise ValueError("Media bundle exceeds recursion or file-count limit")
        active.add(path)
        visited.add(path)
        if path.suffix.lower() in {".cue", ".m3u"}:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Media descriptor exceeds 4 MiB")
            raw = path.read_bytes()
            try:
                lines = raw.decode("utf-8-sig").splitlines()
            except UnicodeDecodeError:
                lines = raw.decode("cp1252").splitlines()
            references = 0
            for line in lines:
                if path.suffix.lower() == ".cue":
                    match = _CUE_FILE.match(line)
                    if match:
                        visit(_reference(root, path, match.group(1) or match.group(2)), depth + 1)
                        references += 1
                    elif re.match(r"^\s*FILE\b", line, re.I):
                        raise ValueError("Malformed FILE line in CUE")
                elif line.strip() and not line.lstrip().startswith("#"):
                    visit(_reference(root, path, line.strip()), depth + 1)
                    references += 1
            if not references:
                raise ValueError("Descriptor contains no media references")
        elif path.suffix.lower() == ".ccd":
            # CloneCD requires its paired IMG; a SUB sidecar is retained if present.
            siblings = {p.name.casefold(): p for p in path.parent.iterdir() if p.is_file()}
            for suffix in (".img", ".sub"):
                sibling = siblings.get((path.stem + suffix).casefold())
                if sibling:
                    visit(sibling.resolve(), depth + 1)
                elif suffix == ".img":
                    raise FileNotFoundError("CloneCD descriptor is missing its paired IMG file")
        active.remove(path)

    visit(entry)
    files = tuple(sorted(visited, key=lambda p: p.relative_to(root).as_posix().casefold()))
    return Bundle(entry, root, files)


def discover_media(folder: str | Path, *, cancelled=None) -> list[Path]:
    def check():
        if cancelled is not None and cancelled.is_set():raise ImportCancelled("Folder scan cancelled")
    check()
    root = Path(folder).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    candidates=[]
    for path in root.rglob("*"):
        check()
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS and not _markdown_document(path):candidates.append(path)
    candidates.sort(key=lambda path:str(path).casefold())
    # Do not add a CUE's BIN tracks or an M3U's component discs as separate games.
    referenced = set()
    for path in candidates:
        check()
        if path.suffix.lower() in {".cue", ".m3u", ".ccd"}:
            try:
                bundle = inspect_bundle(path)
                referenced.update(p for p in bundle.files if p != bundle.entry)
            except (OSError, ValueError):
                # Keep invalid descriptors so importing them produces a useful error.
                pass
    return [p for p in candidates if p.resolve() not in referenced]


def _markdown_document(path: Path) -> bool:
    """Avoid importing ordinary project README.md files as Mega Drive cartridges.

    This is a scanner guard only. Explicit .md imports remain available, and
    binary/undecodable .md files remain candidates. It does not validate ROMs.
    """
    if path.suffix.casefold() != ".md":
        return False
    try:
        with path.open("rb") as stream:
            sample = stream.read(4096)
        text = sample.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return False
    return bool(text) and all(ch.isprintable() or ch in "\r\n\t" for ch in text)


def _utc(value=None) -> str:
    if value is None:
        moment = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Use an ISO-8601 timestamp with a timezone") from error
    else:
        raise TypeError("Timestamp must be a datetime or ISO-8601 string")
    if moment.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 365*24*3600:
        raise ValueError("Session duration must be between 0 and one year in seconds")
    return int(value)


def _name(value, label="Name", maximum=100):
    if not isinstance(value, str):
        raise TypeError(f"{label} must be text")
    value = value.strip()
    if not value or len(value) > maximum or any(ord(ch)<32 for ch in value):
        raise ValueError(f"{label} must contain 1–{maximum} printable characters")
    return value


def _artwork_source_record(value,digest,size):
    """Keep only public attribution fields, never an API request or credentials."""
    result={"provider":"local","sha256":digest,"bytes":size,"stored_at":_utc()}
    if not isinstance(value,dict):return result
    for name in ("provider","provider_game_id","game_id","match_title","region","license","attribution","downloaded_at"):
        text=value.get(name)
        if isinstance(text,(str,int)) and not isinstance(text,bool):
            text=str(text).strip()
            if text and len(text)<=2048 and not any(ord(ch)<32 for ch in text):result[name]=text
    url=value.get("source_url")
    if isinstance(url,str) and len(url)<=8192:
        try:
            parsed=urlsplit(url)
            if parsed.scheme in {"https","http"} and parsed.hostname and not parsed.username and not parsed.password:
                # Several providers return authenticated or expiring media URLs.
                # Persist the public address only; queries/fragments are omitted.
                result["source_url"]=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,"",""))
        except ValueError:pass
    for name in ("width","height"):
        value_number=value.get(name)
        if isinstance(value_number,int) and not isinstance(value_number,bool) and 0<value_number<=32768:
            result[name]=value_number
    return result


def _hash_file(path: Path, cancelled=None, sink=None, progress=None) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            if cancelled and cancelled.is_set():
                raise ImportCancelled("Import cancelled")
            digest.update(chunk)
            if sink:
                sink.write(chunk)
            total += len(chunk)
            if progress:
                progress(len(chunk))
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Source changed during import: {path.name}")
    if total != before.st_size:
        raise ValueError(f"Short read: {path.name}")
    return digest.hexdigest(), total


def _remove_staging(stage: Path, media: Path):
    # Validate the final resolved target before every recursive cleanup.
    resolved, parent = stage.resolve(), media.resolve()
    if resolved.parent != parent or not resolved.name.startswith(".import-"):
        raise ValueError("Refusing cleanup outside the import staging directory")
    if resolved.exists():
        shutil.rmtree(resolved)


class Library:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.media = self.root / "media"
        self.media.mkdir(exist_ok=True)
        if not self.media.resolve().is_relative_to(self.root):
            raise ValueError("Library media directory must stay inside the library")
        self.db = self.root / "library.sqlite3"
        with self.connect() as db:
            application = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if (application != 0x50525831 and tables) or version not in (0,1,2):
                raise ValueError("This is not a supported Redux library database")
            # Use explicit statements inside one write transaction: executescript
            # would commit before ALTER TABLE and leave partial migrations behind.
            db.execute("BEGIN IMMEDIATE")
            for statement in ("""CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, system TEXT NOT NULL,
                    storage TEXT NOT NULL CHECK(storage IN ('managed','linked')),
                    path TEXT NOT NULL, source_path TEXT NOT NULL,
                    bundle_hash TEXT NOT NULL UNIQUE, size INTEGER NOT NULL,
                    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')))""",
                """CREATE TABLE IF NOT EXISTS media_files (
                    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
                    PRIMARY KEY(game_id, relative_path))""",
                """CREATE TABLE IF NOT EXISTS launchers (
                    system TEXT PRIMARY KEY, executable TEXT NOT NULL, arguments TEXT NOT NULL)"""):
                db.execute(statement)
            columns = {row[1] for row in db.execute("PRAGMA table_info(games)")}
            additions = {
                "favorite": "INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1))",
                "wishlist": "INTEGER NOT NULL DEFAULT 0 CHECK(wishlist IN (0,1))",
                "completed": "INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1))",
                "play_seconds": "INTEGER NOT NULL DEFAULT 0 CHECK(play_seconds>=0)",
                "launch_count": "INTEGER NOT NULL DEFAULT 0 CHECK(launch_count>=0)",
                "last_played": "TEXT",
                "description": "TEXT NOT NULL DEFAULT ''",
                "year": "INTEGER", "publisher": "TEXT NOT NULL DEFAULT ''",
                "developer": "TEXT NOT NULL DEFAULT ''", "region": "TEXT NOT NULL DEFAULT ''",
                "genre": "TEXT NOT NULL DEFAULT ''", "artwork_path": "TEXT NOT NULL DEFAULT ''",
                "tile_path": "TEXT NOT NULL DEFAULT ''", "hero_path": "TEXT NOT NULL DEFAULT ''",
                "screenshot_path": "TEXT NOT NULL DEFAULT ''", "logo_path": "TEXT NOT NULL DEFAULT ''",
                "artwork_provenance": "TEXT NOT NULL DEFAULT '{}'",
                "pc_arguments": "TEXT NOT NULL DEFAULT '[]'",
                "pc_working_directory": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE games ADD COLUMN {name} {declaration}")
            for statement in (
                """CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')))""",
                """CREATE TABLE IF NOT EXISTS collection_games (
                    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    PRIMARY KEY(collection_id,game_id))""",
                """CREATE TABLE IF NOT EXISTS play_sessions (
                    id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    started_at TEXT NOT NULL, ended_at TEXT, duration_seconds INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed')),
                    exit_code INTEGER)""",
                "CREATE INDEX IF NOT EXISTS idx_sessions_game ON play_sessions(game_id,started_at)",
                "CREATE INDEX IF NOT EXISTS idx_collection_games_game ON collection_games(game_id)",
            ):
                db.execute(statement)
            # Playlists keep a play order; libraries from before it number existing rows as found.
            if "position" not in {row[1] for row in db.execute("PRAGMA table_info(collection_games)")}:
                db.execute("ALTER TABLE collection_games ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
                db.execute("UPDATE collection_games SET position=rowid")
            required_tables={
                "games":{"id","title","system","storage","path","source_path","bundle_hash","size","imported_at",*additions},
                "media_files":{"game_id","relative_path","sha256","size"},
                "launchers":{"system","executable","arguments"},
                "collections":{"id","name","name_key","created_at"},
                "collection_games":{"collection_id","game_id","position"},
                "play_sessions":{"id","game_id","started_at","ended_at","duration_seconds","status","exit_code"},
            }
            for table,required in required_tables.items():
                actual={row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if not required<=actual:
                    raise ValueError(f"Library table {table} is incomplete or incompatible")
            db.execute("PRAGMA user_version=2")
            db.execute("PRAGMA application_id=1347573809")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _decorate(self, row, file_rows=None):
        game = dict(row)
        for flag in ("favorite", "wishlist", "completed"):
            game[flag] = bool(game.get(flag,False))
        try:
            game["entry_available"] = self.path_for(game).is_file()
        except (OSError, ValueError):
            game["entry_available"] = False
        if file_rows is None:
            with self.connect() as db:
                file_rows = db.execute("SELECT relative_path FROM media_files WHERE game_id=?",(game["id"],)).fetchall()
        base = self.media / game["bundle_hash"] if game["storage"]=="managed" else Path(game["source_path"]).parent
        present = 0
        for file_row in file_rows:
            try:
                path=(base/file_row["relative_path"]).resolve()
                if path.is_relative_to(base.resolve()) and path.is_file():
                    present += 1
            except (OSError,ValueError):
                pass
        game["file_count"] = len(file_rows)
        game["available_file_count"] = present
        game["available"] = bool(game["entry_available"] and present==len(file_rows))
        game["health"] = "available" if game["available"] else "partial" if present else "offline"
        game["playtime_seconds"] = game["play_seconds"]
        return game

    def games(self, query="", system=None, *, view="all", collection_id=None, sort="title"):
        aliases = {"continue_playing":"continue", "favorite":"favorites", "recently_played":"recent"}
        view = aliases.get(view,view)
        clauses = {
            "all":"1", "favorites":"favorite=1", "wishlist":"wishlist=1",
            "completed":"completed=1", "continue":"last_played IS NOT NULL AND completed=0",
            "recent":"last_played IS NOT NULL", "available":"1", "offline":"1", "collection":"1",
        }
        orders = {
            "title":"title COLLATE NOCASE,id", "recent":"last_played DESC,title COLLATE NOCASE,id",
            "added":"imported_at DESC,id DESC", "playtime":"play_seconds DESC,title COLLATE NOCASE,id",
            "year":"COALESCE(year,9999),title COLLATE NOCASE,id",
        }
        if view not in clauses or sort not in orders:
            raise ValueError("Unknown library view or sort order")
        if not isinstance(query,str):
            raise TypeError("Search query must be text")
        if view=="collection" and collection_id is None:
            raise ValueError("A collection ID is required for the collection view")
        with self.connect() as db:
            # LIKE wildcards in user search are literal, not unintended match-all.
            q = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern=f"%{q}%"
            order=orders[sort];parameters=[pattern,pattern,pattern,pattern,system,system,collection_id,collection_id]
            if collection_id is not None:
                # A playlist plays in the order its owner set, whatever the library sort is.
                order="(SELECT cg.position FROM collection_games cg WHERE cg.collection_id=? AND cg.game_id=games.id),id"
                parameters.append(collection_id)
            rows = db.execute("SELECT * FROM games WHERE "
                "(title LIKE ? ESCAPE '\\' OR publisher LIKE ? ESCAPE '\\' OR developer LIKE ? ESCAPE '\\' OR genre LIKE ? ESCAPE '\\') "
                f"AND (? IS NULL OR system=?) AND ({clauses[view]}) "
                "AND (? IS NULL OR id IN (SELECT game_id FROM collection_games WHERE collection_id=?)) "
                f"ORDER BY {order}",parameters).fetchall()
            all_files = db.execute("SELECT game_id,relative_path FROM media_files").fetchall()
        by_id={}
        for row in all_files:
            by_id.setdefault(row["game_id"],[]).append(row)
        result=[self._decorate(row,by_id.get(row["id"],[])) for row in rows]
        if view=="available": result=[game for game in result if game["available"]]
        elif view=="offline": result=[game for game in result if not game["available"]]
        return result

    def game(self, game_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
        if row is None:
            raise KeyError(f"No library entry {game_id}")
        return self._decorate(row)

    def path_for(self, game):
        path = Path(game["path"])
        if game["system"]=="pc" and not path.is_absolute():
            resolved=(self.root.parent/path).resolve()
            if not resolved.is_relative_to(self.root.parent.resolve()):
                raise ValueError("PC game path leaves portable storage")
            return resolved
        if game["storage"] == "managed":
            resolved = (self.root / path).resolve()
            if not resolved.is_relative_to(self.media.resolve()):
                raise ValueError("Managed path leaves library media directory")
            return resolved
        return path

    def add_pc_game(self, executable, *, title=None, game_id=None):
        """Link an installed Windows EXE without copying or hashing its game data."""
        from .pc_games import validate_executable
        path=validate_executable(executable)
        name=_name(title or path.parent.name or path.stem,"Title",500)
        portable=self.root.parent.resolve()
        stored=path.relative_to(portable).as_posix() if path.is_relative_to(portable) else str(path)
        key=hashlib.sha256(("pc-exe\0"+os.path.normcase(stored)+uuid.uuid4().hex).encode("utf-8")).hexdigest()
        if game_id is not None and self.game(game_id)["system"]!="pc":
            raise ValueError("Only a PC game shortcut can be relocated")
        with self.connect() as db:
            duplicate=db.execute("SELECT id FROM games WHERE system='pc' AND path=? COLLATE NOCASE",(stored,)).fetchone()
            if duplicate and game_id is not None and duplicate["id"]!=game_id:
                raise ValueError("That executable already has a library entry")
            if game_id is not None:
                db.execute("UPDATE games SET path=?,source_path=?,size=? WHERE id=?",
                    (stored,str(path),path.stat().st_size,game_id))
            elif duplicate:game_id=duplicate["id"]
            else:
                cursor=db.execute("INSERT INTO games(title,system,storage,path,source_path,bundle_hash,size) VALUES(?,?,?,?,?,?,?)",
                    (name,"pc","linked",stored,str(path),key,path.stat().st_size))
                game_id=cursor.lastrowid
        return self.game(game_id)

    def configure_pc_game(self,game_id,arguments,working_directory=None):
        game=self.game(game_id)
        if game["system"]!="pc":raise ValueError("Choose a PC game")
        from .pc_games import validate_arguments
        arguments=validate_arguments(arguments)
        stored=""
        if working_directory is not None:
            folder=Path(working_directory).resolve(strict=True)
            if not folder.is_dir():raise ValueError("Choose an existing working folder")
            base=self.root.parent.resolve()
            stored=folder.relative_to(base).as_posix() if folder.is_relative_to(base) else str(folder)
        with self.connect() as db:
            db.execute("UPDATE games SET pc_arguments=?,pc_working_directory=? WHERE id=?",
                (json.dumps(arguments),stored,game_id))

    def pc_game_config(self,game):
        from .pc_games import validate_arguments
        if game["system"]!="pc":raise ValueError("Choose a PC game")
        try:arguments=validate_arguments(json.loads(game.get("pc_arguments","[]")))
        except (ValueError,TypeError):raise ValueError("PC launch arguments are invalid; reset them in PC game setup") from None
        value=game.get("pc_working_directory","")
        if not value:return arguments,self.path_for(game).parent
        folder=Path(value)
        if not folder.is_absolute():
            folder=(self.root.parent/folder).resolve()
            if not folder.is_relative_to(self.root.parent.resolve()):raise ValueError("Working folder leaves portable storage")
        return arguments,folder

    def import_game(self, entry, system="auto", *, linked=False, title=None,
                    cancelled: threading.Event | None = None,
                    progress: Callable[[int], None] | None = None,expected_bundle_hash=None):
        if expected_bundle_hash is not None and (not isinstance(expected_bundle_hash,str) or not re.fullmatch(r"[a-f0-9]{64}",expected_bundle_hash)):
            raise ValueError("Expected bundle identity must be a lowercase SHA-256 hash")
        if system != "auto" and system not in SYSTEMS:
            raise ValueError(f"Unknown system: {system}")
        bundle = inspect_bundle(entry)
        system = EXTENSION_SYSTEM.get(bundle.entry.suffix.lower(), "unknown") if system == "auto" else system
        stage = self.media / f".import-{uuid.uuid4().hex}"
        if not linked:
            stage.mkdir()
        records = []
        moved = False
        published = None
        committed = False
        try:
            for source in bundle.files:
                if cancelled and cancelled.is_set():
                    raise ImportCancelled("Import cancelled")
                relative = source.relative_to(bundle.root).as_posix()
                if linked:
                    digest, size = _hash_file(source, cancelled, progress=progress)
                else:
                    destination = stage / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("xb") as sink:
                        digest, size = _hash_file(source, cancelled, sink=sink, progress=progress)
                        sink.flush()
                        os.fsync(sink.fileno())
                records.append(dict(relative_path=relative, sha256=digest, size=size))
            # A descriptor can change after initial discovery but before its own
            # copy. Validate the actual snapshot's dependency set before publish.
            snapshot_entry = bundle.entry if linked else stage / bundle.entry.relative_to(bundle.root)
            snapshot = inspect_bundle(snapshot_entry)
            expected = {r["relative_path"] for r in records}
            observed = {p.relative_to(snapshot.root).as_posix() for p in snapshot.files}
            if observed != expected:
                raise ValueError("Media descriptor references changed during import")
            manifest = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
            bundle_hash = hashlib.sha256(manifest).hexdigest()
            if expected_bundle_hash is not None and bundle_hash!=expected_bundle_hash:
                raise ValueError("Buffered media changed before installation; no library entry was published")
            if cancelled and cancelled.is_set():
                raise ImportCancelled("Import cancelled")
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if cancelled and cancelled.is_set():
                    raise ImportCancelled("Import cancelled")
                existing = db.execute("SELECT * FROM games WHERE bundle_hash=?", (bundle_hash,)).fetchone()
                promoting = bool(existing and existing["storage"] == "linked" and not linked)
                if existing and not promoting:
                    game = self._decorate(existing,db.execute("SELECT relative_path FROM media_files WHERE game_id=?",(existing["id"],)).fetchall())
                    game["import_action"] = "duplicate"
                    return game, False
                destination = self.media / bundle_hash
                if linked:
                    path = str(bundle.entry)
                else:
                    if stage.resolve().parent != self.media.resolve() or destination.resolve().parent != self.media.resolve():
                        raise ValueError("Import directory leaves library media root")
                    if destination.exists():
                        # Removing an entry deliberately retains its media. A
                        # later re-import can adopt it only after full verification.
                        self._validate_retained_bundle(destination,bundle,records)
                    else:
                        os.replace(stage, destination)
                        moved = True
                        published = destination
                    path = (destination / bundle.entry.relative_to(bundle.root)).relative_to(self.root).as_posix()
                if promoting:
                    # Copying a previously linked bundle upgrades the same game.
                    # Its identity, user edits, and original import time survive.
                    game_id = existing["id"]
                    db.execute("UPDATE games SET storage='managed',path=?,source_path=?,size=? WHERE id=?",
                               (path, str(bundle.entry), sum(r["size"] for r in records), game_id))
                else:
                    cursor = db.execute("INSERT INTO games(title,system,storage,path,source_path,bundle_hash,size) "
                        "VALUES(?,?,?,?,?,?,?)", (title or bundle.entry.stem, system,
                        "linked" if linked else "managed", path, str(bundle.entry), bundle_hash,
                        sum(r["size"] for r in records)))
                    game_id = cursor.lastrowid
                    db.executemany("INSERT INTO media_files VALUES(?,?,?,?)",
                        [(game_id,r["relative_path"],r["sha256"],r["size"]) for r in records])
            committed = True
            game = self.game(game_id)
            game["import_action"] = "promoted" if promoting else "added"
            return game, not promoting
        except BaseException:
            if published is not None and not committed:
                # Do not remove files if an exceptional commit actually became
                # visible. If DB state is unreadable, retain the files for recovery.
                try:
                    with self.connect() as db:
                        visible_row = db.execute("SELECT storage FROM games WHERE bundle_hash=?",(bundle_hash,)).fetchone()
                        # A linked row existed before a failed promotion. It
                        # does not reference the directory we just published.
                        visible = bool(visible_row and visible_row["storage"] == "managed")
                except sqlite3.Error:
                    visible = True
                if not visible:
                    if published.resolve().parent != self.media.resolve() or stage.resolve().parent != self.media.resolve():
                        raise ValueError("Refusing rollback outside the library media root")
                    os.replace(published,stage)
                    moved = False
            raise
        finally:
            if not linked and not moved:
                _remove_staging(stage, self.media)

    def update_details(self, game_id, *, title, system):
        if not title.strip() or system not in SYSTEMS:
            raise ValueError("A title and supported system are required")
        if (self.game(game_id)["system"]=="pc") != (system=="pc"):
            raise ValueError("Add PC games through Add PC game; their platform cannot be changed")
        with self.connect() as db:
            db.execute("UPDATE games SET title=?,system=? WHERE id=?", (title.strip(),system,game_id))

    def _validate_retained_bundle(self,destination,bundle,records):
        if destination.is_symlink() or not destination.is_dir():
            raise FileExistsError(f"An incompatible media path already exists: {destination}")
        expected={r["relative_path"]:r for r in records}
        entry=destination/bundle.entry.relative_to(bundle.root)
        try:
            existing=inspect_bundle(entry)
            observed={p.relative_to(existing.root).as_posix():p for p in existing.files}
            if set(observed)!=set(expected):
                raise ValueError("Retained bundle references differ")
            for name,path in observed.items():
                digest,size=_hash_file(path)
                if (digest,size)!=(expected[name]["sha256"],expected[name]["size"]):
                    raise ValueError("Retained bundle bytes differ")
        except (OSError,ValueError) as error:
            raise FileExistsError(f"Existing retained media differs; it was not overwritten: {destination}") from error

    def update_metadata(self,game_id,**fields):
        allowed={"title","system","description","year","publisher","developer","region","genre","artwork_path"}
        if set(fields)-allowed:
            raise ValueError(f"Unknown metadata fields: {', '.join(sorted(set(fields)-allowed))}")
        original=self.game(game_id)
        if "system" in fields and (original["system"]=="pc") != (fields["system"]=="pc"):
            raise ValueError("Add PC games through Add PC game; their platform cannot be changed")
        clean={}
        for name,value in fields.items():
            if name=="year":
                if value in (None,""):
                    clean[name]=None
                    continue
                if isinstance(value,bool):
                    raise ValueError("Year must be a number")
                try: value=int(value)
                except (TypeError,ValueError) as error: raise ValueError("Year must be a number") from error
                if not 1900<=value<=2200:
                    raise ValueError("Year must be between 1900 and 2200")
            elif name=="system":
                if value not in SYSTEMS: raise ValueError("Unsupported system")
            elif name=="title":
                value=_name(value,"Title",500)
            else:
                if not isinstance(value,str) or "\0" in value:
                    raise ValueError(f"{name} must be text without NUL characters")
                value=value.strip()
                if len(value)>(20000 if name=="description" else 4096):
                    raise ValueError(f"{name} is too long")
                if name in ARTWORK_FIELDS.values() and value and not Path(value).is_absolute():
                    if not (self.root/value).resolve().is_relative_to(self.root):
                        raise ValueError("Relative artwork path leaves the library")
            clean[name]=value
        if clean:
            with self.connect() as db:
                db.execute("UPDATE games SET "+",".join(f"{key}=?" for key in clean)+" WHERE id=?",
                           (*clean.values(),game_id))
        return self.game(game_id)

    def set_flags(self,game_id,*,favorite=None,wishlist=None,completed=None):
        self.game(game_id)
        fields={name:value for name,value in dict(favorite=favorite,wishlist=wishlist,completed=completed).items() if value is not None}
        if any(not isinstance(value,(bool,int)) or value not in (0,1) for value in fields.values()):
            raise ValueError("Game flags must be booleans")
        if fields:
            with self.connect() as db:
                db.execute("UPDATE games SET "+",".join(f"{name}=?" for name in fields)+" WHERE id=?",
                           (*[int(value) for value in fields.values()],game_id))
        return self.game(game_id)

    def toggle_favorite(self,game_id):
        self.game(game_id)
        with self.connect() as db:
            db.execute("UPDATE games SET favorite=CASE favorite WHEN 0 THEN 1 ELSE 0 END WHERE id=?",(game_id,))
        return self.game(game_id)

    def collections(self):
        with self.connect() as db:
            rows=db.execute("SELECT c.*,COUNT(cg.game_id) AS game_count FROM collections c "
                            "LEFT JOIN collection_games cg ON cg.collection_id=c.id GROUP BY c.id ORDER BY c.name COLLATE NOCASE,c.id").fetchall()
        return [dict(row) for row in rows]

    def create_collection(self,name):
        name=_name(name,"Collection name")
        try:
            with self.connect() as db:
                cursor=db.execute("INSERT INTO collections(name,name_key) VALUES(?,?)",(name,name.casefold()))
                collection_id=cursor.lastrowid
        except sqlite3.IntegrityError as error:
            raise ValueError("A collection with that name already exists") from error
        return self.collection(collection_id)

    def collection(self,collection_id):
        with self.connect() as db:
            row=db.execute("SELECT c.*,COUNT(cg.game_id) AS game_count FROM collections c "
                           "LEFT JOIN collection_games cg ON cg.collection_id=c.id WHERE c.id=? GROUP BY c.id",(collection_id,)).fetchone()
        if row is None: raise KeyError(f"No collection {collection_id}")
        return dict(row)

    def rename_collection(self,collection_id,name):
        self.collection(collection_id)
        name=_name(name,"Collection name")
        try:
            with self.connect() as db:
                db.execute("UPDATE collections SET name=?,name_key=? WHERE id=?",(name,name.casefold(),collection_id))
        except sqlite3.IntegrityError as error:
            raise ValueError("A collection with that name already exists") from error
        return self.collection(collection_id)

    def delete_collection(self,collection_id):
        original=self.collection(collection_id)
        with self.connect() as db: db.execute("DELETE FROM collections WHERE id=?",(collection_id,))
        return original

    def add_to_collection(self,collection_id,game_id):
        self.collection(collection_id)
        self.game(game_id)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO collection_games(collection_id,game_id,position) VALUES(?,?,COALESCE((SELECT MAX(position) FROM collection_games WHERE collection_id=?),0)+1)",(collection_id,game_id,collection_id))
        return self.collection(collection_id)

    def move_in_collection(self,collection_id,game_id,delta):
        """Move one game earlier (negative) or later in its playlist; positions are renumbered 1..n."""
        self.collection(collection_id)
        with self.connect() as db:
            rows=[row["game_id"] for row in db.execute("SELECT game_id FROM collection_games WHERE collection_id=? ORDER BY position,game_id",(collection_id,))]
            if game_id not in rows:raise KeyError("That game is not in this playlist")
            index=rows.index(game_id);target=max(0,min(len(rows)-1,index+int(delta)))
            rows.insert(target,rows.pop(index))
            for position,identity in enumerate(rows,1):
                db.execute("UPDATE collection_games SET position=? WHERE collection_id=? AND game_id=?",(position,collection_id,identity))
        return rows

    def remove_from_collection(self,collection_id,game_id):
        self.collection(collection_id)
        with self.connect() as db:
            db.execute("DELETE FROM collection_games WHERE collection_id=? AND game_id=?",(collection_id,game_id))
        return self.collection(collection_id)

    def game_collections(self,game_id):
        self.game(game_id)
        with self.connect() as db:
            rows=db.execute("SELECT c.* FROM collections c JOIN collection_games cg ON cg.collection_id=c.id "
                            "WHERE cg.game_id=? ORDER BY c.name COLLATE NOCASE,c.id",(game_id,)).fetchall()
        return [dict(row) for row in rows]

    def start_session(self,game_id,*,started_at=None):
        started=_utc(started_at)
        self.game(game_id)
        with self.connect() as db:
            cursor=db.execute("INSERT INTO play_sessions(game_id,started_at) VALUES(?,?)",(game_id,started))
            db.execute("UPDATE games SET launch_count=launch_count+1,last_played=CASE "
                       "WHEN last_played IS NULL OR last_played<? THEN ? ELSE last_played END WHERE id=?",(started,started,game_id))
            session_id=cursor.lastrowid
        return session_id

    def finish_session(self,session_id,*,duration_seconds=None,ended_at=None,exit_code=None):
        ended=_utc(ended_at)
        if exit_code is not None and (isinstance(exit_code,bool) or not isinstance(exit_code,int) or not -(2**31)<=exit_code<2**32):
            raise ValueError("Exit code must be an integer process status")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM play_sessions WHERE id=?",(session_id,)).fetchone()
            if row is None: raise KeyError(f"No play session {session_id}")
            if row["status"]=="completed": return dict(row)
            elapsed=(datetime.fromisoformat(ended.replace("Z","+00:00"))-
                     datetime.fromisoformat(row["started_at"].replace("Z","+00:00"))).total_seconds()
            if elapsed<0: raise ValueError("A session cannot end before it starts")
            seconds=_seconds(elapsed if duration_seconds is None else duration_seconds)
            db.execute("UPDATE play_sessions SET ended_at=?,duration_seconds=?,status='completed',exit_code=? WHERE id=?",
                       (ended,seconds,exit_code,session_id))
            db.execute("UPDATE games SET play_seconds=play_seconds+?,last_played=CASE "
                       "WHEN last_played IS NULL OR last_played<? THEN ? ELSE last_played END WHERE id=?",
                       (seconds,ended,ended,row["game_id"]))
            result=dict(db.execute("SELECT * FROM play_sessions WHERE id=?",(session_id,)).fetchone())
        return result

    def record_session(self,game_id,seconds,*,played_at=None):
        seconds=_seconds(seconds)
        ended=_utc(played_at)
        started=_utc(datetime.fromisoformat(ended.replace("Z","+00:00"))-timedelta(seconds=seconds))
        self.game(game_id)
        with self.connect() as db:
            cursor=db.execute("INSERT INTO play_sessions(game_id,started_at,ended_at,duration_seconds,status) VALUES(?,?,?,?,'completed')",
                              (game_id,started,ended,seconds))
            db.execute("UPDATE games SET play_seconds=play_seconds+?,launch_count=launch_count+1,last_played=CASE "
                       "WHEN last_played IS NULL OR last_played<? THEN ? ELSE last_played END WHERE id=?",
                       (seconds,ended,ended,game_id))
            session_id=cursor.lastrowid
        return next(row for row in self.sessions(game_id) if row["id"]==session_id)

    def sessions(self,game_id=None):
        with self.connect() as db:
            rows=db.execute("SELECT * FROM play_sessions WHERE (? IS NULL OR game_id=?) ORDER BY started_at DESC,id DESC",
                            (game_id,game_id)).fetchall()
        return [dict(row) for row in rows]

    def remove_game(self,game_id):
        """Remove catalogue/history references only. Never delete source or media."""
        original=self.game(game_id)
        with self.connect() as db: db.execute("DELETE FROM games WHERE id=?",(game_id,))
        original["media_retained"]=True
        return original

    def artwork_for(self,game,role="cover"):
        if role not in ARTWORK_FIELDS:raise ValueError("Unsupported artwork role")
        value=game.get(ARTWORK_FIELDS[role],"")
        if not value: return None
        path=Path(value)
        if not path.is_absolute():
            path=(self.root/path).resolve()
            if not path.is_relative_to(self.root): raise ValueError("Artwork path leaves library")
        return path

    def artwork_provenance(self,game_id):
        """Return per-role source records, without accounts or request credentials."""
        value=self.game(game_id).get("artwork_provenance","{}")
        try:result=json.loads(value)
        except (ValueError,TypeError):return {}
        return result if isinstance(result,dict) else {}

    def set_artwork(self,game_id,source,*,role="cover",provenance=None,overwrite=True):
        """Copy selected artwork into portable storage; the original is unchanged."""
        return self.set_artworks(game_id,{role:source},provenance=provenance,overwrite=overwrite)

    def set_artworks(self,game_id,role_sources,*,provenance=None,overwrite=False,cancelled=None):
        """Publish content files, then commit every selected role in one transaction.

        Missing-role scraping preserves current user artwork by default. A failed
        or cancelled batch never leaves only some role references updated. Shared
        content-addressed files are retained if a database commit fails; deleting
        them could destroy artwork referenced by another game or concurrent job.
        The caller validates downloaded image contents before supplying paths.
        """
        self.game(game_id)
        if not isinstance(role_sources,dict) or not role_sources or set(role_sources)-ARTWORK_FIELDS.keys():
            raise ValueError("Choose one or more supported artwork roles")
        if not isinstance(overwrite,bool):raise ValueError("Artwork overwrite must be a boolean")
        def check():
            if cancelled is not None and cancelled.is_set():raise ImportCancelled("Artwork download cancelled")
        check()
        sources={}
        for role,value in role_sources.items():
            source=Path(value).expanduser().resolve(strict=True)
            if not source.is_file() or source.suffix.casefold() not in {".png",".jpg",".jpeg",".webp",".gif"}:
                raise ValueError("Choose a PNG, JPEG, WebP or GIF artwork file")
            if source.stat().st_size>50*1024*1024:raise ValueError("Artwork exceeds 50 MiB")
            sources[role]=source
        folder=self.root/"artwork"
        folder.mkdir(exist_ok=True)
        if not folder.resolve().is_relative_to(self.root): raise ValueError("Artwork folder leaves library")
        temporary_files=[];published={};records={}
        try:
            for role,source in sources.items():
                check()
                with tempfile.NamedTemporaryFile(prefix=".art-",dir=folder,delete=False) as output:
                    temporary=Path(output.name);temporary_files.append(temporary)
                    digest,size=_hash_file(source,cancelled=cancelled,sink=output)
                    output.flush();os.fsync(output.fileno())
                destination=folder/(digest+source.suffix.casefold())
                if destination.is_symlink() or destination.resolve().parent!=folder.resolve():
                    raise ValueError("Artwork destination leaves its storage folder")
                if destination.exists():
                    if _hash_file(destination,cancelled=cancelled)!=(digest,size):
                        raise ValueError("Existing artwork is damaged; it was not overwritten")
                else:
                    # Atomic no-clobber publication also handles concurrent scrapes.
                    try:os.link(temporary,destination)
                    except FileExistsError:
                        if destination.is_symlink() or _hash_file(destination,cancelled=cancelled)!=(digest,size):
                            raise ValueError("Existing artwork is damaged; it was not overwritten")
                published[role]=destination.relative_to(self.root).as_posix()
                role_info=provenance.get(role) if isinstance(provenance,dict) and role in provenance else provenance
                records[role]=_artwork_source_record(role_info,digest,size)
            check()
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row=db.execute("SELECT * FROM games WHERE id=?",(game_id,)).fetchone()
                if row is None:raise ValueError("The game is no longer in this library")
                try:existing=json.loads(row["artwork_provenance"])
                except (TypeError,ValueError):existing={}
                if not isinstance(existing,dict):existing={}
                fields={};updated_roles=[]
                for role,path in published.items():
                    name=ARTWORK_FIELDS[role]
                    retained=row[name]
                    missing_managed=False
                    if retained and not overwrite:
                        previous=Path(retained)
                        if not previous.is_absolute():
                            previous=(self.root/previous).resolve()
                            missing_managed=(previous.is_relative_to(folder.resolve()) and not previous.exists())
                        if not missing_managed:continue
                    fields[name]=path;existing[role]=records[role]
                    updated_roles.append(role)
                check()
                if fields:
                    fields["artwork_provenance"]=json.dumps(existing,sort_keys=True,separators=(",",":"))
                    db.execute("UPDATE games SET "+",".join(f"{name}=?" for name in fields)+" WHERE id=?",
                               (*fields.values(),game_id))
            result=self.game(game_id)
            # A scrape may race a newly selected manual cover. Tell the worker
            # which roles really committed, rather than counting downloaded files.
            result["artwork_updated_roles"]=updated_roles
            return result
        finally:
            for temporary in temporary_files:
                if not temporary.exists():continue
                if temporary.resolve().parent!=folder.resolve() or not temporary.name.startswith(".art-"):
                    raise ValueError("Refusing artwork cleanup outside the staging folder")
                temporary.unlink()

    def media_health(self,game_id,*,verify_hashes=False):
        game=self.game(game_id)
        if verify_hashes:
            files=self.verify(game_id)
        else:
            with self.connect() as db:
                rows=db.execute("SELECT relative_path FROM media_files WHERE game_id=? ORDER BY relative_path",(game_id,)).fetchall()
            base=self.media/game["bundle_hash"] if game["storage"]=="managed" else Path(game["source_path"]).parent
            files=[]
            for row in rows:
                try:
                    path=(base/row["relative_path"]).resolve()
                    present=path.is_relative_to(base.resolve()) and path.is_file()
                except (OSError,ValueError): present=False
                files.append(dict(path=row["relative_path"],status="available" if present else "missing"))
        problems=[row for row in files if row["status"] not in {"ok","available"}]
        return dict(game_id=game_id,entry_available=game["entry_available"],available=not problems and game["entry_available"],
                    status="healthy" if not problems and game["entry_available"] else "changed" if any(row["status"]=="changed" for row in files) else game["health"],
                    files=files,checked_hashes=verify_hashes)

    def verify(self, game_id, cancelled=None):
        game = self.game(game_id)
        entry = self.path_for(game)
        # Managed descriptors can live below the bundle root (e.g. playlists).
        base = (self.media / game["bundle_hash"]) if game["storage"] == "managed" else Path(game["source_path"]).parent
        with self.connect() as db:
            files = db.execute("SELECT * FROM media_files WHERE game_id=? ORDER BY relative_path", (game_id,)).fetchall()
        results = []
        for row in files:
            path = (base / row["relative_path"]).resolve()
            if not path.is_relative_to(base.resolve()):
                raise ValueError("Invalid stored bundle path")
            try:
                digest,size = _hash_file(path,cancelled)
                status = "ok" if (digest,size) == (row["sha256"],row["size"]) else "changed"
            except OSError:
                status = "missing"
            results.append(dict(path=row["relative_path"], status=status))
        return results

    def configure_launcher(self, system, executable, arguments):
        if system not in SYSTEMS or not isinstance(arguments,list) or not all(isinstance(a,str) for a in arguments):
            raise ValueError("Supported system and JSON array of argument strings required")
        with self.connect() as db:
            db.execute("INSERT INTO launchers VALUES(?,?,?) ON CONFLICT(system) DO UPDATE "
                       "SET executable=excluded.executable,arguments=excluded.arguments",
                       (system,str(Path(executable).expanduser().resolve()),json.dumps(arguments)))

    def launcher(self, system):
        with self.connect() as db:
            row = db.execute("SELECT * FROM launchers WHERE system=?", (system,)).fetchone()
        if not row:
            raise ValueError(f"Configure an emulator for {SYSTEMS.get(system,system)} first")
        return dict(executable=row["executable"],arguments=json.loads(row["arguments"]))
