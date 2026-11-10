# Controller and game-menu status — 0.7.0

This file tracks the user's reference application/Kazeta-style appliance reference against the independent Redux implementation. The reference describes the desired experience; it is not evidence that the current app implements every item. “Implemented” below means a current code path exists, with its tests/native observations described separately. It does not imply acceptance on every controller, game or emulator.

## The requested eleven game-menu entries

| Reference entry | Current implementation | Remaining limits or work |
| --- | --- | --- |
| **RESUME** | Integrated sessions resume from the common menu; supported external sessions return through their owned-process/backend path. Home/Guide toggles the menu; Select/Back + Start held for one second is a fallback. | Physical Guide-button behavior still needs end-to-end acceptance for representative USB/Bluetooth devices and Windows configurations. |
| **SAVE STATE** | Four real slot cards, dates, replacement confirmation and real backend serialization. New integrated saves attempt an associated actual-frame PNG thumbnail. Missing/invalid previews are shown honestly. | New embedded thumbnail support does not supply previews for old states or arbitrary external emulators. |
| **LOAD STATE** | Four cards and a load confirmation warn that current progress and in-game saves may rewind. Valid same-game states with a different core hash show **OLD VERSION** and remain incompatible; empty/corrupt states report actual errors. | Incompatible core states are blocked rather than loaded through a warning override. Generic external programs have no universal state API; state-based battery-memory rewind remains core-dependent. |
| **DISPLAY** | Integrated software-frame renderer provides Clean, Scanlines, CRT and Composite, live paused-frame preview, adjustable parameters and reset. Preferences are saved per system. | These are independent software presets; exact reference pixels/analog behavior are not claimed. They do not filter arbitrary external windows. |
| **CONTROLS** | Shows actual connected controller names/player ports; move a pad to the next host player port by selecting its row or holding Right for two seconds. Per-system **Swap A and B** is remembered. | Four host slots do not establish four-player support in every game/core. Automatic configuration of every external emulator is not implemented. |
| **CHANGE DISC** | Descriptor-bundle import supports multi-disc files; the running integrated menu has no disc-swap action yet. | Pending: show only for a multi-disc game, tray-open state, wait for selected replacement, switch media and show loading/progress. |
| **INSTALL TO CONSOLE / REMOVE FROM CONSOLE** | Buffered sessions expose **Install to library** in the paused menu. Installation copies the existing buffer, shows progress, supports controller B cancellation and keeps the core's paths stable. Initial/final save synchronization preserves states, screenshots and battery memory for managed relaunch. Normal disc installation chooses its destination automatically. | Remove/Keep undo and multi-disc tray transitions remain pending. Save conflicts preserve both namespaces and report recovery paths. |
| **SCREENSHOT** | Integrated host writes a real game frame; the ready PCSX-Redux bridge captures its real GPU frame. Completion currently uses the existing status mechanism. | Pending reference bottom toast and square-wave notification blip. Other external backends need their own capture adapter. |
| **RESTART** | Integrated **Reset game** asks for confirmation and calls the actual core reset. | Naming/presentation may differ from the reference. Generic external reset integration is not available. |
| **SERVICE** | No implemented Service Mode menu or service-only entry. | Pending: conditional visibility, FPS display, recent log, log export to BIOS/USB stick and service tools. No debug-only stub is presented as a working service. |
| **QUIT** | Stop/return-to-library finalizes the supported session. Select/Back + Start + LB + RB requests quick quit. External close has an explicit force-stop fallback when graceful stop fails. | Pending exact reference quit/install/media transitions. Graceful native shutdown and battery writes remain backend-dependent. |

## Display reference details

The current **Display** panel remains over the actual paused game frame. Left/Right adjusts the focused field; selection also advances that field. It stores the following in `<library>/settings/display.json` by system key:

| Setting | Implemented choices / range |
| --- | --- |
| Filter | Clean, Scanlines, CRT, Composite |
| Scanlines | 0–100 percent in 10 percent steps; ignored for Clean |
| Shape | Auto, 4:3, Pixel, Stretch |
| Zoom | 50–150 percent in 5 percent steps |
| Move left/right and up/down | -200 to +200 in 5-unit steps |
| Brightness | 25–200 percent in 5 percent steps |
| Fullscreen | ON/OFF, controlled with A or D-pad left/right while paused; also available in System Display |
| Reset | Resets only this system to its defaults |

