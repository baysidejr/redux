"""Read-only acquisition planning and verified ISO9660 logical-volume export.

An existing mounted image is always preserved through its original source file.
Cooked 2048-byte logical-volume export is separate from raw/full-disc imaging;
it does not preserve audio tracks, raw sector headers, ECC/EDC or subchannels.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import uuid

from .mounted_images import mounted_image_source
from .optical import (DRIVE_CDROM, Iso9660Volume, OpticalError, _drive, _get_api,
                      inspect_disc, normalize_drive, parse_iso9660_pvd)


@dataclass(frozen=True)
class AcquisitionPlan:
    drive: str
    mode: str
    source_image: Path | None
    volume: Iso9660Volume | None
    warnings: tuple[str, ...]

    @property
    def can_import_source(self):
        return self.mode == "source_image" and self.source_image is not None

    @property
    def can_capture_logical(self):
        return self.mode == "logical_volume" and self.volume is not None


@dataclass(frozen=True)
class CaptureResult:
    output_path: Path
    bytes_written: int
    sha256: str
    source_sha256: str
    verified_source_passes: int
    verified_output: bool
    image_kind: str
    warnings: tuple[str, ...]


class AcquisitionError(OpticalError):
    pass


class CaptureCancelled(AcquisitionError):
    def __init__(self):
        super().__init__("Logical-volume export cancelled.", reason="cancelled")


class SourceImageAvailable(AcquisitionError):
    def __init__(self, source_image):
        self.source_image = Path(source_image)
        super().__init__(f"Preserve the existing source image and its tracks: {self.source_image}",
                         reason="source_image_available")


def acquisition_plan(drive: str) -> AcquisitionPlan:
    """Choose a lossless original-image route before considering a cooked export.

    This never represents physical audio/mixed-mode acquisition as a plain ISO.
    A logical-volume plan explicitly covers only its readable ISO9660 data.
    """
    letter = normalize_drive(drive)
    source = mounted_image_source(letter)
    if source is not None:
        return AcquisitionPlan(letter,"source_image",source,None,
            ("Import the original source file and every referenced track; do not convert it to a cooked ISO.",))
    info = inspect_disc(letter)
    if info.has_audio is True:
        return AcquisitionPlan(letter,"unsupported",None,None,
            ("Audio or mixed-mode tracks require a validated raw-disc imaging path.",
             "Cooked ISO export cannot preserve this disc's complete track layout."))
    if info.tracks and (len(info.tracks) != 1 or info.tracks[0].start_lba != 0):
        return AcquisitionPlan(letter,"unsupported",None,None,
            ("This track/session layout requires a validated raw-disc imaging path.",))
    volume = info.volume
    if volume is None:
        try:
            volume = _get_api().read_iso_volume(info.drive)
        except OpticalError as error:
            return AcquisitionPlan(letter,"unsupported",None,None,(str(error),))
    warnings = ["Export covers only the ISO9660 logical data volume, not a complete raw disc image.",
                "Audio, raw-sector headers, error-correction data, session gaps and subchannels are not captured."]
    if info.has_audio is None:
        warnings.append("The driver did not establish a trustworthy track/audio layout.")
    if volume.system_id.strip().upper() == "PLAYSTATION":
        warnings.append("A PlayStation marker does not establish that a cooked export will preserve all game requirements.")
    return AcquisitionPlan(letter,"logical_volume",None,volume,tuple(warnings))


plan_acquisition = acquisition_plan


def capture_full_disc(drive, destination, **kwargs):
    """Refuse to mislabel an unvalidated cooked copy as a faithful raw disc image."""
    plan = acquisition_plan(drive)
    if plan.source_image:
        raise SourceImageAvailable(plan.source_image)
    raise AcquisitionError("Full-disc raw/audio/subchannel acquisition is not validated by this interface. "
                           "Use the original image when available; logical-volume export is explicitly separate.",
                           reason="raw_capture_unverified", drive=plan.drive)


def _check_cancel(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise CaptureCancelled()


class _DeviceReader:
    """Synchronous, buffered read-only volume handle with aligned buffers."""
    def __init__(self, letter):
        self.api = _get_api()
        self.drive = _drive(letter)
        self.handle = None

    def __enter__(self):
        api = self.api
        if api.drive_type(self.drive.root_path) != DRIVE_CDROM:
            raise AcquisitionError("The selected drive is not optical.",reason="not_optical",drive=self.drive.letter)
        self.handle = api.kernel.CreateFileW(self.drive.device_path,0x80000000,3,None,3,0,None)
        if self.handle == api.ctypes.c_void_p(-1).value:
            self.handle = None
            raise api._error("Could not open optical data for reading",self.drive.letter)
        return self

    def read(self, offset, count):
        if offset < 0 or count <= 0 or offset % 2048 or count % 2048 or count > 1024 * 1024:
            raise ValueError("Optical reads require aligned, bounded logical-sector ranges")
        api = self.api
        c = api.ctypes
        if not api.kernel.SetFilePointerEx(self.handle,offset,None,0):
            raise api._error("Could not seek optical data",self.drive.letter)
        storage = c.create_string_buffer(count + 2047)
        address = (c.addressof(storage) + 2047) & ~2047
        returned = api.wintypes.DWORD()
        if not api.kernel.ReadFile(self.handle,c.c_void_p(address),count,c.byref(returned),None):
            raise api._error("Could not read optical data",self.drive.letter)
        if returned.value != count:
            raise AcquisitionError("The optical drive returned a short read; no padding was substituted.",
                                   reason="short_read",drive=self.drive.letter)
        return c.string_at(address,count)

    def __exit__(self,kind,error,traceback):
        if self.handle is not None:
            self.api.kernel.CloseHandle(self.handle)
            self.handle = None


def _primary_descriptor(reader, volume):
    for lba in range(16,min(80,volume.volume_blocks)):
        data = reader.read(lba * 2048,2048)
        if len(data) != 2048 or data[1:7] != b"CD001\x01":
            break
        if data[0] == 255:
            break
        if data[0] != 1:
            continue
        observed = parse_iso9660_pvd(data,device_bytes=volume.device_bytes)
        fields = ("system_id","volume_id","logical_block_size","volume_blocks","volume_bytes")
        if any(getattr(observed,key) != getattr(volume,key) for key in fields):
            raise AcquisitionError("The volume changed after inspection.",reason="media_changed")
        return lba,data
    raise AcquisitionError("The readable ISO9660 descriptor changed or disappeared.",reason="media_changed")


def _notify(progress,done,total,phase):
    if progress is not None:
        progress(done,total,phase)


def _publish(temporary: Path, destination: Path):
    if temporary.resolve().parent != destination.resolve().parent:
        raise AcquisitionError("Capture publication must remain in its selected output directory.",reason="invalid_output")
    if os.name == "nt":
        # Windows rename fails if the destination exists; no replace flag is used.
        os.rename(temporary,destination)
    else:
        # POSIX rename would overwrite. An atomic exclusive link avoids that race.
        os.link(temporary,destination)
        temporary.unlink()


def capture_logical_volume(drive: str, destination: str | Path, *, cancelled=None,
                           progress=None) -> CaptureResult:
    """Export a verified cooked ISO9660 data volume, never a claimed full disc.

    progress(done,total,phase) uses total=3*volume_bytes with phases ``write``,
    ``verify_output``, and ``verify_source``. Every source sector is read twice;
    the fsynced output is read back before atomically publishing it. Failures and
    cancellation remove only this call's private temporary output. Existing files
    are never overwritten. The drive is opened GENERIC_READ only.
    """
    letter = normalize_drive(drive)
    plan = acquisition_plan(letter)
    if plan.source_image is not None:
        raise SourceImageAvailable(plan.source_image)
    if not plan.can_capture_logical:
        raise AcquisitionError("No supported logical-volume export is available. " + " ".join(plan.warnings),
                               reason="unsupported_acquisition",drive=letter)
    _check_cancel(cancelled)
    target = Path(destination).expanduser().resolve()
    if target.suffix.lower() != ".iso":
        raise ValueError("A cooked ISO9660 export must use an .iso filename")
    if target.drive.casefold() == letter.casefold():
        raise ValueError("The output cannot be placed on the source optical drive")
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    volume = plan.volume
    if volume.logical_block_size != 2048 or volume.volume_bytes != volume.volume_blocks * 2048:
        raise AcquisitionError("The volume has an unsupported logical extent.",reason="invalid_volume")
    if shutil.disk_usage(target.parent).free < volume.volume_bytes:
        raise OSError("There is not enough free output storage for this logical volume")
    temporary = target.parent / f".{target.name}.capture-{uuid.uuid4().hex}.partial"
    total = volume.volume_bytes * 3
    done = 0
    source_digest = hashlib.sha256()
    try:
        with _DeviceReader(letter) as reader:
            descriptor_lba,descriptor = _primary_descriptor(reader,volume)
            with temporary.open("xb") as output:
                for offset in range(0,volume.volume_bytes,1024 * 1024):
                    _check_cancel(cancelled)
                    count = min(1024 * 1024,volume.volume_bytes - offset)
                    data = reader.read(offset,count)
                    if len(data) != count:
                        raise AcquisitionError("A source read was truncated.",reason="short_read")
                    output.write(data)
                    source_digest.update(data)
                    done += count
                    _notify(progress,done,total,"write")
                output.flush()
                os.fsync(output.fileno())
            _check_cancel(cancelled)
            output_digest = hashlib.sha256()
            with temporary.open("rb") as image:
                while data := image.read(1024 * 1024):
                    _check_cancel(cancelled)
                    output_digest.update(data)
                    done += len(data)
                    _notify(progress,done,total,"verify_output")
            if temporary.stat().st_size != volume.volume_bytes or output_digest.digest() != source_digest.digest():
                raise AcquisitionError("The exported file does not match the bytes read from the volume.",reason="verification_failed")
            reread = hashlib.sha256()
            for offset in range(0,volume.volume_bytes,1024 * 1024):
                _check_cancel(cancelled)
                count = min(1024 * 1024,volume.volume_bytes - offset)
                data = reader.read(offset,count)
                if len(data) != count:
                    raise AcquisitionError("A verification read was truncated.",reason="short_read")
                reread.update(data)
                done += count
                _notify(progress,done,total,"verify_source")
            if reread.digest() != source_digest.digest() or reader.read(descriptor_lba * 2048,2048) != descriptor:
                raise AcquisitionError("Repeated source reads disagree; no image was published.",reason="media_changed")
        _check_cancel(cancelled)
        _publish(temporary,target)
        return CaptureResult(target,volume.volume_bytes,output_digest.hexdigest(),source_digest.hexdigest(),
                             2,True,"iso9660_logical_volume",plan.warnings)
    finally:
        # This is the only file cleanup: a fresh, generated file in the selected
        # output parent. No recursive deletion, device writes or source changes.
        if temporary.resolve().parent != target.parent.resolve() or ".capture-" not in temporary.name:
            raise AcquisitionError("Refusing cleanup outside the capture output directory.",reason="invalid_output")
        if temporary.exists():
            temporary.unlink()
