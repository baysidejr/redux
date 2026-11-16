"""Controller-operated startup and Xbox-mode settings for a living-room PC."""
from __future__ import annotations

from pathlib import Path
import queue
import sys
import time

from .ui_assets import project_root
from .windows_startup import startup_status,set_start_with_windows


def application_directory():
    root=project_root()
    built=root/"dist"/"Redux"
    if (built/"Redux.exe").is_file():return built
    if (root/"Redux.exe").is_file():return root
    return Path(sys.executable).resolve().parent


def refresh_home_registration(*,force_shutdown=False):
    """Keep the registered home-app copy on this version of Redux; errors go to the log."""
    from .windows_integration import refresh_registration
    try:return refresh_registration(application_directory(),project_root(),force_shutdown=force_shutdown)
    except Exception as error:
        try:
            from .windows_paths import redux_user_data
            log=redux_user_data()/"logs"/"full-screen-experience.log"
            log.parent.mkdir(parents=True,exist_ok=True)
            with log.open("a",encoding="utf-8") as handle:handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} refresh failed: {error}\n")
        except OSError:pass
        return False


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
        ready=getattr(self,"fullscreen_ready",None)
        detail="Redux is the home screen when Windows starts" if ready else "Start this PC straight into Redux, like a console"
        self._setting_row("windows:experience","Full screen experience",detail,382,self.fullscreen_experience_menu,icon="controller",toggle=bool(ready))
        if ready is None:self._check_fullscreen_experience()

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
        checked=getattr(self,"fullscreen_events",None)
        while checked is not None:
            try:ready=checked.get_nowait()
            except queue.Empty:break
            self.fullscreen_checking=False;self.fullscreen_ready=ready;self.schedule_draw()
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

    def _refresh_home_registration(self):
        """A Redux started outside its home-app copy re-registers in the background after an update."""
        if not getattr(sys,"frozen",False):return
        from .windows_gaming import current_package_full_name
        if current_package_full_name():return
        import threading
        threading.Thread(target=refresh_home_registration,daemon=True).start()

    def _check_fullscreen_experience(self):
        """Read Windows' state once in the background; the row shows ON when it is all in place.

        The answer goes through a queue that poll_windows_experience drains on the UI tick:
        Tk must only be touched from the UI thread.
        """
        if getattr(self,"fullscreen_checking",False):return
        self.fullscreen_checking=True
        self.fullscreen_events=getattr(self,"fullscreen_events",None) or queue.Queue()
        import threading
        from .windows_integration import fullscreen_status
        def work():
            try:ready=fullscreen_status().ready
            except (OSError,ValueError):ready=False
            self.fullscreen_events.put(ready)
        threading.Thread(target=work,daemon=True).start()

    def fullscreen_experience_menu(self):
        if self.busy or self.embedded or (self.playback and self.playback.active()):
            self.notice("Finish first","Close the game or wait for the current task before changing how Windows starts.");return
        ready=getattr(self,"fullscreen_ready",False)
        message=("Redux is already the home screen of Windows' full screen experience. Set it up again to repair it."
                 if ready else
                 "Windows will start straight into Redux, the way a console does. Redux makes Windows treat this PC as a "
                 "gaming handheld, turns on Developer Mode, adds Redux to the Xbox home-app list and picks it as home. "
                 "Windows asks for administrator approval once. Restart afterwards to finish.")
        self.menu("Full screen experience",[("Cancel",lambda:None),("Set Redux as full screen experience",self.set_up_fullscreen_experience)],message=message)

    def set_up_fullscreen_experience(self):
        def work():
            from .windows_integration import set_up_fullscreen_experience
            return set_up_fullscreen_experience(application_directory(),project_root())
        def done(status):
            self.fullscreen_ready=status.ready
            self.menu("Restart to finish",[("Restart now",lambda:self._close_now(power="restart")),("Later",lambda:None)],
                      message="Redux is now the full screen experience home. After the restart Windows opens Redux instead of the desktop.")
        self.task(work,done,
                  "Setting up the full screen experience…",cancellable=False)
