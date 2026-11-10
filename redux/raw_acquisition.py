"""Conservative read-only CD main-channel acquisition, separate from ISO export.

Supported pressed single-session CDs become one 2352-byte-sector BIN and a CUE
containing TOC INDEX 01 positions. A bounded, corroborated data-to-audio
transition may also yield an inferred INDEX 00. This is not an archival/full-disc
or subchannel/protection-preserving imager. See docs/raw-acquisition.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import uuid

from .optical import (DRIVE_CDROM, IOCTL_CDROM_READ_TOC_EX, MAXIMUM_CDROM_SIZE,
                      OpticalError, _drive, _get_api, normalize_drive, parse_toc)


IOCTL_CDROM_RAW_READ = 0x0002403E
IOCTL_CDROM_GET_CONFIGURATION = 0x00024058
IOCTL_STORAGE_CHECK_VERIFY = 0x002D4800
CDDA = 2
RAW_WITH_SUBCODE = 5
SECTOR_BYTES = 2352
SUBCODE_SECTOR_BYTES = 2448
_CHUNK_SECTORS = 16
_MAX_SECTORS = 100 * 60 * 75 - 150
_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
_WARNINGS = (
    "BIN preserves driver-returned 2352-byte main sectors and CUE preserves TOC INDEX 01 positions only.",
    "Subchannels, INDEX 00/pregap metadata, hidden lead-in audio, CD-TEXT, ISRC and catalog metadata are not preserved.",
    "This is not a full-disc/protection-preserving or archival-exact dump; subchannel-dependent media is unsupported.",
    "Repeated matching reads do not establish drive audio-offset correction, jitter-free audio, or EDC/ECC correctness.",
    "Drive/media compatibility is specific to observed reads; other hardware and media remain unverified.",
)
_PREGAP_WARNINGS = (
    "BIN preserves returned 2352-byte main sectors; CUE retains TOC INDEX 01 and corroborated inferred audio INDEX 00 boundaries.",
    "INDEX 00 is an inferred main-sector transition, not calibrated raw-subchannel timing or an assumed gap length.",
    "Subchannels, hidden lead-in audio, CD-TEXT, ISRC and catalog metadata are not preserved.",
    *_WARNINGS[2:],
)


class RawAcquisitionError(OpticalError):
    pass


class RawCaptureCancelled(RawAcquisitionError):
    def __init__(self):
        super().__init__("CD main-channel acquisition cancelled.", reason="cancelled")


@dataclass(frozen=True)
class RawTrack:
    number: int
    control: int
    kind: str
    cue_mode: str
    start_lba: int
    end_lba: int
    read_mode: int
    index01_lba: int | None = None
    opaque_pregap_sectors: tuple[dict, ...] = ()

    @property
    def sector_count(self):return self.end_lba-self.start_lba


@dataclass(frozen=True)
class RawCapturePlan:
    drive: str
    tracks: tuple[RawTrack, ...]
    sector_count: int
    bytes_to_capture: int
    toc_sha256: str
    media_change_count: int
    warnings: tuple[str, ...] = _WARNINGS
    mode: str = "cd_main_channel_bin_cue"
    pregap_evidence: tuple[dict, ...] = ()
    read_method: str = "injected_read_sectors"

    @property
    def can_capture(self):return True


@dataclass(frozen=True)
class RawCaptureResult:
    output_directory: Path
    cue_path: Path
    bin_path: Path
    manifest_path: Path
    bytes_written: int
    sha256: str
    source_sha256: str
    tracks: tuple[RawTrack, ...]
    verified_source_passes: int = 2
    verified_output: bool = True
    image_kind: str = "cd_main_channel_bin_cue_toc_index01"
    warnings: tuple[str, ...] = _WARNINGS
    pregap_evidence: tuple[dict, ...] = ()
    read_method: str = "injected_read_sectors"

    @property
    def output_path(self):return self.cue_path


@dataclass(frozen=True)
class MediaSnapshot:
    """Read-only adapter boundary; injectable devices implement this contract."""
    profile: int
    toc: bytes
    session: bytes
    media_change_count: int


def _cancel(event):
    if event is not None and event.is_set():raise RawCaptureCancelled()


def _unsupported(message, reason="unsupported_layout"):
    raise RawAcquisitionError(message,reason=reason)


def _bcd(value):return (value//10)<<4 | value%10


def _expected_header(lba):
    minute,remainder=divmod(lba+150,60*75)
    second,frame=divmod(remainder,75)
    return bytes((_bcd(minute),_bcd(second),_bcd(frame)))


def _data_mode(sector,lba):
    if len(sector)!=SECTOR_BYTES:
        _unsupported("A raw sector was truncated; no padding was substituted.","short_read")
    if sector[:12]!=_SYNC or sector[12:15]!=_expected_header(lba) or sector[15] not in (1,2):
        _unsupported(f"LBA {lba} is not a supported unscrambled Mode 1/2 main sector. "
                     "A pregap, protection, scrambled read, or unsupported raw mode is possible.","unsupported_raw_sector")
    return f"MODE{sector[15]}/2352"


def _validate_data(data,lba,track):
    if not isinstance(data,bytes) or len(data)%SECTOR_BYTES:
        _unsupported("The drive returned a partial raw-sector block.","short_read")
    for offset in range(0,len(data),SECTOR_BYTES):
        sector=data[offset:offset+SECTOR_BYTES];position=lba+offset//SECTOR_BYTES
        if track.kind=="data":
            if _data_mode(sector,position)!=track.cue_mode:
                _unsupported("The data track changes sector mode; it cannot be labeled by one CUE mode.","ambiguous_track")
        else:
            opaque=next((item for item in track.opaque_pregap_sectors if item["lba"]==position),None)
            if opaque:
                if hashlib.sha256(sector).hexdigest()!=opaque["sha256"]:
                    _unsupported("The opaque INDEX 00 transition sector changed from its corroborated bytes.","verification_failed")
            elif sector[:12]==_SYNC:
                _unsupported("An audio extent contains an unexpected data synchronization header; its layout is ambiguous.","ambiguous_track")


def _read(device,lba,count,mode):
    data=device.read_sectors(lba,count,mode)
    if not isinstance(data,bytes) or len(data)!=count*SECTOR_BYTES:
        _unsupported("The optical drive returned a short raw read; no padding or ISO fallback was used.","short_read")
    return data


def _track_chunks(track):
    """Keep one evidenced opaque pregap sector in an isolated Any read.

    Every other sector uses the track's ordinary mode. A chunk never straddles
    this read-mode boundary, and no byte is skipped, duplicated or synthesized.
    """
    position=track.start_lba
    for opaque in track.opaque_pregap_sectors:
        boundary=opaque["lba"]
        while position<boundary:
            count=min(_CHUNK_SECTORS,boundary-position)
            yield position,count,track.read_mode
            position+=count
        yield boundary,1,RAW_WITH_SUBCODE
        position=boundary+1
    while position<track.end_lba:
        count=min(_CHUNK_SECTORS,track.end_lba-position)
        yield position,count,track.read_mode
        position+=count


def _layout(snapshot,drive,device,cancelled):
    if not isinstance(snapshot,MediaSnapshot):raise TypeError("The raw device must return a MediaSnapshot")
    # Restrict to pressed CD-ROM profiles. A completed session does not prove a
    # recordable disc has no additional incomplete session, so CD-R/RW is refused.
    if snapshot.profile!=0x0008:
        _unsupported("Only pressed CD-ROM/CD-DA media with the current CD-ROM profile is supported. "
                     "DVD, Blu-ray, unknown and recordable-media completion states are not accepted.","unsupported_media_profile")
    if not isinstance(snapshot.media_change_count,int) or isinstance(snapshot.media_change_count,bool) or snapshot.media_change_count<0:
        _unsupported("The driver did not provide a valid media-change counter.","media_stability_unavailable")
    session=snapshot.session
    if len(session)!=12 or int.from_bytes(session[:2],"big")+2!=12 or session[2:4]!=b"\x01\x01":
        _unsupported("A single complete session could not be established; multisession or uncertain media is unsupported.","unsupported_sessions")
    try:toc=parse_toc(snapshot.toc)
    except OpticalError as error:
        raise RawAcquisitionError("The raw-CD path requires a trustworthy complete TOC. "+str(error),reason="unsupported_toc") from error
    if toc.first_track!=1 or toc.tracks[0].start_lba!=0 or not 0<toc.lead_out_lba<=_MAX_SECTORS:
        _unsupported("Only contiguous tracks beginning with track 1 at LBA 0 and a bounded CD lead-out are supported.")
    # Session TrackData must agree with the format-0 TOC's first track.
    if session[4:12]!=snapshot.toc[4:12]:
        _unsupported("Session and track TOCs disagree about the first track.","unsupported_sessions")
    for track in toc.tracks:
        if track.control&0x8 or (track.is_data and track.control&0x1):
            _unsupported("Four-channel audio or nonstandard data-track control flags are unsupported.","unsupported_track_flags")
    tracks=[];pregaps=[];audio_boundaries={}
    for position,track in enumerate(toc.tracks):
        _cancel(cancelled)
        boundary=audio_boundaries.get(track.number)
        start=boundary.index00_lba if boundary else track.start_lba
        opaque=tuple(boundary.evidence.get("opaque_transition_sectors",())) if boundary else ()
        if opaque:
            if (len(opaque)!=1 or not isinstance(opaque[0],dict) or opaque[0].get("lba")!=start or
                    opaque[0].get("classification")!="opaque_index00_transition_main_channel" or
                    not isinstance(opaque[0].get("sha256"),str) or len(opaque[0]["sha256"])!=64 or
                    any(character not in "0123456789abcdef" for character in opaque[0]["sha256"])):
                _unsupported("Opaque pregap evidence is not one exact boundary sector.","unsupported_pregap_evidence")
        mode=RAW_WITH_SUBCODE if track.is_data else CDDA
        first=_read(device,start,1,RAW_WITH_SUBCODE if opaque else mode)
        cue_mode=_data_mode(first,start) if track.is_data else "AUDIO"
        item=RawTrack(track.number,track.control,track.kind,cue_mode,start,track.end_lba,mode,
                      track.start_lba if boundary else None,opaque)
        _validate_data(first,start,item)
        if item.sector_count>1:
            last=_read(device,track.end_lba-1,1,mode)
            try:
                _validate_data(last,track.end_lba-1,item)
            except RawAcquisitionError as error:
                following=toc.tracks[position+1] if position+1<len(toc.tracks) else None
                has_evidence_api=(callable(getattr(device,"read_expected_data",None)) and
                                  callable(getattr(device,"read_subq",None)))
                if (not track.is_data or error.reason!="unsupported_raw_sector" or
                        following is None or following.is_data or not has_evidence_api):
                    raise
                from .cd_pregap import resolve_audio_pregap
                gap=resolve_audio_pregap(device,track.start_lba,following.start_lba,
                                         following.number,cue_mode,cancelled=cancelled)
                if (gap.index01_lba!=track.end_lba or
                        not track.start_lba<gap.index00_lba<gap.index01_lba):
                    _unsupported("The corroborated pregap does not fit the neighboring TOC tracks.","unsupported_pregap_evidence")
                item=replace(item,end_lba=gap.index00_lba)
                # Revalidate the new last active-data sector with the same
                # ordinary raw path used for the eventual full capture.
                new_last=_read(device,item.end_lba-1,1,mode)
                _validate_data(new_last,item.end_lba-1,item)
                audio_boundaries[following.number]=gap
                pregaps.append(dict(gap.evidence, data_track=track.number,
                                    audio_track=following.number,index00_lba=gap.index00_lba,
                                    index01_lba=gap.index01_lba))
        tracks.append(item)
    expected=0
    for track in tracks:
        if track.start_lba!=expected or track.end_lba<=track.start_lba:
            _unsupported("Capture extents are not positive and continuous.","unsupported_layout")
        if track.index01_lba is not None and (track.kind!="audio" or
                not track.start_lba<track.index01_lba<track.end_lba):
            _unsupported("An inferred audio pregap does not contain a valid TOC INDEX 01.","unsupported_pregap_evidence")
        expected=track.end_lba
    if expected!=toc.lead_out_lba:
        _unsupported("Capture extents do not reach the observed lead-out.","unsupported_layout")
    _cancel(cancelled)
    return RawCapturePlan(drive,tuple(tracks),toc.lead_out_lba,toc.lead_out_lba*SECTOR_BYTES,
                          hashlib.sha256(snapshot.toc).hexdigest(),snapshot.media_change_count,
                          warnings=((_PREGAP_WARNINGS+(
                              "One corroborated opaque INDEX 00 transition sector is retained byte-for-byte via an Any main-channel read; its encoding is not identified.",))
                              if any(track.opaque_pregap_sectors for track in tracks) else
                              _PREGAP_WARNINGS if pregaps else _WARNINGS),
                          pregap_evidence=tuple(pregaps),
                          read_method=getattr(device,"read_method","injected_read_sectors"))


def _check_scope(require_subchannels,require_protection,require_full_disc):
    if require_subchannels or require_protection or require_full_disc:
        _unsupported("Subchannel/protection-preserving or full-disc capture was requested. "
                     "This interface supports main-channel BIN/CUE without full subchannel preservation.","unsupported_capture_scope")


def _same_media(device,expected):
    if device.snapshot()!=expected:
        _unsupported("The media-change counter, profile, TOC or session changed; no image was published.","media_changed")


def _open_device(drive,device_factory):
    if device_factory is None:
        # scsi_cd inherits the metadata adapter in this module; defer importing
        # it until runtime to keep both modules independently importable.
        from .scsi_cd import WindowsScsiCdDevice
        device_factory=WindowsScsiCdDevice
    return device_factory(drive)


def plan_raw_capture(drive,*,cancelled=None,device_factory=None,require_subchannels=False,
                     require_protection=False,require_full_disc=False):
    """Read metadata and boundary sectors only; never start physical capture.

    Unsupported layouts/drivers raise RawAcquisitionError. No cooked fallback or
    sector repair is attempted. The native default uses read-only READ CD CDBs;
    an injected legacy reader retains its strict TOC-only behavior.
    """
    _check_scope(require_subchannels,require_protection,require_full_disc)
    letter=normalize_drive(drive);_cancel(cancelled)
    with _open_device(letter,device_factory) as device:
        snapshot=device.snapshot();plan=_layout(snapshot,letter,device,cancelled)
        _same_media(device,snapshot)
        return plan


def _cue(plan):
    lines=(['REM REDUX MAIN-CHANNEL CAPTURE: TOC INDEX 01 WITH INFERRED AUDIO INDEX 00',
            'REM Q CORROBORATED MAIN-SECTOR TRANSITION; NO ARCHIVAL OR SUBCHANNEL OFFSET CLAIM']
           if plan.pregap_evidence else
           ['REM REDUX MAIN-CHANNEL CAPTURE: TOC INDEX 01 ONLY',
            'REM NO SUBCHANNELS, INDEX 00 RECONSTRUCTION OR FULL-DISC CLAIM'])
    lines.append('FILE "disc.bin" BINARY')
    for track in plan.tracks:
        lines.append(f"  TRACK {track.number:02d} {track.cue_mode}")
        flags=[]
        if track.control&0x1:flags.append("PRE")
        if track.control&0x2:flags.append("DCP")
        if flags:lines.append("    FLAGS "+" ".join(flags))
        if track.index01_lba is not None:
            minute,remainder=divmod(track.start_lba,60*75);second,frame=divmod(remainder,75)
            lines.append(f"    INDEX 00 {minute:02d}:{second:02d}:{frame:02d}")
        index01=track.start_lba if track.index01_lba is None else track.index01_lba
        minute,remainder=divmod(index01,60*75);second,frame=divmod(remainder,75)
        lines.append(f"    INDEX 01 {minute:02d}:{second:02d}:{frame:02d}")
    return ("\n".join(lines)+"\n").encode("ascii")


def _write(path,data):
    with path.open("xb") as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def _publish_directory(stage,target):
    if stage.resolve().parent!=target.parent.resolve():
        _unsupported("Capture staging left the selected output directory.","invalid_output")
    if os.name=="nt":
        os.rename(stage,target)  # Windows rename refuses an existing destination.
    elif sys.platform.startswith("linux"):
        # Keep injected-device tests honest on Linux: ordinary POSIX rename can
        # overwrite an existing empty directory, so use atomic RENAME_NOREPLACE.
        import ctypes
        libc=ctypes.CDLL(None,use_errno=True)
        rename=getattr(libc,"renameat2",None)
        if rename is None:_unsupported("Atomic no-overwrite directory publication is unavailable.","unsupported_platform")
        rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
        rename.restype=ctypes.c_int
        if rename(-100,os.fsencode(stage),-100,os.fsencode(target),1)!=0:
            number=ctypes.get_errno()
            if number==errno.EEXIST:raise FileExistsError(number,os.strerror(number),str(target))
            raise OSError(number,os.strerror(number),str(target))
    else:_unsupported("Atomic directory publication is implemented for Windows only.","unsupported_platform")


def _cleanup(stage,parent):
    if stage.resolve().parent!=parent.resolve() or not stage.name.startswith(".redux-raw-"):
        _unsupported("Refusing capture cleanup outside its private staging folder.","invalid_output")
    if stage.is_symlink() or (hasattr(stage,"is_junction") and stage.is_junction()):
        _unsupported("Refusing capture cleanup through a redirected staging folder.","invalid_output")
    if stage.exists():shutil.rmtree(stage)


def capture_raw_cd(drive,destination_directory,*,cancelled=None,progress=None,device_factory=None,
                   require_subchannels=False,require_protection=False,require_full_disc=False):
    """Capture twice-read main sectors to a new atomically published folder.

    The destination must not already exist. progress(done,total,phase) has total
    3*BIN bytes and phases write/verify_output/verify_source. The method never
    issues only read/query commands. The native default uses SCSI READ CD through
    the class driver (a shared read/write-access handle is required by Windows,
    with data-in CDBs only). No writes, tray/settings changes or ISO fallback are
    issued. Cancellation is checked between synchronous device reads.
    """
    _check_scope(require_subchannels,require_protection,require_full_disc)
    letter=normalize_drive(drive);_cancel(cancelled)
    original=Path(destination_directory).expanduser()
    if original.is_symlink() or (hasattr(original,"is_junction") and original.is_junction()):
        _unsupported("Capture destination cannot be a redirected path.","invalid_output")
    target=original.resolve()
    if target.exists():raise FileExistsError(target)
    if target.drive.casefold()==letter.casefold():raise ValueError("Output cannot be on the source optical drive")
    target.parent.mkdir(parents=True,exist_ok=True)
    stage=target.parent/(".redux-raw-"+uuid.uuid4().hex)
    try:
        with _open_device(letter,device_factory) as device:
            snapshot=device.snapshot();plan=_layout(snapshot,letter,device,cancelled)
            _same_media(device,snapshot)
            if shutil.disk_usage(target.parent).free<plan.bytes_to_capture+65536:
                raise OSError("There is not enough free storage for the raw main-channel image")
            stage.mkdir();source=hashlib.sha256();track_hashes={};done=0;total=plan.bytes_to_capture*3
            def notify(phase):
                if progress:progress(done,total,phase)
            with (stage/"disc.bin").open("xb") as output:
                for track in plan.tracks:
                    digest=hashlib.sha256()
                    for lba,count,read_mode in _track_chunks(track):
                        _cancel(cancelled)
                        data=_read(device,lba,count,read_mode);_validate_data(data,lba,track)
                        output.write(data);source.update(data);digest.update(data)
                        done+=len(data);notify("write")
                    track_hashes[str(track.number)]=digest.hexdigest()
                output.flush();os.fsync(output.fileno())
            _same_media(device,snapshot);output_hash=hashlib.sha256()
            with (stage/"disc.bin").open("rb") as image:
                while data:=image.read(1024*1024):
                    _cancel(cancelled);output_hash.update(data);done+=len(data);notify("verify_output")
            if (stage/"disc.bin").stat().st_size!=plan.bytes_to_capture or output_hash.digest()!=source.digest():
                _unsupported("The output bytes do not match the captured main sectors.","verification_failed")
            reread=hashlib.sha256()
            for track in plan.tracks:
                digest=hashlib.sha256()
                for lba,count,read_mode in _track_chunks(track):
                    _cancel(cancelled)
                    data=_read(device,lba,count,read_mode);_validate_data(data,lba,track)
                    reread.update(data);digest.update(data);done+=len(data);notify("verify_source")
                if digest.hexdigest()!=track_hashes[str(track.number)]:
                    _unsupported("Repeated source track reads disagree; no image was published.","media_changed")
            if reread.digest()!=source.digest():_unsupported("Repeated source reads disagree.","media_changed")
            _same_media(device,snapshot);_cancel(cancelled)
            cue=_cue(plan);_write(stage/"disc.cue",cue)
            manifest={"format":"redux-cd-main-channel","version":1,"captured_at":datetime.now(timezone.utc).isoformat(),
                "scope":("2352-byte main sectors; TOC INDEX 01 plus Q-corroborated inferred audio INDEX 00"
                         if plan.pregap_evidence else "2352-byte main sectors; TOC INDEX 01 only"),
                "drive":letter,"profile":snapshot.profile,"read_method":plan.read_method,
                "image_kind":"cd_main_channel_bin_cue_toc_index01",
                "pregap_evidence":list(plan.pregap_evidence),
                "media_change_count":snapshot.media_change_count,"toc_hex":snapshot.toc.hex(),"session_hex":snapshot.session.hex(),
                "bytes_written":plan.bytes_to_capture,"sha256":source.hexdigest(),"cue_sha256":hashlib.sha256(cue).hexdigest(),
                "tracks":[dict(asdict(track),opaque_pregap_sectors=list(track.opaque_pregap_sectors),
                               sha256=track_hashes[str(track.number)]) for track in plan.tracks],
                "verified_source_passes":2,"verified_output":True,"warnings":list(plan.warnings)}
            _write(stage/"manifest.json",json.dumps(manifest,indent=2).encode("utf-8"))
            if (stage/"disc.cue").read_bytes()!=cue or json.loads((stage/"manifest.json").read_text(encoding="utf-8"))!=manifest:
                _unsupported("Capture metadata failed output read-back verification.","verification_failed")
            # Recheck the persistent handle immediately before publication; it
            # remains open until all source verification and metadata writes end.
            _same_media(device,snapshot);_cancel(cancelled)
        _publish_directory(stage,target)
        return RawCaptureResult(target,target/"disc.cue",target/"disc.bin",target/"manifest.json",
                                plan.bytes_to_capture,source.hexdigest(),reread.hexdigest(),plan.tracks,
                                warnings=plan.warnings,pregap_evidence=plan.pregap_evidence,read_method=plan.read_method)
    finally:_cleanup(stage,target.parent)


class WindowsRawCdDevice:
    """One persistent GENERIC_READ handle; only read/query IOCTLs are issued."""
    read_method="windows_ioctl_cdrom_raw_read_main_channel"
    def __init__(self,drive,*,api=None):
        self.drive=_drive(drive);self.api=api if api is not None else _get_api();self.handle=None

    def __enter__(self):
        api=self.api
        if api.drive_type(self.drive.root_path)!=DRIVE_CDROM:
            _unsupported("The selected drive is not an optical device.","not_optical")
        self.handle=api.kernel.CreateFileW(self.drive.device_path,0x80000000,3,None,3,0,None)
        if self.handle==api.ctypes.c_void_p(-1).value:
            self.handle=None;raise api._error("Could not open CD for read-only acquisition",self.drive.letter)
        return self

    def _ioctl(self,code,input_data,output_size):
        if self.handle is None:raise RawAcquisitionError("The optical handle is closed.",reason="closed_device")
        api=self.api;c=api.ctypes
        query=c.create_string_buffer(input_data,len(input_data)) if input_data else None
        # METHOD_OUT_DIRECT raw reads use a stable, page-aligned output buffer.
        storage=c.create_string_buffer(output_size+4095)
        address=(c.addressof(storage)+4095)&~4095;returned=api.wintypes.DWORD()
        if not api.kernel.DeviceIoControl(self.handle,code,query,len(input_data),c.c_void_p(address),
                                         output_size,c.byref(returned),None):
            error=api._error("Read-only CD query/read failed",self.drive.letter)
            if code==IOCTL_CDROM_RAW_READ:
                raise RawAcquisitionError("The driver could not supply the requested raw mode; no alternate mode or cooked fallback was used. "+str(error),
                    reason="unsupported_raw_read" if error.winerror in {1,50,87} else error.reason,
                    winerror=error.winerror,drive=self.drive.letter) from error
            raise error
        if returned.value>output_size:_unsupported("The driver returned an invalid byte count.","invalid_driver_output")
        return c.string_at(address,returned.value)

    def _change_count(self):
        data=self._ioctl(IOCTL_STORAGE_CHECK_VERIFY,b"",4)
        if len(data)!=4:_unsupported("The driver did not return its media-change counter.","media_stability_unavailable")
        return int.from_bytes(data,"little")

    def snapshot(self):
        before=self._change_count()
        # Native pointer-width Reserved[2], not a packed 32-bit approximation.
        import ctypes
        query=struct.pack("<II",0,2)+bytes(2*ctypes.sizeof(ctypes.c_void_p))
        config=self._ioctl(IOCTL_CDROM_GET_CONFIGURATION,query,4096)
        if len(config)<8:_unsupported("The drive profile query was truncated.","unsupported_media_profile")
        toc=self._ioctl(IOCTL_CDROM_READ_TOC_EX,b"\0\0\0\0",MAXIMUM_CDROM_SIZE)
        session=self._ioctl(IOCTL_CDROM_READ_TOC_EX,b"\x01\0\0\0",12)
        if len(toc)>=2:toc=toc[:int.from_bytes(toc[:2],"big")+2]
        after=self._change_count()
        if before!=after:_unsupported("Media changed during layout inspection.","media_changed")
        return MediaSnapshot(int.from_bytes(config[6:8],"big"),toc,session,before)

    def read_sectors(self,lba,count,mode):
        if isinstance(lba,bool) or not isinstance(lba,int) or not 0<=lba<_MAX_SECTORS:
            raise ValueError("Raw CD LBA is out of range")
        if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=_CHUNK_SECTORS or lba+count>_MAX_SECTORS:
            raise ValueError("Raw reads require 1–16 bounded sectors")
        if mode not in (CDDA,RAW_WITH_SUBCODE):raise ValueError("Unsupported raw-read mode")
        stride=SECTOR_BYTES if mode==CDDA else SUBCODE_SECTOR_BYTES
        data=self._ioctl(IOCTL_CDROM_RAW_READ,struct.pack("<qII",lba*2048,count,mode),count*stride)
        if len(data)!=count*stride:_unsupported("The driver returned a short raw read.","short_read")
        return data if mode==CDDA else b"".join(data[i:i+SECTOR_BYTES] for i in range(0,len(data),stride))

    def __exit__(self,*args):
        if self.handle is not None:
            self.api.kernel.CloseHandle(self.handle);self.handle=None
