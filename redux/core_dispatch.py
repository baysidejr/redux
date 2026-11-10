"""Original lookup for the native launcher's explicit system-to-core choices.

Evidence: reference applicationApp.exe SHA-256
b6bd9f65f5b7dcda8f4df824092f67daf261cd6c45204c553d9c3a0b60759b77,
dispatcher 0x140121ee0, recorded in
analysis/features/dispatch_saves_decompiles.txt.

This module selects no emulator and executes no native code. An unlisted ID
returns None, meaning the inspected switch makes no explicit core selection.
The original function then continues; its overall default runtime behavior is
not represented as an invented fallback here.
"""

from enum import Enum
from types import MappingProxyType
from typing import Mapping


class CoreBackend(str, Enum):
    MESEN = "mesen"
    MEDNAFEN = "mednafen"
    KEGA = "kega"
    MAME = "mame"
    MUPEN64PLUS = "mupen64plus"
    FAKE = "fake"


EXPLICIT_CORE_DISPATCH: Mapping[int, CoreBackend] = MappingProxyType({
    1: CoreBackend.MESEN,
    18: CoreBackend.MESEN,
    2: CoreBackend.MEDNAFEN,
    9: CoreBackend.MEDNAFEN,
    11: CoreBackend.MEDNAFEN,
    14: CoreBackend.MEDNAFEN,
    15: CoreBackend.MEDNAFEN,
    17: CoreBackend.MEDNAFEN,
    3: CoreBackend.MUPEN64PLUS,
    4: CoreBackend.KEGA,
    5: CoreBackend.KEGA,
    6: CoreBackend.KEGA,
    7: CoreBackend.KEGA,
    8: CoreBackend.KEGA,
    19: CoreBackend.KEGA,
    20: CoreBackend.KEGA,
    12: CoreBackend.MAME,
    13: CoreBackend.MAME,
    16: CoreBackend.MAME,
    23: CoreBackend.MAME,
    26: CoreBackend.MAME,
    33: CoreBackend.MAME,
    34: CoreBackend.MAME,
    24: CoreBackend.FAKE,
})


def core_for_system(system_id: int) -> CoreBackend | None:
    """Return the switch's explicit backend for a uint32 system identifier.

    None is an unmapped result, not a statement that the app refuses the ID.
    Type/range validation is part of this original Python API.
    """
    if not isinstance(system_id, int) or isinstance(system_id, bool):
        raise TypeError("system_id must be an integer")
    if not 0 <= system_id <= 0xFFFFFFFF:
        raise ValueError("system_id must fit uint32")
    return EXPLICIT_CORE_DISPATCH.get(system_id)
