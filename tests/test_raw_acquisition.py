"""Injected media and Windows ABI tests; these do not claim a physical dump."""
import ctypes
import binascii
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux import raw_acquisition as raw
from redux.library import inspect_bundle
from redux.optical import OpticalError
from redux.scsi_cd import ScsiReadError


def toc_bytes(tracks,leadout,first=1):
    def record(number,control,lba):return bytes((0,0x10|control,number,0))+lba.to_bytes(4,"big")
    payload=bytes((first,first+len(tracks)-1))
    payload+=b"".join(record(first+n,control,lba) for n,(control,lba) in enumerate(tracks))
    payload+=record(0xAA,tracks[-1][0],leadout)
    return len(payload).to_bytes(2,"big")+payload


def data_sector(lba,mode=1):
    body=bytes(((lba*17+n)%251 for n in range(raw.SECTOR_BYTES-16)))
    return raw._SYNC+raw._expected_header(lba)+bytes((mode,))+body


class Device:
    def __init__(self,tracks=((4,0),),leadout=20,mode=1):
        toc=toc_bytes(tracks,leadout)
        self.media=raw.MediaSnapshot(8,toc,b"\0\x0a\x01\x01"+toc[4:12],17)
        self.data=[]
        for lba in range(leadout):
            current=next(control for control,start in reversed(tracks) if start<=lba)
            self.data.append(data_sector(lba,mode) if current&4 else bytes((lba%256,))*raw.SECTOR_BYTES)
        self.reads=[];self.snapshots=0;self.closed=False;self.mutate=False;self.full_passes=0

    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def snapshot(self):self.snapshots+=1;return self.media
    def read_sectors(self,lba,count,mode):
        self.reads.append((lba,count,mode))
        if lba==0 and count>1:self.full_passes+=1
        data=b"".join(self.data[lba:lba+count])
        if self.mutate and self.full_passes>=2 and count>1:
            data=data[:30]+bytes((data[30]^1,))+data[31:]
        return data


