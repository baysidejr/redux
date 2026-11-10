"""Bounded, read-only MMC READ CD through the Windows CD-ROM class driver.

The pass-through IOCTL requires a handle with read *and* write access rights.
This module issues only DATA_IN READ CD (0xBE); it never issues a write, tray,
speed, authentication, or mode-changing CDB. Metadata queries are inherited
from WindowsRawCdDevice. No raw data is synthesized or repaired.
"""
from __future__ import annotations

import ctypes
import struct

from .optical import DRIVE_CDROM
from .raw_acquisition import (CDDA, RAW_WITH_SUBCODE, SECTOR_BYTES,
                              RawAcquisitionError, WindowsRawCdDevice,
                              _CHUNK_SECTORS, _MAX_SECTORS, _data_mode)


IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x0004D014
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
SCSI_IOCTL_DATA_IN = 1
READ_CD = 0xBE
_SENSE_BYTES = 64


class SCSI_PASS_THROUGH_DIRECT(ctypes.Structure):
    """Native Windows ABI: DWORD is always 32 bits, pointer is native width."""
    _fields_ = [
        ("Length", ctypes.c_uint16), ("ScsiStatus", ctypes.c_uint8),
        ("PathId", ctypes.c_uint8), ("TargetId", ctypes.c_uint8),
        ("Lun", ctypes.c_uint8), ("CdbLength", ctypes.c_uint8),
        ("SenseInfoLength", ctypes.c_uint8), ("DataIn", ctypes.c_uint8),
        ("DataTransferLength", ctypes.c_uint32), ("TimeOutValue", ctypes.c_uint32),
        ("DataBuffer", ctypes.c_void_p), ("SenseInfoOffset", ctypes.c_uint32),
        ("Cdb", ctypes.c_uint8 * 16),
    ]


class _Packet(ctypes.Structure):
    _fields_ = [("sptd", SCSI_PASS_THROUGH_DIRECT),
                ("sense", ctypes.c_uint8 * _SENSE_BYTES)]


class ScsiReadError(RawAcquisitionError):
    def __init__(self, message, *, diagnostic, reason="io_error", winerror=None, drive=None):
        super().__init__(message, reason=reason, winerror=winerror, drive=drive)
        self.diagnostic = diagnostic


def _sense_fields(data):
    """Decode only fields common to fixed/descriptor SPC sense formats."""
    if not data:
        return {}
    response = data[0] & 0x7F
    if response in (0x70, 0x71) and len(data) >= 14:
        return {"sense_key": data[2] & 15, "asc": data[12], "ascq": data[13]}
    if response in (0x72, 0x73) and len(data) >= 4:
        return {"sense_key": data[1] & 15, "asc": data[2], "ascq": data[3]}
    return {}


def _sense_reason(fields):
    key, asc = fields.get("sense_key"), fields.get("asc")
    if key == 5:
        return {0x20: "unsupported_read_cd", 0x24: "invalid_read_cd_field",
                0x64: "sector_type_mismatch"}.get(asc, "scsi_illegal_request")
    if key == 2:
        return "no_media" if asc == 0x3A else "not_ready"
    if key == 6:
        return "media_changed" if asc == 0x28 else "unit_attention"
    if key == 3:
        return "medium_read_error"
    if key == 4:
        return "hardware_read_error"
    return "scsi_read_error"


def _check_request(lba, count, mode):
    if isinstance(lba, bool) or not isinstance(lba, int) or not 0 <= lba < _MAX_SECTORS:
        raise ValueError("READ CD LBA is out of range")
    if (isinstance(count, bool) or not isinstance(count, int) or
            not 1 <= count <= _CHUNK_SECTORS or lba + count > _MAX_SECTORS):
        raise ValueError("READ CD requires 1–16 bounded sectors")
    if isinstance(mode, bool) or not isinstance(mode, int) or mode not in (CDDA, RAW_WITH_SUBCODE):
        raise ValueError("Unsupported main-channel read mode")


