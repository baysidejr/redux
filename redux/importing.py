"""Shared, conservative media identification and library import workflow."""
from pathlib import Path

from .media_identity import identify_media


_GENERIC_DISC_NAMES=frozenset({"disc","image","cd"})
_DISC_DESCRIPTORS=frozenset({".cue",".iso",".ccd"})


def _generic_header_title(path,identity,selected_system):
    # Exact stems only: even "disc (USA)" or "disc (Rev 1)" carries information
    # that must not disappear. A header cannot override an explicit platform.
    if (path.suffix.casefold() not in _DISC_DESCRIPTORS or
            path.stem.casefold() not in _GENERIC_DISC_NAMES or
            identity.confidence!="header" or not identity.system or identity.system=="unknown" or
            identity.system!=selected_system):
        return None
    title=identity.title.strip() if isinstance(identity.title,str) else ""
    generic={*_GENERIC_DISC_NAMES,"saturn disc","sega cd disc","playstation disc",
             "data disc","audio cd","unrecognized disc","unknown"}
    if (not title or title.casefold() in generic or len(title)>500 or
            any(ord(character)<32 for character in title) or "\ufffd" in title):
        return None
    return title


def _header_region_tag(identity):
    # Do not turn a multi-region Saturn area field into one preferred country.
    # Unknown/conflicting region forms retain the catalogue's existing matching.
    labels={"usa":"USA","us":"USA","north america":"USA",
            "japan":"Japan","jp":"Japan","europe":"Europe","eur":"Europe","eu":"Europe"}
    saturn={"u":"USA","j":"Japan","e":"Europe"}
    hints=set()
    for key in ("region","area_symbols"):
        value=identity.details.get(key)
        if not value:
            continue
        if not isinstance(value,str):
            return None
        text=value.strip().casefold()
        tag=labels.get(text) or (saturn.get(text) if identity.system=="saturn" else None)
        if tag is None:
            return None
        hints.add(tag)
    return next(iter(hints)) if len(hints)==1 else None


def import_identified(library,path,*,system="auto",linked=False,cancelled=None,progress=None,catalogue=None):
    path=Path(path)
    # Preserve meaningful filename region/revision information and all edits.
    # A generated descriptor named just disc/image/cd can use its header title.
    game,created=library.import_game(path,system=system,linked=linked,cancelled=cancelled,progress=progress)
    # Identify the validated copied snapshot; a source can change while copying.
    # Duplicate entries retain the user's platform and all edited metadata.
    identity=identify_media(library.path_for(game))
    if created and system=="auto" and identity.system and identity.system!=game["system"]:
        game=library.update_metadata(game["id"],system=identity.system)
    header_title=_generic_header_title(path,identity,game["system"]) if created else None
    if header_title:
        game=library.update_metadata(game["id"],title=header_title)
    if catalogue and created:
        try:
            region=_header_region_tag(identity) if header_title else None
            match_title=f'{game["title"]} ({region})' if region else game["title"]
            match=catalogue.match(match_title,game["system"])
        except ValueError:
            # Conflicting filename regions cannot justify a metadata guess.
            match=None
        if match:
            fields={key:match[key] for key in ("description","year","publisher","developer","region","genre") if match.get(key) and not game.get(key)}
            if fields:
                library.update_metadata(game["id"],**fields)
                game=library.game(game["id"])
    return game,created,identity
