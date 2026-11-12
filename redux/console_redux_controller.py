"""Console screens and hooks for the Redux Controller phone companion."""
from __future__ import annotations

import threading
from pathlib import Path

from .redux_controller import ReceiverClient, describe, profile_for_title, receiver_executable, start_receiver
from .ui_assets import project_root


class ConsoleReduxController:
    """Mixin for ConsoleApp: Phone controller menu, receiver set-up and overlay hand-off."""

    def _redux_controller(self):
        client = getattr(self, "_redux_controller_client", None)
        if client is None:
            client = self._redux_controller_client = ReceiverClient()
        return client

    def _redux_controller_preferences(self):
        return self.preferences.setdefault("redux_controller", {})

    def redux_controller_menu(self):
        if self.busy:
            return
        client = self._redux_controller()
        state = client.state()
        executable = receiver_executable(project_root(), self.preferences)
        preferences = self._redux_controller_preferences()
        items = []
        if state is None:
            if executable:
                items.append(("Set up the phone controller", self.setup_redux_receiver))
                items.append(("Start the receiver now", self.start_redux_receiver))
            else:
                items.append(("Locate ReduxReceiver.exe…", self.locate_redux_receiver))
        elif state.get("phonePaired"):
            items.append(("Disconnect the phone", lambda: self._redux_controller_background(client.disconnect)))
        items.append((f"Start the receiver with Redux: {'ON' if preferences.get('autostart') else 'OFF'}", self.toggle_redux_receiver_autostart))
        items.append((f"Send game overlays to the phone: {'ON' if preferences.get('overlays', True) else 'OFF'}", self.toggle_redux_overlays))
        items.append(("Back", lambda: None))
        self.menu("Phone controller · Redux Controller", items, message=describe(state, executable))

    def setup_redux_receiver(self):
        """First run: one elevated launch installs the driver, allows the firewall and registers Start with Windows."""
        self._launch_redux_receiver(setup=True)

    def start_redux_receiver(self):
        self._launch_redux_receiver(setup=False)

    def _launch_redux_receiver(self, *, setup):
        executable = receiver_executable(project_root(), self.preferences)
        if not executable:
            self.locate_redux_receiver()
            return
        try:
            start_receiver(executable, setup=setup)
        except OSError as error:
            self.notice("Receiver did not start", str(error))
            return
        if setup:
            self.status.set("Setting up the phone controller • accept the Windows permission prompt; the receiver installs its driver, allows itself through the firewall and will start at sign-in")
        else:
            self.status.set("Starting the Redux Controller receiver • accept the Windows permission prompt")

    def locate_redux_receiver(self):
        preferences = self._redux_controller_preferences()
        start = Path(str(preferences.get("receiver") or "")).parent
        self.open_file_browser("Locate ReduxReceiver.exe", mode="files", on_select=self._redux_receiver_located,
                               extensions={".exe"}, initialdir=str(start) if start.is_dir() else None)

    def _redux_receiver_located(self, path):
        path = Path(path[0] if isinstance(path, (list, tuple)) else path)
        if path.name.casefold() != "reduxreceiver.exe":
            self.notice("Choose ReduxReceiver.exe", "Pick the receiver's own executable from its extracted folder.")
            return
        self._redux_controller_preferences()["receiver"] = str(path)
        self._save_preferences()
        self.redux_controller_menu()

    def toggle_redux_receiver_autostart(self):
        preferences = self._redux_controller_preferences()
        preferences["autostart"] = not preferences.get("autostart")
        self._save_preferences()
        self.redux_controller_menu()

    def toggle_redux_overlays(self):
        preferences = self._redux_controller_preferences()
        preferences["overlays"] = not preferences.get("overlays", True)
        self._save_preferences()
        self.redux_controller_menu()

    def _redux_controller_startup(self):
        """At console start: launch the installed receiver when the user asked for that."""
        preferences = self._redux_controller_preferences()
        if not preferences.get("autostart"):
            return
        executable = receiver_executable(project_root(), self.preferences)
        if not executable:
            return
        def work():
            if self._redux_controller().state() is None:
                try:
                    start_receiver(executable)
                except OSError:
                    pass
        threading.Thread(target=work, name="redux-controller-start", daemon=True).start()

    def _redux_controller_send_profile(self, game):
        """Hand the phone the overlay for a launched Jaguar game, or clear it when none runs."""
        if not self._redux_controller_preferences().get("overlays", True):
            return
        profile = profile_for_title(game.get("title"), game.get("system")) if game else None
        if game and profile is None:
            return
        client = self._redux_controller()
        self._redux_controller_background(lambda: client.set_profile(profile))

    @staticmethod
    def _redux_controller_background(call):
        def work():
            try:
                call()
            except (OSError, ValueError):
                pass
        threading.Thread(target=work, name="redux-controller", daemon=True).start()
