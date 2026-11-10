# Core and configuration alignment with reference application

Audit date: 2026-11-08. This compares the observed Redux 0.4.0 baseline with the supplied reference application Windows 0.7.25.19 executable. The table preserves that baseline; the following section records implementation in Redux 0.5.0. Machine-readable observations are in `analysis/emulator_alignment/config-matrix.json`.

## Implemented in Redux 0.5.0

0.5.1 follow-up: the default BIOS folder is now detected automatically. User-supplied standard Saturn BIOS hashes match; the captured World Series Baseball 2 disc boots into its animated intro through Beetle Saturn and passes a native state round-trip. Evidence is under `analysis/firmware_setup/saturn/`. The initial firmware blockers in the baseline table below are historical; wider gameplay/audio/controller compatibility still requires acceptance.

The workspace now contains public Beetle Saturn, Supafaust, full Beetle PCE, Mupen64Plus-Next, MAME and PicoDrive cores alongside the existing Mesen, Beetle PSX, Genesis Plus GX, Gambatte and compatibility alternatives. Upstream provenance, versions, hashes and licenses are under `tools/cores/`. These files are local optional dependencies; the portable frontend release does not bundle them.

Each of the six added DLLs completed isolated native initialization, option enumeration and shutdown. Every requested profile option was offered by that installed DLL. This validates the requested keys/values, not game compatibility. Evidence: `analysis/emulator_alignment/native-core-probes.json`.

The host now validates explicit overrides against declared core options, serves analog sticks and trigger buttons, and routes input through the common pause/menu barrier. Software cores are selected automatically for the mapped systems; N64 and MAME remain explicit compatibility choices. NES and PSX have recorded game acceptance. Diablo boots inside Redux through Beetle PSX with an empty firmware directory and that build's OpenBIOS fallback; native save/load reproduced the subsequent frame exactly. Desktop checks also verified its shared pause menu, dated thumbnail and state restore. Saturn still requires compatible firmware and game acceptance.

## Baseline and matching targets

| System | Original executable evidence | Redux baseline observed | Matching direction |
| --- | --- | --- | --- |
| NES | Mesen-family hook, load `0x140383180` | Mesen libretro DLL; validated integrated default | Keep hosted Mesen; exact original revision remains unknown. |
| SNES | Mednafen hook explicitly selects `snes_faust` | Snes9x DLL installed; standalone Mesen launcher registered | Use the Faust module/compatible public port for closer alignment. Beetle bSNES is a different Mednafen module. |
| PlayStation | Mednafen hook selects `psx` | Standalone PCSX-Redux used for the verified Diablo boot; software Beetle PSX DLL installed but not accepted with a game | Move normal playback into hosted Mednafen-derived Beetle PSX and validate it. |
| Saturn | Mednafen hook selects `ss` | No Saturn core in the initial inventory; physical disc identification works | Hosted Beetle Saturn is the matching public family; BIOS/backend acceptance remains separate. |
| PC Engine / CD / SuperGrafx | Mednafen hook selects `pce`; `pce.forcesgx` is conditional for SuperGrafx | PCE Fast DLL installed; no SuperGrafx core mapping | Full Beetle PCE is the closer module match. PCE Fast is related but is a different implementation. |
| Genesis / 32X / Sega CD and further Sega IDs | Dispatcher selects a hook named `Kega` | Genesis Plus GX DLL installed; no integrated 32X mapping | Keep the engine identity qualified: the hook consumes `genesis_plus_gx_region_detect`, but its RTTI name does not prove stock Kega Fusion or Genesis Plus GX for every branch. |
| Nintendo 64 | M64P hook, native plugins and shared input callbacks | No N64 core installed in the initial inventory | Mupen64Plus-family integration is required; original custom plugin paths are not portable to a stock Windows build. |
| Neo Geo CD / Atari 2600 / Atari 7800 and additional IDs | MAME hook chooses drivers including `neocdz`, `a2600[p]`, `a7800[p]`, plus Vectrex/Lynx/arcade paths | No MAME core installed in the initial inventory | Use a matching public MAME build and validate driver/content/host requirements. A different MAME revision is not interchangeable by name alone. |
| GB / GBC | Original dispatch identity not established by the inspected catalogue map | Gambatte DLL installed; standalone Mesen launchers registered | Extra Redux support; do not count it as proven reference application alignment. |

