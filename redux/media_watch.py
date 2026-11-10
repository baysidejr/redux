"""Read-only, background optical-media observation for the console frontend.

The worker owns drive queries and identification. It never calls Tk, starts a
game, captures a disc, changes AutoPlay, locks/ejects media or installs drivers.
The UI consumes immutable events through ``poll_events`` without waiting for IO.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
import queue
import threading
import time
from typing import Any, Callable

from .optical import OpticalError, NoMediaError, list_drives, normalize_drive


IOCTL_STORAGE_CHECK_VERIFY2 = 0x002D0800
IOCTL_STORAGE_CHECK_VERIFY = 0x002D4800
FILE_READ_ATTRIBUTES = 0x0080
GENERIC_READ = 0x80000000
_UNSUPPORTED = frozenset({1, 50, 87})
_NOT_READY = frozenset({21, 32, 33, 1110, 1111, 995})
_DISCONNECTED = frozenset({2, 3, 55, 1167})


@dataclass(frozen=True)
class MediaProbe:
    """A cheap accessibility observation, never an identity/format assertion.

    States: ready, empty (explicit no-media), pending (spin-up/verify/busy),
    unknown (driver lacks the query), missing (device detached), or error.
    is_virtual=None means the standard product descriptor was unavailable.
    """
    state: str
    change_count: int | None = None
    is_virtual: bool | None = None
    product: str | None = None
    error: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MediaEvent:
    kind: str
    drive: str
    identity: Any | None
    fingerprint: str | None
    is_virtual: bool | None
    observed_at: str
    monotonic_at: float
    startup: bool = False
    error: str | None = None
    reason: str | None = None

    @property
    def physical(self) -> bool:
        return self.is_virtual is False

    @property
    def source_image(self):
        return getattr(self.identity, "source_image", None)


@dataclass
class _DriveState:
    startup: bool
    identity: Any | None = None
    fingerprint: str | None = None
    token: tuple | None = None
    is_virtual: bool | None = None
    last_identified: float | None = None
    next_retry: float = 0
    failures: int = 0
    last_error: tuple | None = None
    changed_hint: bool = False
    unavailable_since: float | None = None


class WindowsMediaProbe:
    """Use the documented low-access VERIFY2 query and optional change count.

    Some drivers require ordinary read-only VERIFY to return a change count;
    that choice is cached. The product descriptor is standard storage
    metadata, queried at most once per 30 seconds for a given drive letter.
    """
    def __init__(self, *, api=None, clock=time.monotonic):
        self._api = api
        self._clock = clock
        self._products: dict[str, tuple[float, str | None, bool | None]] = {}
        self._read_query: set[str] = set()
        self._counter_checked: set[str] = set()

    def _get_api(self):
        if self._api is None:
            from .optical import _get_api
            self._api = _get_api()
        return self._api

    def forget(self, drive):
        self._products.pop(drive, None)
        self._read_query.discard(drive)
        self._counter_checked.discard(drive)

    @staticmethod
    def _virtual(product):
        if not product:
            return None
        value=product.strip().casefold()
        # Only known virtualization products are labelled virtual. An unknown
        # nonempty product means a regular optical device observation, not a
        # guarantee about its electrical transport or manufacturer.
        return value in {"wincdemu", "vbox cd-rom", "vmware virtual ide cdrom drive",
                         "virtual dvd-rom", "virtual cd-rom"}

    def _product(self, api, handle, drive):
        cached=self._products.get(drive)
        now=self._clock()
        if cached and now-cached[0] < 30:
            return cached[1],cached[2]
        from .mounted_images import _product
        try:product=_product(api,handle,drive)
        except (OSError,ValueError,OpticalError):product=None
        virtual=self._virtual(product)
        self._products[drive]=(now,product,virtual)
        return product,virtual

    def _open(self, api, drive, read):
        return api.kernel.CreateFileW("\\\\.\\"+drive,
            GENERIC_READ if read else FILE_READ_ATTRIBUTES,3,None,3,0,None)

    def _verify(self, api, handle, read):
        output=api.wintypes.DWORD()
        returned=api.wintypes.DWORD()
        success=api.kernel.DeviceIoControl(handle,
            IOCTL_STORAGE_CHECK_VERIFY if read else IOCTL_STORAGE_CHECK_VERIFY2,
            None,0,api.ctypes.byref(output),4,api.ctypes.byref(returned),None)
        if success:
            if returned.value not in {0,4}:
                return False,None,87
            return True,output.value if returned.value==4 else None,0
        return False,None,api.ctypes.get_last_error()

    def __call__(self, drive):
        drive=normalize_drive(drive);api=self._get_api();c=api.ctypes
        read=drive in self._read_query
        handle=self._open(api,drive,read)
        if handle==c.c_void_p(-1).value:
            number=c.get_last_error()
            return self._failed(number,None,None)
        product=virtual=None
        try:
            product,virtual=self._product(api,handle,drive)
            ok,count,number=self._verify(api,handle,read)
        finally:api.kernel.CloseHandle(handle)
        if not read and ((not ok and number in _UNSUPPORTED) or
                (ok and count is None and drive not in self._counter_checked)):
            # This is still a read-only handle/query, with no elevated retry.
            # VERIFY2 can succeed without filling the optional counter, while
            # ordinary VERIFY supplies it (observed on the MATSHITA USB drive).
            previous=(ok,count,number);self._counter_checked.add(drive)
            handle=self._open(api,drive,True)
            if handle!=c.c_void_p(-1).value:
                try:ok,count,number=self._verify(api,handle,True)
                finally:api.kernel.CloseHandle(handle)
            else:ok,count,number=False,None,c.get_last_error()
            if ok:
                if count is not None or not previous[0]:self._read_query.add(drive)
            elif previous[0] and number in (_UNSUPPORTED | {5}):
                # Attribute access already established readiness. A driver
                # refusing the optional counter must not hide that observation.
                ok,count,number=previous
            elif number not in _UNSUPPORTED:self._read_query.add(drive)
        if ok:return MediaProbe("ready",count,virtual,product)
        return self._failed(number,virtual,product)

    @staticmethod
    def _failed(number,virtual,product):
        if number==1112:return MediaProbe("empty",None,virtual,product,reason="no_media")
        if number in _DISCONNECTED:return MediaProbe("missing",None,virtual,product,reason="device_removed")
        if number in _NOT_READY:
            reason=("media_changed" if number in {1110,1111} else
                    "busy" if number in {32,33} else "cancelled" if number==995 else "not_ready")
            return MediaProbe("pending",None,virtual,product,reason=reason)
        if number in _UNSUPPORTED:return MediaProbe("unknown",None,virtual,product,reason="verify_unsupported")
        return MediaProbe("error",None,virtual,product,
            f"The drive is not accessible (Windows error {number}).",
            "access_denied" if number==5 else "io_error")

    def cancel_thread(self,native_id):
        """Cancel only synchronous IO issued by this watcher's own worker."""
        if os.name!="nt" or native_id is None:return
        try:
            api=self._get_api();c=api.ctypes;w=api.wintypes;k=api.kernel
            k.OpenThread.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenThread.restype=w.HANDLE
            k.CancelSynchronousIo.argtypes=[w.HANDLE];k.CancelSynchronousIo.restype=w.BOOL
            handle=k.OpenThread(0x0001,False,native_id)  # THREAD_TERMINATE, per API contract.
            if handle:
                try:k.CancelSynchronousIo(handle)
                finally:k.CloseHandle(handle)
        except (OSError,AttributeError,TypeError):pass


