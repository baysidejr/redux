"""Typed/Q pregap inference fixtures, with no physical drive access."""
import binascii
import hashlib
import json
import math
import threading
import unittest

from redux.cd_pregap import resolve_audio_pregap
from redux.raw_acquisition import (CDDA, RawAcquisitionError,
                                           RawCaptureCancelled, _expected_header)
from redux.scsi_cd import ScsiReadError


def wrong_type(lba):
    return ScsiReadError("Expected sector type does not match", reason="sector_type_mismatch",
                         diagnostic={"lba": lba, "sense_key": 5, "asc": 0x64, "ascq": 0})


def data_sector(lba, mode=1):
    return b"\0" + b"\xff" * 10 + b"\0" + _expected_header(lba) + bytes((mode,)) + bytes(2336)


def q_frame(lba, index, track=2, control=0):
    # Deliberately uninformative relative time: the resolver must not derive
    # INDEX 00 from a presumed countdown or a conventional150-sector length.
    first = bytes(((control << 4) | 1, (track // 10) * 16 + track % 10,
                   index, 0, 0, 0, 0)) + _expected_header(lba)
    checksum = binascii.crc_hqx(first, 0) ^ 0xFFFF
    return first + checksum.to_bytes(2, "big") + bytes(4)


class Device:
    def __init__(self, boundary=8963, index01=9000, mode=1):
        self.boundary = boundary
        self.index01 = index01
        self.mode = mode
        self.data_reads = []
        self.audio_reads = []
        self.q_reads = []
        self.data_errors = {}
        self.audio_errors = {}
        self.q_overrides = {}
        self.q_error = None
        self.data_override = {}
        self.audio_override = {}
        self.on_data_read = None
        self.ignore_expected_type = False
        self.opaque = {}
        self.any_reads = []
        self.any_override = {}
        self.any_errors = {}

    def read_expected_data(self, lba, mode=1, count=1):
        assert count == 1 and mode == self.mode
        self.data_reads.append(lba)
        if self.on_data_read:
            self.on_data_read(lba)
        if lba in self.data_errors:
            raise self.data_errors[lba]
        if lba in self.data_override:
            return self.data_override[lba]
        if lba in self.opaque:
            error=RawAcquisitionError("Full request returned an unidentified sector",reason="unsupported_raw_sector")
            error.typed_read_succeeded=True
            error.raw_sector=self.opaque[lba]
            error.requested_lba=lba
            error.requested_mode=mode
            raise error
        if lba >= self.boundary:
            if self.ignore_expected_type:
                error = RawAcquisitionError("Successful data request returned audio header bytes",
                                            reason="unsupported_raw_sector")
                error.typed_read_succeeded = True
                error.raw_sector = bytes((lba % 251,)) * 2352
                error.requested_lba = lba
                error.requested_mode = mode
                raise error
            raise wrong_type(lba)
        return data_sector(lba, self.mode)

    def read_cdda(self, lba, count=1):
        assert count == 1
        self.audio_reads.append(lba)
        if lba in self.audio_errors:
            raise self.audio_errors[lba]
        if lba in self.audio_override:
            return self.audio_override[lba]
        if lba in self.opaque:
            raise wrong_type(lba)
        if lba < self.boundary:
            raise wrong_type(lba)
        return bytes((lba % 251,)) * 2352

    def read_subq(self, lba, mode=5):
        assert mode in (CDDA,5)
        self.q_reads.append(lba)
        if mode==CDDA and lba in self.opaque:
            raise wrong_type(lba)
        if self.q_error:
            raise self.q_error
        return self.q_overrides.get(lba, q_frame(lba, 0 if lba < self.index01 else 1))

    def read_sectors(self,lba,count,mode):
        assert mode==5 and count==1
        self.any_reads.append(lba)
        if lba in self.any_errors:raise self.any_errors[lba]
        return self.any_override.get(lba,self.opaque.get(lba,bytes(2352)))


class PregapTests(unittest.TestCase):
    def resolve(self, device, **kwargs):
        return resolve_audio_pregap(device, 0, device.index01, 2, device.mode, **kwargs)

    def test_nonstandard_gap_is_observed_not_assumed_and_search_is_bounded(self):
        device = Device()
        result = self.resolve(device)
        self.assertEqual((result.index00_lba, result.index01_lba), (8963, 9000))
        self.assertEqual(result.evidence["observed_gap_sectors"], 37)
        self.assertFalse(result.evidence["archival_calibrated"])
        self.assertEqual(result.evidence["last_data_lba"], 8962)
        self.assertIn(8962, device.data_reads)
        self.assertIn(8963, device.audio_reads)
        self.assertIn(9000, device.audio_reads)
        self.assertGreaterEqual(min(device.data_reads), 9000 - 7500 - 1)
        self.assertLessEqual(len(device.data_reads), math.ceil(math.log2(7501)) + 3)
        self.assertTrue(result.evidence["q_exact_index00_after_boundary_lbas"])
        json.dumps(result.evidence)

    def test_observed_saturn_neighboring_q_does_not_move_main_channel_boundary(self):
        device = Device(boundary=126859, index01=127009)
        device.q_overrides.update({
            126859: bytes.fromhex("01020000017300281335ff9800000000"),
            126860: bytes.fromhex("01020000017300281335ff9800000000"),
            127008: bytes.fromhex("01020000000000281533830c00000000"),
            127009: bytes.fromhex("01020100000000281534b43800000000"),
        })
        result = self.resolve(device)
        self.assertEqual(result.index00_lba, 126859)
        self.assertEqual(result.evidence["observed_gap_sectors"], 150)
        probe = next(value for value in result.evidence["q_probes"] if value["requested_lba"] == 126859)
        self.assertFalse(probe["accepted"])
        self.assertEqual(probe["positions"][0]["requested_lba_delta"], 1)
        self.assertIn(126860, result.evidence["q_exact_index00_after_boundary_lbas"])

    def test_actual_drive_ignoring_expected_type_needs_matching_audio_and_q(self):
        device = Device(boundary=126859, index01=127009)
        device.ignore_expected_type = True
        device.q_overrides.update({
            126859: bytes.fromhex("01020000017300281335ff9800000000"),
            126860: bytes.fromhex("01020000017300281335ff9800000000"),
            127008: bytes.fromhex("01020000000000281533830c00000000"),
            127009: bytes.fromhex("01020100000000281534b43800000000"),
        })
        result = self.resolve(device)
        self.assertEqual((result.index00_lba, result.index01_lba), (126859, 127009))
        self.assertTrue(result.evidence["device_ignored_expected_sector_type"])
        boundary = next(p for p in result.evidence["typed_probes"] if p["lba"] == 126859)
        self.assertEqual(boundary["q_classification_address_deltas"], [1])
        self.assertTrue(boundary["audio_bytes_match_invalid_header_read"])
        self.assertFalse(result.evidence["archival_calibrated"])

    def test_successful_bad_header_with_data_q_is_corruption_not_audio(self):
        device = Device()
        device.ignore_expected_type = True
        device.q_overrides[8999] = q_frame(8999, 0, control=4)
        with self.assertRaisesRegex(RawAcquisitionError, "data corruption"):
            self.resolve(device)

    def test_ignored_expected_type_still_requires_identical_full_audio_bytes(self):
        device = Device()
        device.ignore_expected_type = True
        device.audio_override[8999] = b"x" * 2352
        with self.assertRaisesRegex(RawAcquisitionError, "reads differ"):
            self.resolve(device)

    def test_medium_hardware_and_unsupported_errors_never_become_audio(self):
        for reason in ("medium_read_error", "hardware_read_error", "invalid_read_cd_field",
                       "unsupported_read_cd", "io_error", "media_changed", "short_read"):
            device = Device()
            error = ScsiReadError("Read failed", reason=reason, diagnostic={"lba": 8999})
            device.data_errors[8999] = error
            with self.subTest(reason=reason), self.assertRaises(ScsiReadError) as raised:
                self.resolve(device)
            self.assertIs(raised.exception, error)
            self.assertEqual(device.audio_reads, [])
            self.assertEqual(device.q_reads, [])

    def test_invalid_successful_data_headers_are_rejected_without_audio_guess(self):
        for sector in (bytes(2352), data_sector(8998), data_sector(8999, 2), bytes(2351)):
            device = Device()
            device.data_override[8999] = sector
            with self.subTest(length=len(sector)), self.assertRaises(RawAcquisitionError):
                self.resolve(device)
            self.assertEqual(device.audio_reads, [])

    def test_generic_error_with_wrong_type_label_is_not_definite_scsi_evidence(self):
        device = Device()
        device.data_errors[8999] = RawAcquisitionError("Unverified adapter failure", reason="sector_type_mismatch")
        with self.assertRaises(RawAcquisitionError):
            self.resolve(device)
        self.assertEqual(device.audio_reads, [])

    def test_wrong_type_must_be_followed_by_successful_full_typed_cdda(self):
        for replacement in (bytes(2351), data_sector(8999)):
            device = Device()
            device.audio_override[8999] = replacement
            with self.subTest(length=len(replacement)), self.assertRaises(RawAcquisitionError):
                self.resolve(device)
        device = Device()
        device.audio_errors[8999] = ScsiReadError("Audio medium error", reason="medium_read_error", diagnostic={})
        with self.assertRaisesRegex(ScsiReadError, "Audio medium error"):
            self.resolve(device)

    def test_gap_larger_than_limit_does_not_expand_search_or_guess_an_anchor(self):
        device = Device(boundary=1000)
        with self.assertRaisesRegex(RawAcquisitionError, "bounded"):
            self.resolve(device)
        self.assertGreaterEqual(min(device.data_reads), 1499)
        self.assertEqual(device.q_reads, [])

    def test_valid_data_tail_is_not_rewritten_as_a_pregap(self):
        device = Device(boundary=9000)
        with self.assertRaisesRegex(RawAcquisitionError, "valid typed data"):
            self.resolve(device)
        self.assertEqual(device.audio_reads, [])

    def test_one_frame_q_offset_cannot_be_calibrated_or_silently_accepted(self):
        device = Device()
        for lba in (8963, 8964, 8981, 8999):
            device.q_overrides[lba] = q_frame(lba + 1, 0)
        with self.assertRaisesRegex(RawAcquisitionError, "No exact-address"):
            self.resolve(device)
        device = Device()
        device.q_overrides[9000] = q_frame(9001, 1)
        with self.assertRaisesRegex(RawAcquisitionError, "does not corroborate"):
            self.resolve(device)

    def test_corrupt_q_and_wrong_track_or_index_cannot_corroborate(self):
        for replacement in (q_frame(9000, 0), q_frame(9000, 1, track=3),
                            q_frame(9000, 1, control=4), bytes(16)):
            device = Device()
            device.q_overrides[9000] = replacement
            with self.subTest(replacement=replacement.hex()), self.assertRaises(RawAcquisitionError):
                self.resolve(device)
        device = Device()
        device.q_overrides[8964] = q_frame(8964, 1)
        with self.assertRaisesRegex(RawAcquisitionError, "does not corroborate"):
            self.resolve(device)

    def test_q_medium_error_propagates_without_skipping_to_other_samples(self):
        device = Device()
        device.q_error = ScsiReadError("Q medium error", reason="medium_read_error", diagnostic={})
        with self.assertRaisesRegex(ScsiReadError, "Q medium error"):
            self.resolve(device)
        self.assertEqual(device.q_reads, [9000])

    def test_cancelled_before_and_during_probes_stops_without_q_reads(self):
        event = threading.Event()
        event.set()
        device = Device()
        with self.assertRaises(RawCaptureCancelled):
            self.resolve(device, cancelled=event)
        self.assertEqual(device.data_reads, [])
        event.clear()
        device.on_data_read = lambda _: event.set()
        with self.assertRaises(RawCaptureCancelled):
            self.resolve(device, cancelled=event)
        self.assertEqual(device.audio_reads, [])
        self.assertEqual(device.q_reads, [])

    def test_two_sector_gap_and_mode_two_use_the_same_observed_policy(self):
        device = Device(boundary=8998, mode=2)
        result = self.resolve(device, max_gap=2)
        self.assertEqual(result.index00_lba, 8998)
        self.assertEqual(result.evidence["data_mode"], 2)
        self.assertEqual(result.evidence["q_exact_index00_after_boundary_lbas"], [8999])

    def test_single_sector_gap_cannot_supply_required_after_boundary_q(self):
        with self.assertRaisesRegex(RawAcquisitionError, "after the candidate"):
            self.resolve(Device(boundary=8999))

    def test_input_validation_is_bounded_before_device_access(self):
        cases = [(-1, 9000, 2, 1, 7500), (0, True, 2, 1, 7500),
                 (9000, 9000, 2, 1, 7500), (0, 9000, 1, 1, 7500),
                 (0, 9000, 2, True, 7500), (0, 9000, 2, 1, 7501), (0, 9000, 2, 1, 0)]
        for start, index01, number, mode, limit in cases:
            device = Device()
            with self.subTest(case=(start, index01, number, mode, limit)), self.assertRaises(ValueError):
                resolve_audio_pregap(device, start, index01, number, mode, max_gap=limit)
            self.assertEqual(device.data_reads, [])

    def opaque_device(self, boundary=8963):
        device=Device(boundary=boundary)
        # Deliberately unidentified bytes, including a sync prefix. The resolver
        # must retain exact bytes without claiming that they are zero-fill.
        device.opaque[boundary]=b"\0"+b"\xff"*10+b"\0"+bytes((n*19%251 for n in range(2340)))
        device.q_overrides[boundary]=q_frame(boundary+1,0)
        return device

    def test_one_reproducible_opaque_boundary_sector_is_recorded_unchanged(self):
        device=self.opaque_device()
        result=self.resolve(device)
        opaque=result.evidence["opaque_transition_sectors"]
        self.assertEqual(len(opaque),1)
        self.assertEqual((opaque[0]["lba"],opaque[0]["sha256"]),
                         (8963,hashlib.sha256(device.opaque[8963]).hexdigest()))
        self.assertEqual(opaque[0]["classification"],"opaque_index00_transition_main_channel")
        self.assertEqual(opaque[0]["q_classification_address_deltas"],[1])
        self.assertEqual(result.evidence["first_audio_lba"],8964)
        self.assertEqual(result.evidence["last_data_lba"],8962)
        self.assertEqual(result.index00_lba,8963)
        self.assertEqual(result.evidence["pregap_read_mode"],"mixed_cdda_with_one_opaque_any_sector")
        self.assertEqual(device.any_reads,[8963])
        following=next(q for q in result.evidence["q_probes"] if q["requested_lba"]==8964)
        self.assertTrue(following["accepted"])

    def test_opaque_changed_repeat_is_precisely_unstable_pregap(self):
        device=self.opaque_device();device.any_override[8963]=b"z"*2352
        with self.assertRaises(RawAcquisitionError) as raised:self.resolve(device)
        self.assertEqual(raised.exception.reason,"unstable_pregap")

    def test_opaque_requires_crc_audio_index00_correlation_without_arbitrary_offsets(self):
        for q in (q_frame(8963,0,control=4),q_frame(8963,1),q_frame(8963,0,track=3),
                  q_frame(8965,0),bytes(16)):
            device=self.opaque_device();device.q_overrides[8963]=q
            with self.subTest(q=q.hex()),self.assertRaises(RawAcquisitionError):self.resolve(device)

    def test_opaque_immediately_following_audio_requires_exact_q_even_if_later_q_matches(self):
        device=self.opaque_device();device.q_overrides[8964]=q_frame(8965,0)
        with self.assertRaisesRegex(RawAcquisitionError,"does not corroborate"):self.resolve(device)

    def test_two_opaque_sectors_and_opaque_away_from_boundary_are_rejected(self):
        device=self.opaque_device()
        device.opaque[8964]=device.opaque[8963]
        for candidate in (device,self.opaque_device(boundary=8969)):
            if candidate is not device:candidate.boundary=8963
            with self.subTest(opaque=list(candidate.opaque)),self.assertRaises(RawAcquisitionError):
                self.resolve(candidate)

    def test_opaque_does_not_absorb_medium_errors_short_reads_or_program_data(self):
        for reason in ("medium_read_error","io_error","unsupported_read_cd"):
            device=self.opaque_device()
            error=ScsiReadError("A real audio failure",reason=reason,diagnostic={})
            device.audio_errors[8963]=error
            with self.subTest(reason=reason),self.assertRaises(ScsiReadError) as raised:self.resolve(device)
            self.assertIs(raised.exception,error);self.assertEqual(device.any_reads,[])
        device=self.opaque_device();device.any_override[8963]=bytes(2351)
        with self.assertRaises(RawAcquisitionError) as raised:self.resolve(device)
        self.assertEqual(raised.exception.reason,"short_read")
        device=self.opaque_device()
        error=ScsiReadError("Any medium error",reason="medium_read_error",diagnostic={})
        device.any_errors[8963]=error
        with self.assertRaises(ScsiReadError) as raised:self.resolve(device)
        self.assertIs(raised.exception,error)
        # Unannotated program-data corruption cannot enter the opaque route.
        device=self.opaque_device()
        device.data_errors[8999]=RawAcquisitionError("Bad data header",reason="unsupported_raw_sector")
        with self.assertRaises(RawAcquisitionError):self.resolve(device)
        self.assertEqual(device.any_reads,[])


if __name__ == "__main__":
    unittest.main()