Only NES was in `VALIDATED_DEFAULTS` at audit start. Other installed DLLs had metadata/loading observations, not complete gameplay acceptance. Registered standalone launchers were Mesen for NES/SNES/GB/GBC/PCE/SMS/Game Gear and PCSX-Redux for PSX. No `integrated` overrides or firmware directory were configured in the observed settings file.

The exact original branches are: Mesen `1,18`; Mednafen `2,9,11,14,15,17`; M64P `3`; Kega-named hook `4,5,6,7,8,19,20`; MAME `12,13,16,23,26,33,34`; test/fake hook `24`. Friendly labels for all numeric IDs have not been established. The Mednafen loader also contains a PC-FX case, which is not itself proof of an explicit PC-FX branch in the inspected dispatcher.

The implementation target is now recorded in `redux/core_profiles.py`: immutable public core profiles, original hook/known IDs, matching limits, and conservative requested defaults. `get_profile(system)` returns a profile; `default_options(system)` returns a fresh dictionary for the host to validate and apply. This module does not install cores, configure an external emulator, or promote a system to accepted playback by itself. Its matching levels explicitly distinguish an upstream module, family, qualified replacement, replacement, and extra Redux support.

## Configuration values we can reconstruct

The original load methods set options programmatically. There is no single recovered stock configuration file that can be copied into every public core.

| Original setting / behavior | Translation assessment |
| --- | --- |
| PSX `psx.slstartp=11`, `psx.slendp=280` | Public software Beetle PSX declares PAL initial/last-scanline options accepting 11/280. Validate actual declared options in the installed binary before applying the corresponding `beetle_psx_initial_scanline_pal` / `beetle_psx_last_scanline_pal` values. |
| PSX `psx.h_overscan=0` | Means hide horizontal overscan. It must not be translated to “disable cropping” merely because the original value is zero. Current software Beetle's horizontal crop mode is `beetle_psx_crop_overscan=static`; check the selected revision and resulting image. |
| PSX/Saturn/SNES/PC-FX multitap flags | Derived from the game's player count. PSX ports use thresholds above two and above four players. Preserve the conditional policy; do not enable every multitap for every game. Stock libretro controller-device and multitap APIs/options need explicit adaptation. |
| PSX `psx.memcards_sacred=0` | Memory-card/state behavior is meaningful, but shared card ownership, persistent files and state restore must be adapted to Redux. Copying paths would mix independent save namespaces. |
| Saturn `ss.h_overscan=1` | Original shows horizontal overscan. Current public `beetle_saturn_horizontal_overscan=0` disables the horizontal mask and is the requested translation. Validate resulting geometry in the installed build. |
| Saturn `ss.cart.auto_default=extram4` | A default for automatic cartridge selection is not equivalent to forcing a 4MB cartridge for all games. Preserve per-game ROM-cart requirements and the public core's supported choices. |
| Saturn `ss.affinity.vdp2=0xA` | Host/thread affinity policy; do not carry this machine-specific mask into a different PC automatically. |
| PCE CD PSG/ADPCM 125%, CDDA 150% | Current public Beetle PCE options use 10-point increments. 150 is expressible; 125 is not a declared value. Keep the mismatch explicit rather than claiming exact reproduction through rounding. |
| SuperGrafx `pce.forcesgx=1` only for its system ID | Translate to the selected public core's actual SuperGrafx support/device policy, not to every PCE title. |
| SNES `snes_faust.h_filter=phr256blend_512` | Public Supafaust declares `supafaust_h_filter=phr256blend_512`. This is Faust-specific high-resolution handling, separate from Redux's presentation filter. |
| Sega `genesis_plus_gx_region_detect` with `ntsc-u`, `ntsc-j`, `pal` | Same public option spelling exists, but apply region from the game/profile and verify branch behavior. The hook also contains 32X-specific and other native load paths. |

