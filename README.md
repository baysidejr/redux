# Redux

A controller-first Windows game library for the living room. Browse games, insert a disc, play through integrated emulator cores, or launch installed PC games from a local or removable drive.

![Redux mark](redux/assets/redux.png)

## Install

Download **ReduxSetup-0.28.1-win-x64.exe** from [Releases](https://github.com/baysidejr/redux/releases/latest), run it, and open Redux from the Start menu. Requires Windows 10 version 2004 or newer, x64. The default installation is per user under `%LOCALAPPDATA%\Programs\Redux`; administrator access is not required for ordinary installation or use.

A portable ZIP is also available. Extract the complete `Redux` folder and run `Redux.exe`; keep `_internal` beside it. Python is included in both distributions.

Games, retail BIOS firmware, emulator cores and artwork-provider accounts are not included. Add your own media and firmware. On first launch, choose **Download recommended emulators**. Redux downloads official cores, licenses and metadata and applies built-in defaults. Use **System → Emulators & firmware → Emulator setup** to install, repair or update them later. Pressing Play on a supported game with a missing core also offers download and continue. No terminal or separate Python installation is needed. Provider accounts are configured under System in the controller-operated interface.

The installer offers optional desktop and Windows startup shortcuts. Upgrades preserve the library. Uninstall removes installed application files and leaves user-created library, games and BIOS files in place. Scraper credentials remain in Windows-protected per-user storage and must be configured separately on another PC.

## Features

- Fullscreen console shelf: every system's pixel-art tile, A to play; a wishlist fed from the built-in game database; search.
- Gamepad navigation with SDL3 mappings and XInput fallback.
- Integrated-core menu through Guide or Start+Select; save/load states, display settings and controls depend on core capabilities.
- USB/internal PC optical-drive detection, buffering and installation of supported disc layouts.
- PC game shortcuts, including GOG-installed executables, with return to Redux after the game process exits. PC games provide their own gameplay controller support and save system.
- A shipped game database for the seven CD systems: discs and card games are matched by serial or title and take their description, year, publisher, developer and genre from it, and the screens show its box art, banner and game image whenever a game has no art of its own. No accounts, API keys or online artwork searches.
- Optional Windows startup and Xbox home integration. Current unsigned Xbox home registration requires Developer Mode during registration; ordinary Redux use does not.

Controller and disc-drive compatibility varies. Listing a system does not mean its core or firmware is installed. See the [operating guide](docs/OPERATING_GUIDE.md) and [controller/menu status](docs/CONTROLLER_MENU_STATUS.md) for supported paths and remaining limitations.

## Build from source

Use Windows x64 and Python 3.14 with Tcl/Tk:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m redux
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm Redux.spec
.\.venv\Scripts\python.exe tools/package_release.py
```

To build the installer, install [Inno Setup](https://jrsoftware.org/isdl.php) and run:

```powershell
.\.venv\Scripts\python.exe tools/build_installer.py --compiler "C:\path\to\ISCC.exe"
```

Outputs appear in `dist`. The release guard rejects games, BIOS files, account stores, local databases and unexpected files in the distribution. Source and release bundles exclude local analysis, user libraries and downloaded optional cores.

## Security and dependencies

See [SECURITY.md](SECURITY.md) and the [release security review](docs/SECURITY_REVIEW.md). Bundled SDL3 and Roboto Condensed dependencies retain their licenses and provenance under `redux/assets`. Generated distributions include Python, Pillow, PyInstaller and input/font notices in `licenses` and `THIRD_PARTY_NOTICES.txt`. Optional emulator cores have their own upstream terms.

The application and installer are currently unsigned. Publishing source does not grant a license to third-party game artwork, BIOS, games or trademarks.

## Neo Geo, Neo Geo CD and 3DO

The recommended setup includes **FinalBurn Neo** for Neo Geo AES/MVS cartridges, **NeoCD** for Neo Geo CD and **Opera** for 3DO. All three are enabled for integrated playback by default, as is **Mupen64Plus-Next** for Nintendo 64 (software renderer; Super Mario 64 holds 60 fps on the development PC). **Stella** plays the Atari 2600, **ProSystem** the Atari 7800 and **mGBA** the Game Boy Advance; MAME is no longer used. The recommended set now contains fifteen distinct cores, and none is optional.

Neo Geo cartridge games are zipped ROM sets named by set name (`mslug.zip`, `kof98.zip`), placed in a console cartridge's `Neo Geo` folder (also accepted: `AES`, `MVS`, `Neo Geo AES`). Redux names them from a shipped table of 278 sets (`redux/database/neo_geo/names.json`, from libretro-database) and boots them in AES (home console) mode. The `neogeo.zip` BIOS set goes in `bios/fbneo` under the selected BIOS folder, or beside the games. FinalBurn Neo is licensed for non-commercial use; its license is kept beside the downloaded core.

Current NeoCD can fall back to its built-in HLE BIOS; original firmware improves compatibility and belongs in `bios/neocd` (the `neocd` subfolder of your selected BIOS directory). Current Opera includes Opera Boot; optional original BIOS such as `panafz1.bin` or `panafz10.bin` goes directly in the selected BIOS directory. Some games still need original firmware. Switching NeoCD between HLE and original BIOS can invalidate save-state compatibility. No retail firmware is downloaded.

3DO disc headers and Neo Geo CD ISO9660 IPL files can identify supported images and inserted discs. CHD containers still require explicit system metadata. Native initialization of both core DLLs was verified; retail game, physical disc, audio and save-state acceptance for these two systems still needs real-game testing. See the [NeoCD upstream instructions](https://github.com/libretro/neocd_libretro) and [Opera core documentation](https://docs.libretro.com/library/opera/).

## Atari Jaguar and Jaguar CD

Recommended setup also downloads **BigPEmu 1.221** directly from [Rich Whitehouse](https://www.richwhitehouse.com/jaguar/index.php?content=download). It is a standalone emulator, alongside the ten integrated cores. The pinned archive is checked against its verified SHA-256; its original complete distribution, ReadMe and third-party notices are retained. BigPEmu prohibits use for private financial gain. Its executable is not modified or included in Redux release downloads.

Use **System → Emulators & firmware → Emulator setup → BigPEmu** to install/check it. Jaguar/Jaguar CD Play also offers setup when it is missing. `.j64`/`.jag` imports default to Jaguar and `.bigpimg` to Jaguar CD. CUE/CDI images may need their system selected in game information. BigPEmu does not accept CHD. Portable `-localdata` keeps its data beside the emulator under `tools/emulators/BigPEmu/UserData`.

Redux tracks its process and returns to the launcher when it exits. Guide or Start+Select uses Redux's external-emulator pause/resume/quit menu. **Save states, filters and Jaguar keypad bindings currently use BigPEmu's own interface**, not the libretro host menu. Native BigPEmu menu boot, owned-process pause/resume and graceful exit were verified; Jaguar/Jaguar CD retail gameplay and physical-disc compatibility still need testing. See the [BigPEmu manual](https://www.richwhitehouse.com/jaguar/index.php?content=userman).

## Phone controller (Redux Controller)

A phone in a GameSir telescopic controller can act as one Windows gamepad with twelve Atari Jaguar keypad keys. Redux maps that controller for its menus, shows the receiver's pairing state under Settings → Controllers → Phone controller, and switches the phone to a game's keypad overlay when a Jaguar game launches. See [docs/REDUX_CONTROLLER.md](docs/REDUX_CONTROLLER.md).
