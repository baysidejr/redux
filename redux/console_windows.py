"""Controller-operated startup and Xbox-mode settings for a living-room PC."""
from __future__ import annotations

import os
from pathlib import Path
import queue
import sys

from .ui_assets import project_root
from .windows_gaming import gaming_configuration
from .windows_startup import startup_status,set_start_with_windows


def application_directory():
    root=project_root()
    built=root/"dist"/"Redux"
    if (built/"Redux.exe").is_file():return built
    if (root/"Redux.exe").is_file():return root
    return Path(sys.executable).resolve().parent


class ConsoleWindows:
    def _windows_settings(self):
        executable=application_directory()/"Redux.exe"
        status=startup_status(executable)
        enabled=status.enabled and not status.disabled_by_windows
        detail="Opens Redux fullscreen after sign-in" if enabled else "Keep Redux ready when you turn on this PC"
        if status.disabled_by_windows:detail="Disabled in Windows Startup Apps · A enables it"
        self._setting_row("windows:startup","Start with Windows",detail,218,self.toggle_windows_startup,icon="home",toggle=enabled)
        full=bool(self.preferences.get("fullscreen",True))
        self._setting_row("windows:fullscreen","Open fullscreen","Fill the TV when Redux opens",300,self.toggle_launch_fullscreen,icon="systems",toggle=full)
        self._setting_row("windows:home","Xbox home app","Register Redux or choose your home experience",382,self.xbox_home_menu,icon="controller")
        active=self.windows_experience.query().active
        self._setting_row("windows:launch-xbox","Xbox mode","Active · open Windows Xbox mode settings" if active else "Enter the Windows gaming experience",464,self.launch_xbox_mode,icon="play")
        self._setting_row("windows:advanced","Windows integration options","Startup beside Xbox, setup and removal",546,self.windows_options_menu,icon="settings")

    def toggle_windows_startup(self):
        exe=application_directory()/"Redux.exe"
        current=startup_status(exe)
        result=set_start_with_windows(not(current.enabled and not current.disabled_by_windows),exe)
        self.status.set("Redux will open fullscreen with Windows" if result.enabled else "Windows startup turned off")
        self.schedule_draw()

    def toggle_launch_fullscreen(self):
        enabled=not bool(self.preferences.get("fullscreen",True))
        self.root.attributes("-fullscreen",enabled)
        self._save_preferences();self.schedule_draw()

    def open_xbox_settings(self):
        os.startfile("ms-settings:gaming-fullscreen")

    def open_developer_settings(self):
        os.startfile("ms-settings:developers")

    def launch_xbox_mode(self):
        if self.windows_experience.query().active:self.open_xbox_settings();return
        self.windows_experience.set_active(True)
        self.status.set("Entering Xbox mode…")

    def is_gaming_home(self):
        experience=getattr(self,"windows_experience",None)
        return bool(getattr(self,"home_activation",False) and experience and experience.query().active)

    def handle_activation(self,activation):
        # A second shell activation brings the same UI back. Do not discard a
        # running game or an in-progress disc operation to draw the home page.
        if activation.home:self.home_activation=True
        if activation.fullscreen:self.root.attributes("-fullscreen",True)
        self.root.deiconify();self.root.lift()
        self.root.focus_force();self.canvas.focus_set()
        if not self.busy:
            if self.embedded or self.playback and self.playback.active():
                if not self.overlay:self.game_menu()
            elif activation.home:self.navigate("home",remember=False)
        self.schedule_draw()

    def poll_windows_experience(self):
        events=getattr(self,"windows_events",None)
        if events is None:return
        changed=None
        while True:
            try:changed=events.get_nowait()
            except queue.Empty:break
        if changed is not None:
            if changed.active:self.root.attributes("-fullscreen",True)
            else:self.home_activation=False
            self.schedule_draw()

    def xbox_home_menu(self):
        from .windows_integration import identity_status
        def done(package):
            config=gaming_configuration()
            detail="Redux is registered with Windows." if package else "Register Redux to make it available to Windows Xbox mode."
            if not config.handheld:detail+=" This desktop needs the advanced home-picker setup before Windows shows the home selector."
            if not config.developer_mode and not package:detail+=" Developer Mode is required during package registration."
            items=[]
            if package:
                items.extend([("Use Redux as Xbox home",lambda:self.select_xbox_home(package["aumid"])),
                              ("Keep Xbox as home",self.select_xbox_home)])
            else:items.append(("Register Redux with Windows",self.register_xbox_home))
            items.extend([("Windows Xbox mode settings",self.open_xbox_settings),("Back",self.windows_options_menu)])
            self.menu("Xbox home app",items,message=detail)
        self.task(identity_status,done,"Checking Windows registration…",cancellable=False)

    def register_xbox_home(self):
        from .windows_integration import register_identity
        config=gaming_configuration()
        if not config.developer_mode:
            self.menu("Windows developer setup",[("Open Developer Mode settings",self.open_developer_settings),("Check and register Redux",self.register_xbox_home),("Back",self.windows_options_menu)],
                message="Windows requires Developer Mode to register this home-app package. Turn it on in Windows, then choose Check and register Redux. It can be turned off after registration.")
            return
        self.task(lambda:register_identity(application_directory(),project_root()),lambda _:self.xbox_home_menu(),"Registering Redux with Windows…",cancellable=False)

    def select_xbox_home(self,aumid=None):
        from .windows_integration import select_home_app
        select_home_app(aumid)
        self.status.set("Redux selected as Xbox home" if aumid else "Xbox remains your home app")
        self.xbox_home_menu()

    def windows_options_menu(self):
        status=startup_status(application_directory()/"Redux.exe")
        self.menu("Windows integration",[
            ("Start beside Xbox mode   ·   "+("ON" if status.with_xbox_mode else "OFF"),self.toggle_xbox_startup),
            ("Show Redux in Xbox My Apps",lambda:self.change_xbox_app(True)),
            ("Xbox home app setup",self.xbox_home_menu),
            ("Advanced desktop home picker",self.desktop_home_picker_menu),
            ("Remove Windows integration",self.confirm_remove_windows_integration),
            ("Back to settings",lambda:self.navigate("settings",remember=False))],
            message="Startup can keep Redux available while Xbox remains the Windows home app.")

    def change_xbox_app(self,enabled):
        from .windows_integration import register_xbox_app
        self.task(lambda:register_xbox_app(enabled,application_directory()),
            lambda _:self.notice("Xbox My Apps","Redux is available in Xbox My Apps. Reopen the Xbox app if it was already running." if enabled else "Redux's Xbox My Apps entry was removed."),
            "Updating Xbox My Apps…",cancellable=False)

    def toggle_xbox_startup(self):
        exe=application_directory()/"Redux.exe"
        status=startup_status(exe)
        if not status.enabled:
            self.notice("Start with Windows is off","Enable Start with Windows first. Then choose whether Redux should also start beside Xbox mode.")
            return
        set_start_with_windows(True,exe,with_xbox_mode=not status.with_xbox_mode)
        self.windows_options_menu()

    def desktop_home_picker_menu(self):
        self.menu("Desktop home picker",[
            ("Enable desktop home picker",lambda:self.change_desktop_home_picker(True)),
            ("Restore original device type",lambda:self.change_desktop_home_picker(False)),
            ("Back",self.windows_options_menu)],
            message="Advanced Windows setup: reports this PC as a gaming handheld to expose the home-app selector. This can affect other Windows behavior, may change after updates, and needs an administrator and a reboot. The original device type is backed up before any change.")

    def change_desktop_home_picker(self,enabled):
        from .windows_integration import change_device_form
        self.task(lambda:change_device_form(enabled,application_directory()),
            lambda result:self.notice("Windows setup",result.get("message","Restart Windows to apply the device-type change.")),
            "Preparing Windows home picker…",cancellable=False)

    def confirm_remove_windows_integration(self):
        self.menu("Remove Windows integration?",[("Keep integration",self.windows_options_menu),("Remove startup and home registration",self.remove_windows_integration),("Remove Xbox My Apps entry",lambda:self.change_xbox_app(False))],
            message="Your games, saves and app folder stay in place. If Redux is the selected home app, Windows will return to Xbox. Restore the device type separately from Advanced desktop home picker.")

    def remove_windows_integration(self):
        from .windows_integration import unregister_identity
        def work():
            set_start_with_windows(False,application_directory()/"Redux.exe")
            return unregister_identity(application_directory())
        self.task(work,lambda _:self.notice("Windows integration removed","Redux will no longer start with Windows. The application and library are still available."),"Removing Windows integration…",cancellable=False)
