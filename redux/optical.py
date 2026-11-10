"""Read-only Windows optical-drive discovery and disc table-of-contents inspection.

This module uses the Windows CD-ROM class driver, including drives attached by
USB. It installs no driver, requests no write access, and issues no tray, lock,
speed, raw-sector, or write commands. TOC information does not identify a console
or prove that a data track can be represented faithfully as a 2048-byte ISO.

API references and current limitations are in docs/optical.txt.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
import re


DRIVE_CDROM = 5
IOCTL_CDROM_READ_TOC_EX = 0x00024054
MAXIMUM_CDROM_SIZE = 804
_DATA_TRACK = 0x04  # AUDIO_DATA_TRACK in the Windows SDK ntddcdrm.h.
_LEAD_OUT = 0xAA


class OpticalError(RuntimeError):
    """A discovery, access, or unsupported-disc error suitable for display."""

    def __init__(self, message: str, *, reason: str = "io_error",
                 winerror: int | None = None, drive: str | None = None):
        super().__init__(message)
        self.reason = reason
        self.winerror = winerror
        self.drive = drive


class NoMediaError(OpticalError):
    """Windows reports an empty drive or media that is not ready."""


class TocParseError(OpticalError):
    """The driver returned an incomplete or unsupported table of contents."""

    def __init__(self, message: str):
        super().__init__(message, reason="invalid_toc")


@dataclass(frozen=True)
class OpticalDrive:
    letter: str
    root_path: str
    device_path: str


@dataclass(frozen=True)
class DiscTrack:
    number: int
    control: int
    adr: int
    start_lba: int
    end_lba: int

    @property
    def is_data(self) -> bool:
        return bool(self.control & _DATA_TRACK)

    @property
    def is_audio(self) -> bool:
        return not self.is_data

    @property
    def kind(self) -> str:
        return "data" if self.is_data else "audio"

    @property
    def sector_count(self) -> int:
        """TOC extent, not an assertion about extractable sector payloads."""
        return self.end_lba - self.start_lba


@dataclass(frozen=True)
class TableOfContents:
    first_track: int
    last_track: int
    tracks: tuple[DiscTrack, ...]
    lead_out_lba: int


@dataclass(frozen=True)
class Iso9660Volume:
    system_id: str
    volume_id: str
    logical_block_size: int
    volume_blocks: int
    volume_bytes: int
    device_bytes: int | None = None


def parse_iso9660_pvd(data: bytes, *, device_bytes: int | None = None) -> Iso9660Volume:
    """Validate the ISO9660 primary descriptor without assuming a CD track layout."""
    if len(data) != 2048 or data[:7] != b"\x01CD001\x01":
        raise OpticalError("No supported ISO9660 primary volume descriptor was found.",
                           reason="unsupported_volume")
    blocks = int.from_bytes(data[80:84], "little")
    block_size = int.from_bytes(data[128:130], "little")
    if blocks != int.from_bytes(data[84:88], "big") or block_size != int.from_bytes(data[130:132], "big"):
        raise OpticalError("The ISO9660 descriptor's byte-order fields disagree.", reason="invalid_volume")
    if blocks < 18 or block_size != 2048:
        raise OpticalError("The volume does not describe supported 2048-byte logical blocks.", reason="unsupported_volume")
    size = blocks * block_size
    if device_bytes is not None and (device_bytes <= 0 or size > device_bytes):
        raise OpticalError("The declared ISO9660 volume extends beyond the reported device.", reason="invalid_volume")
    return Iso9660Volume(data[8:40].decode("ascii",errors="replace").rstrip(" \0"),
                         data[40:72].decode("ascii",errors="replace").rstrip(" \0"),
                         block_size, blocks, size, device_bytes)


@dataclass(frozen=True)
class DiscInfo:
    drive: OpticalDrive
    first_track: int | None
    last_track: int | None
    tracks: tuple[DiscTrack, ...]
    lead_out_lba: int | None
    has_audio: bool | None
    has_data: bool
    disc_type: str
    notes: tuple[str, ...]
    volume: Iso9660Volume | None = None
    inspection_kind: str = "track_toc"


def normalize_drive(letter: str) -> str:
    """Accept a drive letter/root only; never arbitrary device or file paths."""
    if not isinstance(letter, str):
        raise TypeError("Drive must be a drive letter such as D:.")
    if not re.fullmatch(r"[A-Za-z](?::(?:[\\/])?)?", letter):
        raise ValueError("Drive must be a drive letter such as D:.")
    return letter[0].upper() + ":"


def _drive(letter: str) -> OpticalDrive:
    letter = normalize_drive(letter)
    return OpticalDrive(letter, letter + "\\", "\\\\.\\" + letter)


def parse_toc(data: bytes, *, msf: bool = False) -> TableOfContents:
    """Parse CDROM_TOC format 0; addresses are big-endian LBA by default.

    With msf=True, addresses are binary [reserved, minute, second, frame].
    Only finalized, complete format-0 TOCs with a lead-out are accepted. Any
    trailing driver-buffer padding is ignored according to the declared length.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("TOC data must be bytes.")
    data = bytes(data)
    if len(data) < 4:
        raise TocParseError("The drive returned a truncated TOC header.")
    total = int.from_bytes(data[:2], "big") + 2
    if total < 4 or total > len(data) or total > MAXIMUM_CDROM_SIZE:
        raise TocParseError("The drive returned an invalid TOC length.")
    first, last = data[2], data[3]
    if not 1 <= first <= last <= 99:
        raise TocParseError("The disc's track range is unsupported or invalid.")
    count = last - first + 1
    expected = 4 + (count + 1) * 8
    if total != expected:
        raise TocParseError("The TOC is partial or has an unsupported track layout.")

    entries = []
    for index in range(count + 1):
        offset = 4 + index * 8
        record = data[offset:offset + 8]
        number = record[2]
        expected_number = first + index if index < count else _LEAD_OUT
        if number != expected_number:
            raise TocParseError("The TOC has missing, duplicate, or unordered tracks.")
        adr, control = record[1] >> 4, record[1] & 0x0F
        if adr != 1:
            raise TocParseError("The TOC does not contain supported position entries.")
        if msf:
            reserved, minute, second, frame = record[4:8]
            if reserved or second >= 60 or frame >= 75:
                raise TocParseError("The TOC contains an invalid MSF address.")
            lba = (minute * 60 + second) * 75 + frame - 150
        else:
            # READ_TOC_EX Msf=0 requests logical block addressing explicitly.
            lba = int.from_bytes(record[4:8], "big")
        if lba < 0 or lba > 0x7FFFFFFF:
            raise TocParseError("The TOC contains an unsupported track address.")
        if entries and lba <= entries[-1][3]:
            raise TocParseError("The TOC's track addresses do not increase.")
        entries.append((number, control, adr, lba))

    tracks = tuple(
        DiscTrack(number, control, adr, start, entries[index + 1][3])
        for index, (number, control, adr, start) in enumerate(entries[:-1])
    )
    return TableOfContents(first, last, tracks, entries[-1][3])


