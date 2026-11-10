"""Optical TOC boundary cases and read-only Windows adapter behavior."""

import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux import optical


def toc_bytes(tracks, lead_out, *, first=1, msf=False):
    """Build Windows format-0 wire bytes, including the lead-out descriptor."""
    def record(number, control, address):
        position = bytes(address) if msf else address.to_bytes(4, "big")
        return bytes([0, 0x10 | control, number, 0]) + position
    payload = bytes([first, first + len(tracks) - 1])
    payload += b"".join(record(first + index, control, address)
                        for index, (control, address) in enumerate(tracks))
    payload += record(0xAA, tracks[-1][0], lead_out)
    return len(payload).to_bytes(2, "big") + payload


class TocTests(unittest.TestCase):
    def test_data_cd_uses_big_endian_lba_and_excludes_lead_out_from_tracks(self):
        disc = optical.parse_toc(toc_bytes([(4, 0)], 180_000) + bytes(200))
        self.assertEqual(disc.lead_out_lba, 180_000)
        self.assertEqual(len(disc.tracks), 1)
        track = disc.tracks[0]
        self.assertEqual((track.number, track.start_lba, track.end_lba), (1, 0, 180_000))
        self.assertEqual(track.sector_count, 180_000)
        self.assertTrue(track.is_data)
        self.assertFalse(track.is_audio)

    def test_mixed_mode_disc_preserves_each_track_type_and_extent(self):
        disc = optical.parse_toc(toc_bytes([(4, 0), (0, 16_000), (2, 22_500)], 40_000))
        self.assertEqual([t.kind for t in disc.tracks], ["data", "audio", "audio"])
        self.assertEqual([t.sector_count for t in disc.tracks], [16_000, 6500, 17_500])
        self.assertEqual(disc.tracks[2].control, 2)  # Audio copy flag is not data.

    def test_binary_msf_conversion_includes_150_frame_offset(self):
        disc = optical.parse_toc(
            toc_bytes([(0, [0, 0, 2, 0]), (0, [0, 3, 12, 14])],
                      [0, 7, 0, 0], msf=True), msf=True)
        self.assertEqual(disc.tracks[0].start_lba, 0)
        self.assertEqual(disc.tracks[1].start_lba, (3 * 60 + 12) * 75 + 14 - 150)
        self.assertEqual(disc.lead_out_lba, 31_350)

    def test_non_one_first_track_is_preserved(self):
        disc = optical.parse_toc(toc_bytes([(4, 15_000), (0, 25_000)], 30_000, first=3))
        self.assertEqual((disc.first_track, disc.last_track), (3, 4))
        self.assertEqual([t.number for t in disc.tracks], [3, 4])

    def test_truncated_header_and_declared_payload_are_rejected(self):
        valid = toc_bytes([(4, 0)], 1000)
        for data in [b"", b"\0\x12\x01", valid[:-1], b"\0\0\x01\x01", b"\xff\xff" + valid[2:]]:
            with self.subTest(data=data), self.assertRaises(optical.TocParseError):
                optical.parse_toc(data)

    def test_missing_leadout_duplicate_tracks_and_reverse_addresses_are_rejected(self):
        valid = bytearray(toc_bytes([(4, 0), (0, 1000)], 2000))
        cases = []
        missing = valid[:-8]
        missing[:2] = (len(missing) - 2).to_bytes(2, "big")
        cases.append(missing)
        duplicate = valid.copy()
        duplicate[14] = 1  # Second descriptor repeats track 1.
        cases.append(duplicate)
        no_leadout = valid.copy()
        no_leadout[22] = 3
        cases.append(no_leadout)
        reverse = valid.copy()
        reverse[24:28] = (900).to_bytes(4, "big")
        cases.append(reverse)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(optical.TocParseError):
                optical.parse_toc(data)

    def test_unknown_adr_and_invalid_msf_are_not_silently_interpreted(self):
        bad_adr = bytearray(toc_bytes([(0, 0)], 1000))
        bad_adr[5] = 0x20
        with self.assertRaises(optical.TocParseError):
            optical.parse_toc(bad_adr)
        for position in [[0, 0, 60, 0], [0, 1, 0, 75], [1, 1, 0, 0], [0, 0, 1, 0]]:
            with self.subTest(position=position), self.assertRaises(optical.TocParseError):
                optical.parse_toc(toc_bytes([(0, position)], [0, 5, 0, 0], msf=True), msf=True)


