"""Native ABI and failure-path tests using an injected driver, never hardware."""
import ctypes
import struct
from types import SimpleNamespace
import unittest

from redux import raw_acquisition as raw
from redux import scsi_cd as scsi
from redux.optical import OpticalError


class Driver:
    def __init__(self):
        self.calls = []
        self.opens = []
        self.closed = []
        self.mask = 8191
        self.maximum = 65536
        self.query = None
        self.failed = False
        self.short = False
        self.status = 0
        self.sense = b""
        self.returned = None
        self.sense_length = None
        self.payload = b""
        self.data_mode_override = None
        self.wrong_address = False
        self.ignore_expected_type = False
        self.api = SimpleNamespace(ctypes=ctypes,
            wintypes=SimpleNamespace(DWORD=ctypes.c_uint32), drive_type=lambda _:5,
            kernel=SimpleNamespace(CreateFileW=self.open, CloseHandle=self.closed.append,
                                   DeviceIoControl=self.ioctl),
            _error=lambda *args:OpticalError("I/O device error",winerror=1117,reason="io_error"))

    def open(self,*args):
        self.opens.append(args)
        return 72

    def ioctl(self, handle, code, query, query_size, output, output_size, returned, overlapped):
        self.calls.append(code)
        if code == scsi.IOCTL_STORAGE_QUERY_PROPERTY:
            assert ctypes.string_at(query,query_size) == struct.pack("<II",1,0)+bytes(4)
            data = (self.query if self.query is not None else
                    struct.pack("<IIIII",32,32,self.maximum,16,self.mask)+bytes(12))
            ctypes.memmove(output,data,len(data))
            ctypes.cast(returned,ctypes.POINTER(ctypes.c_uint32))[0] = len(data)
            return True
        if code != scsi.IOCTL_SCSI_PASS_THROUGH_DIRECT:
            raise AssertionError("Unexpected IOCTL")
        packet = ctypes.cast(query,ctypes.POINTER(scsi._Packet)).contents
        request = packet.sptd
        assert query_size == output_size == ctypes.sizeof(packet)
        assert request.Length == ctypes.sizeof(scsi.SCSI_PASS_THROUGH_DIRECT)
        assert request.CdbLength == 12
        assert request.DataIn == scsi.SCSI_IOCTL_DATA_IN
        assert request.TimeOutValue == 10
        assert request.DataBuffer & self.mask == 0
        assert request.SenseInfoOffset == scsi._Packet.sense.offset
        assert request.SenseInfoLength == 64
        self.cdb = bytes(request.Cdb)
        count = int.from_bytes(self.cdb[6:9],"big")
        self.payload = b"".join(bytes((n,))*2352 for n in range(count))
        if self.cdb[1] in (8,12) and not self.ignore_expected_type:
            lba=int.from_bytes(self.cdb[2:6],"big")
            mode=self.data_mode_override or self.cdb[1]//4-1
            self.payload=b"".join(raw._SYNC+raw._expected_header(lba+n+int(self.wrong_address))+
                bytes((mode,))+bytes(2352-16) for n in range(count))
        if self.cdb[10] == 2:
            self.payload += bytes.fromhex("01020000000100002837000000000000")
        ctypes.memmove(request.DataBuffer,self.payload,len(self.payload))
        request.DataTransferLength -= int(self.short)
        request.ScsiStatus = self.status
        packet.sense[:len(self.sense)] = self.sense
        request.SenseInfoLength = len(self.sense) if self.sense_length is None else self.sense_length
        ctypes.cast(returned,ctypes.POINTER(ctypes.c_uint32))[0] = (
            ctypes.sizeof(packet) if self.returned is None else self.returned)
        return not self.failed


