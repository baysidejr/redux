"""Link the collection and inserted discs to the shipped game database.

The database replaces artwork scraping: a game that arrives from a disc or a game
card is matched by serial (a disc's product code, a GOG game's product id), then by title, and whatever facts it lacks (description,
year, publisher, developer, genre) are filled from the database. Art is not
copied: the screens fall back to the database's box art, banner and game image
whenever a game has no file of its own, so art downloaded later shows up at once.
"""
from __future__ import annotations

import threading

from .gamedb import GameDatabase, SYSTEM_ORDER

FIELDS = ("description", "year", "publisher", "developer", "genre")
ART_ROLES = {"cover": "boxart", "hero": "hero", "screenshot": "gameimage"}

_shared = None
_lock = threading.Lock()


def shared_database():
    """One read-only database for the process; its catalogs load lazily and stay loaded."""
    global _shared
    with _lock:
        if _shared is None:
            _shared = GameDatabase()
        return _shared


def database_game(database, system, *, serial=None, title=None):
    """The database entry for a system's game, by serial first and title second, or None."""
    if system not in SYSTEM_ORDER or not (serial or title):
        return None
    try:
        return database.find(system, serial, title)
    except (OSError, ValueError, TypeError):
        return None


def metadata_fields(entry):
    """Library metadata the database can supply for an entry (only the fields it has).

    Region is left to the disc header and file name: the database holds North American
    releases, so its answer would nearly always be USA and would hide a real difference."""
    fields = {}
    if entry.get("description"):
        fields["description"] = str(entry["description"]).strip()
    try:
        year = int(entry.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    if year:
        fields["year"] = year
    for key in ("publisher", "developer"):
        if entry.get(key):
            fields[key] = str(entry[key]).strip()
    genres = [str(genre).strip() for genre in entry.get("genres") or [] if genre]
    if genres:
        fields["genre"] = ", ".join(genres)
    return fields


def missing_fields(game, entry):
    """The database facts a collection game does not have yet; the user's own values are kept."""
    return {key: value for key, value in metadata_fields(entry).items() if not game.get(key)}


def link_game(library, game, database=None, *, serial=None):
    """Fill a collection game's empty facts from the database. Returns the entry used, or None."""
    entry = database_game(database or shared_database(), game.get("system"),
                          serial=serial or game.get("game_id") or game.get("store_id"), title=game.get("title"))
    if not entry:
        return None
    fields = missing_fields(game, entry)
    if fields:
        library.update_metadata(game["id"], **fields)
    return entry


def backfill(library, database=None, *, cancelled=None):
    """Fill facts for every collection game the database knows and that still lacks a description.

    Returns how many games changed. Safe to run from a worker thread; it only reads
    the database and writes through the library's own metadata API.
    """
    database = database or shared_database()
    changed = 0
    for game in library.games():
        if cancelled is not None and cancelled.is_set():
            break
        if game.get("system") not in SYSTEM_ORDER or game.get("description"):
            continue
        try:
            entry = database_game(database, game["system"], serial=game.get("store_id") or None, title=game.get("title"))
            if entry and missing_fields(game, entry):
                library.update_metadata(game["id"], **missing_fields(game, entry))
                changed += 1
        except (OSError, ValueError):
            continue
    return changed


def art_for(database, entry, role):
    """The database art file standing in for a library art role, or None."""
    database_role = ART_ROLES.get(role)
    if not entry or not database_role:
        return None
    path = database.art(entry, database_role)
    return str(path) if path else None


def disc_enrichment(database, identity):
    """(metadata, artwork) for an identified disc: facts for the disc card and the install,
    cover and hero paths for the home banner. Both empty when the database does not know it."""
    entry = database_game(database, getattr(identity, "system", None),
                          serial=getattr(identity, "game_id", None), title=getattr(identity, "title", None))
    if not entry:
        return {}, {}
    artwork = {}
    cover = database.art(entry, "boxart")
    hero = database.art(entry, "hero") or database.art(entry, "gameimage")
    if cover:
        artwork["cover_path"] = str(cover)
    if hero:
        artwork["hero_path"] = str(hero)
    return metadata_fields(entry), artwork
