"""Read-only save-card metadata; loading remains the backend's responsibility.

No state payload is deserialized, loaded or rewritten here. Backend slot errors
are isolated so one damaged slot cannot hide its neighbors. A different core
hash is labelled an old version only for a structurally valid same-game record.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

_HASH=re.compile(r"[a-f0-9]{64}\Z")
_MAX_MANIFEST=8192
_MAX_THUMBNAIL=8*1024*1024


@dataclass(frozen=True)
class SlotCard:
    slot: int
    exists: bool
    saved_at: str | None = None
    thumbnail_path: Path | None = None
    old_version: bool = False
    compatible: bool = True
    error: str | None = None


def format_saved_at(value, *, tz=None):
    """Format an aware timestamp without inventing a date for missing metadata."""
    if not isinstance(value,str):return "Date unavailable"
    try:
        stamp=datetime.fromisoformat(value.replace("Z","+00:00"))
        if stamp.tzinfo is None:return "Date unavailable"
        return stamp.astimezone(tz).strftime("%b %d, %Y · %I:%M %p")
    except (ValueError,OverflowError,OSError):return "Date unavailable"


def _timestamp(value):
    if not isinstance(value,str):return None
    try:
        stamp=datetime.fromisoformat(value.replace("Z","+00:00"))
        return value if stamp.tzinfo is not None else None
    except ValueError:return None


def _directory(backend):
    value=getattr(backend,"states",None) or getattr(backend,"state_directory",None)
    return Path(value).absolute() if value is not None else None


def _local_file(directory,name):
    if directory is None or not isinstance(name,str) or not name or name in {".",".."} or any(c in name for c in "/\\:"):
        return None
    path=directory/name
    try:
        if any(part.is_symlink() or getattr(part,"is_junction",lambda:False)() for part in (*directory.parents,directory,path)):
            return None
        if not path.resolve().is_relative_to(directory.resolve()) or not path.is_file():return None
        return path
    except (OSError,ValueError):return None


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError("Duplicate state metadata field")
        result[key]=value
    return result


def _manifest(backend,number):
    directory=_directory(backend)
    path=_local_file(directory,f"slot-{number}.json")
    if path is None:return None
    try:
        with path.open("rb") as stream:data=stream.read(_MAX_MANIFEST+1)
        if len(data)>_MAX_MANIFEST:return None
        item=json.loads(data,object_pairs_hook=_unique)
        if not isinstance(item,dict) or type(item.get("slot")) is not int or item["slot"]!=number:return None
        return item
    except (OSError,ValueError,UnicodeError,RecursionError):return None


def _thumbnail(directory,item,slot_info=None):
    # Thumbnails must be explicitly associated with this slot. A random recent
    # screenshot could depict another moment and is never a valid substitute.
    value=item.get("thumbnail") if item else None
    if value is None and slot_info is not None:
        candidate=getattr(slot_info,"thumbnail_path",None)
        if candidate is not None:
            try:
                candidate=Path(candidate)
                if candidate.absolute().parent!=directory:return None
                value=candidate.name
            except (TypeError,ValueError):return None
    path=_local_file(directory,value)
    if path is None:return None
    try:
        size=path.stat().st_size
        if not 0<size<=_MAX_THUMBNAIL:return None
        expected=item.get("thumbnail_sha256") if item else None
        if expected is not None:
            if not isinstance(expected,str) or not _HASH.fullmatch(expected):return None
            with path.open("rb") as stream:
                if hashlib.file_digest(stream,"sha256").hexdigest()!=expected:return None
        from PIL import Image
        with Image.open(path) as image:
            if image.format not in {"PNG","JPEG","WEBP"} or image.width<1 or image.height<1 or image.width*image.height>4_194_304:return None
            image.verify()
        return path
    except (OSError,ValueError):return None


def _older_embedded(backend,number,item):
    if not item:return False
    game=getattr(backend,"game",None)
    current=getattr(backend,"core_hash",None)
    previous=item.get("core")
    if (not isinstance(game,dict) or item.get("game")!=game.get("bundle_hash") or
        not isinstance(current,str) or not _HASH.fullmatch(current) or
        not isinstance(previous,str) or not _HASH.fullmatch(previous) or current==previous or
        type(item.get("schema")) is not int or item["schema"]!=1 or
        _timestamp(item.get("saved_at")) is None):return False
    digest=item.get("sha256");size=item.get("size")
    if not isinstance(digest,str) or not _HASH.fullmatch(digest) or type(size) is not int or not 0<size<=512*1024*1024+16397:return False
    payload=_local_file(_directory(backend),digest+".state")
    try:return payload is not None and payload.stat().st_size==size
    except OSError:return False


def cards(backend,count=4):
    """Inspect the first 1–10 slots, returning immutable cards in slot order.

    Old-core cards are informational and remain incompatible: a UI warning must
    not bypass the backend's game/core integrity checks. External backends with
    no recorded version identity are never guessed to be old.
    """
    if type(count) is not int or not 1<=count<=10:raise ValueError("State-card count must be between 1 and 10")
    inspect=getattr(backend,"slot",None)
    if not callable(inspect):raise ValueError("This backend has no state interface")
    result=[];directory=_directory(backend)
    for number in range(count):
        item=_manifest(backend,number)
        try:
            info=inspect(number)
            if not info.exists:result.append(SlotCard(number,False));continue
            saved_at=_timestamp(getattr(info,"saved_at",None))
            result.append(SlotCard(number,True,saved_at,_thumbnail(directory,item,info)))
        except (OSError,RuntimeError,ValueError) as error:
            older=_older_embedded(backend,number,item)
            result.append(SlotCard(number,True,_timestamp(item.get("saved_at")) if item else None,
                _thumbnail(directory,item),older,False,
                "Saved with a different emulator core build" if older else str(error)))
    return tuple(result)
