# Windows couch integration

Redux keeps its existing controller UI and its ordinary portable EXE. These
helpers add Windows startup/identity integration; they do not replace the game
host, input layer, emulator cores, media storage or save system.

Normal startup can be enabled from Redux's controller-operated Windows settings.
It uses a per-user startup value and does not need package identity, Developer
Mode, administrator rights or a device-form change.

## Xbox home-app selector

`redux.windows_identity.prepare_identity_layout(application_dir,
portable_root, version)` stages the built EXE/runtime and identity files into an
immutable `windows-home/<version>-<payload hash>` folder. The fingerprint includes
the EXE, native runtime, identity templates and setup helpers, so an identity-only
fix also stages a new layout. It copies no library, games,
retail firmware, emulator cores or vendor reference assets. A `redux-launch.json`
marker points the copied app back to its existing portable root. Keep that layout
in place while registered; stage a new layout for updates instead of rebuilding
registered files in place. Registering a new version never force-kills a game.

The package declares `unvirtualizedResources` and disables AppData and HKCU write
virtualization with the documented `desktop6` properties. Settings, activation
files and startup/home-app registry writes therefore use the same locations as
the portable EXE and remain visible to Windows and other processes. Removing
package identity preserves those user files. This follows Microsoft's
[flexible virtualization](https://learn.microsoft.com/en-us/windows/msix/desktop/flexible-virtualization)
configuration for packaged desktop applications.

`Register-HomeApp.ps1 -Layout <layout>` registers the loose development package for
the current Windows user. It checks Developer Mode and returns a structured error
if it is off; the elevated device-type helper turns it on beforehand. `-ForceShutdown`
(updates only) closes an idle old copy Windows reopened as home.
After successful registration it may be turned off again, as reported by the
supplied proof of concept; reboot persistence must still be accepted on the
target machine. No signing certificate is installed by these helpers.

`Add-AppxPackage -AllowUnsigned` is a separate packed-package test mechanism. It
is not accepted in the cmdlet's `-Register` parameter set, requires a special OID
publisher/different identity, and executable packages normally require an
administrator. It does not provide a documented exemption from the developer
SCCD requirement. Redux's loose registration therefore retains its Developer
Mode check instead of attempting an unrelated unsigned install. See Microsoft's
[unsigned-package documentation](https://learn.microsoft.com/en-us/windows/msix/package/unsigned-package).

The exact `windows.gamingApp` extension, gaming-home capability and developer
SCCD are based on the user's tested Windows 11 25H2 brief. Their use for the
consumer home-app picker is not a documented general-purpose registration
contract. The Microsoft-documented route is OEM configuration through
[GamingHomeApp](https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-gaming-configuration-gaminghomeapp).
The SDK packer validates this manifest, but that alone does not prove the picker,
activation or reboot behavior on a particular PC.

`Unregister-HomeApp.ps1` removes only the Redux package and its owned startup
values. It changes the home-app choice back to Xbox only when that choice points
to the Redux package being removed. `-KeepStartup` preserves ordinary startup
when removing identity only. It leaves all portable data in place.

## Desktop home-picker option

The supplied proof of concept reports that `DeviceForm=46` reveals the selector
on otherwise unsupported desktops after a reboot. This is an advanced,
undocumented setting that makes Windows identify the machine as a gaming handheld
and can change other Windows behavior or be reset by updates.

System → Windows → Full screen experience invokes `Invoke-DeviceForm.ps1 -Action
Enable`, which requests administrator approval once and runs `Enable-HomePicker.ps1
-Enable -DeveloperMode`: Developer Mode on, then DeviceForm 46. Keys are created only
when missing; `New-Item -Force` on an existing registry key deletes all its values. It exports the
original OEM key, records the exact original DeviceForm presence/type/value and
checks the backup before writing. The Redux UI remains unelevated. A manual
`Enable-HomePicker.ps1` call also requires explicit `-Enable`.

`Invoke-DeviceForm.ps1 -Action Restore` restores only the original DeviceForm so
unrelated OEM changes made since setup are preserved. If it was originally
absent, the value is removed. Both actions return `restart_required:true`; neither
restarts Windows automatically. Backups default to
`%LOCALAPPDATA%\ReduxMediaLibrary\windows-integration\device-form-backup`.

All helpers write one JSON result to stdout, exit nonzero on failure, and support
`-ResultFile` for the controller UI. Elevated wrappers run only the small setup
scripts. They do not install a service, scheduled task, startup hook, global input
hook, certificate or Windows reboot.

The full-screen experience suppresses some ordinary startup apps, and Windows
owns the physical Guide button in that mode. Redux retains Start+Select for its
in-game menu. See Microsoft's
[launcher guidance](https://learn.microsoft.com/en-us/gaming/gdk/docs/gdk-dev/pc-dev/handheld/handheld-launchers)
and [custom-capability development guidance](https://learn.microsoft.com/en-us/windows-hardware/drivers/devapps/hardware-support-app--hsa--steps-for-app-developers).
