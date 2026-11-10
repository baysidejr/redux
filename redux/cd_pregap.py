"""Q evidence parsing and bounded data/audio transition corroboration.

Formatted Q suffixes may contain BCD or binary MSF. Keep both interpretations
until absolute position resolves them. A CRC-valid Q frame can still describe
a neighboring sector, so a boundary decision must require the exact requested
absolute address; no drive-specific offset is silently applied here. The
resolver uses an injected device and never assumes a conventional gap length.
"""
from __future__ import annotations

import binascii
from dataclasses import asdict, dataclass, replace
import hashlib


class QEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class QPosition:
    encoding: str
    track: int
    index: int
    relative_frames: int
    absolute_lba: int
    control: int


@dataclass(frozen=True)
class QFrame:
    raw: bytes
    adr: int
    control: int
    crc_valid: bool
    stored_crc: int
    computed_crc: int
    positions: tuple[QPosition, ...]

    def diagnostic(self, requested_lba=None):
        values=[]
        for position in self.positions:
            item=asdict(position)
            if requested_lba is not None:
                item["requested_lba_delta"]=position.absolute_lba-requested_lba
            values.append(item)
        return {"q_hex":self.raw.hex(), "adr":self.adr, "control":self.control,
                "crc_valid":self.crc_valid, "stored_crc":f"{self.stored_crc:04x}",
                "computed_crc":f"{self.computed_crc:04x}", "positions":values}


def _number(value, encoding):
    if encoding == "binary":
        return value
    if value & 15 > 9 or value >> 4 > 9:
        raise ValueError("Invalid BCD digit")
    return (value >> 4)*10+(value & 15)


def _msf(values, encoding):
    minute,second,frame=(_number(value,encoding) for value in values)
    if minute > 99 or second >= 60 or frame >= 75:
        raise ValueError("Invalid Q MSF")
    return (minute*60+second)*75+frame


