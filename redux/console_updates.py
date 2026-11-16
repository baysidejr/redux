"""System → About → Updates: one row that checks GitHub, downloads the newer installer and restarts into it."""
from __future__ import annotations

import time

from . import __version__
from .updates import is_newer


class ConsoleUpdates:
    def updates_row_detail(self):
        release = getattr(self, "update_release", None)
        if release and is_newer(release["version"], __version__):
            return f"Redux {release['version']} is available · A installs it"
        if getattr(self, "update_checked", False):
            return f"Redux {__version__} is the latest · A checks again"
        return f"Redux {__version__} · A checks GitHub for a newer release"

    def updates_menu(self):
        """A on the Updates row: install a known newer release, otherwise check."""
        release = getattr(self, "update_release", None)
        if release and is_newer(release["version"], __version__):
            self._offer_update(release)
        else:
            self.check_updates()

    def check_updates(self):
        if self.busy:
            return
        from .updates import latest_release
        def done(release):
            self.update_release = release
            self.update_checked = True
            if is_newer(release["version"], __version__):
                self._offer_update(release)
            else:
                self.notice("You have the latest Redux", f"Redux {__version__} is the newest release on GitHub.")
            self.schedule_draw()
        self.task(latest_release, done, "Checking GitHub for updates…")

    def _offer_update(self, release):
        size = release.get("installer_bytes") or 0
        label = f"Download and install · {size / 2**20:.0f} MB" if size else "Download and install"
        notes = (release.get("notes") or release.get("name") or "").strip()
        if len(notes) > 320:
            notes = notes[:320].rsplit(" ", 1)[0] + "…"
        self.menu(f"Redux {release['version']} is available", [(label, lambda: self.install_update(release)), ("Not now", lambda: None)],
                  message=notes)

    def install_update(self, release):
        from .updates import download_installer
        from .windows_paths import redux_user_data
        folder = redux_user_data() / "updates"
        def work():
            last = [0.0]
            def progress(done, total):
                now = time.monotonic()
                if now - last[0] > 0.2 or done == total:
                    last[0] = now
                    text = f"Downloading Redux {release['version']} • {done / 2**20:.0f} of {total / 2**20:.0f} MB" if total else f"Downloading Redux {release['version']} • {done / 2**20:.0f} MB"
                    self.events.put(("status", text))
            return download_installer(release, folder, progress=progress, cancelled=self.cancelled)
        def done(path):
            self.update_installer = path
            self.menu("Ready to install", [("Install now and restart Redux", lambda: self._apply_update(path)), ("Later", lambda: None)],
                      message=f"Redux closes, {release['installer_name']} installs silently, and Redux opens again. Your library, saves and settings stay where they are.")
        self.task(work, done, f"Downloading Redux {release['version']}…")

    def _apply_update(self, path):
        from .updates import launch_installer, UpdateError
        try:
            from .console_windows import application_directory
            launch_installer(path, application_directory()/"Redux.exe")
        except (UpdateError, OSError) as error:
            self.notice("The update could not start", str(error))
            return
        self.status.set("Installing the update…")
        self.close()