def _read_cd_cdb(lba, count, mode, *, subq=False, expected_data=None):
    _check_request(lba, count, mode)
    if not isinstance(subq, bool) or (subq and count != 1):
        raise ValueError("Q diagnostics require exactly one sector")
    if expected_data is not None and (isinstance(expected_data, bool) or
            not isinstance(expected_data, int) or expected_data not in (1, 2) or
            mode != RAW_WITH_SUBCODE or subq):
        raise ValueError("Typed reads require main-channel Mode 1 or Mode 2 data")
    cdb = bytearray(12)
    cdb[0] = READ_CD
    # Expected sector type: CD-DA=1 for audio, any=0 for data inspection.
    # DAP remains zero: no requested audio mute/interpolation.
    cdb[1] = 0x04 if mode == CDDA else 0
    if expected_data is not None:
        # MMC type 2 is Mode 1; type 3 is Mode 2 formless (optional for drives).
        cdb[1] = (expected_data + 1) << 2
    cdb[2:6] = lba.to_bytes(4, "big")
    cdb[6:9] = count.to_bytes(3, "big")
    # Audio user data is 2352 bytes. Data asks for sync, both headers,
    # user data and EDC/ECC: the complete 2352-byte main channel.
    cdb[9] = 0x10 if mode == CDDA else 0xF8
    # C2 bits in byte 9 stay zero. Optional diagnostics request only formatted
    # Q (16 bytes); normal main-channel reads request no subchannel.
    cdb[10] = 2 if subq else 0
    return bytes(cdb)


