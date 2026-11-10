"""Download explicitly selected optional Windows x64 cores from Libretro.

No BIOS or game downloads. The downloaded upstream .info file records each
core's authors/license; these files are optional local dependencies, not Redux code.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import struct
import urllib.request
from urllib.parse import urlsplit
import zipfile

CORES = {
    "mesen": "https://docs.libretro.com/library/mesen/",
    "snes9x": "https://docs.libretro.com/library/snes9x/",
    "genesis_plus_gx": "https://docs.libretro.com/library/genesis_plus_gx/",
    "gambatte": "https://docs.libretro.com/library/gambatte/",
    "mednafen_pce_fast": "https://docs.libretro.com/library/beetle_pce_fast/",
    "mednafen_psx": "https://docs.libretro.com/library/beetle_psx/",
    "mednafen_saturn": "https://docs.libretro.com/library/beetle_saturn/",
    "mednafen_supafaust": "https://github.com/libretro/supafaust",
    "mednafen_pce": "https://github.com/libretro/beetle-pce-libretro",
    "mupen64plus_next": "https://docs.libretro.com/library/mupen64plus/",
    "mame": "https://docs.libretro.com/library/mame/",
    "picodrive": "https://docs.libretro.com/library/picodrive/",
}
LICENSES = {
    "mesen": "https://raw.githubusercontent.com/libretro/Mesen/master/LICENSE",
    "snes9x": "https://raw.githubusercontent.com/snes9xgit/snes9x/master/LICENSE",
    "genesis_plus_gx": "https://raw.githubusercontent.com/ekeeke/Genesis-Plus-GX/master/LICENSE.txt",
    "gambatte": "https://raw.githubusercontent.com/libretro/gambatte-libretro/master/COPYING",
    "mednafen_pce_fast": "https://raw.githubusercontent.com/libretro/beetle-pce-fast-libretro/master/COPYING",
    "mednafen_psx": "https://raw.githubusercontent.com/libretro/beetle-psx-libretro/master/COPYING",
    "mednafen_saturn": "https://raw.githubusercontent.com/libretro/beetle-saturn-libretro/master/COPYING",
    "mednafen_supafaust": "https://raw.githubusercontent.com/libretro/supafaust/master/COPYING",
    "mednafen_pce": "https://raw.githubusercontent.com/libretro/beetle-pce-libretro/master/COPYING",
    "mupen64plus_next": "https://raw.githubusercontent.com/libretro/mupen64plus-libretro-nx/develop/LICENSE",
    "mame": "https://raw.githubusercontent.com/libretro/mame/master/COPYING",
    "picodrive": "https://raw.githubusercontent.com/libretro/picodrive/master/COPYING",
}


def fetch(url, limit):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'buildbot.libretro.com', 'raw.githubusercontent.com'} or parsed.username or parsed.password:
        raise ValueError('Core downloads require an official HTTPS source')
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("The official download exceeded its expected size limit.")
    return data


def install(name):
    if name not in CORES:
        raise ValueError("Unknown optional core.")
    folder = Path(__file__).resolve().parent
    binary_name = name + "_libretro.dll"
    url = "https://buildbot.libretro.com/nightly/windows/x86_64/latest/" + binary_name + ".zip"
    info_url = "https://raw.githubusercontent.com/libretro/libretro-super/master/dist/info/" + name + "_libretro.info"
    archive = fetch(url, 128 * 1024 * 1024)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entry = zipped.getinfo(binary_name)
        if entry.file_size > (512 if name=="mame" else 256) * 1024 * 1024:
            raise RuntimeError("The core archive contains an oversized binary.")
        binary = zipped.read(binary_name)
    if binary[:2] != b"MZ":
        raise RuntimeError("The downloaded core is not a PE DLL.")
    pe = struct.unpack_from("<I", binary, 0x3C)[0]
    if binary[pe:pe + 4] != b"PE\0\0" or struct.unpack_from("<H", binary, pe + 4)[0] != 0x8664:
        raise RuntimeError("The downloaded core is not Windows x64.")
    info = fetch(info_url, 256 * 1024)
    license_text = fetch(LICENSES[name], 256 * 1024)
    result = {"source_url": url, "info_url": info_url, "documentation_url": CORES[name],
              "license_source_url": LICENSES[name],
              "downloaded_utc": datetime.now(timezone.utc).isoformat(),
              "zip_sha256": hashlib.sha256(archive).hexdigest(),
              "dll_sha256": hashlib.sha256(binary).hexdigest(), "dll_bytes": len(binary),
              "upstream_digest_available": False, "gameplay_validation": "Not yet tested in this frontend",
              "scope": "Optional official upstream binary; no games or proprietary BIOS included"}
    # Core replacement is explicit: installers are run on demand, never by the frontend.
    (folder / (binary_name + ".zip")).write_bytes(archive)
    (folder / binary_name).write_bytes(binary)
    (folder / (name + "_libretro.info")).write_bytes(info)
    (folder / (name + "_LICENSE.txt")).write_bytes(license_text)
    (folder / (name + "_core_provenance.json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="+", choices=sorted(CORES))
    args = parser.parse_args()
    for name in args.names:
        result = install(name)
        print(name, result["dll_bytes"], result["dll_sha256"])


if __name__ == "__main__":
    main()
