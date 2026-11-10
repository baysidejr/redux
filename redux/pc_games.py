"""Launch installed Windows games directly, tracking their owned child processes.

No Galaxy protocol, shell, input injection or controller remapping is used.
An unconfigured Windows Job Object tracks child handoffs without terminating
games when the frontend exits. Processes start suspended until assigned so an
early launcher exit cannot race lifetime tracking.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time


def validate_executable(value):
    path=Path(value).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.casefold()!=".exe":
        raise ValueError("Choose the installed game's Windows EXE, not a shortcut or installer")
    with path.open("rb") as stream:
        if stream.read(2)!=b"MZ":raise ValueError("The selected file is not a Windows executable")
    return path


def launch_pc_game(executable, arguments, game_path, cwd=None):
    path=validate_executable(executable)
    arguments=validate_arguments(arguments)
    if Path(game_path).resolve()!=path:raise ValueError("PC shortcut and executable do not match")
    folder=path.parent if cwd is None else Path(cwd).resolve(strict=True)
    if not folder.is_dir():raise ValueError("The game's working folder is unavailable")
    if os.name!="nt":raise OSError("PC game EXE launching requires Windows")
    return WindowsGameProcess([str(path),*arguments],folder)


def validate_arguments(arguments):
    if not isinstance(arguments,list) or len(arguments)>64 or any(not isinstance(arg,str) or '\0' in arg or len(arg)>4096 for arg in arguments):
        raise ValueError("Launch arguments must be a list of at most 64 text values")
    if sum(map(len,arguments))>16000:raise ValueError("Launch arguments are too long")
    return list(arguments)


class WindowsGameProcess:
    """Small Popen-compatible owner for a game and its ordinary child processes."""
    def __init__(self,argv,cwd):
        import ctypes as c
        from ctypes import wintypes as w
        self.c,self.w=c,w
        self.kernel=c.WinDLL("kernel32",use_last_error=True)
        self.job=self.handle=None;self.returncode=None
        def bind(name,result,args):
            fn=getattr(self.kernel,name);fn.restype=result;fn.argtypes=args;return fn
        class Startup(c.Structure):
            _fields_=[("cb",w.DWORD),("reserved",w.LPWSTR),("desktop",w.LPWSTR),("title",w.LPWSTR),
                ("x",w.DWORD),("y",w.DWORD),("width",w.DWORD),("height",w.DWORD),
                ("xchars",w.DWORD),("ychars",w.DWORD),("fill",w.DWORD),("flags",w.DWORD),
                ("show",w.WORD),("reserved_size",w.WORD),("reserved_bytes",c.POINTER(c.c_byte)),
                ("stdin",w.HANDLE),("stdout",w.HANDLE),("stderr",w.HANDLE)]
        class Process(c.Structure):
            _fields_=[("process",w.HANDLE),("thread",w.HANDLE),("pid",w.DWORD),("tid",w.DWORD)]
        class Accounting(c.Structure):
            _fields_=[("user",c.c_longlong),("kernel",c.c_longlong),("period_user",c.c_longlong),
                ("period_kernel",c.c_longlong),("faults",w.DWORD),("total",w.DWORD),
                ("active",w.DWORD),("terminated",w.DWORD)]
        self.accounting_type=Accounting
        create_job=bind("CreateJobObjectW",w.HANDLE,[c.c_void_p,w.LPCWSTR])
        create=bind("CreateProcessW",w.BOOL,[w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,
            c.c_void_p,w.LPCWSTR,c.POINTER(Startup),c.POINTER(Process)])
        assign=bind("AssignProcessToJobObject",w.BOOL,[w.HANDLE,w.HANDLE])
        resume=bind("ResumeThread",w.DWORD,[w.HANDLE])
        self.query=bind("QueryInformationJobObject",w.BOOL,[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)])
        self.exit_code=bind("GetExitCodeProcess",w.BOOL,[w.HANDLE,c.POINTER(w.DWORD)])
        self.close_handle=bind("CloseHandle",w.BOOL,[w.HANDLE])
        self.terminate_job=bind("TerminateJobObject",w.BOOL,[w.HANDLE,w.UINT])
        terminate=bind("TerminateProcess",w.BOOL,[w.HANDLE,w.UINT])
        startup=Startup();startup.cb=c.sizeof(startup);info=Process()
        try:
            self.job=create_job(None,None)
            if not self.job:raise c.WinError(c.get_last_error())
            command=c.create_unicode_buffer(subprocess.list2cmdline(argv))
            if not create(argv[0],command,None,None,False,0x08000004,None,str(cwd),c.byref(startup),c.byref(info)):
                raise c.WinError(c.get_last_error())
            self.handle=info.process;self.pid=info.pid
            if not assign(self.job,self.handle):raise c.WinError(c.get_last_error())
            if resume(info.thread)==0xffffffff:raise c.WinError(c.get_last_error())
        except BaseException:
            if self.handle:terminate(self.handle,1)
            self.close()
            raise
        finally:
            if info.thread:self.close_handle(info.thread)

    def poll(self):
        if self.returncode is not None:return self.returncode
        value=self.accounting_type()
        if not self.query(self.job,1,self.c.byref(value),self.c.sizeof(value),None):
            raise self.c.WinError(self.c.get_last_error())
        if value.active:return None
        code=self.w.DWORD()
        if not self.exit_code(self.handle,self.c.byref(code)):raise self.c.WinError(self.c.get_last_error())
        self.returncode=code.value
        return self.returncode

    def active_pids(self):
        # A bounded process-ID list belongs only to this private Job Object.
        for count in (32,128,512,2048):
            class IDs(self.c.Structure):
                _fields_=[("assigned",self.w.DWORD),("listed",self.w.DWORD),("pids",self.c.c_size_t*count)]
            value=IDs()
            if self.query(self.job,3,self.c.byref(value),self.c.sizeof(value),None):
                return set(value.pids[:value.listed])
            if self.c.get_last_error()!=234:raise self.c.WinError(self.c.get_last_error())
        raise OSError("The game's process family exceeded the supported tracking limit")

    def wait(self,timeout=None):
        deadline=None if timeout is None else time.monotonic()+timeout
        while (code:=self.poll()) is None:
            if deadline is not None and time.monotonic()>=deadline:
                raise subprocess.TimeoutExpired("PC game",timeout)
            time.sleep(.02)
        return code

    def kill(self):
        if not self.terminate_job(self.job,1):raise self.c.WinError(self.c.get_last_error())

    def close(self):
        # No KILL_ON_JOB_CLOSE flag: detaching the frontend leaves the game alive.
        for name in ("handle","job"):
            handle=getattr(self,name,None)
            if handle:self.close_handle(handle);setattr(self,name,None)


class PCGameControl:
    def __init__(self,process):self.process=process
    def request_close(self):self._windows(close=True)
    def focus(self):self._windows(close=False)
    def close(self):self.process.close()
    def _windows(self,*,close):
        c,w=self.process.c,self.process.w
        user=c.WinDLL("user32",use_last_error=True)
        callback_type=c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
        user.EnumWindows.argtypes=[callback_type,w.LPARAM];user.EnumWindows.restype=w.BOOL
        user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
        user.IsWindowVisible.argtypes=[w.HWND];user.IsWindowVisible.restype=w.BOOL
        user.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];user.PostMessageW.restype=w.BOOL
        user.SetForegroundWindow.argtypes=[w.HWND];user.SetForegroundWindow.restype=w.BOOL
        user.ShowWindow.argtypes=[w.HWND,c.c_int]
        pids=self.process.active_pids()
        @callback_type
        def visit(window,param):
            pid=w.DWORD();user.GetWindowThreadProcessId(window,c.byref(pid))
            if pid.value in pids and user.IsWindowVisible(window):
                if close:user.PostMessageW(window,0x10,0,0)
                else:user.ShowWindow(window,9);user.SetForegroundWindow(window);return False
            return True
        user.EnumWindows(visit,0)
