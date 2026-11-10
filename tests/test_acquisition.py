from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import ctypes

from redux import acquisition
from redux.optical import DiscInfo,DiscTrack,OpticalDrive,parse_iso9660_pvd
try:
    from tests.test_media_identity import iso_fixture
except ModuleNotFoundError:  # unittest discovery can add tests/ directly to sys.path.
    from test_media_identity import iso_fixture


class Reader:
    def __init__(self,data):self.data=data;self.closed=False;self.full_reads=0;self.mutate=False
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def read(self,offset,count):
        if offset==0:
            self.full_reads+=1
            if self.mutate and self.full_reads==2:
                return bytes([self.data[0]^1])+self.data[1:count]
        return self.data[offset:offset+count]


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.drive="Y:" if self.root.drive.upper()=="Z:" else "Z:"
        self.data=iso_fixture(system=b"GENERIC")
        volume=parse_iso9660_pvd(self.data[16*2048:17*2048],device_bytes=len(self.data))
        self.plan=acquisition.AcquisitionPlan(self.drive,"logical_volume",None,volume,("Logical volume only",))

    def tearDown(self):self.temp.cleanup()

    def test_source_image_is_preferred_before_reading_or_exporting_a_volume(self):
        source=self.root/"Game.cue";source.write_text("original descriptor")
        with patch.object(acquisition,"mounted_image_source",return_value=source),patch.object(acquisition,"inspect_disc") as inspect:
            plan=acquisition.acquisition_plan(self.drive)
            self.assertTrue(plan.can_import_source)
            inspect.assert_not_called()
        with patch.object(acquisition,"acquisition_plan",return_value=plan),patch.object(acquisition,"_DeviceReader") as reader:
            with self.assertRaises(acquisition.SourceImageAvailable) as raised:
                acquisition.capture_logical_volume(self.drive,self.root/"copy.iso")
            self.assertEqual(raised.exception.source_image,source)
            reader.assert_not_called()
        self.assertFalse((self.root/"copy.iso").exists())

    def test_mixed_mode_plan_refuses_a_cooked_full_disc_route(self):
        info=DiscInfo(OpticalDrive(self.drive,"D:\\","\\\\.\\D:"),1,2,
            (DiscTrack(1,4,1,0,100),DiscTrack(2,0,1,100,200)),200,True,True,"mixed_mode",())
        with patch.object(acquisition,"mounted_image_source",return_value=None),patch.object(acquisition,"inspect_disc",return_value=info):
            plan=acquisition.acquisition_plan(self.drive)
            self.assertEqual(plan.mode,"unsupported")
            with self.assertRaises(acquisition.AcquisitionError):
                acquisition.capture_full_disc(self.drive,self.root/"bad.iso")

    def test_export_is_read_back_and_source_reread_before_publication(self):
        target=self.root/"data.iso";reader=Reader(self.data);progress=[]
        with patch.object(acquisition,"acquisition_plan",return_value=self.plan),patch.object(acquisition,"_DeviceReader",return_value=reader):
            result=acquisition.capture_logical_volume(self.drive,target,progress=lambda *args:progress.append(args))
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual(result.sha256,result.source_sha256)
        self.assertEqual(result.verified_source_passes,2)
        self.assertTrue(result.verified_output)
        self.assertTrue(reader.closed)
        self.assertEqual({p[2] for p in progress},{"write","verify_output","verify_source"})
        self.assertEqual(progress[-1][:2],(len(self.data)*3,len(self.data)*3))

    def test_cancellation_removes_only_our_partial_output(self):
        event=threading.Event();target=self.root/"cancel.iso";reader=Reader(self.data)
        def progress(done,total,phase):
            if phase=="write":event.set()
        with patch.object(acquisition,"acquisition_plan",return_value=self.plan),patch.object(acquisition,"_DeviceReader",return_value=reader):
            with self.assertRaises(acquisition.CaptureCancelled):
                acquisition.capture_logical_volume(self.drive,target,cancelled=event,progress=progress)
        self.assertEqual(list(self.root.iterdir()),[])
        self.assertTrue(reader.closed)
        self.assertEqual(reader.data,self.data)

    def test_disagreeing_source_reads_are_not_published(self):
        target=self.root/"unstable.iso";reader=Reader(self.data);reader.mutate=True
        with patch.object(acquisition,"acquisition_plan",return_value=self.plan),patch.object(acquisition,"_DeviceReader",return_value=reader):
            with self.assertRaises(acquisition.AcquisitionError) as raised:
                acquisition.capture_logical_volume(self.drive,target)
        self.assertEqual(raised.exception.reason,"media_changed")
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob("*.partial")))

    def test_existing_and_racing_destination_are_never_overwritten(self):
        target=self.root/"existing.iso";target.write_bytes(b"keep")
        with patch.object(acquisition,"acquisition_plan",return_value=self.plan):
            with self.assertRaises(FileExistsError):acquisition.capture_logical_volume(self.drive,target)
        target.unlink()
        def race(done,total,phase):
            if phase=="verify_source" and done==total:target.write_bytes(b"another writer")
        with patch.object(acquisition,"acquisition_plan",return_value=self.plan),patch.object(acquisition,"_DeviceReader",return_value=Reader(self.data)):
            with self.assertRaises(FileExistsError):
                acquisition.capture_logical_volume(self.drive,target,progress=race)
        self.assertEqual(target.read_bytes(),b"another writer")
        self.assertEqual(list(self.root.iterdir()),[target])

    def test_bad_or_changed_descriptor_aborts_without_publishing(self):
        changed=bytearray(self.data);changed[16*2048+40:16*2048+44]=b"SWAP"
        target=self.root/"changed.iso"
        with patch.object(acquisition,"acquisition_plan",return_value=self.plan),patch.object(acquisition,"_DeviceReader",return_value=Reader(bytes(changed))):
            with self.assertRaises(acquisition.AcquisitionError):acquisition.capture_logical_volume(self.drive,target)
        self.assertFalse(target.exists())

    def test_short_reads_fail_instead_of_padding(self):
        class Short(Reader):
            def read(self,offset,count):
                data=super().read(offset,count)
                return data[:-1] if offset==0 else data
        target=self.root/"short.iso"
        with patch.object(acquisition,"acquisition_plan",return_value=self.plan),patch.object(acquisition,"_DeviceReader",return_value=Short(self.data)):
            with self.assertRaises(acquisition.AcquisitionError):acquisition.capture_logical_volume(self.drive,target)
        self.assertFalse(target.exists())

    def test_windows_boundary_opens_read_only_aligns_buffer_and_closes_handle(self):
        calls=[];closed=[]
        def create(*args):calls.append(args);return 42
        def read(handle,buffer,count,returned,overlapped):
            self.assertEqual(buffer.value%2048,0)
            self.assertEqual((handle,count,overlapped),(42,2048,None))
            ctypes.memmove(buffer,b"x"*count,count)
            ctypes.cast(returned,ctypes.POINTER(ctypes.c_uint32)).contents.value=count
            return True
        api=SimpleNamespace(ctypes=ctypes,wintypes=SimpleNamespace(DWORD=ctypes.c_uint32),
            drive_type=lambda root:5,kernel=SimpleNamespace(CreateFileW=create,ReadFile=read,
                SetFilePointerEx=lambda *args:True,CloseHandle=closed.append))
        with patch.object(acquisition,"_get_api",return_value=api):
            with acquisition._DeviceReader(self.drive) as reader:
                self.assertEqual(reader.read(0,2048),b"x"*2048)
                with self.assertRaises(ValueError):reader.read(1,2048)
        self.assertEqual(calls[0][1],0x80000000)
        self.assertEqual(calls[0][4],3)
        self.assertEqual(closed,[42])


if __name__=="__main__":unittest.main()
