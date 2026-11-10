"""Bounded drive identification without imaging, BIOS, or device mutations."""
from pathlib import Path
import tempfile
import unittest

from redux.disc_identity import (
    _BoundedSectors, identify_drive, parse_saturn_header, parse_sega_cd_header,
)
from redux.media_identity import MediaFormatError
from redux.optical import NoMediaError, OpticalError


def sega_cd_header(title=b"ETERNAL CHAMPIONS CD", serial=b"GM MK-4427 -00", region=b"U"):
    data=bytearray(b" "*2048)
    data[:16]=b"SEGADISCSYSTEM  "
    data[0x120:0x150]=title.ljust(48,b" ")
    data[0x150:0x180]=title.ljust(48,b" ")
    data[0x180:0x18e]=serial.ljust(14,b" ")
    data[0x1f0:0x200]=region.ljust(16,b" ")
    return bytes(data)


class SegaCDHeaderTests(unittest.TestCase):
    def test_observed_disc_serial_title_and_region_resolve_canonical_name(self):
        result=parse_sega_cd_header(sega_cd_header())
        self.assertEqual((result.title,result.game_id,result.details["region"]),
            ("Eternal Champions - Challenge from the Dark Side","MK-4427","U"))
        self.assertEqual(result.details["header_title"],"ETERNAL CHAMPIONS CD")

    def test_alias_requires_title_serial_and_region_together(self):
        for data in (sega_cd_header(title=b"OTHER GAME"),sega_cd_header(serial=b"GM MK-9999 -00"),
                     sega_cd_header(region=b"J")):
            self.assertNotEqual(parse_sega_cd_header(data).title,"Eternal Champions - Challenge from the Dark Side")

    def test_missing_title_is_explicit_and_bad_marker_or_truncation_not_accepted(self):
        self.assertEqual(parse_sega_cd_header(sega_cd_header(title=b"")).title,"Sega CD disc")
        self.assertIsNone(parse_sega_cd_header(b"not a disc"))
        with self.assertRaises(MediaFormatError):parse_sega_cd_header(b"SEGADISCSYSTEM")

    def test_captured_image_and_live_header_share_identity(self):
        from redux.media_identity import identify_media
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"arbitrary-name.iso"
            path.write_bytes(sega_cd_header()+b"\0"*(2048*17))
            result=identify_media(path)
        self.assertEqual(result.title,parse_sega_cd_header(sega_cd_header()).title)
        self.assertEqual(result.game_id,"MK-4427")


def toc_bytes(tracks=((4, 0), (0, 127009)), lead_out=130741):
    def record(number, control, address):
        return bytes([0, 0x10 | control, number, 0]) + address.to_bytes(4, "big")
    payload = bytes([1, len(tracks)])
    payload += b"".join(record(i + 1, control, start) for i, (control, start) in enumerate(tracks))
    payload += record(0xAA, tracks[-1][0], lead_out)
    return len(payload).to_bytes(2, "big") + payload


def saturn_header():
    data = bytearray(b" " * 2048)
    data[:16] = b"SEGA SEGASATURN "
    data[0x10:0x20] = b"SEGA ENTERPRISES"
    data[0x20:0x2a] = b"MK-81113  "
    data[0x2a:0x30] = b"V1.001"
    data[0x30:0x38] = b"19960801"
    data[0x38:0x40] = b"CD-1/1  "
    data[0x40:0x50] = b"U".ljust(16, b" ")
    data[0x50:0x60] = b"JE".ljust(16, b" ")
    data[0x60:0xd0] = b"World Series    BASEBALL 2".ljust(0x70, b" ")
    return bytes(data)


def record(name, extent, size, flags=0):
    data = bytearray(33 + len(name) + (len(name) % 2 == 0))
    data[0] = len(data)
    for offset, value in [(2, extent), (10, size)]:
        data[offset:offset + 4] = value.to_bytes(4, "little")
        data[offset + 4:offset + 8] = value.to_bytes(4, "big")
    data[25] = flags
    data[28:30] = (1).to_bytes(2, "little")
    data[30:32] = (1).to_bytes(2, "big")
    data[32] = len(name)
    data[33:33 + len(name)] = name
    return data


def iso_fixture(system=b"PLAYSTATION", config_lba=21):
    blocks = 64
    image = bytearray(blocks * 2048)
    pvd = bytearray(2048)
    pvd[:7] = b"\x01CD001\x01"
    pvd[8:40] = system.ljust(32, b" ")
    pvd[40:72] = b"ORIGINAL TEST".ljust(32, b" ")
    pvd[80:84] = blocks.to_bytes(4, "little")
    pvd[84:88] = blocks.to_bytes(4, "big")
    pvd[128:130] = (2048).to_bytes(2, "little")
    pvd[130:132] = (2048).to_bytes(2, "big")
    root = record(b"\0", 20, 2048, 2)
    pvd[156:156 + len(root)] = root
    image[16 * 2048:17 * 2048] = pvd
    image[17 * 2048:17 * 2048 + 7] = b"\xffCD001\x01"
    config = b"BOOT = cdrom:\\SLUS_006.19;1\r\n"
    directory = root + record(b"\1", 20, 2048, 2) + record(b"SYSTEM.CNF;1", config_lba, len(config))
    image[20 * 2048:20 * 2048 + len(directory)] = directory
    image[config_lba * 2048:config_lba * 2048 + len(config)] = config
    return bytes(image)


