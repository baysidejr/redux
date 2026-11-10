# Read-only CD main-channel acquisition

The raw_acquisition service preserves supported driver-returned 2352-byte main sectors in a BIN and observed TOC starts in a CUE. It is separate from the cooked ISO9660 exporter. The native default now uses bounded MMC READ CD through scsi_cd.WindowsScsiCdDevice. A corroborated data-to-audio transition may also produce an **inferred** audio INDEX 00.

Physical capture succeeded on the attached Saturn disc: both complete source passes and output read-back matched. Comparison with an independent known-good image remains pending.

## Supported scope

The medium must report CD-ROM profile 0x0008, one complete session numbered 1, a trustworthy complete TOC, first track 1 at LBA 0, increasing tracks and a bounded lead-out. Session and track TOCs must agree. Pressed data, two-channel audio and qualifying mixed layouts are supported. CD-R/RW is refused because the limited session query cannot establish the absence of another unfinished session. DVD/Blu-ray, unknown profiles, multisession, four-channel audio and unsupported control flags are refused.

Transfers must return exactly the requested bytes. Ordinary data sectors require the CD sync pattern, expected BCD MSF address for the requested LBA and a consistent Mode 1 or Mode 2 header. They become MODE1/2352 or MODE2/2352 in the CUE. Ordinary audio sectors reject recognizable data synchronization headers. A generic I/O error or short transfer never establishes that a sector is audio.

There is no cooked ISO fallback, padding, sector repair, silence filling, drive-offset database, C2 recovery or format conversion. Full-disc, protection-preserving and subchannel-preserving requests are explicitly rejected. Hidden pre-track/lead-in audio, lead-out contents, CD-TEXT and ISRC/catalog data are not captured. There is no continuous raw P-W subchannel stream or .sub file; selected formatted-Q frames are retained only as manifest evidence.

Repeated matching reads establish observed consistency, not EDC/ECC correctness, audio-offset calibration, jitter correction, independence from the drive cache or universal console compatibility. The TOC cannot establish that subchannel-dependent protection is absent.

## Native commands and Windows access

WindowsScsiCdDevice opens the optical class-device path with **GENERIC_READ | GENERIC_WRITE** (0xC0000000), share read/write and OPEN_EXISTING. These handle access rights are required for the pass-through IOCTL. The implementation issues only **data-IN READ CD** commands, never disc-write/data-OUT commands. The distinction between handle access and transfer direction follows the [Windows pass-through structure](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddscsi/ns-ntddscsi-_scsi_pass_through_direct).

The pass-through CDB whitelist consists solely of 12-byte MMC READ CD, opcode 0xBE, through IOCTL_SCSI_PASS_THROUGH_DIRECT, with DataIn always SCSI_IOCTL_DATA_IN. Metadata uses inherited TOC/session, current-profile and media-change IOCTLs plus a storage-adapter property query for alignment/transfer limits. No eject, tray lock, authentication, filesystem lock, mount, speed/streaming or mode-changing commands, driver installation or automatic elevation is used.

| Read request | Expected sector type | Main-channel selection | Exact returned size |
| --- | --- | --- | --- |
| Ordinary data inspection | Any | Sync, headers, user data and EDC/ECC, byte 9 = 0xF8 | 2352 bytes/sector |
| Typed data probe | Mode 1 or Mode 2 formless | The same complete main-channel fields | 2352 bytes/sector |
| Typed audio | CD-DA | Audio user data, byte 9 = 0x10 | 2352 bytes/sector |
| Formatted-Q evidence | Any or CD-DA | Main channel plus formatted-Q selector 2 | One sector only: 2352 + 16 = 2368 bytes |

Mode 2 formless is drive-dependent and does not promise XA Form 1/2 support. C2 selection and DAP stay zero: no audio mute/interpolation is requested. The internal RAW_WITH_SUBCODE mode identifier selects a data request in this adapter; its ordinary SCSI reads request **no subchannel bytes**. The reported read_method is scsi_read_cd_2352_main_channel.

Reads contain 1–16 bounded sectors, honor adapter maximum transfer length, and stop at track/read-mode boundaries. Buffers satisfy the adapter alignment mask and are at least page-aligned. Windows DWORD fields are fixed 32-bit; pointers use native width. Each command requests a 10-second timeout. Cancellation does not abort an outstanding kernel request.

The adapter validates the returned pass-through structure length, bounded sense length and reported availability, zero SCSI status and **exact DataTransferLength**. IOCTL success with CHECK CONDITION remains failure. Windows errors and fixed/descriptor sense fields remain diagnostic evidence. Medium/hardware errors, unsupported commands and invalid fields are not reclassified as wrong-sector-type responses. [Microsoft documents adapter alignment and separate structure/data-transfer lengths](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddscsi/ni-ntddscsi-ioctl_scsi_pass_through_direct).

