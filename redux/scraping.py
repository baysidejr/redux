"""Artwork lookup, bounded downloads and portable publication for scraper services.

The worker receives account settings explicitly. Provider URLs and credentials
never enter status messages, saved provenance or a release bundle. No game bytes
are uploaded: identification requests use the selected title, system and region.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import ipaddress
from pathlib import Path
import re
import socket
import tempfile
import unicodedata
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .library import ImportCancelled
from . import scraper_providers as providers
from .scraper_settings import ScraperSettings


MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
ROLES = ("cover", "tile", "hero", "screenshot", "logo")


def _cancel(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise ImportCancelled("Artwork download cancelled")


def _safe_image_url(url, *, resolve=False):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        if parsed.hostname.casefold() in {"localhost", "localhost.localdomain"} or parsed.hostname.casefold().endswith((".localhost", ".local")):
            raise ValueError
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                raise ValueError
        except ValueError as error:
            # A DNS hostname is allowed; an actual non-public IP is not.
            if re.fullmatch(r"[0-9.]+", parsed.hostname) or ":" in parsed.hostname:
                raise ValueError from error
        if resolve:
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ValueError
        return url
    except (TypeError, ValueError, OSError):
        raise ValueError("The artwork service returned an unsupported image address.") from None


class _ImageRedirect(HTTPRedirectHandler):
    def __init__(self, *, resolve=False):
        super().__init__()
        self.resolve = resolve

    def redirect_request(self, request, fp, code, message, headers, new_url):
        from urllib.parse import urljoin, parse_qsl
        target = urljoin(request.full_url, new_url)
        _safe_image_url(target, resolve=self.resolve)
        old, new = urlsplit(request.full_url), urlsplit(target)
        sensitive = {"devid", "devpassword", "ssid", "sspassword", "username", "password", "api_key", "apikey", "token", "access_token", "client_secret"}
        authenticated = any(key.casefold() in sensitive for key, _ in parse_qsl(old.query))
        same_screenscraper = all(host == "screenscraper.fr" or host.endswith(".screenscraper.fr") for host in (old.hostname or "", new.hostname or ""))
        if authenticated and old.hostname != new.hostname and not same_screenscraper:
            raise ValueError("Authenticated artwork cannot redirect outside its service.")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def download_image(url, destination, *, cancelled=None, opener=None):
    """Normalize a bounded remote bitmap to PNG without exposing its URL in errors."""
    _cancel(cancelled)
    _safe_image_url(url)
    try:
        from . import __version__
        _safe_image_url(url, resolve=opener is None)
        opener = opener or build_opener(_ImageRedirect(resolve=True)).open
        request = Request(url, headers={"User-Agent": "ReduxMediaLibrary/" + __version__, "Accept": "image/png,image/jpeg,image/webp"})
        with opener(request, timeout=12) as response:
            _safe_image_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_IMAGE_BYTES:
                raise ValueError("Artwork exceeds the download limit.")
            blocks, count = [], 0
            while True:
                _cancel(cancelled)
                block = response.read(min(65536, MAX_IMAGE_BYTES + 1 - count))
                if not block:
                    break
                blocks.append(block); count += len(block)
                if count > MAX_IMAGE_BYTES:
                    raise ValueError("Artwork exceeds the download limit.")
        raw = b"".join(blocks)
        with Image.open(BytesIO(raw)) as source:
            if (source.format not in {"PNG", "JPEG", "WEBP", "GIF"} or
                    min(source.size) < 16 or source.width * source.height > MAX_IMAGE_PIXELS):
                raise ValueError("Artwork is not a supported bounded bitmap.")
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGBA")
            normalized.save(destination, format="PNG")
            width, height = normalized.size
        _cancel(cancelled)
        return {"source_url": providers.sanitize_url(url), "width": width, "height": height,
                "downloaded_at": datetime.now(timezone.utc).isoformat()}
    except (ImportCancelled, providers.ScrapeCancelled):
        raise
    except Exception:
        # urllib/Pillow exceptions may include query strings containing API login.
        raise OSError("The artwork image could not be downloaded or decoded. Check the connection and try again.") from None


def _font(size):
    try:
        from .ui_fonts import font_directory
        paths = sorted(font_directory().glob("*.ttf"), key=lambda path:("italic" in path.name.casefold(), path.name))
        if paths:
            return ImageFont.truetype(str(paths[0]), size)
    except (OSError, ValueError):
        pass
    return ImageFont.load_default(size=size)


def compose_tile(destination, title, sources, *, style="mixed"):
    """Original landscape card composition: keep the complete cover visible."""
    canvas = Image.new("RGBA", (640, 448), (19, 20, 24, 255))
    screenshot = sources.get("screenshot") or sources.get("hero")
    cover = sources.get("cover")
    if screenshot and style != "cover":
        with Image.open(screenshot) as source:
            # Screenshots are contained too; avoid cutting off gameplay UI.
            fitted = ImageOps.contain(source.convert("RGBA"), (640, 366), Image.Resampling.LANCZOS)
            canvas.alpha_composite(fitted, ((640-fitted.width)//2, (366-fitted.height)//2))
    if cover and (style != "screenshot" or not screenshot):
        with Image.open(cover) as source:
            fitted = ImageOps.contain(source.convert("RGBA"), (228 if screenshot and style == "mixed" else 260, 350), Image.Resampling.LANCZOS)
            left = 18 if screenshot and style == "mixed" else (640-fitted.width)//2
            canvas.alpha_composite(fitted, (left, (366-fitted.height)//2))
    if sources.get("logo") and style != "cover":
        with Image.open(sources["logo"]) as source:
            logo = ImageOps.contain(source.convert("RGBA"), (310, 100), Image.Resampling.LANCZOS)
            canvas.alpha_composite(logo, (624-logo.width, 18))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 366, 640, 448), fill=(7, 8, 11, 255))
    font = _font(30)
    text = str(title).strip()[:500]
    lines = []
    while text and len(lines) < 2:
        count = len(text)
        while count > 1 and draw.textlength(text[:count], font=font) > 606:
            count -= 1
        if count < len(text) and " " in text[:count]:
            count = text.rfind(" ", 0, count) or count
        lines.append(text[:count].rstrip()); text = text[count:].lstrip()
    if text and lines:
        lines[-1] = lines[-1].rstrip(" .") + "…"
    for index, line in enumerate(lines):
        draw.text((17, 371 + index*34), line, font=font, fill=(245, 245, 244))
    canvas.save(destination, format="PNG")


def normalized_title(value):
    value = unicodedata.normalize("NFKC", providers.artwork_title(value)).casefold().strip()
    romans={"i":"1","ii":"2","iii":"3","iv":"4","v":"5","vi":"6","vii":"7","viii":"8","ix":"9","x":"10"}
    value = re.sub(r"\b(i|ii|iii|iv|v|vi|vii|viii|ix|x)$", lambda match:romans[match[0]], value)
    return re.sub(r"[^a-z0-9]", "", value)


def _region(value):
    key = str(value or "").casefold().strip()
    return {"usa":"us", "u":"us", "north america":"us", "japan":"jp", "j":"jp",
            "europe":"eu", "e":"eu", "eur":"eu"}.get(key, key)


def exact_candidate(game, candidates, preferred_region="us"):
    matches = {}
    wanted_region = _region(providers._game_region(dict(game,scrape_region=preferred_region)))
    best_rank = 3
    for candidate in candidates:
        if candidate.system != game["system"] or normalized_title(candidate.title) != normalized_title(game["title"]):
            continue
        region = _region(candidate.region)
        if region and region not in {wanted_region, "world", "wor"}:
            continue
        if game.get("year") and candidate.year and int(game["year"]) != int(candidate.year):
            continue
        rank = 0 if region and region == wanted_region else 1 if region in {"world", "wor"} else 2
        if rank < best_rank:
            matches.clear(); best_rank = rank
        if rank == best_rank:
            matches[(candidate.provider, candidate.id)] = candidate
    return next(iter(matches.values())) if len(matches) == 1 else None


@dataclass
class ScrapeBatchResult:
    updated: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    unmatched: list[int] = field(default_factory=list)
    unsupported: list[int] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)
    cancelled: bool = False


class ScraperService:
    def __init__(self, library, settings=None, *, search=None, details=None, downloader=None):
        self.library = library
        self.settings = settings or ScraperSettings(library.root)
        self.searcher = search or providers.search
        self.detailer = details or providers.details
        self.downloader = downloader or download_image

    def _existing(self, game, role):
        path = self.library.artwork_for(game, role)
        return path if path and path.is_file() else None

    def supports(self, game, provider=None):
        """Whether the chosen service has entries for this game's system."""
        provider = provider or self.settings.load()["provider"]
        return game.get("system") in providers.provider_systems(provider)

    def search(self, game_id, *, query=None, cancelled=None):
        options = self.settings.load()
        game = dict(self.library.game(game_id))
        if query is not None:
            game["title"] = str(query).strip()[:500]
        game.update(scrape_region=options["region"], scrape_language=options["language"])
        _cancel(cancelled)
        return self.searcher(options["provider"], game, self.settings.credentials(options["provider"]), cancelled=cancelled)

    def apply(self, game_id, candidate, *, cancelled=None):
        options = self.settings.load()
        game = self.library.game(game_id)
        if candidate.system != game["system"]:
            raise ValueError("Choose artwork for this game's system.")
        original_key = (candidate.provider, candidate.id, candidate.system)
        candidate = self.detailer(candidate, self.settings.credentials(candidate.provider), cancelled=cancelled)
        if (candidate.provider, candidate.id, candidate.system) != original_key:
            raise ValueError("The artwork service returned details for a different game.")
        _cancel(cancelled)
        requested = set(options["roles"])
        overwrite = options["overwrite"]
        folder = self.library.root / "cache" / "scraper-staging"
        if folder.is_symlink() or not folder.resolve().is_relative_to(self.library.root.resolve()):
            raise ValueError("Artwork staging folder leaves the library.")
        folder.mkdir(parents=True, exist_ok=True)
        saved, notes = {}, []
        with tempfile.TemporaryDirectory(prefix="art-", dir=folder) as temporary:
            stage = Path(temporary)
            sources = {role:self._existing(game, role) for role in ROLES}
            provenance = {}
            downloads = {}
            needed = requested | ({"cover", "screenshot", "hero", "logo"} if "tile" in requested and not candidate.media.get("tile") else set())
            for role in ROLES:
                _cancel(cancelled)
                if role not in needed or role not in candidate.media:
                    continue
                if sources.get(role) and not overwrite:
                    continue
                path = stage / (role + ".png")
                try:
                    url = candidate.media[role]
                    if url in downloads:
                        path, info = downloads[url]
                    else:
                        info = self.downloader(url, path, cancelled=cancelled)
                        downloads[url] = (path, info)
                except ImportCancelled:
                    raise
                except OSError:
                    notes.append(role + " image unavailable")
                    continue
                sources[role] = path
                if role in requested:
                    saved[role] = path
                    provenance[role] = dict(info, provider=candidate.provider,
                        provider_game_id=str(candidate.id), match_title=candidate.title, region=candidate.region)
            if "tile" in requested and "tile" not in saved and (overwrite or not sources.get("tile")) and any(sources.get(role) for role in ("cover", "screenshot", "hero")):
                tile = stage / "composed-tile.png"
                compose_tile(tile, candidate.title, sources, style=options.get("tile_style", "mixed"))
                saved["tile"] = tile
                provenance["tile"] = {"provider":candidate.provider, "provider_game_id":str(candidate.id),
                    "match_title":candidate.title, "attribution":"Landscape tile composed by Redux from this game's artwork", "width":640, "height":448}
            _cancel(cancelled)
            if not saved:
                if not notes and any(self._existing(game, role) for role in requested):
                    return {"game":game, "roles":[], "notes":["Existing artwork kept; no additional requested image types were available"]}
                raise OSError("No requested artwork was available for this match. Existing artwork was kept.")
            updated = self.library.set_artworks(game_id, saved, provenance=provenance, overwrite=overwrite, cancelled=cancelled)
            actual_roles = updated.get("artwork_updated_roles", list(saved))
            fields = {}
            for name in ("description", "publisher", "developer", "genre", "region", "year"):
                value = getattr(candidate, name, None)
                if value and not updated.get(name):
                    if name == "year":
                        try:value = int(value)
                        except (TypeError, ValueError):continue
                        if not 1900 <= value <= 2200:continue
                    else:value = str(value).replace("\0", "")[:20000 if name == "description" else 4096]
                    fields[name] = value
            if fields:
                updated = self.library.update_metadata(game_id, **fields)
            return {"game":updated, "roles":list(actual_roles), "notes":notes}

    def batch(self, game_ids, *, cancelled=None, progress=None):
        result = ScrapeBatchResult()
        options = self.settings.load()
        supported = set(providers.provider_systems(options["provider"]))
        identities = list(dict.fromkeys(game_ids))
        for index, game_id in enumerate(identities):
            try:
                _cancel(cancelled)
                game = self.library.game(game_id)
                if progress:
                    progress(index + 1, len(identities), game["title"])
                if not options["overwrite"] and all(self._existing(game, role) for role in options["roles"]):
                    result.skipped.append(game_id); continue
                if game["system"] not in supported:
                    result.unsupported.append(game_id); continue
                candidates = self.search(game_id, cancelled=cancelled)
                match = exact_candidate(game, candidates, options["region"])
                if match is None:
                    result.unmatched.append(game_id); continue
                applied = self.apply(game_id, match, cancelled=cancelled)
                (result.updated if applied["roles"] else result.skipped).append(game_id)
            except (ImportCancelled, providers.ScrapeCancelled):
                result.cancelled = True; break
            except providers.AuthError:
                result.errors.append((game_id, "Account or application credentials need attention.")); break
            except providers.QuotaError:
                result.errors.append((game_id, "The service download limit was reached. Try again later.")); break
            except (providers.ScraperError, OSError, ValueError, KeyError) as error:
                # Provider errors are already written for display without URLs or credentials.
                message = str(error) if isinstance(error, providers.ScraperError) and str(error) else ""
                result.errors.append((game_id, message or "Artwork lookup or download did not complete."))
        return result
