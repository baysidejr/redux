"""Neo Geo AES/MVS cartridge games: ROM sets named by set name, played by FinalBurn Neo.

The set name (mslug, kof98, garou) is all a zipped Neo Geo game carries, so a shipped
table turns it into a title and publisher. The table is built from libretro-database's
"SNK - Neo Geo" list and lives beside the other database files.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

from .gamedb import database_root

BIOS_FILE = "neogeo.zip"
BIOS_FOLDER = "fbneo"


@lru_cache(maxsize=1)
def _names():
    try:
        document = json.loads((Path(database_root()) / "neo_geo" / "names.json").read_text(encoding="utf-8"))
        games = document.get("games") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(key).casefold(): value for key, value in games.items()
            if isinstance(value, dict) and isinstance(value.get("title"), str) and value["title"].strip()}


def game_facts(path):
    """{"title", "publisher"?} for a ROM set file such as mslug.zip, or None when the set is unknown."""
    stem = Path(path).stem.casefold()
    entry = _names().get(stem)
    if not entry:
        return None
    facts = {"title": entry["title"].strip()}
    if isinstance(entry.get("publisher"), str) and entry["publisher"].strip():
        facts["publisher"] = entry["publisher"].strip()
    return facts


def known_sets():
    return len(_names())
