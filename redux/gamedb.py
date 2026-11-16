"""Redux's preloaded game database: art and facts for the CD systems and for PC games on GOG.

Redux never scrapes. Each system folder under redux/database holds games.json
and art/<game id>/ (boxart.jpg, gameimage.png or .jpg, hero.jpg); the layout is
documented in redux/database/README.md. A disc is recognised by the serial in
its header first and by its header title second; a GOG game by its product id.
"""
from __future__ import annotations

import json
import threading
import re
import sys
import unicodedata
from pathlib import Path

SYSTEM_ORDER = ("sega_cd", "psx", "saturn", "pce_cd", "neo_geo_cd", "3do", "jaguar_cd", "pc")
LABELS = {"sega_cd": "Sega CD", "psx": "PlayStation", "saturn": "Saturn", "pce_cd": "PC Engine CD",
          "neo_geo_cd": "Neo Geo CD", "3do": "3DO", "jaguar_cd": "Jaguar CD", "pc": "PC (GOG)"}
ART_ROLES = ("boxart", "gameimage", "hero")
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


RELEASE_URL = "https://github.com/baysidejr/redux/releases/download/{release}/{asset}"
MAX_ASSET_BYTES = 2 * 1024 ** 3
MAX_PARTS = 16


def database_root():
    """The shipped database: inside the build bundle, or the source tree."""
    bundle = getattr(sys, "_MEIPASS", None)
    roots = [Path(bundle) / "database", Path(bundle) / "redux" / "database"] if bundle else []
    roots.append(Path(__file__).resolve().parent / "database")
    for root in roots:
        if root.is_dir():
            return root
    return roots[-1]


def downloaded_root():
    """Where downloaded art lives: the user's data folder, never Program Files."""
    try:
        from .windows_paths import redux_user_data
        return redux_user_data() / "database"
    except (ImportError, OSError):
        return Path.home() / "ReduxMediaLibrary" / "database"


class DownloadError(RuntimeError):
    pass