The older WindowsRawCdDevice remains available through explicit device injection. It uses GENERIC_READ and IOCTL_CDROM_RAW_READ: data requires exactly 2448 RawWithSubCode bytes/sector and retains the first 2352; CDDA requires exactly 2352. Its RAW_READ_INFO disk offset remains LBA × 2048. Without typed/Q evidence methods it retains strict TOC-only behavior. The native default does not automatically fall back to this adapter after SCSI failure; the legacy adapter does not try alternate modes or cooked reads.

## Bounded audio pregap evidence

Ordinary layouts retain TOC INDEX 01 only. The resolver runs only when the last sector of a TOC data extent fails ordinary header validation, the following TOC track is audio and typed-data/Q methods are available. It does not insert a conventional 150-sector gap or derive a boundary from silence.

The search is bounded to at most 7500 candidate pregap sectors plus one data anchor. Typed reads locate adjacent valid active-data and accepted CDDA sectors. A definite SCSI wrong-sector-type response can permit a typed CDDA probe. For drives ignoring expected data type, the alternative evidence requires an explicitly successful full typed transfer with invalid header, identical complete bytes from accepted typed CDDA and CRC-valid next-track audio Q correlation. A generic unsupported_raw_sector label alone is insufficient. Medium/hardware errors, unsupported commands and short reads continue to fail.

Formatted Q retains both BCD and binary interpretations where possible, checks complemented CRC-16/CCITT, ADR 1, track/index and absolute address. Classification evidence may refer to the requested LBA or its immediate +1 neighbor. This **does not** move the main-sector boundary or calibrate a drive offset. Final corroboration requires exact-address, CRC-valid audio INDEX 00 after the boundary and exact-address INDEX 01 at the TOC start. Exact-address contradictory Q rejects the inference. Relative countdowns are diagnostic, not a timing correction.

A single narrow opaque transition exception is supported:

- Exactly one unidentified sector may occur between the last valid data sector and accepted CDDA. It must be the candidate audio INDEX 00 boundary, never an arbitrary program-data sector.
- A successful complete typed-data request must have produced an invalid header; typed CDDA must specifically report sector-type mismatch. An Any main-channel repeat must return identical 2352 bytes.
- CRC-valid next-track audio INDEX 00 Q must correlate with that sector or its +1 neighbor. The immediately following accepted CDDA sector requires **exact-address** INDEX 00 Q; the TOC INDEX 01 also requires an exact match.
- Bytes are retained unchanged, with their encoding unidentified. No decoding, repair or selected-byte replacement occurs. The plan records their SHA-256 and both full source passes must reproduce it. Capture reads the exceptional sector separately in Any mode; surrounding audio remains typed CDDA.

More opaque sectors, an opaque sector away from the transition, changed bytes, uncorroborated Q and unrelated read errors are rejected. Bounded probes cannot prove the entire extent homogeneous: capture validates all ordinary sectors and recorded opaque hashes. Capture extents must remain positive and continuous from LBA 0 through lead-out, without skipped, duplicated or synthesized sectors.

The emitted INDEX 00 is an **inferred main-channel header/type transition**, not calibrated raw-subchannel timing. The manifest records this uncertainty, typed probes and accepted/rejected Q diagnostics. Neighboring Q is never shifted to manufacture an exact-position match.

## API and output

~~~python
plan = plan_raw_capture("E:")
result = capture_raw_cd("E:", r"D:\Captures\New Disc")
# Optional explicit permanent import; capture does not import by itself:
library.import_game(result.cue_path)
~~~

Planning reads metadata, track-boundary sectors and, when needed, bounded pregap evidence. It never starts full capture. Capture reopens and reassesses the drive; a plan cannot promise every later sector is readable. Play Disc separately uses a temporary-disk lease; Install is an explicit library operation.

RawCapturePlan includes drive, tracks, sector/byte counts, TOC SHA-256, media-change counter, warnings, mode, read_method and pregap_evidence. RawTrack records number/control, kind, CUE mode, capture start/end LBAs and read mode. When an inferred INDEX 00 moves the audio capture extent earlier, index01_lba retains the TOC start and opaque_pregap_sectors records any permitted boundary exception. Other tracks keep index01_lba=None and use their capture start as INDEX 01.

capture_raw_cd requires a **new destination directory**, not an existing folder or .iso file:

~~~text
New Disc/
    disc.bin
    disc.cue
    manifest.json
~~~

The bundle is staged beside that directory and published by a no-overwrite rename. Existing destinations, including a racing writer's destination, are retained. Failed/cancelled work cleans only its private staging directory. Cleanup checks the resolved direct-parent location and refuses a redirected stage before recursive removal.

The flushed BIN is read back for size/SHA-256 verification, then all source sectors are read again. Per-track and combined hashes must agree; opaque sectors additionally match their planned hash. Profile, TOC/session and media-change counter are rechecked through the persistent handle. CUE and manifest are also read back. The manifest records read method/scope, hashes, TOC/session bytes, counter, pregap/Q/opaque evidence and limitations. Agreement is not independent validation of the disc interpretation.