def list_drives() -> list[OpticalDrive]:
    """List drive letters that Windows classifies as optical, empty or loaded.

    USB and internal connections are included. The transport and manufacturer
    are deliberately not inferred from the drive letter or DRIVE_CDROM value.
    """
    api = _get_api()
    mask = api.logical_drive_mask()
    drives = []
    for bit in range(26):
        if mask & (1 << bit):
            drive = _drive(chr(65 + bit))
            if api.drive_type(drive.root_path) == DRIVE_CDROM:
                drives.append(drive)
    return drives


def inspect_disc(letter: str) -> DiscInfo:
    """Read the inserted disc's complete format-0 TOC without changing media.

    Run in a worker thread: the drive may spin up or take time to report errors.
    No console identity, sector mode, image compatibility, or playable state is
    inferred from audio/data flags. DVD/BD drivers can return compatibility TOCs.
    """
    drive = _drive(letter)
    api = _get_api()
    if api.drive_type(drive.root_path) != DRIVE_CDROM:
        raise OpticalError(f"{drive.letter} is not an available optical drive.",
                           reason="not_optical", drive=drive.letter)
    try:
        toc = parse_toc(api.read_toc(drive))
    except OpticalError as error:
        if error.reason not in {"invalid_toc","unsupported_toc"}:
            raise
        # Some virtual drivers return an internally inconsistent CD TOC while
        # exposing a valid cooked data volume. Do not invent or fix track entries.
        volume = api.read_iso_volume(drive)
        notes = [f"CD track layout unavailable: {error}",
                 "Readable ISO9660 logical data volume; audio, raw-sector layout and session information remain unknown.",
                 "Preserve an existing CUE/BIN source; a cooked ISO would omit its raw-sector information."]
        if volume.device_bytes is not None and volume.device_bytes != volume.volume_bytes:
            notes.append("The driver's device capacity differs from the ISO9660 volume extent; neither is treated as a complete disc image.")
        return DiscInfo(drive,None,None,(),None,None,True,"logical_data_volume",
                        tuple(notes),volume,"iso9660_volume")
    audio = any(track.is_audio for track in toc.tracks)
    data = any(track.is_data for track in toc.tracks)
    disc_type = "mixed_mode" if audio and data else "data" if data else "audio"
    notes = (
        "Track types come from the drive's TOC; console and sector format are unknown.",
        "TOC extents can include session gaps and do not define an imaging contract.",
    )
    return DiscInfo(drive, toc.first_track, toc.last_track, toc.tracks,
                    toc.lead_out_lba, audio, data, disc_type, notes)


@lru_cache(maxsize=1)
def _get_api():
    if os.name != "nt":
        raise OpticalError("Optical-drive inspection currently requires Windows.",
                           reason="unsupported_platform")
    return _WindowsAPI()


