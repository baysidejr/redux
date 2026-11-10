from pathlib import Path
import tempfile
import unittest

from redux.core_profiles import PROFILES
from redux.embedded import HOSTED_DEFAULTS
from redux.console_cores import recommended_names
from redux.disc_identity import parse_3do_header, _logical_identity
from redux.media_identity import identify_media
from tests.test_media_identity import iso_fixture, directory_record


def opera_image():
    data=bytearray(24*2048)
    data[:7]=b'\x01ZZZZZ\x01'
    data[40:72]=b'REDUX ORIGINAL TEST'.ljust(32,b'\0')
    data[76:80]=(2048).to_bytes(4,'big')
    data[80:84]=(24).to_bytes(4,'big')
    return data


def neocd_image(ipl=b'BOOT.PRG,0,0\r\n'):
    data=bytearray(iso_fixture(system=b'NEOGEO',boot=ipl))
    records=directory_record(b'\0',20,2048,2)+directory_record(b'\1',20,2048,2)+directory_record(b'IPL.TXT;1',21,len(ipl))
    data[20*2048:21*2048]=bytes(2048)
    data[20*2048:20*2048+len(records)]=records
    return data


class NewCDDefaultsTests(unittest.TestCase):
    def test_dedicated_cores_are_recommended_and_enabled(self):
        for system,name in (('neo_geo_cd','neocd'),('3do','opera')):
            self.assertIn(system,HOSTED_DEFAULTS)
            self.assertIn(name,recommended_names())
            self.assertEqual(PROFILES[system].filename,name+'_libretro.dll')

    def test_3do_header_image_and_drive_share_identity(self):
        data=opera_image()
        self.assertEqual(parse_3do_header(data).system,'3do')
        self.assertEqual(_logical_identity(None,data).system,'3do')
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'test.iso';image.write_bytes(data)
            self.assertEqual(identify_media(image).system,'3do')
            data[80:84]=(25).to_bytes(4,'big');image.write_bytes(data)
            self.assertIsNone(identify_media(image).system)

    def test_neocd_identification_requires_valid_ipl_not_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'anything.iso';image.write_bytes(neocd_image())
            self.assertEqual(identify_media(image).system,'neo_geo_cd')
            image.write_bytes(neocd_image(b'ordinary text'))
            self.assertIsNone(identify_media(image).system)

    def test_bad_3do_header_not_accepted(self):
        data=opera_image();data[76:80]=bytes(4)
        self.assertIsNone(parse_3do_header(data))
        self.assertIsNone(parse_3do_header(b'3DO'))