class FakeAPI:
    def __init__(self):
        self.reads = []

    def logical_drive_mask(self):
        return sum(1 << (ord(letter) - 65) for letter in "CDEF")

    def drive_type(self, root):
        return {"C:\\": 3, "D:\\": 5, "E:\\": 5, "F:\\": 4}.get(root, 1)

    def read_toc(self, drive):
        self.reads.append(drive.letter)
        if drive.letter == "E:":
            raise optical.NoMediaError("No disc is present.", reason="no_media", winerror=1112)
        return toc_bytes([(4, 0), (0, 10_000)], 20_000)


class AdapterTests(unittest.TestCase):
    def test_discovery_lists_optical_letters_without_inspecting_or_spinning_media(self):
        api = FakeAPI()
        with patch.object(optical, "_get_api", return_value=api):
            drives = optical.list_drives()
        self.assertEqual([d.letter for d in drives], ["D:", "E:"])
        self.assertEqual(drives[0].device_path, "\\\\.\\D:")
        self.assertEqual(api.reads, [])

    def test_inspect_preserves_mixed_mode_and_clearly_limits_format_claims(self):
        with patch.object(optical, "_get_api", return_value=FakeAPI()):
            disc = optical.inspect_disc("d:\\")
        self.assertTrue(disc.has_data)
        self.assertTrue(disc.has_audio)
        self.assertEqual(disc.disc_type, "mixed_mode")
        self.assertIn("console and sector format are unknown", disc.notes[0])

    def test_fixed_drive_and_arbitrary_device_paths_are_never_opened(self):
        api = FakeAPI()
        with patch.object(optical, "_get_api", return_value=api):
            with self.assertRaises(optical.OpticalError) as raised:
                optical.inspect_disc("C:")
            self.assertEqual(raised.exception.reason, "not_optical")
            for path in ["\\\\.\\PhysicalDrive0", "C:\\games", "D:foo", "../D", "1:", "D::", ""]:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    optical.inspect_disc(path)
        self.assertEqual(api.reads, [])

    def test_no_media_error_stays_distinct_from_malformed_toc(self):
        with patch.object(optical, "_get_api", return_value=FakeAPI()):
            with self.assertRaises(optical.NoMediaError) as raised:
                optical.inspect_disc("e")
        self.assertEqual(raised.exception.winerror, 1112)

    def test_device_read_is_read_only_and_handle_is_closed_after_failure(self):
        calls, closed = [], []
        api = optical._WindowsAPI.__new__(optical._WindowsAPI)
        api.ctypes = SimpleNamespace(c_void_p=ctypes.c_void_p,
            create_string_buffer=ctypes.create_string_buffer, byref=ctypes.byref,
            get_last_error=lambda: 21, FormatError=lambda code: "Device not ready")
        api.wintypes = SimpleNamespace(DWORD=ctypes.c_uint32)
        def open_device(*args):
            calls.append(args)
            return 123
        def ioctl(handle, code, query, query_size, output, output_size, returned, overlapped):
            self.assertEqual(code, 0x24054)
            self.assertEqual(query.raw, bytes(4))
            self.assertIsNone(overlapped)
            return False
        api.kernel = SimpleNamespace(CreateFileW=open_device, DeviceIoControl=ioctl,
                                      CloseHandle=closed.append)
        with self.assertRaises(optical.NoMediaError):
            api.read_toc(optical.OpticalDrive("D:", "D:\\", "\\\\.\\D:"))
        self.assertEqual(calls[0][1], 0x80000000)  # GENERIC_READ only.
        self.assertEqual(calls[0][4], 3)  # OPEN_EXISTING.
        self.assertEqual(closed, [123])


if __name__ == "__main__":
    unittest.main()