class _WindowsAPI:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetLogicalDrives.argtypes = []
        self.kernel.GetLogicalDrives.restype = wintypes.DWORD
        self.kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.kernel.GetDriveTypeW.restype = wintypes.UINT
        self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
            wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            wintypes.HANDLE]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.kernel.DeviceIoControl.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_int64,
            ctypes.POINTER(ctypes.c_int64), wintypes.DWORD]
        self.kernel.SetFilePointerEx.restype = wintypes.BOOL
        self.kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.kernel.ReadFile.restype = wintypes.BOOL

    def _error(self, action: str, drive: str | None = None):
        number = self.ctypes.get_last_error()
        detail = self.ctypes.FormatError(number).strip()
        prefix = f"{drive}: " if drive else ""
        if number in {21, 1112}:  # ERROR_NOT_READY / ERROR_NO_MEDIA_IN_DRIVE
            return NoMediaError(prefix + "No disc is present or the drive is not ready.",
                                reason="no_media", winerror=number, drive=drive)
        reason = "access_denied" if number == 5 else "io_error"
        if number in {1, 50, 87}:  # INVALID_FUNCTION / NOT_SUPPORTED / INVALID_PARAMETER
            reason = "unsupported_toc"
        return OpticalError(f"{prefix}{action}: {detail} (Windows error {number})",
                            reason=reason, winerror=number, drive=drive)

    def logical_drive_mask(self) -> int:
        mask = self.kernel.GetLogicalDrives()
        if not mask:
            raise self._error("Could not enumerate drives")
        return mask

    def drive_type(self, root: str) -> int:
        return self.kernel.GetDriveTypeW(root)

    def read_toc(self, drive: OpticalDrive) -> bytes:
        c = self.ctypes
        # FILE_SHARE_WRITE permits coexistence with other handles; desired access
        # remains GENERIC_READ. There is no GENERIC_WRITE or elevated fallback.
        handle = self.kernel.CreateFileW(drive.device_path, 0x80000000,
            0x00000001 | 0x00000002, None, 3, 0, None)
        if handle == c.c_void_p(-1).value:
            raise self._error("Could not open optical drive for reading", drive.letter)
        try:
            # Format 0, Msf=0 (LBA), SessionTrack=0, reserved bytes=0.
            query = c.create_string_buffer(b"\x00\x00\x00\x00", 4)
            output = c.create_string_buffer(MAXIMUM_CDROM_SIZE)
            returned = self.wintypes.DWORD()
            if not self.kernel.DeviceIoControl(handle, IOCTL_CDROM_READ_TOC_EX,
                    query, len(query), output, len(output), c.byref(returned), None):
                raise self._error("Could not read this disc's TOC", drive.letter)
            if returned.value > len(output):
                raise TocParseError("The drive reported an invalid output-buffer size.")
            return output.raw[:returned.value]
        finally:
            self.kernel.CloseHandle(handle)

    def read_iso_volume(self, drive: OpticalDrive) -> Iso9660Volume:
        """Inspect ISO9660 descriptors and optional device length, without imaging.

        A valid volume is not a claim that audio/raw/session data are absent.
        Only READ access and read/query operations are issued.
        """
        c = self.ctypes
        handle = self.kernel.CreateFileW(drive.device_path,0x80000000,3,None,3,0,None)
        if handle == c.c_void_p(-1).value:
            raise self._error("Could not open the logical volume for reading",drive.letter)
        try:
            length_buffer = c.create_string_buffer(8)
            returned = self.wintypes.DWORD()
            device_bytes = None
            # IOCTL_DISK_GET_LENGTH_INFO is a read-only length query. Geometry
            # can contain driver padding, so it is retained separately from PVD.
            if self.kernel.DeviceIoControl(handle,0x7405c,None,0,length_buffer,8,c.byref(returned),None):
                if returned.value == 8:
                    device_bytes = int.from_bytes(length_buffer.raw,"little",signed=True)
            if not self.kernel.SetFilePointerEx(handle,16*2048,None,0):
                raise self._error("Could not seek to ISO9660 descriptors",drive.letter)
            for _ in range(64):
                block = c.create_string_buffer(2048)
                returned = self.wintypes.DWORD()
                if not self.kernel.ReadFile(handle,block,2048,c.byref(returned),None):
                    raise self._error("Could not read the logical volume descriptor",drive.letter)
                data = block.raw[:returned.value]
                if len(data) != 2048 or data[1:7] != b"CD001\x01":
                    break
                if data[0] == 1:
                    return parse_iso9660_pvd(data,device_bytes=device_bytes)
                if data[0] == 255:
                    break
            raise OpticalError("The TOC is unsupported and no readable ISO9660 volume was found.",
                               reason="unsupported_volume",drive=drive.letter)
        finally:
            self.kernel.CloseHandle(handle)
