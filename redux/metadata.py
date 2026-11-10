"""Optional, read-only adapter for a caller-selected local reference catalogue.

The application has no dependency on this corpus. No file is searched for or
loaded automatically, and no catalogue/artwork data is bundled by this module.
Structural parsing is original code derived from the observed counted-map format.
Matches are exact after conservative filename-tag/Unicode normalization. Ambiguous
titles never become automatic metadata updates.
"""
from __future__ import annotations

from collections import Counter,defaultdict
from pathlib import Path
import re
import struct
import unicodedata

from .library import MEDIA_EXTENSIONS,SYSTEMS

CATALOGUE_SYSTEMS={1:"nes",2:"snes",3:"n64",5:"genesis",6:"32x",7:"sega_cd",
                   9:"saturn",11:"psx",13:"neo_geo_cd",14:"pce",15:"pce_cd",
                   16:"atari2600",17:"supergrafx",23:"atari7800"}
REGIONS={1:"North America",2:"Japan",3:"Europe"}
_REGION_TAGS={"usa":1,"us":1,"north america":1,"usa, canada":1,
              "japan":2,"jp":2,"europe":3,"eur":3,"eu":3}
_LANGUAGES=frozenset("en fr de es it ja jp sv nl pt ru pl ko zh da fi no uk hu cs sk tr ar he".split())
_MISSING=frozenset({"","[missing]","[none]","null"})
_MAX_CATALOGUE_BYTES=128*1024*1024


class CatalogueError(ValueError):
    pass


class _Reader:
    def __init__(self,data):self.data,self.cursor=data,0

    def u32(self):
        if self.cursor+4>len(self.data):raise CatalogueError("Truncated catalogue integer")
        value=struct.unpack_from("<I",self.data,self.cursor)[0]
        self.cursor+=4
        return value

    def count(self,maximum):
        count=self.u32()
        if count>maximum:raise CatalogueError("Catalogue map count exceeds supported bounds")
        return count

    def string(self):
        size=self.u32()
        if not 1<=size<=1024*1024 or size>len(self.data)-self.cursor:
            raise CatalogueError("Catalogue string has invalid bounds")
        raw=self.data[self.cursor:self.cursor+size]
        self.cursor+=size
        if raw[-1]!=0 or b"\0" in raw[:-1]:raise CatalogueError("Catalogue string has an invalid terminator")
        try:return raw[:-1].decode("utf-8")
        except UnicodeDecodeError as error:raise CatalogueError("Catalogue string is not UTF-8") from error


def _records(data):
    reader=_Reader(data)
    if reader.u32()!=0:raise CatalogueError("Unrecognized reference catalogue prefix")
    systems=set()
    total=0
    for _ in range(reader.count(256)):
        system=reader.u32()
        if system in systems:raise CatalogueError("Duplicate system key")
        systems.add(system)
        regions=set()
        for _ in range(reader.count(64)):
            region=reader.u32()
            if region in regions:raise CatalogueError("Duplicate region key")
            regions.add(region)
            indices=set()
            for _ in range(reader.count(200000)):
                total+=1
                if total>200000:raise CatalogueError("Catalogue exceeds 200,000 records")
                index=reader.u32()
                if index in indices:raise CatalogueError("Duplicate record key")
                indices.add(index)
                values={}
                for _ in range(reader.count(256)):
                    name=reader.string()
                    tag=reader.u32()
                    text=reader.string()
                    if name in values:raise CatalogueError("Duplicate metadata field")
                    values[name]=(tag,text)
                yield system,region,index,values
    if reader.cursor!=len(reader.data):raise CatalogueError("Catalogue contains unconsumed bytes")


def _text(value):
    value=value.strip()
    return "" if value.casefold() in _MISSING else value


def _normalized(value):
    value=unicodedata.normalize("NFKC",value).casefold()
    value=value.replace("\u2019","'").replace("\u2018","'").replace("\u2013","-").replace("\u2014","-")
    return " ".join(value.replace("_"," ").split())


def normalize_title(title):
    """Return (normalized exact title, optional explicit country-tag region).

    Only trailing recognized region/language dump tags are removed. Edition,
    revision, disc-number, and arbitrary parenthetical title text are retained.
    Conflicting region tags are rejected instead of choosing one.
    """
    if not isinstance(title,str):raise TypeError("Title must be text")
    title=title.strip()
    if Path(title).suffix.casefold() in MEDIA_EXTENSIONS:title=str(Path(title).with_suffix(""))
    hints=set()
    while match:=re.search(r"\s*[\(\[]([^()\[\]]+)[\)\]]\s*$",title):
        tag=_normalized(match.group(1))
        if tag in _REGION_TAGS:hints.add(_REGION_TAGS[tag])
        elif not all(part.strip() in _LANGUAGES for part in tag.split(",")):
            break
        title=title[:match.start()].rstrip()
    if len(hints)>1:raise ValueError("Conflicting region tags in title")
    return _normalized(title),next(iter(hints),None)