Defaults are Composite for Saturn, Sega CD and the reserved `32x_cd` identifier; other systems use Scanlines at 40 percent. Selecting a system default does not install a core or establish that system's compatibility. Game-local star ratings remain separate and are not overwritten by display updates.

## Controller support and configuration

- SDL3 plus its known-controller database is the preferred input path; a single XInput fallback remains if SDL cannot initialize. This does not install a new Windows controller driver or create a system-wide virtual Xbox controller service.
- Native SDL discovery identified the connected **Atari VCS Modern Controller** as mapped with Guide capability. A process-local virtual SDL pad tested Guide, the one-second Select/Start menu chord, the four-button quit shortcut and removal. The observation explicitly says a physical button press was not tested.
- Guide/menu/quit events take priority over ordinary same-frame navigation, so a held Confirm cannot activate a newly opened menu accidentally. Integrated resume gates all four host player slots until buttons, sticks and triggers are neutral.
- Disconnecting an active controller requests pause/menu and shows **CONTROLLER DISCONNECTED** with a player number. A working external pause adapter is still required to pause that emulator.
- **System → Controllers → Configure a controller** captures real buttons, axes and hats, supports optional controls, detects duplicate assignments, and handles reconnects. Custom GUID/platform mappings persist at `%LOCALAPPDATA%\ReduxMediaLibrary\controller-mappings.txt`.
- A/B swap persists by system in `%LOCALAPPDATA%\ReduxMediaLibrary\settings.json` under `system_controls`. Player-port assignments belong to the current controller session. Bluetooth pairing uses Windows; an integrated Bluetooth pairing or general button-test settings page is not implemented.

The app controls its own menu and integrated digital-joypad sessions directly. Separately launched executable emulators still own gameplay input. Only the ready PCSX-Redux Lua adapter currently exposes real Redux-managed external state save/load, screenshots and backend pause/resume. An emulator executable profile is not a common controller/state/filter implementation.

## Separate settings and notification reference

| Reference area | Current status |
| --- | --- |
| **Data** | In-app controller browsing handles media import, folder scan, library/BIOS/artwork/save-folder selection. Managed/linked storage, verification, metadata and backups exist. The full reference Data settings composition/transitions are not reproduced. |
| **Display** | Integrated per-system panel above exists. Fullscreen is controller-operated both here and in System Display; artwork settings remain separate. |
| **Controllers** | Known-pad mapping, setup wizard, menu routing and integrated Controls panel exist. Dedicated button-test and Bluetooth-pairing settings are pending. |
| **Service Mode** | Pending. No service-only Modules page, Sync USB stick, Scan Drives, Log to Disk or View Log flow is implemented. |
| Bottom status notifications | Existing status messages are present. Reference placement, timing, transitions and square-wave audio blips are pending. |
| Disc/install lifecycle | Automatic managed installation and paused-buffer installation are implemented. Multi-disc tray/loading and remove/keep undo transitions remain pending. |

## Evidence and boundaries

- [Native SDL/controller observation](../analysis/controller_menu/native-input-observation.json): SDL version, Atari identity/capabilities, process-local virtual-input checks and explicit physical-test limits.
- [Original executable input/menu findings](../analysis/controller_menu/original-input-menu-findings.md): proven shared hook/save-buffer and N64 host-input boundaries, original Atari/Home profiles and consumption/shader-menu anchors; unknown Guide-to-menu/filter dispatch is labelled.
- `tests/test_unified_game_menu.py`, `test_console_ingame.py`, `test_state_presentation.py` and `test_game_filter_preferences.py` exercise event priority, neutral gating, disconnect pause, real model callbacks, four-slot metadata and durable choices without claiming native hardware acceptance.
- The display, SDL and controller-wizard test modules supply their own focused validation. Current complete test results and packaged GUI acceptance belong with the versioned test/build records and runtime evidence under `analysis/controller_menu/` as those checks complete.

No statement here establishes all eleven reference flows, every physical controller, every emulator, audible notification output, or complete commercial-game compatibility. The [operating guide](OPERATING_GUIDE.md) describes the reachable controls, and [project status](PROJECT_STATUS.md) tracks broader media/platform acceptance.
