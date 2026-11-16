"""Make Redux the home screen of Windows' full screen experience (Xbox mode).

One action does the whole job, in the order Windows needs it:
1. one administrator prompt sets the device type that shows the home-app picker
   (DeviceForm 46, original backed up) and turns on Developer Mode;
2. Redux registers its home-app package for this user;
3. Redux is chosen as the home app and Windows starts in the full screen experience.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
try:
    import winreg
except ImportError:
    winreg=None

from . import __version__
from .app import config_path
from .windows_gaming import GAMING_CONFIGURATION_KEY,XBOX_HOME_AUMID,gaming_configuration


PACKAGE_NAME="Redux.MediaLibrary"


def _powershell(arguments):
    from .windows_paths import windows_system_directory
    executable=windows_system_directory()/"WindowsPowerShell/v1.0/powershell.exe"
    result=subprocess.run([str(executable),"-NoLogo","-NoProfile","-NonInteractive",*arguments],
        capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=180,
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if result.returncode:
        raise OSError((result.stderr or result.stdout or "Windows setup did not complete.").strip()[-2400:])
    text=result.stdout.lstrip("\ufeff").strip()
    if not text:return {}
    try:return json.loads(text)
    except ValueError:raise OSError("Windows setup returned an unreadable result.")


def identity_status():
    # A constant package name is embedded here; no user path enters PS source.
    value=_powershell(["-Command", "$p = Get-AppxPackage -Name 'Redux.MediaLibrary'; if ($p) { [ordered]@{name=$p.Name; aumid=($p.PackageFamilyName+'!App'); install_location=$p.InstallLocation; package_full_name=$p.PackageFullName} | ConvertTo-Json -Compress } else { '{}' }"])
    return value if value.get("aumid") else None


def _script(application_dir,name,*arguments):
    path=Path(application_dir)/"tools"/"windows_xbox"/name
    if not path.is_file():raise FileNotFoundError("Windows integration tools are missing. Use the complete Redux application folder.")
    result=_powershell(["-ExecutionPolicy","Bypass","-File",str(path),*map(str,arguments)])
    if result.get("ok") is False:raise OSError(result.get("error","Windows setup did not complete."))
    return result


def register_identity(application_dir,portable_root,*,force_shutdown=False):
    if not gaming_configuration().developer_mode:
        raise OSError("Turn on Developer Mode in Windows Settings before registering the Xbox home-app package.")
    from .windows_identity import prepare_identity_layout
    layout=prepare_identity_layout(Path(application_dir),Path(portable_root),__version__)
    # The identity helper returns the immutable layout, not the ordinary build
    # directory. Updating the EXE stages a new layout instead of changing one
    # that Windows already registered.
    if not isinstance(layout,(str,os.PathLike)):
        layout=layout.path if hasattr(layout,"path") else layout["layout"]
    # force_shutdown is for updates only: it closes an idle old copy Windows reopened as home.
    return _script(application_dir,"Register-HomeApp.ps1","-Layout",layout,*(["-ForceShutdown"] if force_shutdown else []))


def unregister_identity(application_dir):
    return _script(application_dir,"Unregister-HomeApp.ps1")


def select_home_app(aumid=None):
    if winreg is None:raise OSError("Xbox home integration is available on Windows only.")
    config=gaming_configuration()
    target=aumid or XBOX_HOME_AUMID
    if aumid:
        package=identity_status()
        if not package or package["aumid"]!=aumid:
            raise ValueError("Register Redux before selecting it as the Xbox home app.")
        if not config.handheld:
            raise OSError("This desktop does not expose the home-app selector yet. Use Advanced desktop home picker, restart Windows, then select Redux.")
    backup=config_path().parent/"windows-home-selection.json"
    if not backup.exists():
        backup.parent.mkdir(parents=True,exist_ok=True)
        backup.write_text(json.dumps({"home_app":config.home_app,"startup_to_gaming_home":config.startup_to_gaming_home},indent=2),encoding="utf-8")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,GAMING_CONFIGURATION_KEY,0,winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key,"GamingHomeApp",0,winreg.REG_SZ,target)
        if aumid:winreg.SetValueEx(key,"StartupToGamingHome",0,winreg.REG_DWORD,1)
    actual=gaming_configuration()
    if actual.home_app!=target:
        raise OSError("Windows did not retain the home-app selection. Open Windows Xbox mode settings to choose it. Redux will not repeatedly rewrite the setting.")
    return actual


def change_device_form(enabled,application_dir):
    """Elevated: DeviceForm 46 plus Developer Mode (enable), or the original DeviceForm (restore)."""
    backup=config_path().parent/"windows-integration"
    backup.mkdir(parents=True,exist_ok=True)
    result=backup/("device-form-result-"+uuid.uuid4().hex+".json")
    return _script(application_dir,"Invoke-DeviceForm.ps1","-Action","Enable" if enabled else "Restore",
                   "-BackupDirectory",backup,"-ResultFile",result)


@dataclass(frozen=True)
class FullscreenStatus:
    handheld: bool=False
    developer_mode: bool=False
    registered: bool=False
    home: bool=False
    starts_in_fullscreen: bool=False
    current: bool=True  # the registered copy is this version of Redux

    @property
    def ready(self):
        return (self.handheld and self.developer_mode and self.registered and self.home
                and self.starts_in_fullscreen and self.current)

    @property
    def outdated(self):
        """Windows starts an older copy of Redux in full screen than the one installed."""
        return self.registered and not self.current


def fullscreen_status(application_dir=None):
    config=gaming_configuration()
    package=identity_status()
    current=True
    if package and application_dir is not None:current=registered_layout_is_current(package,application_dir)
    return FullscreenStatus(config.handheld,config.developer_mode,bool(package),
                            bool(package) and config.home_app==package["aumid"],config.startup_to_gaming_home,current)


def set_up_fullscreen_experience(application_dir,portable_root):
    """Make Redux the full screen experience home; the caller offers the restart that applies it."""
    config=gaming_configuration()
    if not(config.handheld and config.developer_mode):
        change_device_form(True,application_dir)
        config=gaming_configuration()
        if not config.developer_mode:raise OSError("Windows did not turn on Developer Mode, so Redux could not register as a home app.")
        if not config.handheld:raise OSError("Windows did not keep the gaming device type that shows the home-app picker.")
    registered=register_identity(application_dir,portable_root)
    aumid=registered.get("aumid") or (identity_status() or {}).get("aumid")
    if not aumid:raise OSError("Windows did not report Redux's home-app registration.")
    select_home_app(aumid)
    remove_old_layouts(portable_root,registered.get("install_location"))
    return fullscreen_status(application_dir)


def remove_old_layouts(portable_root,keep):
    """Each registration stages a full copy of Redux; only the registered one is needed."""
    folder=Path(portable_root)/"windows-home"
    if not keep or not folder.is_dir():return 0
    keep=Path(keep).resolve()
    removed=0
    for layout in folder.iterdir():
        if layout.is_dir() and layout.resolve()!=keep and (layout/"redux-package.json").is_file():
            shutil.rmtree(layout,ignore_errors=True)
            removed+=not layout.exists()
    return removed


def registered_layout_is_current(package,application_dir):
    """True when the registered home-app copy is this version of Redux."""
    import hashlib
    from .windows_identity import package_version
    try:
        metadata=json.loads((Path(package["install_location"])/"redux-package.json").read_text(encoding="utf-8"))
        executable=hashlib.sha256((Path(application_dir)/"Redux.exe").read_bytes()).hexdigest()
    except (OSError,ValueError,KeyError,TypeError):
        return False
    return metadata.get("version")==package_version(__version__) and metadata.get("executable_sha256")==executable


def refresh_registration(application_dir,portable_root,*,force_shutdown=False):
    """After an update, register the new Redux so Windows does not keep starting the old copy.

    Runs from the ordinary (unpackaged) Redux.exe: the installer runs `Redux.exe refresh-home`
    after copying the files, and every desktop start checks again. Returns "not-registered",
    "current", "developer-mode-off" (Windows refuses the registration; the Full screen
    experience row turns it back on) or "refreshed"; a failed registration raises.
    """
    package=identity_status()
    if not package:return "not-registered"
    if registered_layout_is_current(package,application_dir):return "current"
    if not gaming_configuration().developer_mode:return "developer-mode-off"
    was_home=gaming_configuration().home_app==package["aumid"]
    registered=register_identity(application_dir,portable_root,force_shutdown=force_shutdown)
    if was_home and registered.get("aumid") and registered["aumid"]!=package["aumid"]:select_home_app(registered["aumid"])
    remove_old_layouts(portable_root,registered.get("install_location"))
    return "refreshed"
