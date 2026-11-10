# Redux

A controller-first Windows game library for the living room. Browse games, insert a disc, play through integrated emulator cores, or launch installed PC games from a local or removable drive.

![Redux mark](redux/assets/redux.png)

## Install

Download **ReduxSetup-0.10.6-win-x64.exe** from [Releases](https://github.com/baysidejr/redux/releases/latest), run it, and open Redux from the Start menu. Requires Windows 10 version 2004 or newer, x64. The default installation is per user under `%LOCALAPPDATA%\Programs\Redux`; administrator access is not required for ordinary installation or use.

A portable ZIP is also available. Extract the complete `Redux` folder and run `Redux.exe`; keep `_internal` beside it. Python is included in both distributions.

Games, retail BIOS firmware, emulator cores and artwork-provider accounts are not included. Add your own media and firmware. On first launch, choose **Download recommended emulators**. Redux downloads official cores, licenses and metadata and applies built-in defaults. Use **System → Emulators & firmware → Emulator setup** to install, repair or update them later. Pressing Play on a supported game with a missing core also offers download and continue. No terminal or separate Python installation is needed. Provider accounts are configured under System in the controller-operated interface.

The installer offers optional desktop and Windows startup shortcuts. Upgrades preserve the library. Uninstall removes installed application files and leaves user-created library, games and BIOS files in place. Scraper credentials remain in Windows-protected per-user storage and must be configured separately on another PC.

## Features

- Fullscreen library, platform collections, search, playlists and game details.
- Gamepad navigation with SDL3 mappings and XInput fallback.
- Integrated-core menu through Guide or Start+Select; save/load states, display settings and controls depend on core capabilities.
- USB/internal PC optical-drive detection, buffering and installation of supported disc layouts.
- PC game shortcuts, including GOG-installed executables, with return to Redux after the game process exits. PC games provide their own gameplay controller support and save system.
- Artwork searches through ScreenScraper, TheGamesDB, IGDB, HFSDB, ArcadeDB and Libretro thumbnails, subject to provider platform support and account requirements.
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
