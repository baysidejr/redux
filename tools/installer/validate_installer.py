"""Acceptance-test an installer in a NEW isolated directory; preserve test data.

Runs the installer, repeats installation, exercises the packaged CLI and
uninstalls. It refuses to run when the same product already has an installer
registration, so it cannot replace an existing installed Redux registration.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import winreg

UNINSTALL = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B85D541B-430B-4F95-9389-D43152236C5A}_is1'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def startup_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, 'ReduxMediaLibrary')
    except FileNotFoundError:
        return None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('installer', type=Path)
    parser.add_argument('source_exe', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL):
            raise RuntimeError('An installed Redux registration already exists; use a separate Windows user or VM')
    except FileNotFoundError:
        pass
    destination = args.destination.resolve()
    if destination.exists():
        raise FileExistsError('Use a new isolated test directory')
    args.result.parent.mkdir(parents=True, exist_ok=True)
    before = startup_value()
    command = [str(args.installer.resolve()), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
               '/DIR=' + str(destination), '/GROUP=Redux Installer Acceptance',
               '/LOG=' + str(args.result.with_suffix('.install.log').resolve())]
    subprocess.run(command, check=True, timeout=180)
    executable = destination / 'Redux.exe'
    if digest(executable) != digest(args.source_exe):
        raise RuntimeError('Installed executable differs from packaged executable')
    (destination / 'library/user-data.txt').write_text('preserve library')
    (destination / 'bios/user-data.txt').write_text('preserve firmware')
    subprocess.run([str(executable), '--library', str(destination / 'library'), 'list'],
                   check=True, timeout=30)
    subprocess.run(command, check=True, timeout=180)
    if (destination / 'library/user-data.txt').read_text() != 'preserve library':
        raise RuntimeError('Upgrade damaged library data')
    uninstaller = destination / 'unins000.exe'
    subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                    '/LOG=' + str(args.result.with_suffix('.uninstall.log').resolve())],
                   check=True, timeout=180)
    # Inno's uninstaller starts a temporary worker; await observable completion.
    import time
    deadline = time.monotonic() + 20
    while executable.exists() and time.monotonic() < deadline:
        time.sleep(.2)
    if executable.exists():
        raise RuntimeError('Uninstall left the application executable')
    for folder in ('library', 'bios'):
        if not (destination / folder / 'user-data.txt').is_file():
            raise RuntimeError('Uninstall removed user data')
    if startup_value() != before:
        raise RuntimeError('Installer changed an existing startup preference')
    args.result.write_text(json.dumps({'installed_executable_matches': True,
        'packaged_cli_passed': True, 'repeat_install_passed': True,
        'uninstall_passed': True, 'user_data_preserved': True,
        'existing_startup_preserved': True}, indent=2))
    print(args.result.read_text())


if __name__ == '__main__':
    main()
