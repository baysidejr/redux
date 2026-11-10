"""Public core choices aligned with the recovered original dispatcher.

These are managed host profiles, not copies of reference application's custom hooks.  A
matching family is not a claim of matching source revision or game acceptance.
The host must validate requested options against the installed core's declared
keys/values; optional plugins in particular depend on the actual build.

Evidence and untranslated settings: docs/CORE_CONFIG_ALIGNMENT.md and
analysis/emulator_alignment/config-matrix.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class CoreProfile:
    system: str
    filename: str
    family: str
    original_hook: str | None
    match_level: str
    default_options: Mapping[str, str] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    original_system_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        # A caller must not be able to change another session's defaults.
        object.__setattr__(self, "default_options", MappingProxyType(dict(self.default_options)))
        object.__setattr__(self, "blockers", tuple(self.blockers))
        object.__setattr__(self, "original_system_ids", tuple(self.original_system_ids))


_REVISION_UNKNOWN = "Original custom hook revision is unknown; upstream family is not an exact binary match."
_SEGA_QUALIFICATION = (
    "Original hook is named Kega and consumes genesis_plus_gx_region_detect; "
    "underlying engine identity is not proven for every load branch."
)
_MAME_BLOCKERS = (
    _REVISION_UNKNOWN,
    "Driver, ROM/software-list set, firmware and game acceptance require validation with this MAME revision.",
)

# PSX options/values verified in beetle-psx-libretro/libretro_core_options.h
# (software BEETLE_OPT prefix is beetle_psx_ in libretro_options.h).
# Original psx.h_overscan=0 hides horizontal overscan: "static" is horizontal
# cropping, whereas "disabled" would incorrectly expose those borders.
_PSX_OPTIONS = {
    "beetle_psx_initial_scanline_pal": "11",
    "beetle_psx_last_scanline_pal": "280",
    "beetle_psx_crop_overscan": "static",
}

# SNES: libretro/supafaust/libretro.cpp declares this exact key and value.
# It translates original snes_faust.h_filter, not the host's display filter.
_SNES_OPTIONS = {"supafaust_h_filter": "phr256blend_512"}

# Saturn: current beetle-saturn-libretro/libretro_core_options.h declares 0
# as disabled horizontal mask, preserving original ss.h_overscan=1 (visible).
# Do not force 4MB RAM: ss.cart.auto_default is not a forced cartridge choice.
_SATURN_OPTIONS = {"beetle_saturn_horizontal_overscan": "0"}

# Full Beetle PCE declares volume values in steps of 10. CDDA150 is exact;
# original CD PSG/ADPCM125 cannot be represented and are deliberately omitted.
_PCE_CD_OPTIONS = {"pce_cddavolume": "150"}

# Mupen64Plus-Next's custom/mupen64plus-next_common.h defines CORE_NAME as
# "mupen64plus". libretro/libretro_core_options.h declares these keys/values
# under HAVE_THR_AL and HAVE_LLE respectively. These are a software host
# selection, not a recovered exact reference application plugin configuration. The host
# must reject missing declarations instead of falling back to a GPU plugin.
_N64_SOFTWARE_OPTIONS = {
    "mupen64plus-rdp-plugin": "angrylion",
    "mupen64plus-rsp-plugin": "cxd4",
}

_profiles = (
    CoreProfile("nes", "mesen_libretro.dll", "Mesen", "mesen", "upstream_family",
                blockers=(_REVISION_UNKNOWN,), original_system_ids=(1,)),
    CoreProfile("snes", "mednafen_supafaust_libretro.dll", "Mednafen snes_faust / Supafaust",
                "mednafen", "upstream_module", _SNES_OPTIONS,
                (_REVISION_UNKNOWN, "Multitap is game-dependent; host must translate the player-count policy."), (2,)),
    CoreProfile("psx", "mednafen_psx_libretro.dll", "Mednafen PSX / Beetle PSX",
                "mednafen", "upstream_module", _PSX_OPTIONS,
                (_REVISION_UNKNOWN, "Validate regional firmware or the installed build's OpenBIOS fallback with the game.",
                 "Multitap and memory-card/state policy need host adaptation; original card paths are not portable."), (11,)),
    CoreProfile("saturn", "mednafen_saturn_libretro.dll", "Mednafen Saturn / Beetle Saturn",
                "mednafen", "upstream_module", _SATURN_OPTIONS,
                (_REVISION_UNKNOWN, "Compatible Saturn firmware is required; original fekacronus.bin is a custom filename.",
                 "Original auto-default 4MB cartridge and player-count multitap require per-game adaptation."), (9,)),
    CoreProfile("pce", "mednafen_pce_libretro.dll", "Mednafen PCE / Beetle PCE",
                "mednafen", "upstream_module", blockers=(_REVISION_UNKNOWN,), original_system_ids=(14,)),
    CoreProfile("pce_cd", "mednafen_pce_libretro.dll", "Mednafen PCE / Beetle PCE",
                "mednafen", "upstream_module", _PCE_CD_OPTIONS,
                (_REVISION_UNKNOWN, "Compatible CD firmware is required; original custom BIOS names are not stock configuration.",
                 "Original CD PSG/ADPCM125 volumes are not declared by public options (10-point steps)."), (15,)),
    CoreProfile("supergrafx", "mednafen_pce_libretro.dll", "Mednafen PCE / Beetle PCE",
                "mednafen", "upstream_module", blockers=(
                    _REVISION_UNKNOWN, "Original forcesgx flag is per system; verify public core content detection/device behavior."),
                original_system_ids=(17,)),
    CoreProfile("genesis", "genesis_plus_gx_libretro.dll", "Genesis Plus GX",
                "kega", "qualified_replacement", blockers=(_SEGA_QUALIFICATION,), original_system_ids=(5,)),
    CoreProfile("sega_cd", "genesis_plus_gx_libretro.dll", "Genesis Plus GX",
                "kega", "qualified_replacement", blockers=(
                    _SEGA_QUALIFICATION, "Regional firmware and CD backup/cart save formats require separate validation."),
                original_system_ids=(7,)),
    # Friendly labels for original Sega IDs 4/8/19/20 are not established.
    CoreProfile("sms", "genesis_plus_gx_libretro.dll", "Genesis Plus GX",
                "kega", "qualified_replacement", blockers=(
                    _SEGA_QUALIFICATION, "No numeric original catalogue ID has been established for this label.")),
    CoreProfile("game_gear", "genesis_plus_gx_libretro.dll", "Genesis Plus GX",
                "kega", "qualified_replacement", blockers=(
                    _SEGA_QUALIFICATION, "No numeric original catalogue ID has been established for this label.")),
    CoreProfile("32x", "picodrive_libretro.dll", "PicoDrive", "kega", "replacement",
                blockers=(_SEGA_QUALIFICATION,
                          "PicoDrive is an explicit replacement; Genesis Plus GX does not implement 32X."),
                original_system_ids=(6,)),
    CoreProfile("n64", "mupen64plus_next_libretro.dll", "Mupen64Plus-Next", "mupen64plus",
                "upstream_family", _N64_SOFTWARE_OPTIONS,
                (_REVISION_UNKNOWN, "Installed build must declare Angrylion RDP and CXD4 RSP; software support is conditional.",
                 "Original native custom plugin paths/configuration are not stock Windows libretro settings."), (3,)),
    CoreProfile("neo_geo_cd", "neocd_libretro.dll", "NeoCD", None, "redux_extension",
                blockers=("Original BIOS recommended; built-in HLE is core-version dependent.",)),
    CoreProfile("3do", "opera_libretro.dll", "Opera", None, "redux_extension",
                blockers=("Embedded Opera Boot is core-version dependent; some games require original firmware.",)),
    CoreProfile("atari2600", "mame_libretro.dll", "MAME", "mame", "upstream_family",
                blockers=_MAME_BLOCKERS, original_system_ids=(16,)),
    CoreProfile("atari7800", "mame_libretro.dll", "MAME", "mame", "upstream_family",
                blockers=_MAME_BLOCKERS, original_system_ids=(23,)),
    CoreProfile("gb", "gambatte_libretro.dll", "Gambatte", None, "redux_extension",
                blockers=("Original Game Boy dispatch identity is not established by the inspected catalogue map.",)),
    CoreProfile("gbc", "gambatte_libretro.dll", "Gambatte", None, "redux_extension",
                blockers=("Original Game Boy Color dispatch identity is not established by the inspected catalogue map.",)),
)

PROFILES: Mapping[str, CoreProfile] = MappingProxyType({profile.system: profile for profile in _profiles})


def get_profile(system: str | None) -> CoreProfile | None:
    """Return a known library-system profile, without guessing unknown IDs."""
    if not isinstance(system, str):
        return None
    return PROFILES.get(system.strip().lower())


def default_options(system: str | None) -> dict[str, str]:
    """Return an independent copy of requested, declared upstream defaults.

    No firmware override, game-dependent multitap or guessed region is forced.
    Validation against actual runtime declarations remains the host's job.
    """
    profile = get_profile(system)
    return dict(profile.default_options) if profile else {}
