# Windows startup and the full screen experience

**System → Windows** has three rows: **Start with Windows**, **Open fullscreen** and
**Full screen experience**. Redux installs as an ordinary app; the third row is the
only Xbox setting.

## Start with Windows

**Start with Windows** installs only this user's `ReduxMediaLibrary` entry under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. It launches the complete
portable EXE with `--startup --fullscreen`, independent of the working directory.
Turning it off removes the owned entry. It also writes Redux's
`GamingConfiguration\Startup\Run` DWORD (`1`, start at sign-in in Xbox mode too);
there is no separate switch for it any more.

## Full screen experience

One action makes Redux the home screen of Windows' full screen experience (Xbox
mode), so the PC starts straight into Redux like a console:

1. **One administrator prompt** runs `Invoke-DeviceForm.ps1 -Action Enable`, which
   backs up the original `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\OEM`
   `DeviceForm` and sets it to `46` (gaming handheld, which shows the home-app
   picker on a desktop), and turns on Developer Mode
   (`AppModelUnlock\AllowDevelopmentWithoutDevLicense = 1`). Skipped when both are
   already set.
2. Redux stages its home-app package in `windows-home` beneath the portable root
   and registers it for this user (`Register-HomeApp.ps1`, no elevation, so it
   lands in the signed-in user's profile even when UAC asked for another admin).
3. Redux writes `GamingHomeApp` (its package AUMID) and `StartupToGamingHome = 1`
   under `HKCU\Software\Microsoft\Windows\CurrentVersion\GamingConfiguration`,
   after saving the previous choice in `windows-home-selection.json`.
4. **Restart to finish** offers Restart now, which closes Redux and restarts
   Windows as System → Exit → Restart PC does.

The row shows ON when all five parts are in place (device type, Developer Mode,
package, home choice, start in full screen). Choosing it again repairs the setup.
Redux refuses while a game or task runs.

While Redux is home, Back/Exit offers Keep browsing, Restart PC and Shut down PC;
closing the home app would leave an empty screen. Windows' own Xbox-button menu
reaches the desktop. Windows owns the short Guide press in this mode; **Select +
Start held for one second** opens Redux's game menu.

Developer Mode stays on: Windows registers the unsigned home-app package only with
it, and every update registers again.

## Updates

The registered package is a copy of Redux (`windows-home/<version>-<hash>`), so an
update has to register the new copy or Windows keeps starting the old one:

- The installer runs `Redux.exe refresh-home` after copying the files (hidden, and it
  waits for it), whether it was started by the in-app updater or by hand. That
  registers the new copy with `-ForceApplicationShutdown`, closing an idle old copy
  that Windows started as home. Before 0.31.2 only the in-app updater did this, so a
  release installed from the downloaded setup left Windows starting the old copy.
- Any start of the installed, unpackaged `Redux.exe` checks again in the background
  (a non-daemon thread, so closing Redux does not cut it short).
- Both run only when Redux is already registered and Developer Mode is on, keep
  Redux as home if it was, and delete the copies that are no longer registered.
  Every outcome is written to `%LOCALAPPDATA%\ReduxMediaLibrary\logs\full-screen-experience.log`.
- When the copy stays out of date (Developer Mode turned off, or the registration
  failed), Redux says so instead of failing silently: the desktop Redux shows *Full
  screen mode is out of date*, the Full screen experience row reads *Windows starts
  an older copy of Redux · A updates it* (its setup turns Developer Mode back on and
  registers the current version), and a home copy older than the installed version
  shows the same message when Windows starts it.

## Undoing it

There is no undo row. In Windows, **Settings → Gaming → Full screen experience**
picks another home app or turns startup in full screen off. The helpers in
`tools/windows_xbox` remain for a manual undo: `Unregister-HomeApp.ps1` removes the
package (and returns home to Xbox if Redux was home), and `Invoke-DeviceForm.ps1
-Action Restore` puts the original DeviceForm back from its backup.

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
