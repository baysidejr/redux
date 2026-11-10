# Windows startup and Xbox mode

Redux opens fullscreen by default. **System → Windows & Xbox mode** contains the
controller-operated settings for startup, fullscreen, Windows registration and
removal. F11 remains a secondary keyboard shortcut.

## Start with Windows

**Start with Windows** installs only this user's `ReduxMediaLibrary` entry under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. It launches the complete
portable EXE with `--startup --fullscreen`, independent of the working directory.
Turning it off removes the owned entry. Moving the application to another PC or
folder requires enabling startup there again; games and saves remain portable.

**Windows integration options → Start beside Xbox mode** controls the owned
`GamingConfiguration\Startup\Run` DWORD: `1` starts at sign-in, including Xbox
mode; `0` waits for the ordinary desktop. The meanings were checked against this
machine's Microsoft Settings startup handler, with evidence in
`analysis/windows_integration/startup-policy-binary-evidence.json`. This setting
does not change the home-app selection. When started alongside active Xbox mode,
Redux starts minimized so it does not replace the chosen home screen.

**Windows integration options → Show Redux in Xbox My Apps** adds Redux as an app
you can open while keeping Xbox as home. This is independent of Developer Mode
and the gaming-home package. The small helper asks Windows for administrator
approval, uses only an empty OEM app slot, and saves a restore record. It does not
replace another launcher or change the selected home app. Reopen Xbox if it was
already running. **Remove Windows integration → Remove Xbox My Apps entry** undoes
this separate registration. On this PC, Xbox's Apps page showed the Redux tile;
opening it cold loaded the existing three-game library, and opening it again
returned to the same running frontend. These checks used the real Windows profile,
outside the development tool's private settings view.

## Redux as an Xbox home app

This is an optional Windows development-package integration. It is separate from
ordinary fullscreen startup and uses the existing Redux application and library.

1. Open **Xbox home app → Register Redux with Windows**. If Developer Mode is off,
   the app explains the prerequisite and offers the Windows settings page. The
   setup scripts check it and do not enable it themselves.
2. Registration stages an immutable loose package in `windows-home` beneath the
   portable root, with `windows.gamingApp`, the gaming-home custom capability and
   the developer SCCD. The packaged UI remains `asInvoker` with PerMonitorV2 DPI
   awareness. Its `redux-launch.json` points to the original portable root, so
   BIOS, cores, artwork, managed games and saves stay together.
3. On a supported handheld, select Redux in **Windows Settings → Gaming → Xbox
   mode → Choose home app**, or use **Use Redux as Xbox home** in the app.
4. The supplied findings report that desktop form factors hide the selector on
   this Windows build. **Windows integration options → Advanced desktop home
   picker** offers a separate administrator setup that backs up the original OEM
   registry state before setting DeviceForm to `46`. It explains the wider device
   classification effect and required reboot. Restore is available in the same
   menu. This is a Windows workaround, not a promise about future Windows builds.
5. Developer Mode can be turned off after successful registration according to
   the supplied machine-tested reference. Reboot persistence still needs to be
   verified on each deployed machine.

The package uses the literal home URI `windows.gaming:///home`. Repeated
activation forwards through a user-protected Windows named pipe to one running
frontend and transfers foreground permission. It does not depend on a runtime
descriptor file in an inherited launcher's private AppData view.
While a game or disc operation is active, activation retains that work. A running
game pauses into Redux's existing menu. The initial and forwarded activations
are logged under `%LOCALAPPDATA%\ReduxMediaLibrary\logs\activations.jsonl` with
package and FSE information.

The identity manifest disables AppData and HKCU write virtualization so its
settings and Windows startup changes remain shared with ordinary desktop launches.
When validating from packaged development tools, compare the actual profile view
with the development tool's private view; identical textual paths can resolve to
different files. Acceptance records explicitly identify the real profile context.

Windows owns the short Guide/Xbox press in Xbox mode. **Select + Start held for
one second** remains the consistent Redux game-menu control. Windows' own Xbox
mode UI provides the desktop switch. The ordinary desktop startup path continues
to offer Redux's exit action.

## Updates and removal

Close games before changing Windows registration. New builds stage a different
version/hash directory; setup never rebuilds into an active registered layout.
The portable root must remain available while an identity package points to it.

**Remove Windows integration** removes owned startup entries and the identity
package. If Redux was the selected home app, it returns the selection to Xbox.
It leaves the app, games, saves, BIOS and staged files intact. Restore the original
device type separately before deleting its backup.

## Evidence and limits

The Microsoft SDK `gamingexperience.h` supplies the exact query, state-change,
notification and unregister ABIs. Older Windows builds degrade to ordinary
startup. Windows home-app choice is an OEM-supported configuration; the third-party
developer SCCD and DeviceForm gate are based on the supplied tested brief.
Actual package registration, Windows picker listing and reboot acceptance must be
reported separately from passing application tests.

### This PC: 0.8.1 acceptance, 2026-11-09

- Startup is enabled in the real user's Windows profile, with `--startup --fullscreen`.
  Startup alongside Xbox is enabled. The user's real reboot opened Redux as home;
  the shell activation log confirms the registered package launched fullscreen
  with Xbox mode active. The ordinary desktop Run entry then skipped its launch,
  leaving the registered frontend as the single instance.
- Xbox My Apps listing, cold launch and repeat activation passed. The cold launch
  used the existing portable library with three games, even with a System32 working
  directory. Repeat activation reused that process.
- The desktop selector preparation set DeviceForm to `46`. Its original state and
  the Xbox app-slot registration have validated restore records in the real profile.
  Restore actions have not been exercised against the live settings.
- After the user enabled Developer Mode, Windows accepted the Redux home identity
  `Redux.MediaLibrary_an5npv7fm9m1y!App`. The Windows home-app picker visibly lists
  and selects Redux; entering Xbox mode on startup is enabled. Delayed real-profile
  queries retained both settings.
- A cold package activation carried the actual Redux package identity and opened
  the same three-game portable library. Entering Xbox mode from Redux succeeded:
  the Windows API reported active, and `sihost.exe` delivered the literal home URI
  to that same process through repeat activation. No duplicate frontend appeared.
- Windows offered restart or immediate entry during the transition. No automated
  restart was performed. The user subsequently restarted Windows and confirmed
  the expected Redux home startup. Turning Developer Mode off afterward has not
  yet been verified for this registered Redux package.
- Automated checks: 700 tests, no failures or errors, three skipped.

The detailed records are `analysis/windows_identity/home-acceptance-0.8.1.json`,
`analysis/windows_identity/post-reboot-acceptance-0.8.1.json`,
`analysis/windows_integration/desktop-acceptance-0.8.1.json`,
`analysis/windows_integration/home-mode-real-0.8.1.json` and
`analysis/test_results_0.8.1.json`.

- [Microsoft: game launchers and handhelds](https://learn.microsoft.com/en-us/xbox/gdk/docs/gdk-dev/pc-dev/handheld/handheld-launchers)
- [Microsoft: GamingHomeApp](https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-gaming-configuration-gaminghomeapp)
