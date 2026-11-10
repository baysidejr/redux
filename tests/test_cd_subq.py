"""Q evidence fixtures include real observations, with no hardware access."""
import binascii
import unittest

from redux.cd_pregap import QEvidenceError, parse_formatted_q, require_position


OBSERVED = (
    (0,"41010100000100000201924200000000",1,1,1),
    (16,"41010100001700000217296a00000000",1,1,17),
    (126858,"01020000017400281334886d00000000",2,0,126859),
    (126859,"01020000017300281335ff9800000000",2,0,126860),
    (126860,"01020000017300281335ff9800000000",2,0,126860),
    (127008,"01020000000000281533830c00000000",2,0,127008),
    (127009,"01020100000000281534b43800000000",2,1,127009),
    (127010,"010201000001002815350e4800000000",2,1,127010),
)


def q_bytes(first10):
    checksum=binascii.crc_hqx(first10,0)^65535
    return first10+checksum.to_bytes(2,"big")+bytes(4)


class SubqTests(unittest.TestCase):
    def test_observed_q_crc_and_bcd_positions_are_verified(self):
        for requested,hexdata,track,index,absolute in OBSERVED:
            with self.subTest(requested=requested):
                frame=parse_formatted_q(bytes.fromhex(hexdata))
                self.assertTrue(frame.crc_valid)
                self.assertEqual(frame.adr,1)
                bcd=next(p for p in frame.positions if p.encoding=="bcd")
                self.assertEqual((bcd.track,bcd.index,bcd.absolute_lba),(track,index,absolute))
                self.assertEqual(frame.diagnostic(requested)["positions"][0]["requested_lba_delta"],absolute-requested)

    def test_exact_address_is_required_without_drive_offset_correction(self):
        for requested,hexdata,track,index,absolute in OBSERVED:
            data=bytes.fromhex(hexdata)
            if absolute!=requested:
                with self.subTest(lba=requested),self.assertRaises(QEvidenceError):
                    require_position(data,requested,track=track,index=index)
            else:
                position=require_position(data,requested,track=track,index=index)
                self.assertEqual(position.absolute_lba,requested)

    def test_crc_track_index_and_control_mismatch_fail_closed(self):
        data=bytes.fromhex(OBSERVED[6][1])
        corrupt=bytearray(data);corrupt[4]^=1
        self.assertFalse(parse_formatted_q(corrupt).crc_valid)
        for value,kwargs in ((corrupt,{"track":2,"index":1}),
                             (data,{"track":1,"index":1}),
                             (data,{"track":2,"index":0}),
                             (data,{"track":2,"index":1,"control":4})):
            with self.subTest(kwargs=kwargs),self.assertRaises(QEvidenceError):
                require_position(value,127009,**kwargs)

    def test_binary_format_is_an_independent_candidate(self):
        # Binary minute28 has an invalid BCD nibble, avoiding guessed encoding.
        data=q_bytes(bytes((1,2,0,0,1,73,0,28,13,35)))
        frame=parse_formatted_q(data)
        self.assertEqual(len(frame.positions),1)
        position=require_position(frame,126860,track=2,index=0,control=0)
        self.assertEqual(position.encoding,"binary")

    def test_ambiguous_identical_low_digits_do_not_prove_encoding(self):
        data=q_bytes(bytes((1,2,0,0,0,0,0,0,2,0)))
        self.assertEqual(require_position(data,0,track=2,index=0).encoding,"bcd_or_binary")

    def test_reserved_suffix_is_preserved_and_not_included_in_q_crc(self):
        data=bytes.fromhex(OBSERVED[6][1])
        frame=parse_formatted_q(data[:12]+b"abcd")
        self.assertTrue(frame.crc_valid);self.assertEqual(frame.raw[-4:],b"abcd")
        self.assertEqual(parse_formatted_q(data[:12]).positions,frame.positions)

    def test_invalid_msf_nonposition_q_and_wrong_lengths_cannot_establish_position(self):
        for first10 in (bytes((1,2,0,0,0,0,0,0,0x60,0)),
                        bytes((2,2,0,0,0,0,0,0,2,0)),
                        bytes((1,2,0,0,0,0,1,0,2,0))):
            data=q_bytes(first10)
            self.assertEqual(parse_formatted_q(data).positions,())
            with self.assertRaises(QEvidenceError):require_position(data,0,track=2,index=0)
        for data in (b"",bytes(11),bytes(13),bytes(17)):
            with self.assertRaises(QEvidenceError):parse_formatted_q(data)


if __name__=="__main__":unittest.main()