class FakeAPI:
    def __init__(self, *states, drive_type=5):
        self.states = list(states) or [toc_bytes()]
        self.calls = []
        self.kind = drive_type

    def drive_type(self, root):
        self.calls.append(("type", root))
        return self.kind

    def read_toc(self, drive):
        self.calls.append(("toc", drive.letter))
        value = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(value, BaseException):
            raise value
        return value


class Reader:
    def __init__(self, data, *, second_header=None):
        self.data = data
        self.second_header = second_header
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, offset, count):
        self.calls.append((offset, count))
        if offset == 0 and sum(item[0] == 0 for item in self.calls) > 1 and self.second_header is not None:
            return self.second_header
        return self.data[offset:offset + count]


class DriveIdentityTests(unittest.TestCase):
    def identify(self, data, *, api=None, **options):
        reader = Reader(data, **options)
        identity = identify_drive("g:\\", api=api or FakeAPI(),
            reader_factory=lambda _: reader, source_resolver=lambda _: None)
        return identity, reader

    def test_saturn_identity_is_header_derived_and_reads_only_two_first_sectors(self):
        result, reader = self.identify(saturn_header())
        self.assertEqual((result.drive, result.system, result.game_id), ("G:", "saturn", "MK-81113"))
        self.assertEqual(result.title, "World Series BASEBALL 2")
        self.assertEqual(result.region, "U")
        self.assertEqual(result.details["area_symbols"], "U")
        self.assertEqual(result.details["region"], "U")
        self.assertEqual(result.details["release_date"], "1996-08-01")
        self.assertEqual(result.details["peripherals"], "JE")
        self.assertEqual(result.details["logical_sectors_read"], 2)
        self.assertEqual([item["kind"] for item in result.details["tracks"]], ["data", "audio"])
        self.assertEqual(reader.calls, [(0, 2048), (0, 2048)])
        self.assertTrue(reader.closed)
        self.assertFalse(result.is_virtual)
        self.assertIsNone(result.source_image)
        self.assertEqual(len(result.fingerprint), 64)

    def test_header_requires_exact_marker_and_validates_dates_and_product(self):
        self.assertIsNone(parse_saturn_header(b"SEGA GENESIS" + bytes(2048)))
        with self.assertRaises(MediaFormatError):
            parse_saturn_header(b"SEGA SEGASATURN ")
        malformed = bytearray(saturn_header())
        malformed[0x20:0x2a] = b"***bad*** "
        malformed[0x30:0x38] = b"19960231"
        malformed[0x60:0xd0] = bytes(0x70)
        result = parse_saturn_header(malformed)
        self.assertEqual(result.title, "Saturn disc")
        self.assertIsNone(result.game_id)
        self.assertIsNone(result.details["release_date"])
        self.assertEqual(len(result.notes), 3)

    def test_audio_disc_never_attempts_data_reads(self):
        api = FakeAPI(toc_bytes(((0, 0), (0, 15000)), 30000))
        def forbidden(_):
            self.fail("Audio CD must not be opened as logical data")
        result = identify_drive("G", api=api, reader_factory=forbidden, source_resolver=lambda _: None)
        self.assertEqual(result.format, "Audio CD")
        self.assertEqual(result.details["logical_sectors_read"], 0)
        self.assertIsNone(result.system)

    def test_no_media_and_fixed_drive_do_not_open_reader_or_resolve_source(self):
        def forbidden(_):
            self.fail("No absent or fixed drive should be read")
        for api, reason in [(FakeAPI(NoMediaError("No media", reason="no_media")), "no_media"),
                            (FakeAPI(drive_type=3), "not_optical")]:
            with self.subTest(reason=reason), self.assertRaises(OpticalError) as raised:
                identify_drive("G", api=api, reader_factory=forbidden, source_resolver=forbidden)
            self.assertEqual(raised.exception.reason, reason)

    def test_virtual_image_with_partial_toc_uses_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "anything.iso"
            source.write_bytes(saturn_header() + bytes(17 * 2048))
            api = FakeAPI(b"\0\2\1\1")
            def forbidden(_):
                self.fail("Discoverable mounted image should use source file")
            result = identify_drive("F:", api=api, reader_factory=forbidden, source_resolver=lambda _: source)
            self.assertEqual((result.system, result.game_id), ("saturn", "MK-81113"))
            self.assertEqual(result.source_image, source.resolve())
            self.assertTrue(result.is_virtual)
            self.assertFalse(result.details["track_layout_available"])
            self.assertEqual(result.details["logical_sectors_read"], 0)

    def test_changed_header_and_changed_toc_are_never_published(self):
        reader = Reader(saturn_header(), second_header=bytes(2048))
        with self.assertRaises(OpticalError) as raised:
            identify_drive("G", api=FakeAPI(), reader_factory=lambda _: reader, source_resolver=lambda _: None)
        self.assertEqual(raised.exception.reason, "media_changed")
        self.assertTrue(reader.closed)
        with self.assertRaises(OpticalError) as raised:
            self.identify(saturn_header(), api=FakeAPI(toc_bytes(), toc_bytes(lead_out=140000)))
        self.assertEqual(raised.exception.reason, "media_changed")

    def test_padding_changes_do_not_change_toc_or_fingerprint(self):
        first, _ = self.identify(saturn_header(), api=FakeAPI(toc_bytes() + bytes(100), toc_bytes()))
        second, _ = self.identify(saturn_header(), api=FakeAPI(toc_bytes()))
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_unsupported_toc_does_not_prevent_a_real_saturn_header_identity(self):
        api = FakeAPI(OpticalError("TOC not supported", reason="unsupported_toc"))
        result, reader = self.identify(saturn_header(), api=api)
        self.assertEqual((result.system, result.game_id), ("saturn", "MK-81113"))
        self.assertFalse(result.details["track_layout_available"])
        self.assertTrue(result.notes)
        self.assertEqual(reader.calls, [(0, 2048), (0, 2048)])

    def test_reader_failure_closes_handle_and_stays_an_error(self):
        class BrokenReader(Reader):
            def read(self, offset, count):
                raise OpticalError("Device is spinning up", reason="not_ready", winerror=21)
        reader = BrokenReader(b"")
        with self.assertRaises(OpticalError) as raised:
            identify_drive("G", api=FakeAPI(), reader_factory=lambda _: reader, source_resolver=lambda _: None)
        self.assertEqual(raised.exception.reason, "not_ready")
        self.assertTrue(reader.closed)

    def test_missing_virtual_source_falls_back_to_small_drive_probe(self):
        reader = Reader(saturn_header())
        def missing(_):
            raise FileNotFoundError("The mounted source is unavailable")
        result = identify_drive("G", api=FakeAPI(), reader_factory=lambda _: reader, source_resolver=missing)
        self.assertEqual(result.game_id, "MK-81113")
        self.assertTrue(any("source" in note for note in result.notes))
        self.assertIsNone(result.source_image)
        self.assertEqual(reader.calls, [(0, 2048), (0, 2048)])

    def test_psx_serial_is_read_only_from_bounded_system_cnf(self):
        result, reader = self.identify(iso_fixture())
        self.assertEqual((result.system, result.game_id), ("psx", "SLUS-00619"))
        self.assertEqual(result.details["boot_executable"], "SLUS_006.19")
        self.assertEqual([offset // 2048 for offset, _ in reader.calls], [0, 16, 20, 21, 0])
        result, reader = self.identify(iso_fixture(config_lba=40))
        self.assertEqual(result.system, "psx")
        self.assertIsNone(result.game_id)
        self.assertTrue(any("bounded" in note for note in result.notes))
        self.assertTrue(all(offset < 32 * 2048 for offset, _ in reader.calls))

    def test_generic_and_malformed_data_are_not_guessed_as_console_games(self):
        result, _ = self.identify(iso_fixture(system=b"GENERIC"))
        self.assertEqual(result.title, "ORIGINAL TEST")
        self.assertIsNone(result.system)
        self.assertEqual(result.confidence, "filesystem")
        malformed = bytearray(iso_fixture())
        malformed[16 * 2048 + 84:16 * 2048 + 88] = (65).to_bytes(4, "big")
        result, _ = self.identify(malformed)
        self.assertIsNone(result.system)
        self.assertEqual(result.confidence, "unknown")
        result, reader = self.identify(bytes(32 * 2048))
        self.assertIsNone(result.system)
        self.assertEqual(result.confidence, "unknown")
        self.assertEqual([offset // 2048 for offset, _ in reader.calls], [0, 16, 0])

    def test_probe_rejects_out_of_bounds_sectors_and_incomplete_reads(self):
        reader = Reader(bytes(32 * 2048))
        sectors = _BoundedSectors(reader)
        for lba in (-1, 32, True, 1.0, "0"):
            with self.subTest(lba=lba), self.assertRaises(MediaFormatError):
                sectors.read_sector(None, lba)
        self.assertEqual(reader.calls, [])
        sectors.read_sector(None, 31)
        sectors.read_sector(None, 31)
        self.assertEqual(reader.calls, [(31 * 2048, 2048)])
        sectors.blocks = 18
        with self.assertRaises(MediaFormatError):
            sectors.read_sector(None, 18)
        with self.assertRaises(OpticalError) as raised:
            _BoundedSectors(Reader(bytes(12))).read_sector(None, 0)
        self.assertEqual(raised.exception.reason, "short_read")


if __name__ == "__main__":
    unittest.main()
