Controller-first setup
======================

Normal users do not need Python or a terminal. On first launch choose Download recommended emulators, or open System > Emulators & firmware > Emulator setup. Redux downloads official core DLLs, licenses and metadata and applies its integrated defaults. Missing-core Play offers Download and continue. Repair/update is explicit and unavailable while a game runs. Games and BIOS files are never downloaded.

Developer reference
===================

Optional local Libretro cores
============================

These DLLs are official upstream Windows x64 nightly downloads, not vendor
files. Redux's original host implements the public libretro C ABI. The frontend
can still use external backend profiles when a suitable embedded core is absent.

Sources:
https://buildbot.libretro.com/nightly/windows/x86_64/latest/
https://github.com/libretro/libretro-common/blob/master/include/libretro.h
https://docs.libretro.com/guides/download-cores/

Each *_core_provenance.json records origin and local SHA256. These hashes detect
local changes; they are not independent upstream signatures. Each *_libretro.info
is official upstream core metadata including license and firmware filenames.

Local dependencies installed November 8, 2026:
  mesen             NES/Famicom               GPLv3
  snes9x            SNES                     Non-commercial (upstream metadata)
  genesis_plus_gx   Sega MS/GG/MD/CD          Non-commercial (upstream metadata)
  gambatte          Game Boy / Color         GPLv2
  mednafen_pce_fast PC Engine / CD           GPLv2
  mednafen_psx      PlayStation software     GPLv2

These third-party license labels are recorded from the upstream .info files.
They do not change the ownership or license of Redux's independent host code.
They are optional local dependencies. Do not treat the complete workspace as an
unrestricted redistribution bundle; the actual upstream terms accompany each core.

Reinstall only the named dependencies on demand:
  .venv\Scripts\python.exe tools\cores\install_official.py mesen
  .venv\Scripts\python.exe tools\cores\install_official.py snes9x gambatte
The installer downloads no BIOS, games, vendor assets or vendor components.
It replaces the selected local DLLs explicitly; no automatic update runs in UI.

Verified native acceptance:
  .venv\Scripts\python.exe tools\cores\verify_embedded.py
This executes only the project's original generated Redux Self Test NES ROM.
Mesen 0.9.9 emitted real 256x240 RGB frames at reported 60.0998265 fps. After
saving, advancing 32 frames, restoring and advancing 32 frames, RGB bytes match.
The captured PNG was inspected and contains the original self-test display.
Evidence: analysis\embedded_test\native_verification.json and native-frame.png.
WaveOut was also opened on this Windows machine at 48000 Hz, consumed core PCM,
paused/resumed and closed without errors. This fixture is silent, so audible game
content and latency have not been evaluated. Fake API tests cover PCM byte order,
bounded queue/buffer lifetime, SRAM persistence, controller masks, worker-thread
ownership, pixel formats, state integrity and real-frame PNG chunk checks.

The five additional DLLs loaded and reported libretro API version 1 plus their
system metadata. Their gameplay, controller layouts and save compatibility have
not yet been validated here. They are available for explicit compatibility tests,
not evidence that all games/platform features work. See optional_core_metadata.json.

Host limits:
  Software video only: XRGB8888, RGB565 and RGB1555. GPU context/frame requests
  fail explicitly. Standard joypads only; analog/lightgun/mouse/rumble/disc swap
  are not currently implemented. Options use each core's legacy default values.
  Battery saves use a core-build plus entry-file hash to avoid collisions. Save
  states carry matching hashes and a payload integrity hash. A CUE entry hash
  identifies its CUE text, not every referenced track's content.
  Existing battery saves with an incompatible byte size are preserved untouched.
  Proprietary firmware is not supplied. If required, user-supplied firmware goes
  in the selected absolute system directory. A core's accepted extensions do not
  prove a given disc image or game will run.
