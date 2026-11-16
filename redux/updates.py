"""Updating Redux from its GitHub releases: check, download, verify, hand off to the installer.

Nothing here runs on its own. System → About → Updates asks GitHub for the
latest release, compares it with the running version, downloads the installer
into the user's data folder, checks it against the SHA256SUMS file published
beside it, and finally starts a small helper that waits for Redux to close,
runs the installer silently and opens Redux again.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from . import net

REPOSITORY = "baysidejr/redux"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
INSTALLER_PATTERN = re.compile(r"^ReduxSetup-(\d+\.\d+\.\d+)-win-x64\.exe$")
SUMS_PATTERN = re.compile(r"^SHA256SUMS-(\d+\.\d+\.\d+)\.txt$")
MAX_INSTALLER_BYTES = 1024 ** 3
USER_AGENT = "Redux"


class UpdateError(RuntimeError):
    pass


def parse_version(text):
    """'v0.11.2' or '0.11.2' -> (0, 11, 2); anything else is None."""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return tuple(int(part) for part in match.groups()) if match else None


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


def _open(url, opener=None, timeout=30):
    opener = opener or (lambda u: net.urlopen(u, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}, timeout=timeout))
    return opener(url)


def latest_release(opener=None):
    """The latest release's version, notes and installer, or raise UpdateError."""
    try:
        with _open(LATEST_URL, opener) as response:
            data = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except (OSError, ValueError) as error:
        raise UpdateError(f"GitHub could not be reached: {error}") from error
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise UpdateError("GitHub returned no release.")
    installer = sums = None
    for asset in data.get("assets") or []:
        name = str(asset.get("name", ""))
        if INSTALLER_PATTERN.match(name):
            installer = asset
        elif SUMS_PATTERN.match(name):
            sums = asset
    version = parse_version(data["tag_name"])
    if not version or not installer or not installer.get("browser_download_url"):
        raise UpdateError("The latest release has no Windows installer attached.")
    return dict(version=".".join(map(str, version)), tag=data["tag_name"], name=data.get("name") or data["tag_name"],
                notes=(data.get("body") or "").strip(), installer_url=installer["browser_download_url"],
                installer_name=installer["name"], installer_bytes=int(installer.get("size") or 0),
                sums_url=(sums or {}).get("browser_download_url"), page=data.get("html_url") or f"https://github.com/{REPOSITORY}/releases")


def expected_digest(sums_text, filename):
    """The hash for one file in a 'sha256  name' listing, or None."""
    for line in str(sums_text or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def download_installer(release, destination_dir, *, progress=None, cancelled=None, opener=None):
    """Fetch the installer into destination_dir and verify it against the release's checksums."""
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    expected = None
    if release.get("sums_url"):
        try:
            with _open(release["sums_url"], opener) as response:
                expected = expected_digest(response.read(1 << 20).decode("utf-8", "replace"), release["installer_name"])
        except OSError as error:
            raise UpdateError(f"The release's checksum file could not be read: {error}") from error
        if not expected:
            raise UpdateError("The release's checksum file does not list the installer.")
    total = int(release.get("installer_bytes") or 0)
    if total > MAX_INSTALLER_BYTES:
        raise UpdateError("The installer is larger than Redux accepts.")
    digest = hashlib.sha256()
    done = 0
    with tempfile.NamedTemporaryFile(prefix="ReduxSetup-", suffix=".part", dir=destination_dir, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            with _open(release["installer_url"], opener, timeout=60) as response:
                while True:
                    if cancelled is not None and cancelled.is_set():
                        raise UpdateError("Download cancelled.")
                    chunk = response.read(1 << 18)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > MAX_INSTALLER_BYTES:
                        raise UpdateError("The installer is larger than Redux accepts.")
                    digest.update(chunk)
                    temp.write(chunk)
                    if progress:
                        progress(done, total)
        except OSError as error:
            temp.close()
            temp_path.unlink(missing_ok=True)
            raise UpdateError(f"The download failed: {error}") from error
        except UpdateError:
            temp.close()
            temp_path.unlink(missing_ok=True)
            raise
    if expected and digest.hexdigest() != expected:
        temp_path.unlink(missing_ok=True)
        raise UpdateError("The downloaded installer did not match the release's checksum, so it was discarded.")
    final = destination_dir / release["installer_name"]
    final.unlink(missing_ok=True)
    temp_path.rename(final)
    return final


def helper_script(installer, redux_executable, pid):
    """A batch file that waits for this Redux to exit, installs silently, and opens Redux again.

    The bundled receiver holds a mutex that makes Setup refuse to run, so its
    scheduled task is stopped first and started again afterwards.
    """
    return "\r\n".join([
        "@echo off",
        ":wait",
        f'tasklist /FI "PID eq {int(pid)}" 2>nul | find "{int(pid)}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
        'schtasks /End /TN "Redux Controller Receiver" >nul 2>&1',
        "taskkill /IM ReduxReceiver.exe /F >nul 2>&1",
        f'"{installer}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
        'schtasks /Run /TN "Redux Controller Receiver" >nul 2>&1',
        f'start "" "{redux_executable}"',
        f'del "%~f0"',
        "",
    ])


def launch_installer(installer, redux_executable=None):
    """Start the detached helper; the caller then closes Redux."""
    installer = Path(installer)
    if not installer.is_file():
        raise UpdateError("The downloaded installer is missing.")
    executable = Path(redux_executable) if redux_executable else (Path(sys.executable) if getattr(sys, "frozen", False) else None)
    if executable is None:
        raise UpdateError("Updating in place needs the installed Redux, not a source checkout.")
    script = installer.with_suffix(".cmd")
    script.write_text(helper_script(installer, executable, os.getpid()), encoding="utf-8")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(installer.parent))
    return script
