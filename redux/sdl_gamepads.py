"""Read-only SDL3 controller input and mappings; no OS driver or key injection.

All native initialization/event pumping belongs to the creating main thread.
The console polls once per UI tick, then gameplay reads immutable cached states.
SDL owns device protocol handling and duplicate-device suppression. Unknown
joysticks are exposed for a user binding wizard, never assigned guessed buttons.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import threading

from .controllers import GamepadState

SDL_INIT_GAMEPAD=0x00002000
GUIDE_MASK=0x0400
_MAX_DEVICES=128
_BUTTONS={0:0x1000,1:0x2000,2:0x4000,3:0x8000,4:0x0020,
          5:GUIDE_MASK,6:0x0010,7:0x0040,8:0x0080,9:0x0100,
          10:0x0200,11:0x0001,12:0x0002,13:0x0004,14:0x0008}


class SDLInputError(RuntimeError):pass


class _GUID(C.Structure):
    _fields_=[("data",C.c_uint8*16)]


def native_directory():
    source=Path(__file__).resolve().parent/"assets"/"native"
    bundle=getattr(sys,"_MEIPASS",None)
    candidates=([Path(bundle)/"assets"/"native",Path(bundle)/"redux"/"assets"/"native"] if bundle else [])+[source]
    return next((path for path in candidates if (path/"SDL3.dll").is_file()),candidates[0])


def _text(value):return value.decode("utf-8",errors="replace") if value else ""


def validate_mapping(mapping):
    if not isinstance(mapping,str) or not 1<=len(mapping)<=8192 or "\n" in mapping or "\r" in mapping or "\0" in mapping:
        raise ValueError("A controller mapping must be one bounded text line")
    parts=mapping.split(",")
    if len(parts)<3 or not re.fullmatch(r"(?:[0-9a-fA-F]{32}|xinput)",parts[0]) or not parts[1].strip():
        raise ValueError("The controller mapping needs a GUID, name and bindings")
    return mapping


class _SDL:
    """Small, explicitly typed subset of SDL3's public C ABI."""
    def __init__(self,path=None):
        if os.name!="nt" and path is None:raise SDLInputError("The bundled SDL3 library is for Windows x64")
        self.lib=C.CDLL(str(path or native_directory()/"SDL3.dll"))
        def bind(name,args,result):
            fn=getattr(self.lib,name);fn.argtypes=args;fn.restype=result;setattr(self,name[4:],fn)
        p=C.c_void_p;i=C.c_int;u=C.c_uint32;b=C.c_bool;s=C.c_char_p
        bind("SDL_SetHint",[s,s],b)
        bind("SDL_InitSubSystem",[u],b);bind("SDL_QuitSubSystem",[u],None)
        bind("SDL_GetError",[],s);bind("SDL_GetVersion",[],i)
        bind("SDL_PumpEvents",[],None);bind("SDL_UpdateGamepads",[],None)
        bind("SDL_SetGamepadEventsEnabled",[b],None);bind("SDL_SetJoystickEventsEnabled",[b],None)
        bind("SDL_GetGamepads",[C.POINTER(i)],C.POINTER(u));bind("SDL_GetJoysticks",[C.POINTER(i)],C.POINTER(u))
        bind("SDL_free",[p],None)
        bind("SDL_OpenGamepad",[u],p);bind("SDL_CloseGamepad",[p],None)
        bind("SDL_GetGamepadName",[p],s);bind("SDL_GetGamepadSerial",[p],s)
        bind("SDL_GetGamepadButton",[p,i],b);bind("SDL_GamepadHasButton",[p,i],b)
        bind("SDL_GetGamepadAxis",[p,i],C.c_int16)
        bind("SDL_GetGamepadJoystick",[p],p);bind("SDL_GetGamepadType",[p],i)
        bind("SDL_GetGamepadMapping",[p],p)
        bind("SDL_GetJoystickNameForID",[u],s);bind("SDL_GetJoystickGUIDForID",[u],_GUID)
        bind("SDL_GetJoystickVendorForID",[u],C.c_uint16);bind("SDL_GetJoystickProductForID",[u],C.c_uint16)
        bind("SDL_OpenJoystick",[u],p);bind("SDL_CloseJoystick",[p],None)
        bind("SDL_GetJoystickSerial",[p],s)
        bind("SDL_GetNumJoystickButtons",[p],i);bind("SDL_GetNumJoystickAxes",[p],i);bind("SDL_GetNumJoystickHats",[p],i)
        bind("SDL_GetJoystickButton",[p,i],b);bind("SDL_GetJoystickAxis",[p,i],C.c_int16);bind("SDL_GetJoystickHat",[p,i],C.c_uint8)
        bind("SDL_AddGamepadMapping",[s],i)

    def error(self):return _text(self.GetError()) or "SDL3 input is unavailable"

    def ids(self,gamepads):
        count=C.c_int()
        pointer=(self.GetGamepads if gamepads else self.GetJoysticks)(C.byref(count))
        try:
            if not pointer:
                if count.value==0:return []
                raise SDLInputError(self.error())
            if not 0<=count.value<=_MAX_DEVICES:raise SDLInputError("SDL reported an excessive device count")
            return [int(pointer[index]) for index in range(count.value)]
        finally:
            if pointer:self.free(pointer)


