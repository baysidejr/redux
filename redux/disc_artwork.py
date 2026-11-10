"""Optional, bounded artwork lookup for an already identified physical disc.

Call this from a worker, never the UI thread. Artwork cannot establish a disc's
identity. Exact title/region matches are required and failures leave detection
usable. Downloads contain PNG artwork only; no emulator or game data is fetched.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from PIL import Image

_BASE = "https://raw.githubusercontent.com/libretro-thumbnails/Sega_-_Saturn/master/"
_REPOSITORIES = {"saturn": "Sega_-_Saturn", "sega_cd": "Sega_-_Mega-CD_-_Sega_CD"}
_MAX_BYTES = 8 * 1024 * 1024
_TIMEOUT = 5
_US_REGIONS = {"u", "us", "usa", "north america", "north america/us"}


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _title_key(value):
    """Whitespace/case/punctuation plus a terminal II/2 are the only aliases."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"\s*\((?:usa|us|north america)\)\s*$", "", text)
    text = re.sub(r"\bii$", "2", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _match(identity, catalogue):
    if _field(identity, "system") not in _REPOSITORIES:
        return None, "Online disc artwork needs a supported identified disc."
    details = _field(identity, "details", {}) or {}
    regions = []
    for source in (identity, details, catalogue or {}):
        for key in ("region", "region_name", "area_symbols", "area", "saturn_region"):
            value = _field(source, key)
            if value:
                regions.append(str(value).casefold().strip())
    if not regions or any(value not in _US_REGIONS for value in regions):
        return None, "Artwork lookup needs an unambiguous USA disc region."
    disc_title = str(_field(identity, "title", "") or "").strip()
    canonical = str(_field(catalogue, "title", "") or "").strip()
    if canonical and _title_key(canonical) != _title_key(disc_title):
        return None, "Catalogue and disc titles disagree; artwork was not guessed."
    serial = re.sub(r"[^A-Z0-9]", "", str(_field(identity, "game_id", "") or "").upper())
    # This alias is anchored to the observed USA serial and exact header title.
    if _field(identity, "system") == "saturn" and serial == "MK81113":
        if _title_key(disc_title) != "worldseriesbaseball2":
            return None, "Disc serial and known title disagree; artwork was not guessed."
        canonical = "World Series Baseball II"
    elif not canonical:
        canonical = disc_title
    canonical = re.sub(r"\s*\((?:USA|US|North America)\)\s*$", "", canonical, flags=re.I)
    if _title_key(canonical) in {"segacddisc", "saturndisc"}:
        return None, "Disc title is unavailable; artwork was not guessed."
    if not canonical or len(canonical) > 180 or any(c in canonical for c in "/\\") or any(ord(c) < 32 for c in canonical):
        return None, "Disc title is unsuitable for an exact artwork lookup."
    return canonical, None


def _image_info(data, *, png_only=True):
    if not 1 <= len(data) <= _MAX_BYTES:
        raise ValueError("Artwork exceeds the byte limit")
    with Image.open(BytesIO(data)) as image:
        width, height = image.size
        formats = {"PNG"} if png_only else {"PNG", "JPEG", "WEBP", "BMP", "GIF"}
        if image.format not in formats or not (64 <= width <= 8192 and 64 <= height <= 8192) or width * height > 16_000_000:
            raise ValueError("Artwork is not a bounded supported image")
        image.verify()
    with Image.open(BytesIO(data)) as image:
        image.load()
    return width, height


def _local_art(value):
    if not value:
        return None
    try:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file() or not 1 <= path.stat().st_size <= _MAX_BYTES:
            return None
        _image_info(path.read_bytes(), png_only=False)
        return str(path)
    except (OSError, ValueError, TypeError):
        return None


def _download(url):
    initial = urlparse(url)
    if initial.scheme != "https" or initial.hostname != "raw.githubusercontent.com" or initial.username or initial.password:
        raise ValueError("Artwork requires the expected HTTPS host")
    request = Request(url, headers={"User-Agent": "ReduxMediaLibrary/1.0", "Accept": "image/png"})
    with urlopen(request, timeout=_TIMEOUT) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname != "raw.githubusercontent.com":
            raise ValueError("Artwork redirected outside the expected HTTPS host")
        length = response.headers.get("Content-Length")
        if length and int(length) > _MAX_BYTES:
            raise ValueError("Artwork exceeds the byte limit")
        data = response.read(_MAX_BYTES + 1)
    width, height = _image_info(data)
    return data, width, height


def _safe_child(folder, name):
    candidate = folder / name
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Artwork cache path leaves its dedicated directory")
    return candidate


def _cached(folder, entry, urls):
    if not isinstance(entry, dict) or entry.get("url") not in urls:
        return None
    name = entry.get("file", "")
    if not isinstance(name, str) or not re.fullmatch(r"(?:cover|hero)-[a-f0-9]{64}\.png", name):
        return None
    path = _safe_child(folder, name)
    if not path.is_file() or not 1 <= path.stat().st_size <= _MAX_BYTES:
        return None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        return None
    _image_info(data)
    return str(path)


def _atomic_bytes(path, data, *, replace):
    fd, temporary = tempfile.mkstemp(prefix=".art-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        if replace:
            os.replace(temporary, path)
        else:
            # Linking publishes a complete file atomically without replacing a
            # pre-existing file, even if another worker finishes concurrently.
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ValueError("Existing artwork cache file was not replaced")
    finally:
        Path(temporary).unlink(missing_ok=True)


def fetch_disc_artwork(cache_root, identity, catalogue_match=None):
    """Return cover_path/hero_path/source/notes; network/cache failures are soft.

    Existing catalogue artwork wins and is never modified. Only exact USA
    Saturn and Sega CD names are requested from libretro-thumbnails. Download provenance
    and content hashes are recorded beside content-addressed PNG cache files.
    """
    result = {"cover_path": None, "hero_path": None, "source": None, "notes": []}
    try:
        canonical, note = _match(identity, catalogue_match)
        if note:
            result["notes"].append(note)
            return result
        catalogue = catalogue_match or {}
        result["cover_path"] = _local_art(_field(catalogue, "artwork_path"))
        result["hero_path"] = _local_art(_field(catalogue, "hero_path"))
        if not result["hero_path"]:
            for value in _field(catalogue, "screenshot_paths", ()) or ():
                result["hero_path"] = _local_art(value)
                if result["hero_path"]:
                    break
        if result["cover_path"] or result["hero_path"]:
            result["source"] = "catalogue artwork"
        if result["cover_path"] and result["hero_path"]:
            return result
        system = _field(identity, "system")
        repository = _REPOSITORIES[system]
        base = f"https://raw.githubusercontent.com/libretro-thumbnails/{repository}/master/"
        fingerprint = {"system": system, "title": canonical, "region": "USA",
                       "serial": str(_field(identity, "game_id", "") or ""),
                       "fingerprint": str(_field(_field(identity, "details", {}), "fingerprint", "") or "")}
        digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode("utf-8")).hexdigest()
        root = Path(cache_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        folder = _safe_child(root, system + "-" + digest)
        folder.mkdir(exist_ok=True)
        metadata_path = _safe_child(folder, "provenance.json")
        metadata = {"identity": fingerprint, "entries": {}}
        if metadata_path.is_file() and metadata_path.stat().st_size <= 64 * 1024:
            try:
                previous = json.loads(metadata_path.read_text(encoding="utf-8"))
                if previous.get("identity") == fingerprint and isinstance(previous.get("entries"), dict):
                    metadata = previous
            except (ValueError, OSError, AttributeError):
                pass
        filename = quote(canonical + " (USA).png", safe="")
        for kind, directories in (("cover", ("Named_Boxarts",)), ("hero", ("Named_Snaps", "Named_Titles"))):
            field = kind + "_path"
            if result[field]:
                continue
            urls = [base + directory + "/" + filename for directory in directories]
            try:
                cached = _cached(folder, metadata["entries"].get(kind), urls)
                if cached:
                    result[field] = cached
                    result["source"] = "libretro-thumbnails / " + repository
                    continue
            except (OSError, ValueError):
                pass
            for url in urls:
                try:
                    data, width, height = _download(url)
                    sha256 = hashlib.sha256(data).hexdigest()
                    path = _safe_child(folder, f"{kind}-{sha256}.png")
                    _atomic_bytes(path, data, replace=False)
                    metadata["entries"][kind] = {"url": url, "file": path.name, "sha256": sha256,
                        "bytes": len(data), "width": width, "height": height,
                        "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
                    _atomic_bytes(metadata_path, (json.dumps(metadata, indent=2) + "\n").encode("utf-8"), replace=True)
                    result[field] = str(path)
                    result["source"] = "libretro-thumbnails / " + repository
                    break
                except Exception:
                    # A network/PNG/cache failure must not interrupt insertion
                    # detection. No traceback or remote response enters the UI.
                    continue
            if not result[field]:
                result["notes"].append(f"No verified exact-match {kind} artwork was available.")
    except Exception:
        result["notes"].append("Optional disc artwork lookup was unavailable; disc identification remains valid.")
    return result
