"""Virtual-driver regression: preserve strict TOCs and inspect data separately."""
import unittest
from unittest.mock import patch
from redux import optical


def pvd(blocks=277738,block_size=2048):
    data=bytearray(2048)
    data[:7]=b"\x01CD001\x01"
    data[8:40]=b"PLAYSTATION".ljust(32,b" ")
    data[40:72]=b"TEST VOLUME".ljust(32,b" ")
    data[80:84]=blocks.to_bytes(4,"little")
    data[84:88]=blocks.to_bytes(4,"big")
    data[128:130]=block_size.to_bytes(2,"little")
    data[130:132]=block_size.to_bytes(2,"big")
    return bytes(data)


# Captured shape: header advertises last track 2 but only track 1 + lead-out
# are present. Padding to 804 bytes is not another valid track.
VIRTUAL_TOC=bytes.fromhex("0012010200140100000000000014aa0000043d7f")+bytes(784)


class VolumeTests(unittest.TestCase):
    def test_live_driver_shape_still_rejected_by_strict_track_parser(self):
        with self.assertRaises(optical.TocParseError): optical.parse_toc(VIRTUAL_TOC)

    def test_iso_extent_and_device_capacity_are_kept_distinct(self):
        volume=optical.parse_iso9660_pvd(pvd(),device_bytes=277887*2048)
        self.assertEqual(volume.volume_bytes,568807424)
        self.assertEqual(volume.device_bytes,569112576)
        self.assertEqual(volume.system_id,"PLAYSTATION")
        self.assertEqual(volume.volume_id,"TEST VOLUME")

    def test_invalid_or_disagreeing_descriptors_are_not_accepted(self):
        mismatch=bytearray(pvd()); mismatch[84:88]=(42).to_bytes(4,"big")
        for value in (pvd()[:-1],bytes(2048),bytes(mismatch),pvd(blocks=1),pvd(block_size=512)):
            with self.subTest(value=value[:8]),self.assertRaises(optical.OpticalError):
                optical.parse_iso9660_pvd(value)
        with self.assertRaises(optical.OpticalError):
            optical.parse_iso9660_pvd(pvd(),device_bytes=10000)

    def test_malformed_virtual_toc_can_report_a_volume_without_inventing_tracks(self):
        class API:
            def drive_type(self,root): return 5
            def read_toc(self,drive): return VIRTUAL_TOC
            def read_iso_volume(self,drive): return optical.parse_iso9660_pvd(pvd(),device_bytes=569112576)
        with patch.object(optical,"_get_api",return_value=API()):
            info=optical.inspect_disc("F:")
        self.assertEqual(info.inspection_kind,"iso9660_volume")
        self.assertEqual(info.tracks,())
        self.assertIsNone(info.last_track)
        self.assertIsNone(info.has_audio)
        self.assertTrue(info.has_data)
        self.assertEqual(info.volume.volume_blocks,277738)

    def test_unready_media_is_not_reinterpreted_as_a_data_volume(self):
        class API:
            def drive_type(self,root): return 5
            def read_toc(self,drive): raise optical.NoMediaError("No disc",reason="no_media")
            def read_iso_volume(self,drive): raise AssertionError("Must not probe an empty drive")
        with patch.object(optical,"_get_api",return_value=API()),self.assertRaises(optical.NoMediaError):
            optical.inspect_disc("F:")


if __name__=="__main__": unittest.main()