class PregapDevice(Device):
    read_method="fixture_typed_read_cd"

    def __init__(self,*,opaque=False):
        super().__init__(tracks=((4,0),(0,50)),leadout=70)
        self.boundary=37;self.index01=50;self.opaque=opaque
        self.q_reads=[];self.bad_q=False;self.capture_chunks=[]
        for lba in range(self.boundary,70):
            self.data[lba]=bytes((lba%251,))*2352
        if opaque:
            self.data[self.boundary]=raw._SYNC+b"bad!"+bytes(2336)

    def read_sectors(self,lba,count,mode):
        result=super().read_sectors(lba,count,mode)
        if self.full_passes:
            self.capture_chunks.append((self.full_passes,lba,count,mode))
        return result

    def read_expected_data(self,lba,mode=1,count=1):
        if lba<self.boundary:
            return self.read_sectors(lba,count,raw.RAW_WITH_SUBCODE)
        if self.opaque and lba==self.boundary:
            error=raw.RawAcquisitionError("Successful raw read has no active data header",reason="unsupported_raw_sector")
            error.typed_read_succeeded=True;error.raw_sector=self.data[lba]
            error.requested_lba=lba;error.requested_mode=mode
            raise error
        raise ScsiReadError("Expected data type does not match",reason="sector_type_mismatch",
                            diagnostic={"lba":lba,"sense_key":5,"asc":0x64,"ascq":0})

    def read_cdda(self,lba,count=1):
        if lba<self.boundary or (self.opaque and lba==self.boundary):
            raise ScsiReadError("Expected audio type does not match",reason="sector_type_mismatch",
                                diagnostic={"lba":lba,"sense_key":5,"asc":0x64,"ascq":0})
        return self.read_sectors(lba,count,raw.CDDA)

    def read_subq(self,lba,mode=raw.CDDA):
        self.q_reads.append(lba)
        if self.bad_q:return bytes(16)
        absolute=lba+1 if self.opaque and lba==self.boundary else lba
        first=bytes((1,2,0 if absolute<self.index01 else 1,0,0,0,0))+raw._expected_header(absolute)
        return first+(binascii.crc_hqx(first,0)^0xFFFF).to_bytes(2,"big")+bytes(4)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        self.target=self.root/"New Disc"
    def tearDown(self):self.temporary.cleanup()
    def capture(self,device,**kwargs):
        return raw.capture_raw_cd("E:",self.target,device_factory=lambda drive:device,**kwargs)

    def test_real_bin_cue_roundtrip_preserves_data_audio_toc_and_flags(self):
        device=Device(tracks=((4,0),(3,12)),leadout=20,mode=2);progress=[]
        result=self.capture(device,progress=lambda *args:progress.append(args))
        expected=b"".join(device.data)
        self.assertEqual(result.bin_path.read_bytes(),expected)
        self.assertEqual(result.sha256,hashlib.sha256(expected).hexdigest())
        self.assertEqual(result.source_sha256,result.sha256);self.assertTrue(result.verified_output)
        self.assertEqual(result.verified_source_passes,2);self.assertTrue(device.closed)
        cue=result.cue_path.read_text(encoding="ascii")
        self.assertIn("TRACK 01 MODE2/2352",cue);self.assertIn("TRACK 02 AUDIO",cue)
        self.assertIn("FLAGS PRE DCP",cue);self.assertIn("INDEX 01 00:00:12",cue)
        self.assertNotIn("INDEX 00",cue.split('FILE "disc.bin" BINARY')[1])
        self.assertEqual({p.name for p in inspect_bundle(result.cue_path).files},{"disc.cue","disc.bin"})
        manifest=json.loads(result.manifest_path.read_text())
        self.assertEqual(manifest["sha256"],result.sha256)
        self.assertEqual([track["kind"] for track in manifest["tracks"]],["data","audio"])
        self.assertEqual({value[2] for value in progress},{"write","verify_output","verify_source"})
        self.assertEqual(progress[-1][:2],(len(expected)*3,len(expected)*3))
        self.assertEqual(set(p.name for p in self.target.iterdir()),{"disc.bin","disc.cue","manifest.json"})

    def test_audio_only_and_mode1_are_supported_without_plain_iso_conversion(self):
        for tracks in [((0,0),(2,10)),((4,0),)]:
            with self.subTest(tracks=tracks):
                device=Device(tracks=tracks)
                target=self.root/str(len(tracks))
                result=raw.capture_raw_cd("E:",target,device_factory=lambda _:device)
                self.assertEqual(result.bytes_written,20*2352)
                self.assertEqual(result.tracks[0].cue_mode,"AUDIO" if len(tracks)==2 else "MODE1/2352")

    def test_plan_only_reads_track_boundaries_and_rechecks_media(self):
        device=Device(tracks=((4,0),(0,12)),leadout=20)
        plan=raw.plan_raw_capture("E:",device_factory=lambda _:device)
        self.assertEqual(plan.bytes_to_capture,20*2352);self.assertEqual(plan.sector_count,20)
        self.assertEqual([(lba,count) for lba,count,mode in device.reads],[(0,1),(11,1),(12,1),(19,1)])
        self.assertEqual(device.snapshots,2);self.assertTrue(device.closed)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_data_audio_pregap_ambiguity_and_interior_mode_change_are_rejected(self):
        device=Device(tracks=((4,0),(0,12)),mode=2)
        device.data[11]=bytes(2352)  # Audio index-00-like bytes before TOC track 2.
        with self.assertRaises(raw.RawAcquisitionError):self.capture(device)
        self.assertFalse(self.target.exists())
        device=Device();device.data[8]=data_sector(8,mode=2)
        with self.assertRaisesRegex(raw.RawAcquisitionError,"changes sector mode"):self.capture(device)
        self.assertFalse(self.target.exists());self.assertEqual(list(self.root.iterdir()),[])

    def test_scrambled_or_wrong_address_data_is_not_guessed_or_repaired(self):
        for sector in [bytes(2352),data_sector(1),data_sector(0,mode=0)]:
            device=Device();device.data[0]=sector
            with self.subTest(sector=sector[:16]),self.assertRaises(raw.RawAcquisitionError):self.capture(device)
        self.assertFalse(self.target.exists())

    def test_uncertain_sessions_profiles_and_track_controls_are_refused(self):
        cases=[]
        device=Device();device.media=replace(device.media,profile=0x10);cases.append(device)
        device=Device();device.media=replace(device.media,profile=9);cases.append(device)
        device=Device();device.media=replace(device.media,session=b"\0\x0a\x01\x02"+device.media.toc[4:12]);cases.append(device)
        device=Device();device.media=replace(device.media,session=b"");cases.append(device)
        device=Device(tracks=((8,0),));cases.append(device)
        device=Device(tracks=((5,0),));cases.append(device)
        device=Device();changed=bytearray(device.media.session);changed[6]=2
        device.media=replace(device.media,session=bytes(changed));cases.append(device)
        for toc in (b"",toc_bytes(((4,0),),20,first=2),toc_bytes(((4,10),),20)):
            device=Device();device.media=replace(device.media,toc=toc,
                session=b"\0\x0a\x01\x01"+toc[4:12] if toc else device.media.session)
            cases.append(device)
        for device in cases:
            with self.subTest(snapshot=device.media),self.assertRaises(raw.RawAcquisitionError):self.capture(device)
            self.assertTrue(device.closed);self.assertFalse(self.target.exists())

    def test_source_changes_short_reads_and_device_failures_do_not_publish(self):
        device=Device();device.mutate=True
        with self.assertRaisesRegex(raw.RawAcquisitionError,"reads disagree"):self.capture(device)
        device=Device();original=device.read_sectors
        device.read_sectors=lambda lba,count,mode:original(lba,count,mode)[:-1] if count>1 else original(lba,count,mode)
        with self.assertRaisesRegex(raw.RawAcquisitionError,"short raw read"):self.capture(device)
        device=Device();original=device.read_sectors
        def failed(lba,count,mode):
            if count>1:raise OSError("device vanished")
            return original(lba,count,mode)
        device.read_sectors=failed
        with self.assertRaisesRegex(OSError,"vanished"):self.capture(device)
        self.assertTrue(device.closed);self.assertFalse(self.target.exists());self.assertEqual(list(self.root.iterdir()),[])

    def test_changed_counter_or_toc_during_capture_is_rejected(self):
        for field in ("media_change_count","toc"):
            device=Device();snapshot=device.snapshot
            def changed():
                value=snapshot()
                if device.snapshots>=3:
                    return replace(value,media_change_count=18) if field=="media_change_count" else replace(value,toc=value.toc[:-1]+b"\x15")
                return value
            device.snapshot=changed
            with self.subTest(field=field),self.assertRaisesRegex(raw.RawAcquisitionError,"changed"):self.capture(device)
            self.assertFalse(self.target.exists());self.assertEqual(list(self.root.iterdir()),[])

    def test_cancellation_and_output_corruption_remove_only_private_stage(self):
        preserved=self.root/"keep.txt";preserved.write_text("original")
        event=threading.Event()
        with self.assertRaises(raw.RawCaptureCancelled):
            self.capture(Device(),cancelled=event,progress=lambda *args:event.set())
        real_fsync=os.fsync;corrupted=[]
        def corrupt_after_flush(descriptor):
            real_fsync(descriptor)
            if not corrupted:
                corrupted.append(True)
                path=next(self.root.glob(".redux-raw-*/disc.bin"))
                with path.open("r+b") as output:output.seek(20);output.write(b"bad data")
        with patch.object(raw.os,"fsync",side_effect=corrupt_after_flush):
            with self.assertRaisesRegex(raw.RawAcquisitionError,"output bytes"):self.capture(Device())
        self.assertEqual(list(self.root.iterdir()),[preserved]);self.assertEqual(preserved.read_text(),"original")

    def test_existing_and_racing_destinations_are_never_overwritten(self):
        self.target.mkdir();(self.target/"keep").write_text("existing")
        factory=lambda _:(_ for _ in ()).throw(AssertionError("No drive access for an existing output"))
        with self.assertRaises(FileExistsError):raw.capture_raw_cd("E:",self.target,device_factory=factory)
        target=self.root/"racing"
        def race(done,total,phase):
            if done==total and phase=="verify_source":target.mkdir()
        with self.assertRaises(FileExistsError):raw.capture_raw_cd("E:",target,device_factory=lambda _:Device(),progress=race)
        self.assertTrue(target.is_dir());self.assertEqual(list(target.iterdir()),[])
        self.assertEqual((self.target/"keep").read_text(),"existing")
        self.assertFalse(list(self.root.glob(".redux-raw-*")))

    def test_full_disc_and_protection_scope_are_rejected_before_device_access(self):
        factory=lambda _:(_ for _ in ()).throw(AssertionError("Must not open device"))
        for option in ("require_full_disc","require_subchannels","require_protection"):
            with self.subTest(option=option),self.assertRaises(raw.RawAcquisitionError):
                raw.capture_raw_cd("E:",self.target,device_factory=factory,**{option:True})

    def test_proved_pregap_preserves_continuous_bytes_toc_index01_and_source_passes(self):
        device=PregapDevice();progress=[]
        result=self.capture(device,progress=lambda *value:progress.append(value))
        self.assertEqual(result.bin_path.read_bytes(),b"".join(device.data))
        self.assertEqual([(track.start_lba,track.end_lba) for track in result.tracks],[(0,37),(37,70)])
        self.assertIsNone(result.tracks[0].index01_lba)
        self.assertEqual(result.tracks[1].index01_lba,50)
        cue=result.cue_path.read_text()
        self.assertIn("INDEX 00 00:00:37",cue)
        self.assertIn("INDEX 01 00:00:50",cue)
        manifest=json.loads(result.manifest_path.read_text())
        self.assertEqual(manifest["read_method"],"fixture_typed_read_cd")
        self.assertEqual(manifest["pregap_evidence"][0]["observed_gap_sectors"],13)
        self.assertEqual(manifest["tracks"][1]["index01_lba"],50)
        self.assertEqual(result.image_kind,"cd_main_channel_bin_cue_toc_index01")
        self.assertEqual(progress[-1],(70*2352*3,70*2352*3,"verify_source"))
        for source_pass in (1,2):
            chunks=[value[1:] for value in device.capture_chunks if value[0]==source_pass]
            self.assertEqual(chunks,[(0,16,5),(16,16,5),(32,5,5),(37,16,2),(53,16,2),(69,1,2)])
            self.assertEqual([lba for start,count,_ in chunks for lba in range(start,start+count)],list(range(70)))

    def test_failed_pregap_evidence_does_not_publish_or_expand_legacy_support(self):
        device=PregapDevice();device.bad_q=True
        with self.assertRaises(raw.RawAcquisitionError):self.capture(device)
        self.assertFalse(self.target.exists());self.assertTrue(device.closed)
        self.assertEqual(list(self.root.iterdir()),[])
        legacy=Device(tracks=((4,0),(0,50)),leadout=70)
        legacy.data[49]=bytes(2352)
        with self.assertRaisesRegex(raw.RawAcquisitionError,"not a supported unscrambled"):
            self.capture(legacy)
        self.assertFalse(self.target.exists())

    def test_pregap_support_does_not_accept_corrupt_active_data_or_short_boundary_reads(self):
        device=PregapDevice();device.data[8]=bytes(2352)
        with self.assertRaisesRegex(raw.RawAcquisitionError,"not a supported unscrambled"):
            self.capture(device)
        self.assertFalse(self.target.exists())
        device=PregapDevice();original=device.read_sectors
        def short_tail(lba,count,mode):
            value=original(lba,count,mode)
            return value[:-1] if lba==49 else value
        device.read_sectors=short_tail
        with patch("redux.cd_pregap.resolve_audio_pregap") as resolver:
            with self.assertRaisesRegex(raw.RawAcquisitionError,"short raw read"):
                self.capture(device)
            resolver.assert_not_called()

    def test_native_default_factory_is_lazy_scsi_for_plan_and_capture(self):
        with patch("redux.scsi_cd.WindowsScsiCdDevice",return_value=Device()) as factory:
            raw.plan_raw_capture("e")
            factory.assert_called_once_with("E:")
        with patch("redux.scsi_cd.WindowsScsiCdDevice",return_value=Device()) as factory:
            result=raw.capture_raw_cd("e",self.target)
            factory.assert_called_once_with("E:")
            self.assertEqual(result.bytes_written,20*2352)

    def test_single_opaque_index00_sector_is_captured_unchanged_with_an_isolated_any_read(self):
        device=PregapDevice(opaque=True)
        expected=b"".join(device.data)
        result=self.capture(device)
        self.assertEqual(result.bin_path.read_bytes(),expected)
        opaque=result.tracks[1].opaque_pregap_sectors
        self.assertEqual(len(opaque),1)
        self.assertEqual(opaque[0]["lba"],37)
        self.assertEqual(opaque[0]["sha256"],hashlib.sha256(device.data[37]).hexdigest())
        self.assertEqual(opaque[0]["encoding"],"unidentified_no_decoding_or_repair")
        for source_pass in (1,2):
            chunks=[value[1:] for value in device.capture_chunks if value[0]==source_pass]
            self.assertEqual(chunks,[(0,16,5),(16,16,5),(32,5,5),(37,1,5),(38,16,2),(54,16,2)])
            self.assertEqual([lba for start,count,_ in chunks for lba in range(start,start+count)],list(range(70)))
        manifest=json.loads(result.manifest_path.read_text())
        self.assertEqual(manifest["tracks"][1]["opaque_pregap_sectors"][0]["lba"],37)
        self.assertEqual(manifest["pregap_evidence"][0]["opaque_transition_sectors"][0]["sha256"],opaque[0]["sha256"])
        self.assertTrue(any("opaque" in value for value in result.warnings))

    def test_changed_opaque_transition_bytes_fail_capture_instead_of_being_repaired(self):
        for corrupt_pass in (1,2):
            device=PregapDevice(opaque=True)
            original=device.read_sectors
            def changed_during_capture(lba,count,mode):
                value=original(lba,count,mode)
                if lba==37 and device.full_passes>=corrupt_pass:
                    value=value[:-1]+bytes((value[-1]^1,))
                return value
            device.read_sectors=changed_during_capture
            with self.subTest(source_pass=corrupt_pass),self.assertRaisesRegex(raw.RawAcquisitionError,"opaque INDEX 00 transition sector changed"):
                self.capture(device)
            self.assertFalse(self.target.exists())
            self.assertEqual(list(self.root.iterdir()),[])

    def test_unobserved_extra_opaque_sector_is_rejected_during_full_capture(self):
        device=PregapDevice(opaque=True)
        device.data[61]=raw._SYNC+b"bad!"+bytes(2336)
        with self.assertRaisesRegex(raw.RawAcquisitionError,"unexpected data synchronization"):
            self.capture(device)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.iterdir()),[])