RawCaptureResult exposes cue_path, bin_path, manifest_path, output_directory, bytes_written, sha256, source_sha256, tracks, verified_source_passes=2, verified_output=True, image_kind, warnings, pregap_evidence and read_method. output_path aliases the CUE. The current image_kind remains cd_main_channel_bin_cue_toc_index01; inspect pregap_evidence and manifest scope to determine whether inferred INDEX 00 was included.

Both APIs accept cancelled with is_set(). Capture accepts progress(done, total, phase): total is three times BIN size; phases are write, verify_output and verify_source. Planning/metadata probes are outside that total. Cancellation is checked between synchronous reads; a blocked driver call must return first. require_subchannels, require_protection or require_full_disc fails before drive access when true. device_factory remains the injection seam; the native default is WindowsScsiCdDevice.

## Recorded physical evidence

On 2026-11-08 the attached G: disc identified from its header as Saturn **World Series BASEBALL 2**, product **MK-81113**, with profile 0x0008. Existing local evidence:

- [Legacy raw-read probe](../analysis/emulator_alignment/raw-read-probe.json): bounded read/query observations and header identity.
- [READ CD probe](../analysis/emulator_alignment/scsi-read-probe.json): six bounded main-sector reads, exact return sizes, adapter alignment/transfer limits and unchanged media snapshot.
- [Boundary probe](../analysis/emulator_alignment/pregap-boundary-probe.json): main-sector/Q observations near the data/audio transition.
- [Resolved capture plan](../analysis/emulator_alignment/resolved-pregap.json): 130741 sectors / 307502832 main-channel bytes. Data spans [0, 126859), audio [126859, 130741), TOC audio INDEX 01 is 127009, and one reproducible opaque sector is recorded at 126859.

The observed 150-sector gap is specific to this disc, not a default rule. The opaque sector's Q described LBA 126860 (+1): it supplied classification evidence only; following exact-address Q supplied corroboration.

The complete capture then succeeded in 293.96 seconds. Both source passes and the saved BIN matched at **307502832 bytes**, SHA-256 `2ff8d56470957da4d5c7644c25619ce1bdb8d1b539196656be077e1a2775d50f`. The bundle was published under `analysis/emulator_alignment/saturn-world-series-baseball-2/`; [the acceptance record](../analysis/emulator_alignment/physical-capture-acceptance.json) and its manifest retain the exact plan, evidence and warnings. This establishes verified main-channel capture for this drive/disc combination.

Independent known-good image comparison, audio-offset/jitter calibration, other USB/internal drives and representative discs, unusual pregaps and removal/read-error acceptance remain pending. Emulator boot and firmware acceptance are separate checks.

## Verification

These suites use injected media/native calls and do not access a real optical drive:

~~~powershell
python -m unittest tests.test_raw_acquisition tests.test_scsi_cd tests.test_cd_pregap -v
~~~

They cover BIN/CUE round trips and verification, typed CDBs, native pointer/DWORD layout/alignment, exact transfer/sense bounds, wrong-type versus real-error classification, Q CRC/BCD/binary interpretation, bounded search/rejections, unchanged opaque bytes, cancellation, changed media, existing/racing destinations and explicit scope refusal. Injected success remains separate from physical planning and pending full-capture/independent-image acceptance.

## Primary references

- [Microsoft IOCTL_SCSI_PASS_THROUGH_DIRECT](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddscsi/ni-ntddscsi-ioctl_scsi_pass_through_direct)
- [Microsoft SCSI_PASS_THROUGH_DIRECT](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddscsi/ns-ntddscsi-_scsi_pass_through_direct)
- [Microsoft storage-adapter descriptor](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddstor/ns-ntddstor-_storage_adapter_descriptor)
- [Microsoft SDK SCSI IOCTL definitions/access rights](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/shared/ntddscsi.h)
- [Microsoft IOCTL_CDROM_RAW_READ](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddcdrm/ni-ntddcdrm-ioctl_cdrom_raw_read)
- [Microsoft RAW_READ_INFO](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddcdrm/ns-ntddcdrm-__raw_read_info)
- [Microsoft TRACK_MODE_TYPE](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddcdrm/ne-ntddcdrm-_track_mode_type)
- [Microsoft TOC query/session formats](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddcdrm/ns-ntddcdrm-_cdrom_read_toc_ex)
- [Microsoft session result semantics](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddcdrm/ni-ntddcdrm-ioctl_cdrom_get_last_session)
- [Microsoft current-profile input structure](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddmmc/ns-ntddmmc-_get_configuration_ioctl_input)
- [Microsoft media-change verification](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddstor/ni-ntddstor-ioctl_storage_check_verify)
- [Microsoft SDK CD-ROM header/constants](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/shared/ntddcdrm.h)
- [Microsoft SDK multimedia profile constants](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/shared/ntddmmc.h)
