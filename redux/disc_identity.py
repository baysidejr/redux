"""Bounded, read-only identification of media already inside an optical drive.

Saturn identity comes from the public IP.BIN system header, not a BIOS or an
emulator.  SEGA's original disc-format sample describes the byte fields:
https://docs.exodusemulator.com/Archives/SSDDV25/segahtml/prgg/sofg/disc/hon/p05_10.htm

The default reader issues only buffered GENERIC_READ volume reads. It neither
images media nor issues tray, speed, write, authentication or protection commands.
The watcher owns spin-up retries; a not-ready/error result is never a fake title.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from .media_identity import (MediaIdentity,MediaFormatError,identify_media,
    _boot_identity,_record,_root_file)
from .optical import (DRIVE_CDROM,OpticalError,NoMediaError,_drive,_get_api,
    normalize_drive,parse_toc,parse_iso9660_pvd)

MAX_HEADER_SECTORS=32
SECTOR_BYTES=2048
SATURN_MAGIC=b"SEGA SEGASATURN "


@dataclass(frozen=True)
class DiscIdentity:
    drive: str
    title: str
    system: str | None
    game_id: str | None
    format: str
    confidence: str
    notes: tuple[str,...]
    details: dict
    fingerprint: str
    source_image: Path | None = None
    # True establishes a discoverable mounted source. False does not assert
    # that an arbitrary unrecognised virtual-storage driver is physical.
    is_virtual: bool = False

    @property
    def region(self):
        return self.details.get("area_symbols") or self.details.get("region")


def _text(raw: bytes) -> str:
    try:value=raw.decode("ascii")
    except UnicodeDecodeError:value=raw.decode("shift_jis",errors="replace")
    value=value.replace("\0"," ")
    return re.sub(r"\s+"," ","".join(char if char.isprintable() else " " for char in value)).strip()


def parse_saturn_header(data: bytes) -> MediaIdentity | None:
    """Parse an exact Saturn hardware marker plus its bounded system fields."""
    if not data.startswith(SATURN_MAGIC):return None
    if len(data)<0x100:raise MediaFormatError("The Saturn system header is truncated")
    raw_id=_text(data[0x20:0x2a])
    game_id=raw_id if re.fullmatch(r"[A-Z0-9][A-Z0-9._ /-]{1,9}",raw_id) else None
    name=_text(data[0x60:0xd0])
    notes=[]
    if not name:notes.append("The Saturn title field is empty.")
    if not game_id:notes.append("The Saturn product identifier is unavailable or malformed.")
    date=_text(data[0x30:0x38]);valid_date=None
    try:
        if not re.fullmatch(r"\d{8}",date):raise ValueError("Invalid date")
        valid_date=datetime.strptime(date,"%Y%m%d").date().isoformat()
    except ValueError:notes.append("The Saturn header does not contain a valid release date.")
    details={"system_marker":"SEGA SEGASATURN","maker_id":_text(data[0x10:0x20]),
        "header_product_id":raw_id,"version":_text(data[0x2a:0x30]),"release_date":valid_date,
        "disc_number":_text(data[0x38:0x40]),"area_symbols":_text(data[0x40:0x50]),
        "region":_text(data[0x40:0x50]),
        "peripherals":_text(data[0x50:0x60]),"header_title":name,
        "header_offset":0,"logical_sector_bytes":SECTOR_BYTES}
    return MediaIdentity(name or "Saturn disc","saturn",game_id,"Saturn CD","header",tuple(notes),details)


def _reader(letter):
    from .acquisition import _DeviceReader
    return _DeviceReader(letter)


def parse_sega_cd_header(data: bytes) -> MediaIdentity | None:
    """Read the Sega CD IP header's title, product and region fields.

    Layout: file/file magic/Magdir/console, sega-mega-drive-header.
    The MK-4427 title alias is corroborated by libretro-database's Redump
    Sega CD metadata and MAME hash/segacd.xml; require BOTH serial and title.
    """
    if not data.startswith(b"SEGADISCSYSTEM"):return None
    if len(data)<0x200:raise MediaFormatError("The Sega CD system header is truncated")
    domestic=_text(data[0x120:0x150]);overseas=_text(data[0x150:0x180])
    region=_text(data[0x1f0:0x200])
    name=(domestic if region=="J" else overseas) or domestic or overseas
    raw_id=_text(data[0x180:0x18e])
    product=re.fullmatch(r"(?:GM|AI)\s+([A-Z0-9][A-Z0-9._ -]*?)(?:\s*-\d{2})?",raw_id)
    game_id=product[1].strip() if product else None
    title=name or "Sega CD disc"
    if (game_id=="MK-4427" and region=="U" and
            re.sub(r"[^A-Z0-9]","",name.upper())=="ETERNALCHAMPIONSCD"):
        title="Eternal Champions - Challenge from the Dark Side"
    notes=() if name else ("The Sega CD title fields are empty.",)
    return MediaIdentity(title,"sega_cd",game_id,"Sega CD","header",notes,
        {"system_marker":"SEGADISCSYSTEM","header_offset":0,
         "header_title":name,"domestic_title":domestic,"overseas_title":overseas,
         "header_product_id":raw_id,"region":region,"area_symbols":region,
         "volume_name":_text(data[0x10:0x1b]),"logical_sector_bytes":SECTOR_BYTES})


class _BoundedSectors:
    """Adapter for the existing ISO metadata parser; never reads beyond LBA31."""
    def __init__(self,reader):
        self.reader=reader;self.cache={};self.blocks=MAX_HEADER_SECTORS
    def read_sector(self,stream,lba):
        if isinstance(lba,bool) or not isinstance(lba,int) or not 0<=lba<min(MAX_HEADER_SECTORS,self.blocks):
            raise MediaFormatError("Disc metadata is outside the bounded header probe")
        if lba not in self.cache:
            block=self.reader.read(lba*SECTOR_BYTES,SECTOR_BYTES)
            if len(block)!=SECTOR_BYTES:
                raise OpticalError("The drive returned an incomplete header sector.",reason="short_read")
            self.cache[lba]=bytes(block)
        return self.cache[lba]


def _toc_state(api,drive):
    try:
        raw=bytes(api.read_toc(drive))
        if len(raw)>=4:
            declared=int.from_bytes(raw[:2],"big")+2
            if 4<=declared<=len(raw):raw=raw[:declared]
        try:toc=parse_toc(raw)
        except OpticalError as error:
            if error.reason!="invalid_toc":raise
            return raw,None,("The drive did not provide a complete track layout.",)
        return raw,toc,()
    except OpticalError as error:
        if error.reason not in {"invalid_toc","unsupported_toc"}:raise
        return None,None,("The drive did not provide a supported track layout.",)


def _toc_details(toc):
    if toc is None:return {"track_layout_available":False}
    return {"track_layout_available":True,"first_track":toc.first_track,
        "last_track":toc.last_track,"lead_out_lba":toc.lead_out_lba,
        "tracks":[{"number":track.number,"kind":track.kind,"start_lba":track.start_lba,
                   "end_lba":track.end_lba} for track in toc.tracks]}


def _fingerprint(toc,header,identity,source):
    stable={"system":identity.system,"game_id":identity.game_id,"title":identity.title,
        "format":identity.format,"source":str(source) if source else None}
    if source:
        stat=source.stat();stable.update(source_bytes=stat.st_size,source_mtime_ns=stat.st_mtime_ns)
    digest=hashlib.sha256(json.dumps(stable,sort_keys=True).encode("utf-8"))
    digest.update(toc or b"");digest.update(header or b"")
    return digest.hexdigest()


def parse_3do_header(data):
    """Read the public Opera DiscLabel structure; no game title is invented."""
    if len(data)<84 or data[:7]!=b'\x01ZZZZZ\x01':return None
    block_size=int.from_bytes(data[76:80],'big')
    blocks=int.from_bytes(data[80:84],'big')
    if block_size!=2048 or blocks<1:return None
    name=_text(data[40:72])
    return MediaIdentity(name or '3DO disc','3do',None,'3DO CD','header',(),
        {'volume_id':name,'volume_blocks':blocks,'logical_sector_bytes':block_size})


def _logical_identity(sectors,first):
    opera=parse_3do_header(first)
    if opera:return opera
    saturn=parse_saturn_header(first)
    if saturn:return saturn
    sega_cd=parse_sega_cd_header(first)
    if sega_cd:return sega_cd
    notes=[];pvd=None;volume=None
    for lba in range(16,MAX_HEADER_SECTORS):
        data=sectors.read_sector(None,lba)
        if data[1:7]!=b"CD001\x01":break
        if data[0]==255:break
        if data[0]==1:
            try:volume=parse_iso9660_pvd(data)
            except OpticalError as error:
                notes.append(str(error));break
            pvd=data;break
    if pvd is None:
        return MediaIdentity("Unrecognized disc",None,None,"Optical disc","unknown",tuple(notes),{})
    sectors.blocks=volume.volume_blocks
    details={"system_marker":volume.system_id,"volume_id":volume.volume_id,
        "volume_blocks":volume.volume_blocks,"volume_bytes":volume.volume_bytes,
        "logical_sector_bytes":volume.logical_block_size}
    if volume.system_id.strip().upper()=="PLAYSTATION":
        serial=None
        try:
            length=pvd[156]
            if not 34<=length<=255:raise MediaFormatError("Invalid ISO root record")
            root=_record(pvd[156:156+length],volume.volume_blocks)
            if not root[2]&2:raise MediaFormatError("The ISO root is not a directory")
            config=_root_file(sectors,None,volume.volume_blocks,root,"SYSTEM.CNF")
            if config is not None:
                executable,serial=_boot_identity(config);details["boot_executable"]=executable
        except MediaFormatError as error:notes.append(str(error))
        if not serial:notes.append("No PlayStation game serial was established in the bounded metadata probe.")
        return MediaIdentity(volume.volume_id.strip() or "PlayStation disc","psx",serial,
            "PlayStation CD","filesystem",tuple(notes),details)
    try:
        root=_record(pvd[156:156+pvd[156]],volume.volume_blocks)
        ipl=_root_file(sectors,None,volume.volume_blocks,root,'IPL.TXT')
        if ipl and re.search(rb'(?im)^[^\r\n,]+\.PRG\s*,',ipl):
            return MediaIdentity(volume.volume_id.strip() or 'Neo Geo CD disc','neo_geo_cd',None,'Neo Geo CD','filesystem',(),details)
    except (MediaFormatError,OpticalError,ValueError) as error:
        notes.append(str(error))
    return MediaIdentity(volume.volume_id.strip() or "Data disc",None,None,"ISO9660 data disc",
        "filesystem",tuple(notes),details)


def identify_drive(letter: str, *, reader_factory=None, api=None, source_resolver=None) -> DiscIdentity:
    """Identify current media using small read-only queries, independently of BIOS.

    NoMediaError and OpticalError propagate for absent, spinning-up or unreadable
    media so the watcher can retry. Unknown readable media returns system=None.
    Only first32 logical sectors are eligible, read lazily and cached. Source
    image discovery is performed only after a media-presence TOC query succeeds
    or returns its explicit unsupported-layout condition.
    """
    letter=normalize_drive(letter);drive=_drive(letter);api=api or _get_api()
    if api.drive_type(drive.root_path)!=DRIVE_CDROM:
        raise OpticalError("The requested drive is not optical.",reason="not_optical",drive=letter)
    before,toc,toc_notes=_toc_state(api,drive)
    if source_resolver is None:
        from .mounted_images import mounted_image_source
        source_resolver=mounted_image_source
    source=None;notes=list(toc_notes)
    try:
        value=source_resolver(letter)
        if value is not None:source=Path(value).resolve(strict=True)
    except (OSError,ValueError,OpticalError) as error:
        notes.append("The original mounted-image path is unavailable: "+str(error))
    header=b"";read_sectors=0
    if source:
        identity=identify_media(source)
    elif toc and not any(track.is_data for track in toc.tracks):
        identity=MediaIdentity("Audio CD",None,None,"Audio CD","toc",(),{})
    else:
        with (reader_factory or _reader)(letter) as reader:
            sectors=_BoundedSectors(reader)
            header=sectors.read_sector(None,0)
            identity=_logical_identity(sectors,header)
            # The header must still belong to the media being published.
            check=reader.read(0,SECTOR_BYTES)
            if check!=header:
                raise OpticalError("The disc changed during identification.",reason="media_changed",drive=letter)
            read_sectors=len(sectors.cache)+1
    after,_,_=_toc_state(api,drive)
    if after!=before:
        raise OpticalError("The drive's track layout changed during identification.",reason="media_changed",drive=letter)
    details=dict(identity.details);details.update(_toc_details(toc))
    details.update(probed_drive=letter,header_probe_sector_limit=MAX_HEADER_SECTORS,
        logical_sectors_read=read_sectors,source_kind="mounted_image" if source else "drive")
    notes.extend(identity.notes)
    return DiscIdentity(letter,identity.title,identity.system,identity.game_id,identity.format,
        identity.confidence,tuple(notes),details,_fingerprint(before,header,identity,source),source,bool(source))