@dataclass
class _Device:
    instance_id:int
    handle:object
    info:dict
    slot:int|None=None


class SDLGamepadAPI:
    def __init__(self,*,native=None,mapping_file=None):
        if threading.current_thread() is not threading.main_thread():raise SDLInputError("Initialize controller input on the main thread")
        self._thread=threading.get_ident();self._closed=False;self._initialized=False
        self._native=native if native is not None else _SDL()
        self.warnings=[]
        self._gamepads={};self._raw={};self._states={};self._slot_history={};self._sequence=0
        self._native.SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS",b"1")
        # Physical button positions remain stable even when printed A/B labels differ.
        self._native.SetHint(b"SDL_GAMECONTROLLER_USE_BUTTON_LABELS",b"0")
        if not self._native.InitSubSystem(SDL_INIT_GAMEPAD):raise SDLInputError(self._native.error())
        self._initialized=True
        try:
            self._native.SetGamepadEventsEnabled(False)
            self._native.SetJoystickEventsEnabled(False)
            self.load_mappings(native_directory()/"gamecontrollerdb.txt")
            from .redux_controller import MAPPING as redux_controller_mapping
            self.add_mapping(redux_controller_mapping)
            if mapping_file:
                try:self.load_mappings(mapping_file)
                except (OSError,ValueError,SDLInputError):self.warnings.append("Custom controller mappings could not be loaded; built-in mappings remain active.")
            self.refresh()
        except Exception:
            self.close();raise

    def _check(self):
        if self._closed:raise SDLInputError("Controller input is closed")
        if threading.get_ident()!=self._thread:raise SDLInputError("Controller input must be polled on its creating thread")

    @property
    def version(self):
        value=self._native.GetVersion()
        return f"{value//1000000}.{value//1000%1000}.{value%1000}"

    def _info(self,instance_id,handle,mapped):
        n=self._native
        guid=bytes(n.GetJoystickGUIDForID(instance_id).data).hex()
        joystick=n.GetGamepadJoystick(handle) if mapped else handle
        serial=_text(n.GetGamepadSerial(handle) if mapped else n.GetJoystickSerial(handle))
        return {"instance_id":instance_id,"name":_text(n.GetGamepadName(handle) if mapped else n.GetJoystickNameForID(instance_id)) or "USB gamepad",
                "guid":guid,"serial":serial,"vendor":int(n.GetJoystickVendorForID(instance_id)),
                "product":int(n.GetJoystickProductForID(instance_id)),"mapped":mapped,
                "has_guide":bool(n.GamepadHasButton(handle,5)) if mapped else None,
                "buttons":max(0,min(128,n.GetNumJoystickButtons(joystick))),
                "axes":max(0,min(32,n.GetNumJoystickAxes(joystick))),
                "hats":max(0,min(16,n.GetNumJoystickHats(joystick)))}

    def refresh(self):
        self._check();n=self._native
        n.PumpEvents();n.UpdateGamepads()
        gamepad_ids=set(n.ids(True));joystick_ids=set(n.ids(False))
        for instance,device in list(self._gamepads.items()):
            if instance not in gamepad_ids:
                n.CloseGamepad(device.handle);del self._gamepads[instance]
        for instance,device in list(self._raw.items()):
            if instance not in joystick_ids or instance in gamepad_ids:
                n.CloseJoystick(device.handle);del self._raw[instance]
        for instance in sorted(gamepad_ids-set(self._gamepads)):
            handle=n.OpenGamepad(instance)
            if not handle:continue
            info=self._info(instance,handle,True)
            # Slots never move while connected; unique serials can reclaim a free
            # previous slot. Identical pads lacking serials cannot be distinguished
            # persistently after both are disconnected, so no such claim is made.
            key=(info["guid"],info["serial"]) if info["serial"] else (info["guid"],instance)
            occupied={d.slot for d in self._gamepads.values() if d.slot is not None}
            preferred=self._slot_history.get(key)
            slot=preferred if preferred is not None and preferred not in occupied else next((i for i in range(4) if i not in occupied),None)
            if slot is not None:self._slot_history[key]=slot
            self._gamepads[instance]=_Device(instance,handle,info,slot)
        for instance in sorted(joystick_ids-gamepad_ids-set(self._raw)):
            handle=n.OpenJoystick(instance)
            if handle:self._raw[instance]=_Device(instance,handle,self._info(instance,handle,False))
        self._sequence+=1
        states={}
        for device in self._gamepads.values():
            if device.slot is None:continue
            buttons=sum(mask for button,mask in _BUTTONS.items() if n.GetGamepadButton(device.handle,button))
            # SDL Y grows downward; the established Redux input model grows up.
            x=int(n.GetGamepadAxis(device.handle,0));y=max(-32768,min(32767,-int(n.GetGamepadAxis(device.handle,1))))
            rx=int(n.GetGamepadAxis(device.handle,2));ry=max(-32768,min(32767,-int(n.GetGamepadAxis(device.handle,3))))
            lt=max(0,min(255,int(n.GetGamepadAxis(device.handle,4))*255//32767))
            rt=max(0,min(255,int(n.GetGamepadAxis(device.handle,5))*255//32767))
            states[device.slot]=GamepadState(buttons,x,y,self._sequence,rx,ry,lt,rt)
        self._states=states
        if len(self._slot_history)>256:
            # Only live devices need remembered slots; bound hotplug bookkeeping.
            self._slot_history={((d.info["guid"],d.info["serial"]) if d.info["serial"] else (d.info["guid"],d.instance_id)):d.slot for d in self._gamepads.values() if d.slot is not None}

    def state(self,index):
        if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<4:raise ValueError("Controller index must be 0–3")
        return None if self._closed else self._states.get(index)

    def devices(self):
        return [dict(device.info,slot=device.slot) for device in (*self._gamepads.values(),*self._raw.values())] if not self._closed else []

    def slot_ids(self):
        return {device.slot:device.instance_id for device in self._gamepads.values() if device.slot is not None} if not self._closed else {}

    def assign_slot(self,instance_id,target_slot):
        """Move a mapped pad to a player port, swapping an occupied port."""
        self._check()
        if isinstance(target_slot,bool) or not isinstance(target_slot,int) or not 0<=target_slot<4:
            raise ValueError("Player port must be 0–3")
        device=self._gamepads.get(instance_id)
        if not device:raise ValueError("Choose a connected mapped gamepad")
        previous=device.slot
        if previous==target_slot:return
        other=next((item for item in self._gamepads.values() if item.slot==target_slot),None)
        own_state=self._states.pop(previous,None) if previous is not None else None
        other_state=self._states.pop(target_slot,None)
        if other:
            other.slot=previous
            if previous is not None and other_state is not None:self._states[previous]=other_state
        device.slot=target_slot
        if own_state is not None:self._states[target_slot]=own_state
        for item in (device,other):
            if item is not None:
                key=(item.info["guid"],item.info["serial"]) if item.info["serial"] else (item.info["guid"],item.instance_id)
                if item.slot is not None:self._slot_history[key]=item.slot
                else:self._slot_history.pop(key,None)

    def raw_state(self,instance_id):
        self._check()
        device=self._raw.get(instance_id) or self._gamepads.get(instance_id)
        if not device:return None
        n=self._native;handle=device.handle if instance_id in self._raw else n.GetGamepadJoystick(device.handle)
        return dict(device.info,slot=device.slot,
                    buttons=tuple(bool(n.GetJoystickButton(handle,i)) for i in range(device.info["buttons"])),
                    axes=tuple(int(n.GetJoystickAxis(handle,i)) for i in range(device.info["axes"])),
                    hats=tuple(int(n.GetJoystickHat(handle,i)) for i in range(device.info["hats"])))

    def add_mapping(self,mapping):
        self._check();mapping=validate_mapping(mapping)
        if self._native.AddGamepadMapping(mapping.encode("utf-8"))<0:raise SDLInputError(self._native.error())

    def load_mappings(self,path):
        self._check();path=Path(path)
        if not path.is_file():return 0
        if path.stat().st_size>2*1024*1024:raise ValueError("Controller mapping file is too large")
        count=0
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line=line.strip()
            if not line or line.startswith("#"):continue
            platform=re.search(r"(?:^|,)platform:([^,]+)",line)
            if platform and platform.group(1)!="Windows":continue
            self.add_mapping(line);count+=1
        return count

    def mapping(self,instance_id):
        self._check();device=self._gamepads.get(instance_id)
        if not device:return None
        pointer=self._native.GetGamepadMapping(device.handle)
        try:return C.string_at(pointer).decode("utf-8",errors="replace") if pointer else None
        finally:
            if pointer:self._native.free(pointer)

    def close(self):
        if self._closed:return
        self._check();self._closed=True
        n=self._native
        for device in self._gamepads.values():n.CloseGamepad(device.handle)
        for device in self._raw.values():n.CloseJoystick(device.handle)
        self._gamepads.clear();self._raw.clear();self._states.clear()
        if self._initialized:n.QuitSubSystem(SDL_INIT_GAMEPAD);self._initialized=False
