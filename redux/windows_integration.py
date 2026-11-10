"""Windows identity setup kept separate from ordinary library and game use."""
from __future__ import annotations

import json
import os
from pathlib import Path
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


def register_identity(application_dir,portable_root):
    if not gaming_configuration().developer_mode:
        raise OSError("Turn on Developer Mode in Windows Settings before registering the Xbox home-app package.")
    from .windows_identity import prepare_identity_layout
    layout=prepare_identity_layout(Path(application_dir),Path(portable_root),__version__)
    # The identity helper returns the immutable layout, not the ordinary build
    # directory. Updating the EXE stages a new layout instead of changing one
    # that Windows already registered.
    if not isinstance(layout,(str,os.PathLike)):
        layout=layout.path if hasattr(layout,"path") else layout["layout"]
    return _script(application_dir,"Register-HomeApp.ps1","-Layout",layout)


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
    backup=config_path().parent/"windows-integration"
    backup.mkdir(parents=True,exist_ok=True)
    result=backup/("device-form-result-"+uuid.uuid4().hex+".json")
    return _script(application_dir,"Invoke-DeviceForm.ps1","-Action","Enable" if enabled else "Restore",
                   "-BackupDirectory",backup,"-ResultFile",result)


def register_xbox_app(enabled,application_dir):
    directory=Path(application_dir)
    backup=config_path().parent/"windows-integration"
    backup.mkdir(parents=True,exist_ok=True)
    result=backup/("xbox-app-result-"+uuid.uuid4().hex+".json")
    return _script(directory,"Invoke-XboxApp.ps1","-Action","Add" if enabled else "Remove",
                   "-Executable",directory/"Redux.exe","-Icon",directory/"_internal/assets/redux.ico",
                   "-BackupDirectory",backup,"-ResultFile",result)