def parse_formatted_q(data):
    """Parse first 12 Q bytes; preserve a 16-byte suffix's trailing bytes.

    CRC is complemented CRC-16/CCITT over bytes 0..9, stored big-endian in
    bytes 10..11. Only ADR=1 frames generate position candidates. Invalid CRC
    remains visible as diagnostic data and cannot pass require_position().
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("Q data must be bytes")
    raw=bytes(data)
    if len(raw) not in (12,16):
        raise QEvidenceError("Formatted Q requires 12 Q bytes or the 16-byte suffix")
    q=raw[:12]
    computed=binascii.crc_hqx(q[:10],0)^0xFFFF
    stored=int.from_bytes(q[10:12],"big")
    adr,control=q[0]&15,q[0]>>4
    positions=[]
    if adr == 1 and q[6] == 0:
        for encoding in ("bcd","binary"):
            try:
                track=0xAA if q[1] == 0xAA else _number(q[1],encoding)
                index=_number(q[2],encoding)
                relative=_msf(q[3:6],encoding)
                absolute=_msf(q[7:10],encoding)-150
                if (not (1 <= track <= 99 or track == 0xAA) or
                        not 0 <= index <= 99 or absolute < -150):
                    continue
                positions.append(QPosition(encoding,track,index,relative,absolute,control))
            except ValueError:
                continue
    return QFrame(raw,adr,control,computed==stored,stored,computed,tuple(positions))


def require_position(data, lba, *, track, index, control=None):
    """Require CRC, ADR and exact absolute address plus track/index/control.

    This checks one piece of evidence. It does not locate a transition or prove
    an entire pregap is homogeneous. Consumers must separately prove the data
    and audio boundary with typed reads and verify neighboring frames.
    """
    if isinstance(lba,bool) or not isinstance(lba,int) or lba < 0:
        raise ValueError("Requested LBA must be a nonnegative integer")
    frame=data if isinstance(data,QFrame) else parse_formatted_q(data)
    if not frame.crc_valid:
        raise QEvidenceError("Q CRC does not match; it cannot establish a boundary")
    if frame.adr != 1:
        raise QEvidenceError("Q frame does not contain an ADR=1 position")
    positions=[p for p in frame.positions if p.absolute_lba==lba and
               p.track==track and p.index==index and (control is None or p.control==control)]
    if not positions:
        raise QEvidenceError("Q absolute position/track/index does not match the requested sector")
    if len(positions)==1:
        return positions[0]
    # Some positions look identical under BCD and binary. Do not claim that
    # this frame proves either encoding when both mean exactly the same thing.
    semantic={(p.track,p.index,p.relative_frames,p.absolute_lba,p.control) for p in positions}
    if len(semantic)!=1:
        raise QEvidenceError("Q encoding remains ambiguous")
    return replace(positions[0],encoding="bcd_or_binary")


@dataclass(frozen=True)
class PregapBoundary:
    """Inferred main-channel transition, corroborated by positional Q.

    This is not calibrated archival subchannel timing. In particular, a drive's
    neighboring-sector Q is not shifted to manufacture an exact INDEX 00.
    """
    index00_lba: int
    index01_lba: int
    evidence: dict


def resolve_audio_pregap(device, data_start_lba, toc_index01_lba, next_track_number,
                         data_mode, cancelled=None, max_gap=7500):
    """Resolve a data-to-audio tail using bounded typed reads and exact Q.

    Call only after the data track's last TOC sector failed ordinary header
    validation. A definite SCSI wrong-sector-type response can be followed by
    an accepted typed CDDA read. For drives ignoring expected type, an explicitly
    successful full transfer with invalid header additionally needs unchanged
    typed CDDA bytes and CRC-valid audio Q correlation. Medium errors, short
    reads and unsupported commands never become a gap classification. One
    opaque main sector may instead be preserved between valid data and typed
    CDDA, but only if its full bytes repeat and Q corroborates that transition.
    It is never decoded, repaired, or claimed to be valid data or audio.

    The search uses at most ``max_gap`` candidate pregap sectors plus one data
    anchor. Adjacent data/audio reads locate the main-channel transition. At
    least one CRC-valid, exact-address INDEX 00 frame *after* that transition
    and an exact INDEX 01 frame at the TOC position must corroborate it. Raw Q
    offsets are diagnostic only; neither padding nor address repair is used.
    The capture must still validate the resulting extents sector by sector.
    """
    # Local imports keep this evidence module usable by raw_acquisition's
    # planner without introducing a module-initialization cycle.
    from .raw_acquisition import (CDDA, RAW_WITH_SUBCODE, SECTOR_BYTES, RawAcquisitionError,
                                  RawCaptureCancelled, _MAX_SECTORS, _data_mode)
    from .scsi_cd import ScsiReadError

    def cancel():
        if cancelled is not None and cancelled.is_set():
            raise RawCaptureCancelled()

    def reject(message, reason="unsupported_pregap"):
        raise RawAcquisitionError(message, reason=reason)

    cancel()
    for label, value in (("Data start", data_start_lba), ("TOC INDEX 01", toc_index01_lba)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < _MAX_SECTORS:
            raise ValueError(label + " LBA must be a bounded nonnegative integer")
    if data_start_lba >= toc_index01_lba:
        raise ValueError("The data track must begin before the next TOC INDEX 01")
    if (isinstance(next_track_number, bool) or not isinstance(next_track_number, int) or
            not 2 <= next_track_number <= 99):
        raise ValueError("The next audio track number must be 2–99")
    if isinstance(max_gap, bool) or not isinstance(max_gap, int) or not 1 <= max_gap <= 7500:
        raise ValueError("Pregap search must be bounded to 1–7500 sectors")
    if isinstance(data_mode, str):
        data_mode = {"MODE1/2352": 1, "MODE2/2352": 2}.get(data_mode)
    if isinstance(data_mode, bool) or not isinstance(data_mode, int) or data_mode not in (1, 2):
        raise ValueError("The preceding data track must use Mode 1 or Mode 2")
    if not callable(getattr(device, "read_expected_data", None)):
        reject("Typed data reads are unavailable; the pregap cannot be established.")
    if not callable(getattr(device, "read_subq", None)):
        reject("Formatted Q reads are unavailable; the pregap cannot be corroborated.")

    probes = {}

    def probe(lba):
        cancel()
        if lba in probes:
            return probes[lba]["kind"]
        try:
            sector = device.read_expected_data(lba, mode=data_mode, count=1)
        except RawAcquisitionError as error:
            definite_mismatch = isinstance(error, ScsiReadError) and error.reason == "sector_type_mismatch"
            original_sector = getattr(error, "raw_sector", None)
            ignored_expected_type = (
                error.reason == "unsupported_raw_sector" and
                getattr(error, "typed_read_succeeded", False) is True and
                getattr(error, "requested_lba", None) == lba and
                getattr(error, "requested_mode", None) == data_mode and
                isinstance(original_sector, bytes) and len(original_sector) == SECTOR_BYTES)
            if not definite_mismatch and not ignored_expected_type:
                raise
            # Only definite wrong type or an explicitly successful full read
            # whose header validation failed permits the audio probe. A plain
            # unsupported_raw_sector label alone proves no successful transfer.
            read_audio = getattr(device, "read_cdda", None)
            cancel()
            try:
                sector = (read_audio(lba, count=1) if callable(read_audio) else
                          device.read_sectors(lba, 1, CDDA))
            except ScsiReadError as audio_error:
                if not ignored_expected_type or audio_error.reason != "sector_type_mismatch":
                    raise
                # A single unidentified main-channel transition may be kept
                # unchanged. It is neither decoded data nor accepted CDDA.
                # It must reproduce exactly and is only provisional until the
                # final adjacent data/CDDA and exact-Q checks below succeed.
                if lba >= toc_index01_lba or any(p["kind"] == "opaque_transition" for p in probes.values()):
                    reject("More than one opaque transition sector or an opaque program sector is unsupported.",
                           "unsupported_pregap_evidence")
                cancel()
                repeated = device.read_sectors(lba, 1, RAW_WITH_SUBCODE)
                if not isinstance(repeated, bytes) or len(repeated) != SECTOR_BYTES:
                    reject("The opaque transition repeat was incomplete.", "short_read")
                if repeated != original_sector:
                    reject("The opaque pregap sector changed between full reads; no bytes were selected or repaired.",
                           "unstable_pregap")
                cancel()
                frame = parse_formatted_q(device.read_subq(lba, mode=RAW_WITH_SUBCODE))
                positions = [p for p in frame.positions if p.track == next_track_number and
                             p.index == 0 and not p.control & 4 and p.absolute_lba in (lba, lba+1)]
                if not frame.crc_valid or frame.adr != 1 or not positions:
                    reject("An opaque transition lacks CRC-valid next-track audio INDEX 00 Q correlation; "
                           "arbitrary bad headers cannot become pregap sectors.", "unsupported_pregap_evidence")
                item = {"lba": lba, "kind": "opaque_transition",
                        "classification": "opaque_index00_transition_main_channel",
                        "data_read": "successful_full_typed_request_invalid_header",
                        "audio_read": "typed_cdda_sector_type_mismatch",
                        "any_read": "identical_full_main_channel_repeat",
                        "sha256": hashlib.sha256(original_sector).hexdigest(),
                        "encoding": "unidentified_no_decoding_or_repair",
                        "q_classification": frame.diagnostic(lba),
                        "q_classification_address_deltas": sorted({p.absolute_lba-lba for p in positions}),
                        "q_uncertainty": "Q is classification evidence only; following CDDA requires exact Q."}
                probes[lba] = item
                cancel()
                return item["kind"]
            if not isinstance(sector, bytes) or len(sector) != SECTOR_BYTES:
                reject("Typed CDDA returned a short sector; no padding was substituted.", "short_read")
            # A drive that ignores expected type must not turn a recognizable
            # data sector into accepted audio merely because the CDB succeeded.
            sync = b"\0" + b"\xff" * 10 + b"\0"
            if sector[:12] == sync:
                reject("Typed CDDA returned a data-sector synchronization header; the boundary is ambiguous.",
                       "ambiguous_track")
            item = {"lba": lba, "kind": "audio", "data_read": "sector_type_mismatch",
                    "audio_read": "typed_cdda_accepted", "sha256": hashlib.sha256(sector).hexdigest()}
            if ignored_expected_type:
                if sector != original_sector:
                    reject("Audio and successful invalid-header reads differ; the boundary is uncertain.",
                           "unsupported_pregap_evidence")
                cancel()
                frame = parse_formatted_q(device.read_subq(lba, mode=CDDA))
                expected_index = 0 if lba < toc_index01_lba else 1
                positions = [p for p in frame.positions if p.track == next_track_number and
                             p.index == expected_index and not p.control & 4 and
                             p.absolute_lba in (lba, lba + 1)]
                if not frame.crc_valid or frame.adr != 1 or not positions:
                    reject("Successful invalid-header reads lack CRC-valid neighboring audio Q correlation; "
                           "data corruption cannot be reclassified as a pregap.", "unsupported_pregap_evidence")
                item.update(data_read="successful_full_typed_request_invalid_header",
                            expected_sector_type_ignored=True, audio_bytes_match_invalid_header_read=True,
                            q_classification=frame.diagnostic(lba),
                            q_classification_address_deltas=sorted({p.absolute_lba - lba for p in positions}))
                # A +1 Q frame can classify this returned audio, but never moves
                # the boundary or satisfies the later exact-position checks.
            diagnostic = getattr(error, "diagnostic", None)
            if diagnostic:
                item["data_read_diagnostic"] = diagnostic
        else:
            # Validate ourselves as well as requiring the typed adapter call.
            if not isinstance(sector, bytes) or len(sector) != SECTOR_BYTES:
                reject("Typed data returned a short sector; no padding was substituted.", "short_read")
            if _data_mode(sector, lba) != f"MODE{data_mode}/2352":
                reject("Typed data returned a different sector mode.", "unexpected_data_mode")
            item = {"lba": lba, "kind": "data", "data_read": "typed_data_accepted",
                    "sha256": hashlib.sha256(sector).hexdigest()}
        probes[lba] = item
        cancel()
        return item["kind"]

    last = toc_index01_lba - 1
    if probe(last) != "audio":
        reject("The preceding TOC tail is valid typed data; an audio pregap was not established.")
    low = max(data_start_lba, toc_index01_lba - max_gap - 1)
    if low >= last or probe(low) != "data":
        reject("No typed data anchor exists within the bounded pregap search window.")
    high = last
    while high - low > 1:
        cancel()
        middle = (low + high) // 2
        if probe(middle) == "data":
            low = middle
        else:
            high = middle
    boundary = high
    if toc_index01_lba - boundary > max_gap:
        reject("The observed audio tail exceeds the bounded pregap search window.")
    if probe(boundary - 1) != "data" or probe(boundary) not in ("audio", "opaque_transition"):
        reject("Adjacent typed data/audio sectors did not establish one transition.")
    if probe(toc_index01_lba) != "audio":
        reject("The next TOC track does not begin with accepted typed CDDA.")
    if boundary + 1 >= toc_index01_lba:
        reject("There is no sector after the candidate boundary to corroborate INDEX 00.")
    opaque = [item for item in probes.values() if item["kind"] == "opaque_transition"]
    if opaque and (len(opaque) != 1 or opaque[0]["lba"] != boundary or probe(boundary + 1) != "audio"):
        reject("An opaque sector is allowed only at the single data-to-CDDA transition.",
               "unsupported_pregap_evidence")
    if any((item["kind"] == "data") != (lba < boundary) for lba, item in probes.items()):
        reject("Typed probes reveal more than one data/audio transition.", "ambiguous_track")

    q_probes = []

    def q_position(lba, index, *, required=False):
        cancel()
        # A read failure is a real error, not an invalid frame to skip.
        raw = device.read_subq(lba, mode=RAW_WITH_SUBCODE if opaque and lba == boundary else CDDA)
        frame = parse_formatted_q(raw)
        item = {"requested_lba": lba, "expected_track": next_track_number,
                "expected_index": index, **frame.diagnostic(lba)}
        try:
            position = require_position(frame, lba, track=next_track_number, index=index)
            if position.control & 4:
                raise QEvidenceError("Q marks the expected audio position as data")
        except QEvidenceError as error:
            item.update(accepted=False, rejection=str(error))
            q_probes.append(item)
            # Exact-address, CRC-valid contradictory track/index is stronger
            # evidence than a neighboring-sector frame and must not be ignored.
            contradictory = (frame.crc_valid and frame.adr == 1 and
                             any(p.absolute_lba == lba for p in frame.positions))
            if required or contradictory:
                reject("Formatted Q does not corroborate the observed pregap: " + str(error),
                       "unsupported_pregap_evidence")
            return None
        item.update(accepted=True, position=asdict(position))
        q_probes.append(item)
        cancel()
        return position

    index01 = q_position(toc_index01_lba, 1, required=True)
    corroborating = []
    sample_lbas = dict.fromkeys((boundary, boundary + 1, (boundary + last) // 2, last))
    for lba in sample_lbas:
        position = q_position(lba, 0, required=bool(opaque and lba == boundary + 1))
        if position is not None and lba > boundary:
            corroborating.append(lba)
    if not corroborating:
        reject("No exact-address, CRC-valid INDEX 00 frame after the main-sector boundary was observed.",
               "unsupported_pregap_evidence")
    cancel()
    evidence = {
        "method": "main_sector_transition_corroborated_by_exact_q",
        "boundary_basis": "Adjacent valid data headers and accepted CDDA, with at most one reproducible opaque main sector between them. Opaque sectors require audio INDEX 00 Q correlation and immediately following exact-address audio Q. No encoding or repair is inferred.",
        "archival_calibrated": False,
        "index00_claim": "Inferred main-sector header/type boundary, not calibrated raw-subchannel INDEX 00 timing.",
        "device_ignored_expected_sector_type": any(item.get("expected_sector_type_ignored") for item in probes.values()),
        "data_mode": data_mode,
        "search_max_gap": max_gap,
        "search_data_anchor_lba": max(data_start_lba, toc_index01_lba - max_gap - 1),
        "last_data_lba": boundary - 1,
        "first_audio_lba": boundary + 1 if opaque else boundary,
        "toc_index01_lba": toc_index01_lba,
        "observed_gap_sectors": toc_index01_lba - boundary,
        "q_exact_index00_after_boundary_lbas": corroborating,
        "q_index01": asdict(index01),
        "typed_probes": [probes[lba] for lba in sorted(probes)],
        "opaque_transition_sectors": [dict(item) for item in opaque],
        "pregap_read_mode": "mixed_cdda_with_one_opaque_any_sector" if opaque else "typed_cdda",
        "q_probes": q_probes,
        "limits": ["No Q offset was applied; neighboring Q never supplies exact-position corroboration or moves LBAs.",
                   "No padding, sector rewriting, relative-Q countdown inference or assumed gap length was used.",
                   "Bounded probes do not prove an entire extent homogeneous; acquisition must validate every sector.",
                   "Opaque transition bytes, if present, have unidentified encoding and must match the planned SHA on every source pass.",
                   "No audio-offset calibration, subchannel preservation or archival-exact claim."],
    }
    return PregapBoundary(boundary, toc_index01_lba, evidence)