class MediaWatcher:
    def __init__(self, *, interval=1.5, fallback_interval=15.0, not_ready_timeout=6.0,
                 drive_provider: Callable | None=None, prober: Callable | None=None,
                 identifier: Callable | None=None, clock=time.monotonic):
        if isinstance(interval,bool) or not isinstance(interval,(int,float)) or not math.isfinite(interval) or not .25<=interval<=60:
            raise ValueError("Watcher interval must be between 0.25 and 60 seconds")
        if isinstance(fallback_interval,bool) or not isinstance(fallback_interval,(int,float)) or not math.isfinite(fallback_interval) or fallback_interval<interval:
            raise ValueError("Fallback interval must be at least the normal polling interval")
        if isinstance(not_ready_timeout,bool) or not isinstance(not_ready_timeout,(int,float)) or not math.isfinite(not_ready_timeout) or not_ready_timeout<interval:
            raise ValueError("Not-ready grace must be at least the normal polling interval")
        self.interval=float(interval);self.fallback_interval=float(fallback_interval)
        self.not_ready_timeout=float(not_ready_timeout)
        self._drive_provider=drive_provider or list_drives
        self._prober=prober or WindowsMediaProbe(clock=clock)
        self._identifier=identifier or self._identify
        self._clock=clock
        self.events=queue.Queue(maxsize=128)
        self._stop=threading.Event();self._thread=None;self._native_id=None
        self._states: dict[str,_DriveState]={}
        self._active: dict[str,MediaEvent]={};self._active_lock=threading.Lock()
        self._initial_scan=True;self._enumeration_error=None;self._worker_error=None

    @staticmethod
    def _identify(drive):
        from .disc_identity import identify_drive
        return identify_drive(drive)

    def start(self):
        if self._thread is not None:
            if self._thread.is_alive():return self
            raise RuntimeError("A stopped media watcher cannot be restarted")
        if self._stop.is_set():raise RuntimeError("A closed media watcher cannot be started")
        self._thread=threading.Thread(target=self._run,name="Redux optical media",daemon=True)
        self._thread.start();return self

    @property
    def running(self):return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def close(self,timeout=.25):
        if not isinstance(timeout,(int,float)) or isinstance(timeout,bool) or not math.isfinite(timeout) or not 0<=timeout<=5:
            raise ValueError("Close timeout must be between zero and five seconds")
        with self._active_lock:self._stop.set()
        if self._thread and self._thread.is_alive():
            cancel=getattr(self._prober,"cancel_thread",None)
            if cancel:
                try:cancel(self._native_id)
                except Exception:pass
            if threading.current_thread() is not self._thread:self._thread.join(timeout)
        return not bool(self._thread and self._thread.is_alive())

    stop=close

    def poll_events(self,maximum=None):
        if maximum is not None and (isinstance(maximum,bool) or not isinstance(maximum,int) or maximum<1):
            raise ValueError("Event maximum must be a positive integer")
        result=[]
        while maximum is None or len(result)<maximum:
            try:result.append(self.events.get_nowait())
            except queue.Empty:break
        return result

    def snapshot(self):
        with self._active_lock:return dict(self._active)

    def _run(self):
        self._native_id=threading.get_native_id()
        while not self._stop.is_set():
            try:
                self.scan_once();self._worker_error=None
            except Exception as error:
                # A custom/driver implementation failure must not kill future
                # polling. Emit one deduplicated diagnostic and retain media.
                signature=(type(error).__name__,str(error))
                if signature!=self._worker_error:
                    self._worker_error=signature
                    self._emit("error","",None,None,None,False,str(error),"watch_error")
            self._stop.wait(self.interval)

    def _emit(self,kind,drive,identity,fingerprint,virtual,startup,error=None,reason=None):
        if self._stop.is_set():return
        event=MediaEvent(kind,drive,identity,fingerprint,virtual,
            datetime.now(timezone.utc).isoformat(),self._clock(),startup,error,reason)
        with self._active_lock:
            if self._stop.is_set():return
            if kind in {"inserted","changed"}:self._active[drive]=event
            elif kind=="removed":self._active.pop(drive,None)
            while True:
                try:self.events.put_nowait(event);return
                except queue.Full:
                    try:self.events.get_nowait()
                    except queue.Empty:pass

    def _remove(self,drive,state,reason):
        if state.identity is not None:
            self._emit("removed",drive,None,state.fingerprint,state.is_virtual,False,reason=reason)
        state.identity=None;state.fingerprint=None;state.token=None
        state.failures=0;state.next_retry=0;state.last_error=None;state.changed_hint=False
        state.startup=False;state.unavailable_since=None

    def _error(self,drive,state,error,reason):
        signature=(reason,str(error))
        if signature!=state.last_error:
            self._emit("error",drive,state.identity,state.fingerprint,state.is_virtual,state.startup,str(error),reason)
            state.last_error=signature

    def _retry(self,state,now):
        state.failures=min(state.failures+1,5)
        state.next_retry=now+min(30,self.interval*2**(state.failures-1))

    def scan_once(self):
        """One worker scan, public for deterministic injected-device tests.

        Do not call this from a Tk callback: real device IO can spin up a drive.
        poll_events()/snapshot()/close() remain bounded/nonblocking UI methods.
        """
        if self._stop.is_set():return
        try:
            drives=sorted({normalize_drive(getattr(item,"letter",item)) for item in self._drive_provider()})
        except Exception as error:
            signature=(type(error).__name__,str(error))
            if signature!=self._enumeration_error:
                self._enumeration_error=signature
                self._emit("error","",None,None,None,self._initial_scan,str(error),"enumeration_error")
            return
        self._enumeration_error=None
        for drive in sorted(set(self._states)-set(drives)):
            state=self._states.pop(drive);self._remove(drive,state,"device_removed")
            forget=getattr(self._prober,"forget",None)
            if forget:
                try:forget(drive)
                except Exception:pass
        observations=[]
        for drive in drives:
            if self._stop.is_set():return
            state=self._states.setdefault(drive,_DriveState(self._initial_scan))
            try:probe=self._prober(drive)
            except NoMediaError as error:
                # Windows NOT_READY is also used during spin-up. Only the
                # explicit NO_MEDIA code is a definitive removal observation.
                empty=getattr(error,"winerror",None)==1112
                probe=MediaProbe("empty" if empty else "pending",reason="no_media" if empty else "not_ready")
            except Exception as error:
                probe=MediaProbe("error",error=str(error),reason=getattr(error,"reason","io_error"))
            if not isinstance(probe,MediaProbe):
                probe=MediaProbe("error",error="Media prober must return MediaProbe",reason="invalid_probe")
            observations.append((drive,state,probe))
        # Startup virtual-image observations precede physical-media events so
        # straightforward UI consumers finish the batch on the physical disc.
        observations.sort(key=lambda item:0 if item[2].is_virtual is True else 1)
        for drive,state,probe in observations:
            if self._stop.is_set():return
            now=self._clock()
            if probe.is_virtual is not None:state.is_virtual=probe.is_virtual
            if probe.state in {"empty","missing"}:
                self._remove(drive,state,probe.reason or "no_media");continue
            if probe.state=="pending":
                if probe.reason=="media_changed":state.changed_hint=True
                if probe.reason=="not_ready":
                    if state.unavailable_since is None:state.unavailable_since=now
                    elif now-state.unavailable_since>=self.not_ready_timeout:
                        # NOT_READY cannot prove tray ejection. Clear a stale
                        # banner only after sustained unavailability, labelled
                        # distinctly from explicit no-media/device removal.
                        self._remove(drive,state,"not_ready_timeout")
                        state.unavailable_since=now
                else:state.unavailable_since=None
                continue
            state.unavailable_since=None
            if probe.state=="error":
                self._error(drive,state,probe.error or "The drive is unavailable.",probe.reason or "io_error");continue
            if probe.state not in {"ready","unknown"}:
                self._error(drive,state,"The drive returned an invalid observation.","invalid_probe");continue
            token=(probe.change_count,probe.product)
            changed=state.token is not None and token!=state.token
            fallback_due=(probe.change_count is None and
                (state.last_identified is None or now-state.last_identified>=self.fallback_interval))
            need=state.identity is None or changed or state.changed_hint or fallback_due
            if not need:
                state.last_error=None
                continue
            if now<state.next_retry:continue
            try:
                identity=self._identifier(drive)
                fingerprint=getattr(identity,"fingerprint",None)
                if not isinstance(fingerprint,str) or not fingerprint:
                    raise ValueError("Drive identity did not include a media fingerprint")
            except NoMediaError as error:
                if getattr(error,"winerror",None)==1112:self._remove(drive,state,"no_media")
                else:self._retry(state,now)
                continue
            except Exception as error:
                self._error(drive,state,error,getattr(error,"reason","identity_error"))
                self._retry(state,now);continue
            if self._stop.is_set():return
            identity_virtual=getattr(identity,"is_virtual",None)
            if identity_virtual is True:state.is_virtual=True
            # An absent source image is not proof of physical hardware. Retain
            # unknown transport unless a standard product descriptor was read.
            kind="inserted" if state.identity is None else "changed"
            emit=state.identity is None or fingerprint!=state.fingerprint or changed or state.changed_hint
            state.identity=identity;state.fingerprint=fingerprint;state.token=token
            state.last_identified=now;state.failures=0;state.next_retry=0
            state.last_error=None;state.changed_hint=False
            if emit:self._emit(kind,drive,identity,fingerprint,state.is_virtual,state.startup)
            state.startup=False
        self._initial_scan=False

