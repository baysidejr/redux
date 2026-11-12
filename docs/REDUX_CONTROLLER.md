# Redux Controller (phone companion)

Redux Controller is a separate project (`C:\dev\redux-controller`) that turns a phone mounted in a GameSir telescopic controller into one Windows gamepad: the GameSir's physical controls plus twelve on-screen Atari Jaguar keypad keys arrive as a single 32-button generic HID device, "Redux Jaguar Controller", created by its Windows receiver (`ReduxReceiver.exe`, an elevated tray application).

## What Redux does with it

- **Menu navigation.** `redux/redux_controller.py` holds the SDL mapping for the device (VID 0x1209, PID 0x5244). `SDLGamepadAPI` loads it after the community database, so the device appears as a normal gamepad: D-pad or left stick to move, A to select, B to go back, Home/Guide for the game menu. The phone's keypad keys (buttons 17–28) are left unmapped for menus.
- **Phone controller menu.** Settings → Controllers → **Phone controller** shows the receiver state read from its loopback control port (`http://127.0.0.1:19444/control/state`): version, PIN, PC address, whether the Windows controller is ready, and which phone is connected. From there you can start the receiver, locate `ReduxReceiver.exe`, and toggle two preferences stored under `redux_controller` in `settings.json`: `autostart` (launch the receiver when Redux starts; Windows shows its elevation prompt unless the receiver's own *Start with Windows* task is on) and `overlays` (default on).
- **The PIN where you need it.** The Controllers settings page shows a live line with the receiver's pairing PIN and the PC address (refreshed every two seconds from the control port while the page is open), and the Phone controller menu leads with the PIN. After *Set up* or *Start the receiver now*, Redux waits for the receiver to answer and pops the PIN up with the phone-side steps, or confirms that a paired phone reconnected by itself.
- **Per-game overlays.** When a Jaguar or Jaguar CD game launches through BigPEmu, `ConsoleApp.play()` calls `_redux_controller_send_profile(game)`, which posts a profile id to `/control/profile`; the phone switches to that game's keypad overlay while its *Follow the PC* setting is on. When the session ends, `_harvest_sessions()` clears the hand-off. Title patterns live in `PROFILES` in `redux_controller.py`; ids match the phone's `profiles.json` (`jaguar`, `jaguar-avp`, `jaguar-doom`, `jaguar-hover-strike`, …).

## Installing the receiver

The Redux release package and installer ship the receiver under `tools/ReduxReceiver` (the packager copies it from the source tree's `tools/ReduxReceiver`, where the Redux Controller receiver ZIP is extracted before packaging; the release guard allows that folder). On a fresh PC, open Settings → Controllers → **Phone controller** and choose **Set up the phone controller**: one elevated launch installs the HIDMaestro driver, registers the firewall rules and the *Start with Windows* logon task, and leaves the receiver running in the tray showing its pairing PIN. After that the receiver starts at sign-in on its own. If the files are missing, the menu offers to locate `ReduxReceiver.exe` instead. The receiver keeps its state in `%LOCALAPPDATA%\ReduxController`.

All calls to the receiver go to 127.0.0.1 only, use a 0.5 second timeout, and run off the UI thread, so a missing receiver never stalls the console. Tests: `tests/test_redux_controller.py` and `tests/test_release_guard.py`.

## Building the installer

`tools/build_installer.py` compiles `tools/installer/Redux.iss` with Inno Setup 6 into `dist/ReduxSetup-<version>-win-x64.exe`, receiver included. A per-user Inno Setup (`winget install JRSoftware.InnoSetup`) lives at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` and is not on PATH, so pass it with `--compiler`. Setup refuses to run while the bundled receiver is running (its `Local\ReduxControllerReceiver` mutex): exit the receiver from the tray first, and close Redux. Upgrades keep `library`, `bios` and `tools` in place.

## Status

Verified on the owner's PC on November 11, 2026: the installed Redux 0.10.9 build navigates its menus with the GameSir controls routed through the phone and the receiver's virtual controller. The bundled receiver's one-click set-up and the PIN display on the Controllers page were confirmed on the owner's PC the same evening. Remember that the installed copy under `%LOCALAPPDATA%\Programs\Redux` must be rebuilt (`PyInstaller Redux.spec`, then `tools/package_release.py`) and updated for any change here to reach it.
