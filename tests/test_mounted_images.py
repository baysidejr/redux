"""Read-only virtual-image metadata, strict bounds, Unicode and product gating."""
import ctypes
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux import mounted_images, optical


def storage_descriptor(product=b"WinCDEmu", *, product_offset=36):
    data = bytearray(36 + len(product) + 1)
    struct.pack_into("<II", data, 0, 36, len(data))
    struct.pack_into("<I", data, 16, product_offset)
    data[36:] = product + b"\0"
    return bytes(data)


def path_payload(text):
    raw = text.encode("utf-16-le") + b"\0\0"
    return struct.pack("<I",len(raw)) + raw


class FakeAPI:
    def __init__(self, source=None, product=b"WinCDEmu", descriptor=None):
        self.source = path_payload(source or "")
        self.descriptor = descriptor or storage_descriptor(product)
        self.calls, self.opened, self.closed = [], [], []
        self.failed_code = None
        self.ctypes = ctypes
        self.wintypes = SimpleNamespace(DWORD=ctypes.c_uint32)
        self.kernel = SimpleNamespace(CreateFileW=self.open,DeviceIoControl=self.ioctl,
                                      CloseHandle=self.closed.append)

    def drive_type(self, root):
        return 5 if root in {"F:\\","G:\\"} else 3

    def open(self,*args):
        self.opened.append(args)
        return 123

    def ioctl(self,handle,code,query,query_length,output,output_size,returned,overlapped):
        self.calls.append(code)
        assert handle == 123 and overlapped is None
        if code == self.failed_code:
            return False
        if code == mounted_images.IOCTL_STORAGE_QUERY_PROPERTY:
            assert query.raw == bytes(12) and query_length == 12
            data = self.descriptor[:output_size]
        elif code == mounted_images.IOCTL_VDEV_GET_IMAGE_PATH:
            assert query is None and query_length == 0
            data = self.source[:output_size]
        else:
            raise AssertionError(f"Unexpected device operation {code:#x}")
        ctypes.memmove(output,data,len(data))
        returned._obj.value = len(data)
        return True


class MountedImageTests(unittest.TestCase):
    def test_non_optical_or_bad_drive_never_opens_a_device(self):
        api = FakeAPI()
        with patch.object(optical,"_get_api",return_value=api):
            self.assertIsNone(mounted_images.mounted_image_source("C:"))
            for name in ["F:filename",r"\\.\PhysicalDrive0","../F","F::"]:
                with self.subTest(name=name),self.assertRaises(ValueError):
                    mounted_images.mounted_image_source(name)
        self.assertEqual(api.opened,[])

    def test_other_product_never_receives_private_ioctl(self):
        api = FakeAPI(product=b"Physical CD drive")
        with patch.object(optical,"_get_api",return_value=api):
            self.assertIsNone(mounted_images.mounted_image_source("F:"))
        self.assertNotIn(mounted_images.IOCTL_VDEV_GET_IMAGE_PATH,api.calls)
        self.assertEqual(api.closed,[123])

    def test_product_matching_is_exact_case_insensitive(self):
        for product in [b"WinCDEmu malicious suffix",b" WinCDEmu",b"WinCDEmu "]:
            api=FakeAPI(product=product)
            with self.subTest(product=product),patch.object(optical,"_get_api",return_value=api):
                self.assertIsNone(mounted_images.mounted_image_source("F:"))
            self.assertNotIn(mounted_images.IOCTL_VDEV_GET_IMAGE_PATH,api.calls)

    @unittest.skipUnless(os.name=="nt","Windows path fixture")
    def test_unicode_spaces_nt_prefix_and_read_only_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"日本語 Disc (USA).cue"
            source.write_bytes(b'FILE "track.bin" BINARY\n')
            api=FakeAPI("\\??\\"+str(source),product=b"wInCdEmU")
            with patch.object(optical,"_get_api",return_value=api):
                self.assertEqual(mounted_images.mounted_image_source("f:\\"),source.resolve())
            self.assertEqual(api.opened[0],(r"\\.\F:",0x80000000,3,None,3,0,None))
            self.assertEqual(api.closed,[123])
            self.assertEqual(api.calls,[mounted_images.IOCTL_STORAGE_QUERY_PROPERTY]*2+
                             [mounted_images.IOCTL_VDEV_GET_IMAGE_PATH]*2)

    @unittest.skipUnless(os.name=="nt","Windows path fixture")
    def test_missing_path_directory_and_unsupported_extension_return_none(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            unsupported=directory/"note.txt";unsupported.write_text("not a disc")
            for path in [directory/"missing.iso",directory,unsupported]:
                api=FakeAPI(str(path))
                with self.subTest(path=path),patch.object(optical,"_get_api",return_value=api):
                    self.assertIsNone(mounted_images.mounted_image_source("F:"))
                self.assertEqual(api.closed,[123])

    def test_unknown_or_unavailable_metadata_returns_none_and_closes_handle(self):
        for code in [mounted_images.IOCTL_STORAGE_QUERY_PROPERTY,mounted_images.IOCTL_VDEV_GET_IMAGE_PATH]:
            api=FakeAPI();api.failed_code=code
            with self.subTest(code=code),patch.object(optical,"_get_api",return_value=api):
                self.assertIsNone(mounted_images.mounted_image_source("F:"))
            self.assertEqual(api.closed,[123])

    def test_empty_source_is_supported_absence(self):
        api=FakeAPI("")
        with patch.object(optical,"_get_api",return_value=api):
            self.assertIsNone(mounted_images.mounted_image_source("G:"))
        self.assertEqual(api.opened[0][0],r"\\.\G:")

    def test_malformed_descriptor_bounds_or_string_are_explicit(self):
        cases=[]
        bad=bytearray(storage_descriptor());struct.pack_into("<I",bad,16,4096);cases.append(bytes(bad))
        bad=bytearray(storage_descriptor());bad[-1]=ord("X");cases.append(bytes(bad))
        bad=bytearray(storage_descriptor());struct.pack_into("<I",bad,4,1000000);cases.append(bytes(bad))
        cases.append(storage_descriptor(b"\xff"))
        for data in cases:
            api=FakeAPI(descriptor=data)
            with self.subTest(data=data),patch.object(optical,"_get_api",return_value=api):
                with self.assertRaises(optical.OpticalError):mounted_images.mounted_image_source("F:")
            self.assertNotIn(mounted_images.IOCTL_VDEV_GET_IMAGE_PATH,api.calls)
            self.assertEqual(api.closed,[123])

    def test_malformed_or_ambiguous_source_payload_is_not_guessed(self):
        valid=path_payload(r"C:\Games\Disc.iso")
        cases=[valid[:-2],struct.pack("<I",3)+b"abc",struct.pack("<I",0),
               path_payload("C:\\bad\0suffix.iso"),path_payload(r"C:relative.iso"),
               path_payload(r"\\server\share\disc.iso"),path_payload(r"\\.\PhysicalDrive0"),
               path_payload(r"F:\self.iso"),path_payload(r"C:\Disc.iso:stream")]
        for data in cases:
            with self.subTest(data=data),self.assertRaises(optical.OpticalError):
                mounted_images._source_path(data,"F:")

    def test_unsupported_platform_does_not_try_helpers_or_open_devices(self):
        error=optical.OpticalError("Unsupported",reason="unsupported_platform")
        with patch.object(optical,"_get_api",side_effect=error):
            self.assertIsNone(mounted_images.mounted_image_source("F:"))


if __name__=="__main__":unittest.main()