class WindowsScsiCdDevice(WindowsRawCdDevice):
    """An injectable raw-acquisition device; opening never starts a capture.

    The legacy mode value 5 selects a *data* main-channel request here, not
    RAW_WITH_SUBCODE. Both modes return exactly 2352 bytes per sector.
    """
    read_method = "scsi_read_cd_2352_main_channel"

    def __init__(self, drive, *, api=None):
        super().__init__(drive, api=api)
        self.alignment_mask = None
        self.maximum_transfer_length = None
        self.last_diagnostic = None

    def __enter__(self):
        api = self.api
        if api.drive_type(self.drive.root_path) != DRIVE_CDROM:
            raise RawAcquisitionError("The selected drive is not optical.", reason="not_optical")
        self.handle = api.kernel.CreateFileW(self.drive.device_path, 0xC0000000,
                                             3, None, 3, 0, None)
        if self.handle == api.ctypes.c_void_p(-1).value:
            self.handle = None
            error = api._error("READ CD requires a shared read/write-access device handle; "
                               "only read CDBs would be issued", self.drive.letter)
            raise RawAcquisitionError(str(error), reason=error.reason,
                                      winerror=error.winerror, drive=self.drive.letter) from error
        try:
            # STORAGE_PROPERTY_QUERY: AdapterProperty, StandardQuery, additional byte/padding.
            data = self._ioctl(IOCTL_STORAGE_QUERY_PROPERTY, struct.pack("<II", 1, 0) + bytes(4), 4096)
            if len(data) < 20:
                raise RawAcquisitionError("Storage adapter alignment query was truncated.",
                                          reason="adapter_alignment_unavailable")
            version, size, maximum, _pages, mask = struct.unpack_from("<IIIII", data)
            if (version < 20 or size < 20 or mask > 65535 or
                    (mask & (mask + 1)) or maximum < SECTOR_BYTES):
                raise RawAcquisitionError("Storage adapter returned invalid alignment/transfer limits.",
                                          reason="invalid_adapter_limits")
            self.alignment_mask = mask
            self.maximum_transfer_length = maximum
        except BaseException:
            self.__exit__()
            raise
        return self

    def read_sectors(self, lba, count, mode):
        """Return untouched main bytes, or fail with Windows/SCSI diagnostics."""
        return self._read_cd(lba, count, mode, subq=False)

    def read_subq(self, lba, mode=RAW_WITH_SUBCODE):
        """Diagnostic only: return one untouched 16-byte formatted-Q suffix.

        Support is drive-dependent. This does not infer INDEX 00, BCD encoding,
        CRC validity, or preservation of raw P-W subchannels for capture.
        """
        return self._read_cd(lba, 1, mode, subq=True)[SECTOR_BYTES:]

    def read_cdda(self, lba, count=1):
        """Read typed CD-DA; unrelated read errors never imply a sector type."""
        return self.read_sectors(lba, count, CDDA)

    def read_expected_data(self, lba, mode=1, count=1):
        """Read typed data and verify every returned sector's mode/address.

        Mode 1 requests MMC expected type 2. Mode 2 requests type 3 (formless),
        which is optional and does not imply support for XA Form 1/2. A read
        error or invalid header is never reinterpreted as an audio sector.
        """
        if isinstance(mode, bool) or not isinstance(mode, int) or mode not in (1, 2):
            raise ValueError("Typed data mode must be 1 or 2")
        data = self._read_cd(lba, count, RAW_WITH_SUBCODE, subq=False, expected_data=mode)
        for offset in range(count):
            sector=data[offset*SECTOR_BYTES:(offset+1)*SECTOR_BYTES]
            try:
                observed_mode=_data_mode(sector, lba+offset)
            except RawAcquisitionError as error:
                # Some drives ignore READ CD's expected sector type. Preserve
                # complete successful-transfer evidence for an independent
                # Q/CDDA-correlated resolver; this is not an audio assertion.
                if error.reason == "unsupported_raw_sector" and len(sector)==SECTOR_BYTES:
                    error.typed_read_succeeded=True
                    error.raw_sector=sector
                    error.requested_lba=lba+offset
                    error.requested_mode=mode
                raise
            if observed_mode != f"MODE{mode}/2352":
                raise RawAcquisitionError("READ CD returned a different data mode than requested.",
                                          reason="unexpected_data_mode", drive=self.drive.letter)
        return data

    def _read_cd(self, lba, count, mode, *, subq, expected_data=None):
        cdb = _read_cd_cdb(lba, count, mode, subq=subq, expected_data=expected_data)
        if self.handle is None or self.alignment_mask is None:
            raise RawAcquisitionError("The optical handle is closed.", reason="closed_device")
        expected = count * (SECTOR_BYTES + (16 if subq else 0))
        if expected > self.maximum_transfer_length:
            raise RawAcquisitionError("READ CD exceeds the adapter transfer limit.",
                                      reason="adapter_transfer_limit")
        # Honor the adapter mask and page-align even when it requests less.
        mask = max(self.alignment_mask, 4095)
        storage = ctypes.create_string_buffer(expected + mask)
        address = (ctypes.addressof(storage) + mask) & ~mask
        packet = _Packet()
        request = packet.sptd
        request.Length = ctypes.sizeof(SCSI_PASS_THROUGH_DIRECT)
        request.CdbLength = 12
        request.SenseInfoLength = _SENSE_BYTES
        request.DataIn = SCSI_IOCTL_DATA_IN
        request.DataTransferLength = expected
        request.TimeOutValue = 10
        request.DataBuffer = address
        request.SenseInfoOffset = _Packet.sense.offset
        request.Cdb[:12] = cdb
        returned = self.api.wintypes.DWORD()
        ok = self.api.kernel.DeviceIoControl(self.handle, IOCTL_SCSI_PASS_THROUGH_DIRECT,
                ctypes.byref(packet), ctypes.sizeof(packet), ctypes.byref(packet),
                ctypes.sizeof(packet), ctypes.byref(returned), None)
        sense_length = min(request.SenseInfoLength, _SENSE_BYTES)
        sense = bytes(packet.sense[:sense_length])
        diagnostic = {"method": self.read_method, "lba": lba, "sectors": count,
                      "subchannel": "formatted_q_16" if subq else "none",
                      "expected_data_mode": expected_data,
                      "cdb": cdb.hex(), "scsi_status": request.ScsiStatus,
                      "transferred": request.DataTransferLength,
                      "ioctl_returned": returned.value, "sense": sense.hex(),
                      **_sense_fields(sense)}
        self.last_diagnostic = diagnostic
        if not ok:
            error = self.api._error("READ CD failed", self.drive.letter)
            diagnostic["winerror"] = error.winerror
            reason = _sense_reason(diagnostic) if "sense_key" in diagnostic else error.reason
            raise ScsiReadError(str(error), diagnostic=diagnostic, reason=reason,
                                winerror=error.winerror, drive=self.drive.letter) from error
        if not ctypes.sizeof(SCSI_PASS_THROUGH_DIRECT) <= returned.value <= ctypes.sizeof(packet):
            raise ScsiReadError("READ CD returned an invalid pass-through structure length.",
                                diagnostic=diagnostic, reason="invalid_driver_output", drive=self.drive.letter)
        if request.SenseInfoLength > _SENSE_BYTES:
            raise ScsiReadError("READ CD returned an invalid sense length.", diagnostic=diagnostic,
                                reason="invalid_driver_output", drive=self.drive.letter)
        if request.SenseInfoLength and returned.value < request.SenseInfoOffset + request.SenseInfoLength:
            raise ScsiReadError("READ CD truncated its reported sense data.", diagnostic=diagnostic,
                                reason="invalid_driver_output", drive=self.drive.letter)
        if request.ScsiStatus:
            raise ScsiReadError("READ CD returned SCSI status " + hex(request.ScsiStatus) + ".",
                                diagnostic=diagnostic, reason=_sense_reason(diagnostic), drive=self.drive.letter)
        if request.DataTransferLength != expected:
            raise ScsiReadError("READ CD returned a partial main-channel block; no padding was substituted.",
                                diagnostic=diagnostic, reason="short_read", drive=self.drive.letter)
        return ctypes.string_at(address, expected)