def _system_id(system):
    if system is None:return None
    if isinstance(system,bool):raise ValueError("System must be a library slug or catalogue integer ID")
    if isinstance(system,int):return system
    if system not in SYSTEMS:raise ValueError(f"Unknown library system: {system}")
    return next((key for key,value in CATALOGUE_SYSTEMS.items() if value==system),-1)


class Catalogue:
    def __init__(self,path,asset_root=None):
        self.path=Path(path).expanduser().resolve(strict=True)
        self.asset_root=Path(asset_root).expanduser().resolve() if asset_root is not None else None
        if self.asset_root is not None and self.asset_root.exists() and not self.asset_root.is_dir():
            raise ValueError("Asset root must be a directory")
        before=self.path.stat()
        if not self.path.is_file() or not 4<=before.st_size<=_MAX_CATALOGUE_BYTES:
            raise CatalogueError("Catalogue must be a regular file between 4 bytes and 128 MiB")
        data=self.path.read_bytes()
        after=self.path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise CatalogueError("Catalogue changed while being read")
        self._rows=[]
        self._index=defaultdict(list)
        self._counts=Counter()
        for system,region,index,fields in _records(data):
            get=lambda key:_text(fields.get(key,(0,""))[1])
            title=get("name")
            year=get("year")
            year=int(year) if re.fullmatch(r"\d{4}",year) and 1900<=int(year)<=2200 else None
            row=dict(catalogue_system_id=system,catalogue_region_id=region,catalogue_index=index,
                     title=title,system=CATALOGUE_SYSTEMS.get(system,"unknown"),description=get("copy"),
                     year=year,publisher=get("publisher"),developer=get("developer"),genre=get("genre"),
                     region=REGIONS.get(region,f"Region {region}"),
                     _box=get("box image front"),_tile=get("tile image"),_hero=get("hero banner"),
                     _screenshots=get("screenshots"))
            aliases={_normalized(name) for name in (title,get("alt name")) if name}
            row["_aliases"]=aliases
            self._rows.append(row)
            self._counts[system]+=1
            for alias in aliases:self._index[(system,alias)].append(row)
        self.record_count=len(self._rows)

    def stats(self):
        return dict(source=str(self.path),record_count=self.record_count,system_count=len(self._counts),
                    system_counts={CATALOGUE_SYSTEMS.get(key,f"system_{key}"):value for key,value in self._counts.items()},
                    all_bytes_consumed=True,asset_root=str(self.asset_root) if self.asset_root else None)

    @property
    def statistics(self):return self.stats()

    def _asset(self,value):
        if not value or self.asset_root is None:return None
        value=value.replace("\\","/").lstrip("/")
        if ":" in value:return None
        candidate=(self.asset_root/value).resolve()
        if not candidate.is_relative_to(self.asset_root) or not candidate.is_file():return None
        return str(candidate)

    def _public(self,row):
        result={key:value for key,value in row.items() if not key.startswith("_")}
        box,tile,hero=(self._asset(row[key]) for key in ("_box","_tile","_hero"))
        result.update(artwork_path=box or tile or "",box_art_path=box,tile_path=tile,hero_path=hero)
        screenshots=[]
        prefix=row["_screenshots"]
        if prefix:
            for index in range(1,9):
                image=self._asset(f"{prefix}_{index:04}.png")
                if image:screenshots.append(image)
        result["screenshot_paths"]=screenshots
        return result

    def match(self,title,system):
        system_id=_system_id(system)
        if system_id is None or system_id<0:return None
        normalized,region=normalize_title(title)
        candidates=self._index.get((system_id,normalized),[])
        if region is not None:candidates=[row for row in candidates if row["catalogue_region_id"]==region]
        if len(candidates)!=1:return None
        return self._public(candidates[0])

    def search(self,query,system=None,limit=64):
        if not isinstance(query,str):raise TypeError("Search query must be text")
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=1000:
            raise ValueError("Search limit must be between 1 and 1000")
        system_id=_system_id(system)
        normalized=_normalized(query)
        result=[]
        for row in self._rows:
            if not row["title"] or (system_id is not None and row["catalogue_system_id"]!=system_id):continue
            if normalized and not any(normalized in value for value in row["_aliases"]):continue
            result.append(self._public(row))
            if len(result)>=limit:break
        return result
