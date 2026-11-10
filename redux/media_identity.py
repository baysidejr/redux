"""Bounded, read-only media identification from documented headers and ISO9660.

This identifies observable format/platform markers, not authenticity, ownership,
emulator compatibility or successful playback. No catalogue or vendor resource is
required. Source references are recorded in docs/media-acquisition.txt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path, PureWindowsPath
import re
from typing import BinaryIO

_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
_FILE = re.compile(r'^\s*FILE\s+(?:"([^"\r\n]+)"|(\S+))\s+BINARY\s*$', re.I)
_TRACK = re.compile(r"^\s*TRACK\s+(\d{1,2})\s+(\S+)\s*$", re.I)
_INDEX = re.compile(r"^\s*INDEX\s+01\s+(\d+):(\d{2}):(\d{2})\s*$", re.I)


@dataclass(frozen=True)
class MediaIdentity:
    title: str
    system: str | None
    game_id: str | None
    format: str
    confidence: str
    notes: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)

    @property
    def name(self):
        return self.title


class MediaFormatError(ValueError):
    pass


@dataclass(frozen=True)
class _SectorSource:
    path: Path
    stride: int
    payload_offset: int
    start_sector: int = 0
    block_limit: int | None = None
    mode: str = "MODE1/2048"

    @property
    def blocks(self):
        available = self.path.stat().st_size // self.stride - self.start_sector
        return min(available, self.block_limit) if self.block_limit is not None else available

    def read_sector(self, stream: BinaryIO, lba: int):
        if not isinstance(lba, int) or not 0 <= lba < self.blocks:
            raise MediaFormatError("Sector extent leaves the selected data track")
        stream.seek((self.start_sector + lba) * self.stride)
        data = stream.read(self.stride)
        if len(data) != self.stride:
            raise MediaFormatError("Truncated image sector")
        if self.stride == 2352:
            expected_mode = 1 if self.mode.startswith("MODE1") else 2
            if data[:12] != _SYNC or data[15] != expected_mode:
                raise MediaFormatError("Raw-sector synchronization or mode is inconsistent")
            if expected_mode == 2 and (data[16:20] != data[20:24] or data[18] & 0x20):
                raise MediaFormatError("ISO metadata is not in a supported Mode 2 Form 1 sector")
        elif self.stride == 2336:
            if data[:4] != data[4:8] or data[2] & 0x20:
                raise MediaFormatError("Unsupported Mode 2 sector layout")
        return data[self.payload_offset:self.payload_offset + 2048]


def _local_reference(descriptor: Path, text: str) -> Path:
    text = text.replace("\\", "/")
    if not text or Path(text).is_absolute() or PureWindowsPath(text).is_absolute() or ":" in text:
        raise MediaFormatError("Image references must be relative local files")
    root = descriptor.parent.resolve()
    candidate = (root / text).resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise MediaFormatError("Image reference leaves the selected media bundle")
    return candidate


def _descriptor_text(path: Path):
    if path.stat().st_size > 1024 * 1024:
        raise MediaFormatError("Media descriptor exceeds the identification limit")
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def _cue_source(path: Path) -> _SectorSource:
    current_file = None
    tracks = []
    for line in _descriptor_text(path).splitlines():
        match = _FILE.match(line)
        if match:
            current_file = _local_reference(path, match.group(1) or match.group(2))
            continue
        match = _TRACK.match(line)
        if match:
            number = int(match.group(1))
            if current_file is None or not 1 <= number <= 99 or (tracks and number <= tracks[-1]["number"]):
                raise MediaFormatError("CUE track order or source file is invalid")
            tracks.append(dict(number=number, path=current_file, mode=match.group(2).upper(), start=None))
            continue
        match = _INDEX.match(line)
        if match:
            if not tracks:
                raise MediaFormatError("CUE index has no track")
            minute, second, frame = map(int, match.groups())
            if second >= 60 or frame >= 75 or tracks[-1]["start"] is not None:
                raise MediaFormatError("CUE INDEX 01 is invalid")
            tracks[-1]["start"] = (minute * 60 + second) * 75 + frame
    for index, track in enumerate(tracks):
        if track["mode"] == "AUDIO":
            continue
        modes = {"MODE1/2048": (2048, 0), "MODE1/2352": (2352, 16),
                 "MODE2/2352": (2352, 24), "MODE2/2336": (2336, 8)}
        if track["mode"] not in modes or track["start"] is None:
            raise MediaFormatError("The first CUE data track has an unsupported layout")
        stride, offset = modes[track["mode"]]
        block_limit = None
        for following in tracks[index + 1:]:
            if following["path"] == track["path"]:
                if following["start"] is None or following["start"] <= track["start"]:
                    raise MediaFormatError("CUE track extents are invalid")
                block_limit = following["start"] - track["start"]
                break
        source = _SectorSource(track["path"],stride,offset,track["start"],block_limit,track["mode"])
        if source.blocks <= 0:
            raise MediaFormatError("CUE data track is empty")
        return source
    raise MediaFormatError("No supported data track was found in the CUE")


def _both32(data, offset):
    if offset + 8 > len(data):
        raise MediaFormatError("Truncated ISO9660 numeric field")
    little = int.from_bytes(data[offset:offset + 4],"little")
    if little != int.from_bytes(data[offset + 4:offset + 8],"big"):
        raise MediaFormatError("ISO9660 byte-order copies disagree")
    return little


def _record(data: bytes, volume_blocks: int):
    if len(data) < 34 or data[0] != len(data) or data[32] + 33 > len(data):
        raise MediaFormatError("Malformed ISO9660 directory record")
    extent, size = _both32(data,2), _both32(data,10)
    if data[1] or data[26] or data[27] or data[25] & 0x80:
        raise MediaFormatError("Extended, interleaved or multi-extent ISO records are unsupported")
    if extent >= volume_blocks or math.ceil(size / 2048) > volume_blocks - extent:
        raise MediaFormatError("ISO9660 directory extent exceeds the volume")
    return extent,size,data[25],data[33:33 + data[32]]


def _pvd(source: _SectorSource, stream: BinaryIO):
    for lba in range(16,min(80,source.blocks)):
        data = source.read_sector(stream,lba)
        if data[1:7] != b"CD001\x01":
            raise MediaFormatError("No ISO9660 descriptor sequence at the standard location")
        if data[0] == 255:
            break
        if data[0] != 1:
            continue
        blocks = _both32(data,80)
        block_size = int.from_bytes(data[128:130],"little")
        if block_size != int.from_bytes(data[130:132],"big") or block_size != 2048:
            raise MediaFormatError("Unsupported ISO9660 logical block size")
        if blocks < 18 or blocks > source.blocks:
            raise MediaFormatError("Declared ISO9660 volume exceeds the selected image track")
        root_length = data[156]
        if not 34 <= root_length <= 255:
            raise MediaFormatError("ISO9660 root record is invalid")
        root = _record(data[156:156 + root_length],blocks)
        if not root[2] & 2:
            raise MediaFormatError("ISO9660 root record is not a directory")
        return data,blocks,root
    raise MediaFormatError("No ISO9660 primary volume descriptor was found")


def _read_extent(source, stream, start, size, limit):
    if size > limit:
        raise MediaFormatError("ISO9660 metadata exceeds the identification limit")
    result = bytearray()
    for lba in range(start,start + math.ceil(size / 2048)):
        result.extend(source.read_sector(stream,lba))
    return bytes(result[:size])


def _root_file(source, stream, blocks, root, target):
    directory = _read_extent(source,stream,root[0],root[1],4 * 1024 * 1024)
    position = 0
    while position < len(directory):
        size = directory[position]
        if size == 0:
            position = (position // 2048 + 1) * 2048
            continue
        if size < 34 or position + size > len(directory) or position % 2048 + size > 2048:
            raise MediaFormatError("ISO9660 directory record crosses its boundary")
        extent,length,flags,name = _record(directory[position:position + size],blocks)
        position += size
        if name in (b"\0",b"\1"):
            continue
        try:
            identifier = name.decode("ascii").split(";",1)[0].upper()
        except UnicodeDecodeError:
            continue
        if identifier == target and not flags & 2:
            return _read_extent(source,stream,extent,length,64 * 1024)
    return None


def _boot_identity(data):
    text = data.decode("ascii",errors="replace")
    for line in text.splitlines():
        key,separator,value = line.partition("=")
        if separator and key.strip().upper() == "BOOT":
            value = re.sub(r"\s+","",value).strip('"')
            if not value.lower().startswith("cdrom:"):
                return None,None
            executable = value[6:].lstrip("\\/").split(";",1)[0]
            basename = re.split(r"[\\/]",executable)[-1]
            match = re.fullmatch(r"([A-Za-z]{4})[_-](\d{3})\.(\d{2})",basename)
            serial = f"{match.group(1).upper()}-{match.group(2)}{match.group(3)}" if match else None
            return executable,serial
    return None,None


def _identify_disc(entry: Path, source: _SectorSource, label: str):
    details = {"source_bytes":source.path.stat().st_size,"sector_stride":source.stride,
               "payload_offset":source.payload_offset,"track_start_sector":source.start_sector}
    with source.path.open("rb") as stream:
        first = source.read_sector(stream,0)
        if first[:16] == b"SEGA SEGASATURN ":
            # Share the physical-disc header parser so captured images retain
            # the same region, product and normalized title as the inserted CD.
            # Import locally because disc_identity also uses this image module.
            from .disc_identity import parse_saturn_header
            header=parse_saturn_header(first)
            details.update(header.details)
            return MediaIdentity(header.title,"saturn",header.game_id,label,"header",header.notes,details)
        if first[:14] == b"SEGADISCSYSTEM":
            from .disc_identity import parse_sega_cd_header
            header=parse_sega_cd_header(first)
            details.update(header.details)
            return MediaIdentity(header.title,"sega_cd",header.game_id,label,"header",header.notes,details)
        pvd,blocks,root = _pvd(source,stream)
        system = pvd[8:40].decode("ascii",errors="replace").strip(" \0")
        volume_id = pvd[40:72].decode("ascii",errors="replace").strip(" \0")
        details.update(system_marker=system,volume_id=volume_id,volume_blocks=blocks,
                       volume_bytes=blocks * 2048)
        if system.upper() == "PLAYSTATION":
            config = _root_file(source,stream,blocks,root,"SYSTEM.CNF")
            executable,serial = _boot_identity(config) if config is not None else (None,None)
            details["boot_executable"] = executable
            notes = () if serial else ("PlayStation volume marker is present; a standard game serial was not found in SYSTEM.CNF.",)
            return MediaIdentity(entry.stem,"psx",serial,label,"filesystem",notes,details)
        return MediaIdentity(entry.stem,None,None,label,"filesystem",
            ("ISO9660 volume found; no supported platform marker was established.",),details)


def identify_media(path: str | Path, *, _depth=0) -> MediaIdentity:
    """Identify format/platform markers using bounded reads of a selected image.

    Unrecognized or malformed contents return system=None plus explanatory notes.
    Missing files still raise OSError. Filename extensions alone never establish
    a platform. Large containers are not unpacked or executed.
    """
    entry = Path(path).expanduser().resolve(strict=True)
    if not entry.is_file():
        raise ValueError("Media must be a regular file")
    if _depth > 8:
        raise MediaFormatError("Playlist recursion exceeds the identification limit")
    suffix = entry.suffix.lower()
    try:
        if suffix == ".m3u":
            for line in _descriptor_text(entry).splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    target = _local_reference(entry,line.strip())
                    if target == entry:
                        raise MediaFormatError("Cyclic playlist")
                    identity = identify_media(target,_depth=_depth + 1)
                    return MediaIdentity(entry.stem,identity.system,identity.game_id,"M3U playlist",
                        identity.confidence,identity.notes + ("Identity comes from the first playlist entry.",),identity.details)
            raise MediaFormatError("Playlist contains no media entries")
        if suffix == ".cue":
            source = _cue_source(entry)
            return _identify_disc(entry,source,"CUE/BIN " + source.mode)
        with entry.open("rb") as stream:
            header = stream.read(512)
        size = entry.stat().st_size
        if header[:4] == b"NES\x1a":
            if len(header) < 16:
                raise MediaFormatError("Truncated iNES header")
            nes2 = header[7] & 0x0C == 0x08
            if nes2:
                return MediaIdentity(entry.stem,"nes",None,"NES 2.0","header",
                    ("NES 2.0 header recognized; payload sizing is delegated to the emulator.",),{"source_bytes":size})
            expected = 16 + (512 if header[6] & 4 else 0) + header[4] * 16384 + header[5] * 8192
            if not header[4] or size < expected:
                raise MediaFormatError("iNES payload is shorter than its declared banks")
            return MediaIdentity(entry.stem,"nes",None,"iNES","header",(),
                                 {"source_bytes":size,"mapper":(header[6] >> 4) | (header[7] & 0xF0)})
        if header[:4] in (b"\x80\x37\x12\x40",b"\x37\x80\x40\x12",b"\x40\x12\x37\x80"):
            alignment = {b"\x80\x37\x12\x40":1,b"\x37\x80\x40\x12":2,b"\x40\x12\x37\x80":4}[header[:4]]
            if size < 64 or size % alignment:
                raise MediaFormatError("Truncated Nintendo 64 header")
            return MediaIdentity(entry.stem,"n64",None,"N64 ROM","header",(),{"source_bytes":size})
        if header[0x100:0x110].startswith((b"SEGA MEGA DRIVE",b"SEGA GENESIS")):
            title = header[0x150:0x180].decode("ascii",errors="replace").strip(" \0") or entry.stem
            return MediaIdentity(title,"genesis",None,"Mega Drive / Genesis ROM","header",(),{"source_bytes":size})
        with entry.open("rb") as stream:
            for location in (0x1FF0,0x3FF0,0x7FF0):
                if size < location + 16:
                    continue
                stream.seek(location)
                sega8=stream.read(16)
                if sega8[:8] == b"TMR SEGA":
                    region=sega8[15] >> 4
                    system="sms" if region in (3,4) else "game_gear" if region in (5,6,7) else None
                    return MediaIdentity(entry.stem,system,None,"SEGA 8-bit ROM","header",
                        () if system else ("SEGA header found with an unrecognized platform/region code.",),
                        {"source_bytes":size,"header_offset":location,"region_code":region})
        if suffix == ".chd":
            return MediaIdentity(entry.stem,None,None,"CHD container" if header[:8] == b"MComprHD" else "Unrecognized CHD file","unknown",
                ("CHD identification requires a configured decoder or explicit metadata.",),{"source_bytes":size})
        if header[:12] == _SYNC and len(header) >= 24 and header[15] in (1,2):
            mode = header[15]
            source = _SectorSource(entry,2352,16 if mode == 1 else 24,mode=f"MODE{mode}/2352")
            return _identify_disc(entry,source,f"Raw MODE{mode}/2352")
        if size >= 18 * 2048:
            return _identify_disc(entry,_SectorSource(entry,2048,0),"ISO9660 / 2048")
        return MediaIdentity(entry.stem,None,None,suffix.lstrip(".").upper() or "Unknown","unknown",
                             ("No supported platform header was established.",),{"source_bytes":size})
    except (MediaFormatError,UnicodeError,ValueError) as error:
        return MediaIdentity(entry.stem,None,None,suffix.lstrip(".").upper() or "Unknown","unknown",
                             (str(error),),{"source_bytes":entry.stat().st_size})
