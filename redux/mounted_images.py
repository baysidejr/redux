"""Optional read-only discovery of a WinCDEmu drive's original image file.

The private path query is issued only after the standard Windows storage
descriptor identifies the product as WinCDEmu. No helper executable, elevation,
mount, eject, or write operation is used. Unsupported devices return None.
"""
from __future__ import annotations

from pathlib import Path, PureWindowsPath
import re
import struct

IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
IOCTL_VDEV_GET_IMAGE_PATH = 0x00244054
_MAX_DESCRIPTOR = 64 * 1024
_MAX_PATH_BYTES = 64 * 1024
_IMAGE_EXTENSIONS = frozenset({".iso", ".cue", ".bin", ".ccd", ".chd", ".m3u"})


def _invalid(message: str, letter: str):
    from .optical import OpticalError
    return OpticalError(message, reason="invalid_mounted_image", drive=letter)


def _query(api, handle, code: int, query: bytes | None, size: int, letter: str):
    c = api.ctypes
    source = c.create_string_buffer(query, len(query)) if query is not None else None
    output = c.create_string_buffer(size)
    returned = api.wintypes.DWORD()
    if not api.kernel.DeviceIoControl(handle, code, source,
            len(query) if query is not None else 0, output, size, c.byref(returned), None):
        return None
    if returned.value > size:
        raise _invalid("The mounted-image driver reported an invalid buffer length.", letter)
    return output.raw[:returned.value]


def _product(api, handle, letter: str) -> str | None:
    # STORAGE_PROPERTY_QUERY: StorageDeviceProperty, PropertyStandardQuery,
    # one zero additional-parameters byte plus native structure padding.
    query = bytes(12)
    header = _query(api, handle, IOCTL_STORAGE_QUERY_PROPERTY, query, 8, letter)
    if header is None:
        return None
    if len(header) != 8:
        raise _invalid("The storage descriptor header is incomplete.", letter)
    version, size = struct.unpack("<II", header)
    if version < 36 or not 36 <= size <= _MAX_DESCRIPTOR:
        raise _invalid("The storage descriptor has invalid bounds.", letter)
    descriptor = _query(api, handle, IOCTL_STORAGE_QUERY_PROPERTY, query, size, letter)
    if descriptor is None:
        return None
    if len(descriptor) < 36:
        raise _invalid("The storage device descriptor is incomplete.", letter)
    current_version, current_size = struct.unpack_from("<II", descriptor)
    if current_version < 36 or current_size != size or len(descriptor) < current_size:
        raise _invalid("The storage descriptor changed or was truncated.", letter)
    product_offset = struct.unpack_from("<I", descriptor, 16)[0]
    if product_offset == 0:
        return None
    if not 36 <= product_offset < size:
        raise _invalid("The storage product string is outside its descriptor.", letter)
    end = descriptor.find(b"\0", product_offset, size)
    if end < 0:
        raise _invalid("The storage product string is not terminated.", letter)
    try:
        return descriptor[product_offset:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise _invalid("The storage product string is not valid ASCII.", letter) from error


def _source_path(payload: bytes, letter: str) -> Path | None:
    if len(payload) < 4:
        raise _invalid("The mounted-image path response is incomplete.", letter)
    length = struct.unpack_from("<I", payload)[0]
    if not 2 <= length <= _MAX_PATH_BYTES or length % 2 or len(payload) != 4 + length:
        raise _invalid("The mounted-image path has invalid bounds.", letter)
    encoded = payload[4:]
    if not encoded.endswith(b"\0\0"):
        raise _invalid("The mounted-image path is not terminated.", letter)
    try:
        text = encoded[:-2].decode("utf-16-le")
    except UnicodeDecodeError as error:
        raise _invalid("The mounted-image path is not valid UTF-16.", letter) from error
    if not text:
        return None
    if any(ord(ch) < 32 for ch in text):
        raise _invalid("The mounted-image path contains control characters.", letter)
    # WinCDEmu stores local image paths using the NT DOS-device prefix. This
    # conversion is also performed by its own VirtualCDClient list operation.
    if text.startswith("\\??\\"):
        text = text[4:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    windows_path = PureWindowsPath(text)
    if (not windows_path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", windows_path.drive)
            or windows_path.drive.casefold() == letter.casefold()
            or ":" in str(windows_path)[2:]):
        # Do not probe arbitrary device paths, UNC/network locations or streams.
        raise _invalid("The mounted image does not have a supported local file path.", letter)
    if windows_path.suffix.casefold() not in _IMAGE_EXTENSIONS:
        return None
    try:
        path = Path(text).resolve(strict=True)
        return path if path.is_file() else None
    except (OSError, ValueError):
        return None


def mounted_image_source(letter: str) -> Path | None:
    """Return the existing original image for a verified WinCDEmu optical drive.

    None means unsupported device, unavailable metadata, no mounted source, a
    missing source file, or an image type this app cannot import. Malformed or
    changing driver responses raise OpticalError rather than guessing a path.
    Only the requested drive is opened, with GENERIC_READ and OPEN_EXISTING.
    """
    # Lazy import avoids a cycle if optical inspection calls optional discovery.
    from .optical import DRIVE_CDROM, OpticalError, _drive, _get_api, normalize_drive
    letter = normalize_drive(letter)
    try:
        api = _get_api()
    except OpticalError as error:
        if error.reason == "unsupported_platform":
            return None
        raise
    drive = _drive(letter)
    if api.drive_type(drive.root_path) != DRIVE_CDROM:
        return None
    handle = api.kernel.CreateFileW(drive.device_path, 0x80000000, 3, None, 3, 0, None)
    if handle == api.ctypes.c_void_p(-1).value:
        return None
    try:
        product = _product(api, handle, letter)
        if product is None or product.casefold() != "wincdemu":
            return None
        header = _query(api, handle, IOCTL_VDEV_GET_IMAGE_PATH, None, 4, letter)
        if header is None:
            return None
        if len(header) != 4:
            raise _invalid("The mounted-image path header is incomplete.", letter)
        length = struct.unpack("<I", header)[0]
        if not 2 <= length <= _MAX_PATH_BYTES or length % 2:
            raise _invalid("The mounted-image path length is unsupported.", letter)
        payload = _query(api, handle, IOCTL_VDEV_GET_IMAGE_PATH, None, 4 + length, letter)
        return None if payload is None else _source_path(payload, letter)
    finally:
        api.kernel.CloseHandle(handle)