def title_key(value):
    """Loose title identity: case, punctuation, 'the', '&' and roman numerals fold away."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()
    text = re.sub(r"\s*\([^)]*\)", " ", text)
    text = re.sub(r"&|\band\b", " ", text)
    text = re.sub(r"^the\s+|,\s*the$", " ", text)
    text = re.sub(r"\b(?:ii|iii|iv|v|vi|vii|viii|ix|x)\b", lambda m: _ROMAN[m[0]], text)
    return re.sub(r"[^a-z0-9]", "", text)


def serial_variants(value):
    """Spellings of one product code, most exact first: T-93175-50, T-93175, T9317550, T93175."""
    text = re.sub(r"\s+", "", str(value or "")).upper()
    if not text:
        return []
    variants = [text]
    base = re.sub(r"-\d{1,2}$", "", text)
    if base != text:
        variants.append(base)
    for item in list(variants):
        plain = item.replace("-", "")
        if plain not in variants:
            variants.append(plain)
    return variants


def players_text(value):
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    if count < 1:
        return None
    return "1 player" if count == 1 else f"{count} players"


class GameDatabase:
    """Lazy, read-only access to every system's catalog."""

    def __init__(self, root=None, downloads=None):
        self.root = Path(root) if root else database_root()
        self.downloads = Path(downloads) if downloads else downloaded_root()
        self._systems = {}
        self._lock = threading.RLock()
        self._manifest = None

    # ------------------------------------------------------------------ loading

    def available(self, system):
        return (self.root / system / "games.json").is_file()

    def manifest(self):
        """What art the release holds for each system; empty when the build has no manifest."""
        if self._manifest is None:
            try:
                self._manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._manifest = {}
        return self._manifest

    def art_status(self, system):
        """'bundled', 'installed', 'available' (downloadable), or 'none'."""
        entry = (self.manifest().get("systems") or {}).get(system, {})
        if entry.get("bundled") or (self.root / system / "art").is_dir() and any((self.root / system / "art").iterdir()):
            return "bundled"
        marker = self.downloads / system / "art" / ".complete"
        if marker.is_file():
            return "installed" if marker.read_text(encoding="utf-8").strip() == str(entry.get("sha256", "")) else "available"
        return "available" if self.art_parts(entry) else "none"

    def art_size(self, system):
        return int(((self.manifest().get("systems") or {}).get(system, {}) or {}).get("bytes") or 0)

    def download_art(self, system, *, progress=None, cancelled=None, opener=None):
        """Fetch a system's art from the GitHub release, check each file's hash, unpack it.

        Large systems ship their art in several zips (the manifest's ``parts``); every
        part must arrive before the art is swapped in. Everything lands in the downloads
        folder; the bundle is never written to. ``progress(done_bytes, total_bytes)`` is
        called as data arrives, counting across all parts.
        """
        import shutil
        entry = (self.manifest().get("systems") or {}).get(system)
        parts = self.art_parts(entry)
        if not parts:
            raise DownloadError("This build has no art download for that system.")
        release = self.manifest().get("release", "database")
        total = sum(int(part.get("bytes") or 0) for part in parts)
        if any(int(part.get("bytes") or 0) > MAX_ASSET_BYTES for part in parts):
            raise DownloadError("The art download is larger than Redux accepts.")
        target_root = self.downloads / system
        target_root.mkdir(parents=True, exist_ok=True)
        staging = target_root / "art.new"
        if staging.exists():
            shutil.rmtree(staging)
        offset = 0
        try:
            for part in parts:
                url = RELEASE_URL.format(release=release, asset=part["asset"])
                archive = self._download_part(url, str(part.get("sha256", "")), offset, total, target_root, progress, cancelled, opener)
                try:
                    self._unpack_art(archive, staging)
                finally:
                    archive.unlink(missing_ok=True)
                offset += int(part.get("bytes") or 0)
        except DownloadError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        art_root = target_root / "art"
        if art_root.exists():
            shutil.rmtree(art_root)
        staging.rename(art_root)
        (art_root / ".complete").write_text(str(entry.get("sha256", "")), encoding="utf-8")
        self._systems.pop(system, None)
        return art_root

    @staticmethod
    def art_parts(entry):
        """The zips a manifest entry names: ``parts`` for a split system, else its single ``asset``."""
        if not entry:
            return []
        parts = entry.get("parts")
        if isinstance(parts, list) and parts:
            return [part for part in parts[:MAX_PARTS] if isinstance(part, dict) and part.get("asset")]
        return [dict(asset=entry["asset"], bytes=entry.get("bytes"), sha256=entry.get("sha256"))] if entry.get("asset") else []

    def _download_part(self, url, expected, offset, total, target_root, progress, cancelled, opener):
        import hashlib
        import tempfile
        from . import net
        digest = hashlib.sha256()
        done = 0
        with tempfile.NamedTemporaryFile(prefix="art-", suffix=".zip", dir=target_root, delete=False) as temp:
            temp_path = Path(temp.name)
            try:
                opener = opener or (lambda u: net.urlopen(u, headers={"User-Agent": "Redux"}, timeout=60))
                with opener(url) as response:
                    while True:
                        if cancelled is not None and cancelled.is_set():
                            raise DownloadError("Download cancelled.")
                        chunk = response.read(1 << 18)
                        if not chunk:
                            break
                        done += len(chunk)
                        if done > MAX_ASSET_BYTES:
                            raise DownloadError("The art download is larger than Redux accepts.")
                        digest.update(chunk)
                        temp.write(chunk)
                        if progress:
                            progress(offset + done, total)
            except OSError as error:
                # Close before deleting: Windows refuses to delete an open file, and that
                # "being used by another process" error used to hide the real failure.
                temp.close()
                temp_path.unlink(missing_ok=True)
                raise DownloadError(f"The download failed: {net.friendly_error(error)}") from error
            except DownloadError:
                temp.close()
                temp_path.unlink(missing_ok=True)
                raise
        if expected and digest.hexdigest() != expected:
            temp_path.unlink(missing_ok=True)
            raise DownloadError("The downloaded file did not match the release's checksum, so it was discarded.")
        return temp_path

    @staticmethod
    def _unpack_art(archive_path, staging):
        import shutil
        import zipfile
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    name = member.filename
                    if member.is_dir() or not name.startswith("art/") or ".." in name.split("/") or name.startswith("/"):
                        continue
                    destination = staging / Path(name).relative_to("art")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, destination.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
        except (zipfile.BadZipFile, OSError) as error:
            raise DownloadError(f"The art archive could not be unpacked: {error}") from error

    def remove_art(self, system):
        import shutil
        shutil.rmtree(self.downloads / system / "art", ignore_errors=True)
        self._systems.pop(system, None)

    def _load(self, system):
        with self._lock:
            return self._load_locked(system)

    def _load_locked(self, system):
        if system in self._systems:
            return self._systems[system]
        entry = dict(games=[], by_id={}, by_serial={}, by_title={}, label=LABELS.get(system, system))
        path = self.root / system / "games.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            games = [game for game in data.get("games", []) if isinstance(game, dict) and game.get("id") and game.get("title")]
            entry["label"] = data.get("label") or entry["label"]
        except (OSError, ValueError, AttributeError):
            games = []
        games.sort(key=lambda game: (title_key(game["title"]), game["title"].casefold()))
        entry["games"] = games
        for game in games:
            entry["by_id"][game["id"]] = game
            entry["by_title"].setdefault(title_key(game["title"]), []).append(game)
            for release in game.get("releases") or []:
                entry["by_title"].setdefault(title_key(release.get("name", "")), []).append(game)
                for serial in release.get("serials") or []:
                    for variant in serial_variants(serial):
                        owners = entry["by_serial"].setdefault(variant, [])
                        if game not in owners:
                            owners.append(game)
        self._systems[system] = entry
        return entry

    def invalidate(self):
        self._systems.clear()

    # ------------------------------------------------------------------ reading

    def systems(self):
        """Every system Redux plans to cover, with what the database holds for it."""
        result = []
        for system in SYSTEM_ORDER:
            available = self.available(system)
            games = self._load(system)["games"] if available else []
            result.append(dict(system=system, label=LABELS.get(system, system), available=available, games=len(games),
                               boxart=sum(1 for g in games if "boxart" in (g.get("art") or {})),
                               hero=sum(1 for g in games if "hero" in (g.get("art") or {})),
                               descriptions=sum(1 for g in games if g.get("description"))))
        return result

    def games(self, system):
        return list(self._load(system)["games"])

    def game(self, system, game_id):
        return self._load(system)["by_id"].get(game_id)

    def art(self, game, role):
        """The art file for a role, or None when the database has none."""
        relative = (game.get("art") or {}).get(role)
        if not relative or not game.get("system"):
            return None
        for base in (self.root, self.downloads):
            path = base / game["system"] / relative
            if path.is_file():
                return path
        return None

    def find(self, system, serial=None, title=None):
        """The database game for an identified disc: serial first, header title second.

        Serials repeat across games now and then (a publisher reused a code, a
        32X CD edition shares its parent's), so a matching title settles a tie;
        with no title help, the plainest edition wins.
        """
        entry = self._load(system)
        wanted = title_key(title) if title else ""
        for variant in serial_variants(serial):
            owners = entry["by_serial"].get(variant)
            if not owners:
                continue
            if len(owners) == 1:
                return owners[0]
            if wanted:
                for game in owners:
                    if title_key(game["title"]) == wanted or any(title_key(r.get("name", "")) == wanted for r in game.get("releases") or []):
                        return game
            return sorted(owners, key=lambda game: ("(" in game["title"], game["title"]))[0]
        if wanted:
            owners = entry["by_title"].get(wanted)
            if owners:
                return owners[0]
        return None

    # ------------------------------------------------------------------ presentation

    @staticmethod
    def facts(game):
        """(label, value) pairs for the information page, only for facts the database has."""
        facts = []
        if game.get("year"):
            facts.append(("Released", str(game["year"])))
        if game.get("developer"):
            facts.append(("Developer", game["developer"]))
        if game.get("publisher"):
            facts.append(("Publisher", game["publisher"]))
        if game.get("genres"):
            facts.append(("Genre", ", ".join(game["genres"])))
        players = players_text(game.get("players"))
        if players:
            facts.append(("Players", players))
        if game.get("rating"):
            facts.append(("Rating", game["rating"]))
        return facts

    @staticmethod
    def releases_text(game):
        """'USA 4407 · Europe 4407 · Japan G-6025', one entry per region."""
        seen = {}
        for release in game.get("releases") or []:
            region = release.get("region") or "Unknown region"
            serials = [s for s in release.get("serials") or []]
            if region not in seen:
                seen[region] = serials[0] if serials else ""
        return " · ".join(f"{region} {serial}".strip() for region, serial in seen.items())