class ScsiCdTests(unittest.TestCase):
    def test_native_abi_uses_fixed_dwords_and_native_pointer_alignment(self):
        structure = scsi.SCSI_PASS_THROUGH_DIRECT
        pointer_bytes = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(structure.DataTransferLength.offset,12)
        self.assertEqual(structure.TimeOutValue.offset,16)
        self.assertEqual(structure.DataBuffer.offset,24 if pointer_bytes==8 else 20)
        self.assertEqual(structure.SenseInfoOffset.offset,32 if pointer_bytes==8 else 24)
        self.assertEqual(structure.Cdb.offset,36 if pointer_bytes==8 else 28)
        self.assertEqual(ctypes.sizeof(structure),56 if pointer_bytes==8 else 44)
        self.assertEqual(scsi._Packet.sense.offset,ctypes.sizeof(structure))

    def test_main_channel_is_unchanged_and_commands_are_read_only(self):
        driver=Driver()
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            for mode,byte1,byte9 in ((raw.RAW_WITH_SUBCODE,0,0xF8),(raw.CDDA,4,0x10)):
                data=device.read_sectors(0x1234,2,mode)
                self.assertEqual(data,driver.payload)
                self.assertEqual(driver.cdb[:12],bytes((0xBE,byte1,0,0,0x12,0x34,0,0,2,byte9,0,0)))
                self.assertEqual(driver.cdb[12:],bytes(4))
                self.assertEqual(device.last_diagnostic["lba"],0x1234)
                self.assertEqual(device.last_diagnostic["transferred"],4704)
        self.assertEqual(driver.opens[0][:6],(r"\\.\E:",0xC0000000,3,None,3,0))
        self.assertEqual(driver.closed,[72])
        self.assertEqual(driver.calls,[scsi.IOCTL_STORAGE_QUERY_PROPERTY,
            scsi.IOCTL_SCSI_PASS_THROUGH_DIRECT,scsi.IOCTL_SCSI_PASS_THROUGH_DIRECT])

    def test_invalid_requests_never_reach_native_driver(self):
        driver=Driver()
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            cases=[(-1,1,2),(True,1,2),(0,False,2),(0,0,2),(0,17,2),
                   (raw._MAX_SECTORS-1,2,2),(0,1,True),(0,1,2.0),(0,1,0)]
            for arguments in cases:
                with self.subTest(arguments=arguments),self.assertRaises(ValueError):
                    device.read_sectors(*arguments)
        self.assertEqual(driver.calls,[scsi.IOCTL_STORAGE_QUERY_PROPERTY])

    def test_optional_q_diagnostic_requests_exactly_one_main_plus_q_sector(self):
        driver=Driver()
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            q=device.read_subq(127008,mode=raw.CDDA)
            self.assertEqual(q,driver.payload[2352:])
            self.assertEqual(len(q),16)
            self.assertEqual(driver.cdb[0],0xBE);self.assertEqual(driver.cdb[1],4)
            self.assertEqual(driver.cdb[9:12],b"\x10\x02\0")
            self.assertEqual(device.last_diagnostic["transferred"],2368)
            self.assertEqual(device.last_diagnostic["subchannel"],"formatted_q_16")
            with self.assertRaises(ValueError):scsi._read_cd_cdb(0,2,raw.CDDA,subq=True)
        self.assertEqual(driver.closed,[72])

    def test_typed_data_and_audio_requests_use_their_expected_sector_types(self):
        driver=Driver()
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            for mode,read_type in ((1,8),(2,12)):
                data=device.read_expected_data(1234,mode,count=2)
                self.assertEqual(data,driver.payload)
                self.assertEqual(driver.cdb[1],read_type)
                self.assertEqual(driver.cdb[9:12],b"\xf8\0\0")
                self.assertEqual(device.last_diagnostic["expected_data_mode"],mode)
            self.assertEqual(device.read_cdda(127009),driver.payload)
            self.assertEqual(driver.cdb[1],4);self.assertEqual(driver.cdb[9],0x10)

    def test_typed_data_rejects_wrong_headers_without_claiming_audio(self):
        for change,reason in (({"data_mode_override":2},"unexpected_data_mode"),
                              ({"wrong_address":True},"unsupported_raw_sector")):
            driver=Driver()
            for field,value in change.items():setattr(driver,field,value)
            with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
                with self.subTest(change=change),self.assertRaises(raw.RawAcquisitionError) as raised:
                    device.read_expected_data(127008,mode=1)
                self.assertEqual(raised.exception.reason,reason)
                self.assertEqual(getattr(raised.exception,"typed_read_succeeded",False),
                                 reason=="unsupported_raw_sector")
        driver=Driver()
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            for mode in (None,True,1.0,0,3):
                with self.subTest(mode=mode),self.assertRaises(ValueError):device.read_expected_data(0,mode)
        self.assertEqual(driver.calls,[scsi.IOCTL_STORAGE_QUERY_PROPERTY])

    def test_ignored_expected_type_preserves_full_read_evidence_but_still_raises(self):
        driver=Driver();driver.ignore_expected_type=True
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            with self.assertRaises(raw.RawAcquisitionError) as raised:
                device.read_expected_data(126859)
            error=raised.exception
            self.assertEqual(error.reason,"unsupported_raw_sector")
            self.assertTrue(error.typed_read_succeeded)
            self.assertEqual(error.raw_sector,bytes(2352))
            self.assertEqual((error.requested_lba,error.requested_mode),(126859,1))
            self.assertEqual(device.read_cdda(126859),error.raw_sector)
        for change in ({"short":True},{"failed":True},{"status":2},
                       {"data_mode_override":2}):
            driver=Driver()
            for field,value in change.items():setattr(driver,field,value)
            with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
                with self.subTest(change=change),self.assertRaises(raw.RawAcquisitionError) as raised:
                    device.read_expected_data(126859)
                self.assertFalse(hasattr(raised.exception,"typed_read_succeeded"))
                self.assertFalse(hasattr(raised.exception,"raw_sector"))

    def test_short_transfer_and_invalid_output_do_not_return_zero_padded_data(self):
        for field,value,reason in (("short",True,"short_read"),("returned",0,"invalid_driver_output"),
                                   ("returned",4096,"invalid_driver_output"),
                                   ("sense_length",65,"invalid_driver_output")):
            driver=Driver();setattr(driver,field,value)
            with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
                with self.subTest(field=field,value=value),self.assertRaises(scsi.ScsiReadError) as raised:
                    device.read_sectors(16,1,raw.RAW_WITH_SUBCODE)
                self.assertEqual(raised.exception.reason,reason)
                self.assertEqual(raised.exception.diagnostic["lba"],16)

    def test_scsi_check_condition_is_failure_even_if_ioctl_succeeds(self):
        driver=Driver();driver.status=2
        driver.sense=bytes((0x70,0,5,0,0,0,0,10,0,0,0,0,0x24,0))+bytes(4)
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            with self.assertRaises(scsi.ScsiReadError) as raised:device.read_sectors(0,1,raw.RAW_WITH_SUBCODE)
        self.assertEqual(raised.exception.reason,"invalid_read_cd_field")
        self.assertEqual(raised.exception.diagnostic["sense_key"],5)
        self.assertEqual(raised.exception.diagnostic["asc"],0x24)

    def test_windows_1117_is_preserved_and_not_called_unsupported(self):
        driver=Driver();driver.failed=True
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            with self.assertRaises(scsi.ScsiReadError) as raised:device.read_sectors(0,1,raw.RAW_WITH_SUBCODE)
        self.assertEqual(raised.exception.winerror,1117)
        self.assertEqual(raised.exception.reason,"io_error")
        self.assertEqual(raised.exception.diagnostic["winerror"],1117)

    def test_reported_sense_must_fit_inside_returned_structure_bytes(self):
        driver=Driver();driver.status=2;driver.sense=bytes((0x72,5,0x24,0,0,0,0,0))
        driver.returned=ctypes.sizeof(scsi.SCSI_PASS_THROUGH_DIRECT)
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            with self.assertRaises(scsi.ScsiReadError) as raised:device.read_sectors(0,1,raw.CDDA)
        self.assertEqual(raised.exception.reason,"invalid_driver_output")

    def test_descriptor_sense_classifies_media_and_hardware_failures(self):
        for key,asc,reason in ((2,0x3A,"no_media"),(6,0x28,"media_changed"),
                              (3,0x11,"medium_read_error"),(4,0x44,"hardware_read_error"),
                              (5,0x64,"sector_type_mismatch"),(5,0x20,"unsupported_read_cd")):
            driver=Driver();driver.failed=True;driver.status=2;driver.sense=bytes((0x72,key,asc,0,0,0,0,0))
            with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
                with self.subTest(key=key,asc=asc),self.assertRaises(scsi.ScsiReadError) as raised:
                    device.read_sectors(0,1,raw.RAW_WITH_SUBCODE)
                self.assertEqual(raised.exception.reason,reason)

    def test_unknown_or_invalid_adapter_limits_close_handle_without_read(self):
        for change in ({"query":b""},{"mask":6},{"mask":0x1FFFF},{"maximum":100}):
            driver=Driver()
            for field,value in change.items():setattr(driver,field,value)
            with self.subTest(change=change),self.assertRaises(raw.RawAcquisitionError):
                with scsi.WindowsScsiCdDevice("E:",api=driver.api):pass
            self.assertEqual(driver.closed,[72]);self.assertEqual(driver.calls,[scsi.IOCTL_STORAGE_QUERY_PROPERTY])

    def test_adapter_transfer_limit_is_checked_before_read(self):
        driver=Driver();driver.maximum=2352
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:
            with self.assertRaisesRegex(raw.RawAcquisitionError,"transfer limit"):
                device.read_sectors(0,2,raw.CDDA)
        self.assertEqual(driver.calls,[scsi.IOCTL_STORAGE_QUERY_PROPERTY])

    def test_inherited_snapshot_and_close_api_are_preserved(self):
        self.assertIs(scsi.WindowsScsiCdDevice.snapshot,raw.WindowsRawCdDevice.snapshot)
        driver=Driver()
        with scsi.WindowsScsiCdDevice("E:",api=driver.api) as device:pass
        device.__exit__()
        with self.assertRaises(raw.RawAcquisitionError) as raised:device.read_sectors(0,1,raw.CDDA)
        self.assertEqual(raised.exception.reason,"closed_device");self.assertEqual(driver.closed,[72])


if __name__=="__main__":unittest.main()