Public option keys and supported values can change. The 0.5.0 host enumerates and validates actual declarations before applying requested values; absent keys and unsupported values fail explicitly. The 0.4.0 baseline selected only the default value.

For the current software video host, the N64 profile requests `mupen64plus-rdp-plugin=angrylion` and `mupen64plus-rsp-plugin=cxd4`. The public source declares these values only when built with `HAVE_THR_AL` and `HAVE_LLE`. They must be present in the installed core's runtime declarations; unsupported selections must not silently revert to a GPU renderer. This is a public software plugin selection, not an exact copy of reference application's custom plugin configuration.

## Files and settings that are not drop-in configurations

- Original PS1 firmware settings name `scph5500.bin`, `scph5501.bin`, `scph5502.bin`. That establishes filenames, not validated firmware availability. Current public Beetle PSX source has an OpenBIOS override/fallback path; using that is an independent usability choice and must be checked against the actual DLL and game.
- Original Saturn points both regions at `fekacronus.bin`; PCE CD references `rbx_pce_cd_bios_prealpha.pce` and `gecard.pce`; PC-FX references `pcfx.rom`. These names are evidence, not confirmation that custom blobs work in stock cores.
- The Sega hook uses `cart.rbxb`, `cdbram.rbxb`, `cdcart.rbxb`, shared-game identifiers, and `rbx_mcd_bios.bin`. Preserve Redux's own safe save namespaces and adapt formats deliberately.
- M64P defaults include `mupen64plus-video-glide64mk2-reference.so`, audio/input/RSP reference application `.so` plugins and a paraLLEl RSP load path. These are custom/native plugin references, not usable Windows DLL paths or a proven complete Windows plugin selection.
- `analysis/formats/decoded/data/mame-controller.cfg` contains MAME `JOYCODE_*` / `KEYCODE_*` sequences. `gamepadconfigs/*.config` and `layouts/*.config` describe original host device/button/icon mappings. They need semantic translation into Redux's SDL player model; raw indices and original host bindings are not portable controller configuration for every public emulator.
- `data/mame-hash/neocd.xml`, `neogeo.xml` and MAME driver identifiers are content/software-list metadata. Their presence does not establish compatibility with arbitrary MAME revisions or images.

## Evidence

Original binary SHA256: `b6bd9f65f5b7dcda8f4df824092f67daf261cd6c45204c553d9c3a0b60759b77`. Dispatcher `0x140121ee0`; loads Mesen `0x140383180`, Mednafen `0x14037a890`, Kega-named `0x1403566c0`, MAME `0x14035f390`, M64P `0x140388f10`. Native configuration literals were checked against adjacent decompiled setter calls and decoded directly from the local PE where necessary.

Local references: `analysis/features/core_dispatch.json`, `core_decompiles.txt`, `function_decompiles.txt`, `core_function_literals.json`, `findings.txt`, and `analysis/formats/decoded/data/`.

Public primary references: [Beetle PSX options](https://raw.githubusercontent.com/libretro/beetle-psx-libretro/master/libretro_core_options.h), [PSX implementation](https://raw.githubusercontent.com/libretro/beetle-psx-libretro/master/libretro.c), [Beetle Saturn options](https://raw.githubusercontent.com/libretro/beetle-saturn-libretro/master/libretro_core_options.h), [Beetle PCE options](https://raw.githubusercontent.com/libretro/beetle-pce-libretro/master/libretro_core_options.h), [Supafaust identity](https://github.com/libretro/supafaust), [Genesis Plus GX region option](https://github.com/libretro/docs/blob/master/docs/library/genesis_plus_gx.md). These were consulted on the audit date; an upstream HEAD is not a pin for an installed binary.

Additional option declarations: [Supafaust settings](https://raw.githubusercontent.com/libretro/supafaust/master/libretro.cpp), [Mupen64Plus-Next options](https://raw.githubusercontent.com/libretro/mupen64plus-libretro-nx/master/libretro/libretro_core_options.h), and [Mupen64Plus option prefix](https://raw.githubusercontent.com/libretro/mupen64plus-libretro-nx/master/custom/mupen64plus-next_common.h).
