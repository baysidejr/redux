from pathlib import Path
import tempfile
import unittest

from redux.media_identity import identify_media


def directory_record(name, extent, size, flags=0):
    record=bytearray(33 + len(name) + (len(name) % 2 == 0))
    record[0]=len(record)
    record[2:6]=extent.to_bytes(4,"little");record[6:10]=extent.to_bytes(4,"big")
    record[10:14]=size.to_bytes(4,"little");record[14:18]=size.to_bytes(4,"big")
    record[25]=flags;record[28:30]=(1).to_bytes(2,"little");record[30:32]=(1).to_bytes(2,"big")
    record[32]=len(name);record[33:33 + len(name)]=name
    return record


def iso_fixture(system=b"PLAYSTATION", boot=b"BOOT = cdrom:\\SLUS_006.19;1\r\n", blocks=24):
    image=bytearray(blocks * 2048)
    pvd=bytearray(2048);pvd[:7]=b"\x01CD001\x01"
    pvd[8:40]=system.ljust(32,b" ");pvd[40:72]=b"ORIGINAL TEST".ljust(32,b" ")
    pvd[80:84]=blocks.to_bytes(4,"little");pvd[84:88]=blocks.to_bytes(4,"big")
    pvd[128:130]=(2048).to_bytes(2,"little");pvd[130:132]=(2048).to_bytes(2,"big")
    root=directory_record(b"\0",20,2048,2);pvd[156:156 + len(root)]=root
    image[16*2048:17*2048]=pvd
    image[17*2048:17*2048+7]=b"\xffCD001\x01"
    records=root+directory_record(b"\1",20,2048,2)+directory_record(b"SYSTEM.CNF;1",21,len(boot))
    image[20*2048:20*2048+len(records)]=records
    image[21*2048:21*2048+len(boot)]=boot
    return bytes(image)


def raw_image(cooked, mode=2):
    blocks=[]
    for offset in range(0,len(cooked),2048):
        header=b"\0"+b"\xff"*10+b"\0"+b"\0\0\0"+bytes([mode])
        if mode==2:header+=b"\0\0\x08\0"*2
        payload=header+cooked[offset:offset+2048]
        blocks.append(payload+bytes(2352-len(payload)))
    return b"".join(blocks)


class MediaIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def file(self,name,data):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path

    def test_playstation_iso_reads_system_cnf_serial_without_filename_guessing(self):
        identity=identify_media(self.file("arbitrary.iso",iso_fixture()))
        self.assertEqual(identity.system,"psx")
        self.assertEqual(identity.game_id,"SLUS-00619")
        self.assertEqual(identity.details["boot_executable"],"SLUS_006.19")
        self.assertEqual(identity.confidence,"filesystem")

    def test_realistic_mode2_cue_preserves_raw_sector_contract(self):
        self.file("Disc track.bin",raw_image(iso_fixture(),2))
        cue=self.file("Disc.cue",b'FILE "Disc track.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n')
        identity=identify_media(cue)
        self.assertEqual((identity.system,identity.game_id),("psx","SLUS-00619"))
        self.assertEqual(identity.format,"CUE/BIN MODE2/2352")
        self.assertEqual(identity.details["payload_offset"],24)

    def test_mode1_raw_image_and_nonzero_cue_index_are_read_correctly(self):
        data=raw_image(iso_fixture(),1)
        direct=identify_media(self.file("direct.bin",data))
        self.assertEqual(direct.game_id,"SLUS-00619")
        self.file("pregap.bin",bytes(75*2352)+data)
        cue=self.file("pregap.cue",b'FILE "pregap.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:01:00\n')
        identity=identify_media(cue)
        self.assertEqual(identity.game_id,"SLUS-00619")
        self.assertEqual(identity.details["track_start_sector"],75)

    def test_playstation_marker_without_standard_serial_is_not_invented(self):
        result=identify_media(self.file("SLUS_999.99.iso",iso_fixture(boot=b"BOOT=cdrom:\\PSX.EXE;1\n")))
        self.assertEqual(result.system,"psx")
        self.assertIsNone(result.game_id)
        self.assertTrue(result.notes)

    def test_non_playstation_iso_does_not_infer_a_console(self):
        result=identify_media(self.file("PlayStation.iso",iso_fixture(system=b"GENERIC")))
        self.assertIsNone(result.system)
        self.assertEqual(result.confidence,"filesystem")

    def test_malformed_iso_extents_and_dual_endian_fields_are_rejected(self):
        cases=[]
        mismatch=bytearray(iso_fixture());mismatch[16*2048+84:16*2048+88]=(25).to_bytes(4,"big");cases.append(mismatch)
        invalidroot=bytearray(iso_fixture());off=16*2048+156
        invalidroot[off+2:off+6]=(1000).to_bytes(4,"little");invalidroot[off+6:off+10]=(1000).to_bytes(4,"big");cases.append(invalidroot)
        cases.append(bytearray(iso_fixture()[:-2048]))
        for number,data in enumerate(cases):
            with self.subTest(number=number):
                result=identify_media(self.file(f"bad{number}.iso",data))
                self.assertIsNone(result.system)
                self.assertEqual(result.confidence,"unknown")

    def test_cue_traversal_and_form2_metadata_are_not_followed(self):
        external=self.root.parent/(self.root.name+"-outside.bin")
        try:
            external.write_bytes(raw_image(iso_fixture()))
            cue=self.file("outside.cue",f'FILE "../{external.name}" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n'.encode())
            self.assertIsNone(identify_media(cue).system)
        finally:external.unlink(missing_ok=True)
        raw=bytearray(raw_image(iso_fixture()));raw[16*2352+18]|=0x20;raw[16*2352+22]|=0x20
        self.assertIsNone(identify_media(self.file("form2.bin",raw)).system)

    def test_playlist_uses_first_disc_and_reports_its_scope(self):
        self.file("first.iso",iso_fixture())
        playlist=self.file("Set.m3u",b"# discs\nfirst.iso\n")
        result=identify_media(playlist)
        self.assertEqual((result.title,result.system,result.game_id),("Set","psx","SLUS-00619"))
        self.assertIn("first playlist entry",result.notes[-1])

    def test_header_identification_not_extension_establishes_cartridge_system(self):
        nes=b"NES\x1a"+bytes([1,1])+bytes(10)+bytes(16384+8192)
        result=identify_media(self.file("unknown.dat",nes))
        self.assertEqual(result.system,"nes")
        self.assertIsNone(identify_media(self.file("fake.nes",b"a document")).system)
        self.assertIsNone(identify_media(self.file("short.nes",nes[:20])).system)

    def test_sega_disc_headers_have_explicit_identity(self):
        saturn=bytearray(18*2048);saturn[:16]=b"SEGA SEGASATURN "
        saturn[0x20:0x2A]=b"T-12345G  ";saturn[0x60:0x60+9]=b"TEST GAME"
        result=identify_media(self.file("saturn.iso",saturn))
        self.assertEqual((result.system,result.game_id,result.title),("saturn","T-12345G","TEST GAME"))
        sega=bytearray(18*2048);sega[:14]=b"SEGADISCSYSTEM"
        self.assertEqual(identify_media(self.file("sega.iso",sega)).system,"sega_cd")

    def test_n64_swapped_alignment_and_sega8_platform_are_header_derived(self):
        valid=b"\x37\x80\x40\x12"+bytes(60)
        self.assertEqual(identify_media(self.file("rom.unknown",valid)).system,"n64")
        self.assertIsNone(identify_media(self.file("odd.v64",valid+b"x")).system)
        for code,expected in [(3,"sms"),(4,"sms"),(5,"game_gear"),(7,"game_gear")]:
            image=bytearray(0x2000);image[0x1ff0:0x1ff8]=b"TMR SEGA";image[0x1fff]=code<<4
            with self.subTest(code=code):self.assertEqual(identify_media(self.file(f"rom{code}.bin",image)).system,expected)


if __name__=="__main__":unittest.main()