class WindowsAdapterTests(unittest.TestCase):
    def test_read_only_flags_pointer_width_queries_raw_offset_and_subcode_stride(self):
        calls=[];closed=[]
        device=Device();media=device.media
        def ioctl(handle,code,input_buffer,input_size,output_buffer,output_size,returned,overlapped):
            query=ctypes.string_at(input_buffer,input_size) if input_size else b""
            calls.append((code,query,output_size,output_buffer.value%4096))
            if code==raw.IOCTL_STORAGE_CHECK_VERIFY:data=(17).to_bytes(4,"little")
            elif code==raw.IOCTL_CDROM_GET_CONFIGURATION:
                self.assertEqual(input_size,8+2*ctypes.sizeof(ctypes.c_void_p));data=b"\0\0\0\x04\0\0\0\x08"
            elif code==raw.IOCTL_CDROM_READ_TOC_EX:data=media.session if query[0]==1 else media.toc
            elif code==raw.IOCTL_CDROM_RAW_READ:
                offset,count,mode=struct.unpack("<qII",query)
                self.assertEqual(offset,12*2048);self.assertEqual(count,2)
                stride=2352 if mode==raw.CDDA else 2448
                self.assertEqual(output_size,stride*count)
                data=b"a"*2352+(b"s"*96 if stride==2448 else b"")+b"b"*2352+(b"t"*96 if stride==2448 else b"")
            else:raise AssertionError("Unexpected IOCTL")
            ctypes.memmove(output_buffer,data,len(data));ctypes.cast(returned,ctypes.POINTER(ctypes.c_uint32))[0]=len(data)
            return True
        opened=[]
        def create(*args):opened.append(args);return 72
        api=SimpleNamespace(ctypes=ctypes,wintypes=SimpleNamespace(DWORD=ctypes.c_uint32),drive_type=lambda _:5,
            kernel=SimpleNamespace(CreateFileW=create,DeviceIoControl=ioctl,CloseHandle=lambda handle:closed.append(handle)))
        with raw.WindowsRawCdDevice("E:",api=api) as reader:
            self.assertEqual(reader.snapshot(),media)
            for mode in (raw.RAW_WITH_SUBCODE,raw.CDDA):self.assertEqual(reader.read_sectors(12,2,mode),b"a"*2352+b"b"*2352)
            for arguments in [(-1,1,2),(0,0,2),(0,17,2),(0,1,0)]:
                with self.subTest(arguments=arguments),self.assertRaises(ValueError):reader.read_sectors(*arguments)
        self.assertEqual(opened[0][0],r"\\.\E:");self.assertEqual(opened[0][1:6],(0x80000000,3,None,3,0))
        self.assertEqual(closed,[72]);self.assertTrue(all(alignment==0 for code,query,size,alignment in calls))
        self.assertEqual({code for code,query,size,alignment in calls},{raw.IOCTL_STORAGE_CHECK_VERIFY,raw.IOCTL_CDROM_GET_CONFIGURATION,raw.IOCTL_CDROM_READ_TOC_EX,raw.IOCTL_CDROM_RAW_READ})

    def test_unsupported_raw_driver_never_tries_an_alternate_mode(self):
        calls=[]
        def ioctl(*args):calls.append(args[1]);return False
        api=SimpleNamespace(ctypes=ctypes,wintypes=SimpleNamespace(DWORD=ctypes.c_uint32),drive_type=lambda _:5,
            kernel=SimpleNamespace(CreateFileW=lambda *a:72,DeviceIoControl=ioctl,CloseHandle=lambda _:None),
            _error=lambda *a:OpticalError("unsupported",winerror=50))
        with raw.WindowsRawCdDevice("E:",api=api) as reader:
            with self.assertRaises(raw.RawAcquisitionError) as raised:reader.read_sectors(0,1,raw.RAW_WITH_SUBCODE)
        self.assertEqual(raised.exception.reason,"unsupported_raw_read");self.assertEqual(calls,[raw.IOCTL_CDROM_RAW_READ])


if __name__=="__main__":unittest.main()
