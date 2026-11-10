"""Owned emulator-process lifecycle and explicit backend capability profiles.

Processes are launched through launch.py with separate arguments and no shell.
This module never injects hotkeys, reads retail firmware from unrelated folders,
or claims that an external emulator exposes a universal save-state interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Callable, Mapping
import uuid

from .launch import launch_game


class PlaybackError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendCapabilities:
    process_pause: bool = True
    graceful_stop: bool = True
    frontend_save_states: bool = False
    frontend_screenshots: bool = False
    external_save_files: bool = True


@dataclass(frozen=True)
class BackendProfile:
    id: str
    name: str
    systems: tuple[str, ...]
    arguments: tuple[str, ...]
    executable_names: tuple[str, ...]
    notes: str
    firmware: str = "none"
    capabilities: BackendCapabilities = field(default_factory=BackendCapabilities)


_PROFILES = (
    BackendProfile("mesen", "Mesen", ("nes","snes","gb","gbc","pce","sms","game_gear"),
        ("--fullscreen","{game}"), ("Mesen.exe","MesenCE.exe"),
        "Basic cartridge playback; special chips or CD systems can need additional files."),
    BackendProfile("pcsx_redux", "PCSX-Redux / OpenBIOS", ("psx",),
        ("-iso","{game}","-run","-bios","openbios.bin","-portable","-noupdate","-no-debugger","-no-viewports"),
        ("PCSXReduxCore.exe","pcsx-redux.exe"),
        "Uses the bundled open-source OpenBIOS. Game compatibility must be verified; no retail firmware is downloaded.",
        "openbios"),
    BackendProfile("duckstation", "DuckStation", ("psx",),
        ("-batch","-fullscreen","--","{game}"),
        ("duckstation-qt-x64-ReleaseLTCG.exe","duckstation-qt-x64-Release.exe","duckstation.exe"),
        "Requires a separately supplied compatible PlayStation BIOS.", "psx"),
    BackendProfile("mednafen", "Mednafen", ("nes","snes","gb","gbc","gba","genesis","sms","game_gear","pce","pce_cd","psx","saturn"),
        ("-video.fs","1","{game}"), ("mednafen.exe",),
        "Disc systems require their documented firmware and compatible images.", "system"),
    BackendProfile("mupen64plus", "Mupen64Plus", ("n64",),
        ("--fullscreen","{game}"), ("mupen64plus-ui-console.exe","mupen64plus.exe"),
        "The installation must include compatible video, audio, input and RSP plugins."),
    BackendProfile("custom", "Custom emulator", ("*",), ("{game}",), (),
        "Compatibility and firmware are controlled by the configured emulator.", "unknown"),
)

_PC_PROFILE=BackendProfile("pc_game","Installed PC game",("pc",),(),(),
    "Controls, saves and display settings are provided by the game.",
    capabilities=BackendCapabilities(process_pause=False,external_save_files=False))


def backend_profiles() -> tuple[BackendProfile, ...]:
    return _PROFILES


def get_profile(profile_id: str) -> BackendProfile:
    if profile_id=="pc_game":return _PC_PROFILE
    try:
        return next(p for p in _PROFILES if p.id == profile_id)
    except StopIteration as error:
        raise ValueError(f"Unknown backend profile: {profile_id}") from error


def profile_for_executable(path: str | Path) -> BackendProfile:
    name = Path(path).name.casefold()
    for profile in _PROFILES:
        if name in {n.casefold() for n in profile.executable_names}:
            return profile
    return get_profile("custom")


def profile_arguments(profile_id: str, executable: str | Path, *, fullscreen: bool = True) -> list[str]:
    profile = get_profile(profile_id)
    arguments = list(profile.arguments)
    if not fullscreen:
        if profile_id in {"mesen","mupen64plus"}:
            arguments.remove("--fullscreen")
        elif profile_id == "duckstation":
            arguments[arguments.index("-fullscreen")] = "-nofullscreen"
        elif profile_id == "mednafen":
            arguments[arguments.index("-video.fs")+1] = "0"
    if profile_id == "pcsx_redux":
        arguments[arguments.index("openbios.bin")] = str(Path(executable).expanduser().resolve().parent / "openbios.bin")
        # Supplying an explicit portable path makes state/memory-card placement
        # independent of the frontend's current directory.
        arguments.insert(arguments.index("-portable")+1, str(Path(executable).expanduser().resolve().parent))
    return arguments


@dataclass(frozen=True)
class DiscoveredBackend:
    profile_id: str
    name: str
    executable: str
    systems: tuple[str, ...]
    arguments: tuple[str, ...]


def discover_backends(project_root: str | Path, configured: Mapping | None = None) -> list[DiscoveredBackend]:
    """Inspect explicit configuration and known tool directories, never user trees."""
    root = Path(project_root).expanduser().resolve()
    folders = {"mesen":("Mesen","MesenCE"), "pcsx_redux":("PCSXRedux",),
               "duckstation":("DuckStation",), "mednafen":("Mednafen",),
               "mupen64plus":("Mupen64Plus",)}
    result = []
    seen = set()
    for profile in _PROFILES:
        for folder in folders.get(profile.id, ()):
            for name in profile.executable_names:
                path = root / "tools" / "emulators" / folder / name
                if path.is_file():
                    resolved = str(path.resolve())
                    if resolved.casefold() not in seen:
                        result.append(DiscoveredBackend(profile.id,profile.name,resolved,profile.systems,
                            tuple(profile_arguments(profile.id,path))))
                        seen.add(resolved.casefold())
                    # Prefer the first/direct executable in each backend folder.
                    break
    for config in (configured or {}).values():
        if not isinstance(config,Mapping) or not config.get("executable"):
            continue
        path = Path(config["executable"]).expanduser().resolve()
        if path.is_file() and str(path).casefold() not in seen:
            profile = profile_for_executable(path)
            arguments = config.get("arguments",profile_arguments(profile.id,path))
            if isinstance(arguments,(list,tuple)) and all(isinstance(a,str) for a in arguments):
                result.append(DiscoveredBackend(profile.id,profile.name,str(path),profile.systems,tuple(arguments)))
                seen.add(str(path).casefold())
    return result


@dataclass(frozen=True)
class Readiness:
    ready: bool
    profile_id: str
    profile_name: str
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    firmware_files: tuple[str, ...]


def assess_readiness(system: str, executable: str | Path, *, profile_id: str | None = None,
                     firmware_dir: str | Path | None = None) -> Readiness:
    path = Path(executable).expanduser().resolve()
    profile = get_profile(profile_id) if profile_id else profile_for_executable(path)
    issues, warnings, firmware = [], [], []
    if not path.is_file(): issues.append("The configured emulator executable is unavailable.")
    if "*" not in profile.systems and system not in profile.systems:
        issues.append(f"{profile.name} is not configured to support this system.")
    if profile.firmware == "openbios":
        bios = path.parent / "openbios.bin"
        if bios.is_file() and bios.stat().st_size == 524288:
            firmware.append(str(bios))
        else: issues.append("The PCSX-Redux installation is missing its bundled 512 KiB openbios.bin.")
        warnings.append("OpenBIOS compatibility varies by game; starting the backend is not a gameplay verification.")
    elif profile.firmware == "psx" or (profile.firmware == "system" and system in {"psx","saturn","pce_cd"}):
        roots = [Path(firmware_dir).expanduser().resolve()] if firmware_dir else []
        roots += [path.parent / "bios",path.parent / "firmware"]
        expected = {"psx":("scph5500.bin","scph5501.bin","scph5502.bin"),
                    "saturn":("sega_101.bin","mpr-17933.bin"),"pce_cd":("syscard3.pce",)}.get(system,())
        for folder in roots:
            if not folder.is_dir(): continue
            for candidate in folder.iterdir():
                if not candidate.is_file(): continue
                if profile.id == "duckstation":
                    valid = candidate.suffix.casefold() in {".bin",".rom",".bios"} and candidate.stat().st_size in {524288,4194304}
                else:
                    valid = candidate.name.casefold() in expected and candidate.stat().st_size > 0
                if valid: firmware.append(str(candidate.resolve()))
        if not firmware:
            issues.append("Compatible firmware was not found in the selected firmware folder or this emulator's bios/firmware folder.")
        else:
            warnings.append("Firmware presence was checked; the emulator still validates its contents and region.")
    elif profile.firmware == "unknown":
        warnings.append("This custom backend's image and firmware requirements are not known to the frontend.")
    return Readiness(not issues,profile.id,profile.name,tuple(issues),tuple(warnings),tuple(firmware))


def suggested_save_directories(executable: str | Path, profile_id: str | None = None) -> list[Path]:
    """Known local directories only; never scrape personal folders or game media."""
    path = Path(executable).expanduser().resolve()
    profile = get_profile(profile_id) if profile_id else profile_for_executable(path)
    names = {"mesen":("SaveStates","Saves"),"duckstation":("savestates","memcards"),
             "mednafen":("sav","mcs"),"mupen64plus":("save",),"pcsx_redux":()}.get(profile.id,())
    directories = [path.parent/name for name in names if (path.parent/name).is_dir()]
    if profile.id == "pcsx_redux":
        # PCSX-Redux stores portable .mcd/.state files beside its settings. Return
        # the root only when callers explicitly choose its save-file filters.
        directories.append(path.parent)
    return directories


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    game_id: int
    pid: int
    profile_id: str
    title: str
    started_at: str
    elapsed_seconds: float
    play_seconds: float
    paused: bool
    can_pause: bool
    frontend_save_states: bool = False
    frontend_screenshots: bool = False
    pause_source: str | None = None


@dataclass(frozen=True)
class SessionResult:
    session_id: str
    game_id: int
    pid: int
    profile_id: str
    title: str
    started_at: str
    ended_at: str
    elapsed_seconds: float
    play_seconds: float
    exit_code: int
    forced: bool

    @property
    def duration_seconds(self):
        return self.play_seconds


@dataclass
class _Session:
    id: str
    game_id: int
    process: subprocess.Popen
    profile: BackendProfile
    title: str
    started_at: str
    started_clock: float
    control: object | None
    paused_since: float | None = None
    paused_total: float = 0
    forced: bool = False
    pause_source: str | None = None


class PlaybackManager:
    """Track only processes launched by this instance, including pause duration.

    pause/resume use balanced owned-thread suspension on Windows or SIGSTOP/
    SIGCONT on POSIX. This freezes the process, not an emulated save state.
    stop defaults to a graceful close request. force=True is explicit and can
    discard unsaved emulator data. Run blocking stop calls outside a UI thread.
    """
    def __init__(self, *, clock: Callable[[],float] = time.monotonic, launcher=launch_game, control_factory=None,
                 pc_launcher=None,pc_control_factory=None):
        from .pc_games import launch_pc_game,PCGameControl
        self._pc_launcher=pc_launcher or launch_pc_game
        self._pc_control_factory=pc_control_factory or PCGameControl
        self._clock, self._launcher = clock, launcher
        self._control_factory = control_factory or _process_controller
        self._sessions: dict[str,_Session] = {}
        self._lock = threading.RLock()

    def launch(self, game_id: int, executable, arguments, game_path, *, profile_id=None, title=None, cwd=None) -> SessionSnapshot:
        with self._lock:
            if self._sessions and any(s.process.poll() is None for s in self._sessions.values()):
                raise PlaybackError("An emulator is already running. Return to or stop it before starting another game.")
            profile=get_profile(profile_id) if profile_id else profile_for_executable(executable)
            native=profile.id=="pc_game"
            process=(self._pc_launcher if native else self._launcher)(executable,arguments,game_path,cwd=cwd)
            control=None
            if process.poll() is None:
                try: control=(self._pc_control_factory if native else self._control_factory)(process)
                except (OSError,PlaybackError): pass
            session=_Session(uuid.uuid4().hex,int(game_id),process,profile,title or Path(game_path).stem,
                datetime.now(timezone.utc).isoformat(),self._clock(),control)
            self._sessions[session.id]=session
            return self._snapshot(session)

    def _get(self, session_id):
        try: return self._sessions[session_id]
        except KeyError as error: raise PlaybackError("This playback session is no longer active.") from error

    def _snapshot(self, session):
        now=self._clock()
        pause=session.paused_total+(now-session.paused_since if session.paused_since is not None else 0)
        elapsed=max(0,now-session.started_clock)
        return SessionSnapshot(session.id,session.game_id,session.process.pid,session.profile.id,session.title,
            session.started_at,elapsed,max(0,elapsed-pause),session.paused_since is not None,session.control is not None and session.profile.capabilities.process_pause,
            pause_source=session.pause_source)

    def active(self) -> list[SessionSnapshot]:
        with self._lock:
            return [self._snapshot(s) for s in self._sessions.values() if s.process.poll() is None]

    def pause(self, session_id) -> bool:
        with self._lock:
            session=self._get(session_id)
            if not session.profile.capabilities.process_pause:
                raise PlaybackError("Pause this PC game using its own game menu")
            if session.process.poll() is not None: return False
            if session.pause_source=="backend":
                raise PlaybackError("Resume the emulator's own pause before applying a process pause.")
            if session.paused_since is not None: return True
            if session.control is None: raise PlaybackError("Process pause is unavailable for this session.")
            session.control.pause()
            session.paused_since=self._clock()
            session.pause_source="process"
            return True

    def resume(self, session_id) -> bool:
        with self._lock:
            session=self._get(session_id)
            if session.process.poll() is not None: return False
            if session.pause_source=="backend":
                raise PlaybackError("Resume this session through its emulator bridge.")
            if session.paused_since is None: return True
            if session.control is None: raise PlaybackError("Process resume is unavailable for this session.")
            session.control.resume()
            session.paused_total+=max(0,self._clock()-session.paused_since)
            session.paused_since=None
            session.pause_source=None
            return True

    def foreground(self,session_id):
        with self._lock:
            session=self._get(session_id)
            if session.control and hasattr(session.control,"focus"):session.control.focus()

    def set_backend_paused(self, session_id, paused: bool) -> bool:
        """Record an acknowledged emulator-API pause without freezing its host.

        Call only after the backend bridge confirms its pause/resume command.
        The bridge event loop must stay alive to service state and image RPCs.
        This method never sends commands and must not clear a native pause.
        """
        if not isinstance(paused,bool):raise TypeError("Backend paused state must be a boolean")
        with self._lock:
            session=self._get(session_id)
            if session.process.poll() is not None:return False
            if session.pause_source=="process":
                raise PlaybackError("Resume the native process pause before using the emulator bridge.")
            if paused and session.paused_since is None:
                session.paused_since=self._clock();session.pause_source="backend"
            elif not paused and session.paused_since is not None:
                session.paused_total+=max(0,self._clock()-session.paused_since)
                session.paused_since=None;session.pause_source=None
            return True

    def stop(self, session_id, *, force=False, grace_seconds=3.0) -> bool:
        if not 0 <= grace_seconds <= 30: raise ValueError("Grace period must be 0–30 seconds")
        with self._lock:
            session=self._get(session_id)
            if session.process.poll() is not None: return True
            if session.pause_source=="process" and not force:
                self.resume(session_id)
            if force:
                session.forced=True
                session.process.kill()
            elif session.control is not None:
                session.control.request_close()
            elif os.name != "nt":
                session.process.terminate()
            else:
                return False
            process=session.process
        # A slow backend close must not prevent another thread from displaying
        # its running status or issuing the explicit force-stop fallback.
        try: process.wait(timeout=max(0.01,grace_seconds))
        except subprocess.TimeoutExpired: return False
        return True

    def poll(self) -> list[SessionResult]:
        results=[]
        with self._lock:
            for sid,session in list(self._sessions.items()):
                code=session.process.poll()
                if code is None: continue
                snapshot=self._snapshot(session)
                if session.control:
                    session.control.close()
                elif session.profile.id=="pc_game":session.process.close()
                results.append(SessionResult(sid,session.game_id,session.process.pid,session.profile.id,session.title,
                    session.started_at,datetime.now(timezone.utc).isoformat(),snapshot.elapsed_seconds,
                    snapshot.play_seconds,code,session.forced))
                del self._sessions[sid]
        return results

    def close(self):
        """Detach frontend control, resuming paused games rather than killing them."""
        errors=[]
        with self._lock:
            for session in self._sessions.values():
                try:
                    if session.pause_source=="process" and session.process.poll() is None:
                        self.resume(session.id)
                    if session.control: session.control.close()
                    session.control=None
                except (OSError,PlaybackError) as error: errors.append(str(error))
        if errors: raise PlaybackError("; ".join(errors))


class _PosixProcessController:
    def __init__(self, process): self.process=process;self.paused=False
    def pause(self):
        if self.process.poll() is not None: raise PlaybackError("The emulator has exited")
        if not self.paused:self.process.send_signal(signal.SIGSTOP);self.paused=True
    def resume(self):
        if self.paused and self.process.poll() is None:self.process.send_signal(signal.SIGCONT)
        self.paused=False
    def request_close(self):self.resume();self.process.terminate()
    def close(self):self.resume()


def _process_controller(process):
    return _WindowsProcessController(process) if os.name=="nt" else _PosixProcessController(process)


class _WindowsProcessController:
    """Documented thread APIs, scoped by a persistent handle to our own child.

    Every thread is owner-checked and every successful SuspendThread call has
    exactly one matching ResumeThread. No parent/emulator shared lock is acquired
    while waiting on an emulator thread. Close requests target owned windows only.
    """
    def __init__(self, process):
        import ctypes as c
        from ctypes import wintypes as w
        self.c,self.w,self.process=c,w,process
        self.k=c.WinDLL("kernel32",use_last_error=True)
        self.u=c.WinDLL("user32",use_last_error=True)
        def bind(dll,name,ret,args):
            fn=getattr(dll,name);fn.restype=ret;fn.argtypes=args;return fn
        self.open_process=bind(self.k,"OpenProcess",w.HANDLE,[w.DWORD,w.BOOL,w.DWORD])
        self.wait=bind(self.k,"WaitForSingleObject",w.DWORD,[w.HANDLE,w.DWORD])
        self.close_handle=bind(self.k,"CloseHandle",w.BOOL,[w.HANDLE])
        self.snapshot=bind(self.k,"CreateToolhelp32Snapshot",w.HANDLE,[w.DWORD,w.DWORD])
        class THREADENTRY32(c.Structure):
            _fields_=[("dwSize",w.DWORD),("cntUsage",w.DWORD),("thread_id",w.DWORD),
                ("owner_pid",w.DWORD),("base_priority",w.LONG),("delta_priority",w.LONG),("flags",w.DWORD)]
        self.entry_type=THREADENTRY32
        self.first=bind(self.k,"Thread32First",w.BOOL,[w.HANDLE,c.POINTER(THREADENTRY32)])
        self.next=bind(self.k,"Thread32Next",w.BOOL,[w.HANDLE,c.POINTER(THREADENTRY32)])
        self.open_thread=bind(self.k,"OpenThread",w.HANDLE,[w.DWORD,w.BOOL,w.DWORD])
        self.thread_owner=bind(self.k,"GetProcessIdOfThread",w.DWORD,[w.HANDLE])
        self.thread_exit=bind(self.k,"GetExitCodeThread",w.BOOL,[w.HANDLE,c.POINTER(w.DWORD)])
        self.suspend_thread=bind(self.k,"SuspendThread",w.DWORD,[w.HANDLE])
        self.resume_thread=bind(self.k,"ResumeThread",w.DWORD,[w.HANDLE])
        self.window_pid=bind(self.u,"GetWindowThreadProcessId",w.DWORD,[w.HWND,c.POINTER(w.DWORD)])
        self.post=bind(self.u,"PostMessageW",w.BOOL,[w.HWND,w.UINT,w.WPARAM,w.LPARAM])
        self.visible=bind(self.u,"IsWindowVisible",w.BOOL,[w.HWND])
        self.window_owner=bind(self.u,"GetWindow",w.HWND,[w.HWND,w.UINT])
        self.enum_callback=c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
        self.enum=bind(self.u,"EnumWindows",w.BOOL,[self.enum_callback,w.LPARAM])
        self.handle=self.open_process(0x100000|0x1000,False,process.pid)
        if not self.handle:raise c.WinError(c.get_last_error())
        self.threads={}
        if not self._alive():
            self.close_handle(self.handle);self.handle=None
            raise PlaybackError("The emulator exited before process control was initialized")

    def _alive(self):return self.handle is not None and self.wait(self.handle,0)==258

    def _thread_ids(self):
        handle=self.snapshot(4,0)
        if handle==self.c.c_void_p(-1).value:raise self.c.WinError(self.c.get_last_error())
        try:
            entry=self.entry_type();entry.dwSize=self.c.sizeof(entry)
            ok=self.first(handle,self.c.byref(entry))
            while ok:
                if entry.owner_pid==self.process.pid:yield entry.thread_id
                entry.dwSize=self.c.sizeof(entry)
                ok=self.next(handle,self.c.byref(entry))
        finally:self.close_handle(handle)

    def pause(self):
        if self.threads:return
        if not self._alive():raise PlaybackError("The emulator has exited")
        try:
            # Re-enumeration catches threads created during the first snapshot.
            for _ in range(3):
                added=0
                for tid in self._thread_ids():
                    if tid in self.threads:continue
                    if not self._alive():raise PlaybackError("The emulator exited during pause")
                    thread=self.open_thread(0x2|0x800,False,tid)
                    if not thread:
                        if self.c.get_last_error()==87:continue  # Already exited.
                        raise self.c.WinError(self.c.get_last_error())
                    if self.thread_owner(thread)!=self.process.pid or not self._alive():
                        self.close_handle(thread);continue
                    prior=self.suspend_thread(thread)
                    if prior==0xffffffff:
                        self.close_handle(thread)
                        raise self.c.WinError(self.c.get_last_error())
                    self.threads[tid]=thread;added+=1
                if not added:break
            if not self.threads:raise PlaybackError("No live emulator threads could be paused")
        except BaseException:
            self.resume()
            raise

    def resume(self):
        failed=[]
        for tid,thread in list(self.threads.items()):
            if not self._alive():
                self.close_handle(thread);del self.threads[tid];continue
            prior=self.resume_thread(thread)
            if prior==0xffffffff:
                code=self.w.DWORD()
                if self.thread_exit(thread,self.c.byref(code)) and code.value==259:
                    failed.append(tid);continue
            self.close_handle(thread);del self.threads[tid]
        if failed:raise PlaybackError("Could not resume every owned emulator thread; retry resume or explicitly force-stop it")

    def request_close(self):
        self.resume()
        if not self._alive():return
        @self.enum_callback
        def callback(window,param):
            pid=self.w.DWORD();self.window_pid(window,self.c.byref(pid))
            if self._alive() and pid.value==self.process.pid and self.visible(window) and not self.window_owner(window,4):
                self.post(window,0x10,0,0)  # WM_CLOSE, not forced process termination.
            return True
        self.enum(callback,0)

    def close(self):
        if self.handle is None:return
        self.resume()
        self.close_handle(self.handle);self.handle=None
