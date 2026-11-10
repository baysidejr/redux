"""Read-only normalized gamepads, navigation, and reserved menu shortcuts.

No input is injected into another application. The frontend consumes these
navigation events; the emulator keeps responsibility for gameplay input.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import time


@dataclass(frozen=True)
class GamepadState:
    buttons: int = 0
    thumb_lx: int = 0
    thumb_ly: int = 0
    packet: int = 0
    thumb_rx: int = 0
    thumb_ry: int = 0
    left_trigger: int = 0
    right_trigger: int = 0


@dataclass(frozen=True)
class InputEvent:
    action: str
    pressed: bool = True
    controller: int = 0
    repeat: bool = False
    chord: bool = False


_BUTTONS = {0x0001:"up",0x0002:"down",0x0004:"left",0x0008:"right",
            0x0010:"menu",0x0020:"home",0x0100:"previous_tab",0x0200:"next_tab",
            0x1000:"confirm",0x2000:"back",0x4000:"details",0x8000:"search"}
_DIRECTIONS = frozenset({"up","down","left","right"})


class GameplayInputMapper:
    """Pure XInput-to-libretro joypad mapping with a reserved menu chord.

    Start and Back wait ``chord_window`` seconds before reaching the game.
    A quick single tap is preserved as one update's pulse when released. A
    held single becomes a held libretro button after the delay, without pulses
    repeating. Start+Back suppresses both until both physical buttons release.
    As with navigation, a second button pressed after the delay cannot retract
    a first button already delivered to the running game.

    Xbox A/B retain their labels (libretro A/B); X/Y map to libretro Y/X.
    Analog left-stick directions use hysteresis and combine with the D-pad.
    Triggers map to libretro L2/R2, with 128/85 press/release hysteresis.
    ``update`` must still run during overlays; discard its mask while paused
    so deferred taps cannot be delivered when returning from a menu.
    """

    _BUTTON_IDS = {0x0001: 4, 0x0002: 5, 0x0004: 6, 0x0008: 7,
                   0x0040: 14, 0x0080: 15, 0x0100: 10, 0x0200: 11,
                   0x1000: 8, 0x2000: 0, 0x4000: 1, 0x8000: 9}
    _SPECIAL_IDS = {0x0010: 3, 0x0020: 2}

    def __init__(self, *, chord_window=0.10, press_threshold=0.52, release_threshold=0.34):
        if chord_window < 0 or not 0 <= release_threshold < press_threshold <= 1:
            raise ValueError("Invalid gameplay controller timing or stick thresholds")
        self.chord_window = chord_window
        self.press_threshold, self.release_threshold = press_threshold, release_threshold
        self._controllers = {}
        self._blocked = set()

    def block_until_release(self,controller=None):
        """Discard pending taps and hold gameplay input until controls release."""
        ports=range(4) if controller is None else (controller,)
        for port in ports:
            if isinstance(port,bool) or not isinstance(port,int) or not 0<=port<4:raise ValueError("Controller index must be 0–3")
            self._controllers.pop(port,None);self._blocked.add(port)

    def reset(self, controller=None):
        """Release all remembered controls, or one disconnected controller."""
        if controller is None:
            self._controllers.clear()
            self._blocked.clear()
        else:
            self._controllers.pop(controller, None)
            self._blocked.discard(controller)

    def _direction(self, value, previous):
        value = max(-1.0, min(1.0, value / (32767 if value >= 0 else 32768)))
        if previous == 1 and value > self.release_threshold:
            return 1
        if previous == -1 and value < -self.release_threshold:
            return -1
        if value >= self.press_threshold:
            return 1
        if value <= -self.press_threshold:
            return -1
        return 0

    def update(self, controller: int, state: GamepadState | None, now: float) -> int:
        if isinstance(controller, bool) or not isinstance(controller, int) or not 0 <= controller <= 3:
            raise ValueError("XInput controller index must be 0–3")
        if state is None:
            self.reset(controller)
            return 0
        if controller in self._blocked:
            neutral=(not state.buttons and state.left_trigger<32 and state.right_trigger<32 and
                     abs(state.thumb_lx)<32768*self.release_threshold and abs(state.thumb_ly)<32768*self.release_threshold and
                     abs(state.thumb_rx)<32768*self.release_threshold and abs(state.thumb_ry)<32768*self.release_threshold)
            if neutral:self._blocked.remove(controller)
            return 0
        old = self._controllers.setdefault(controller, {"pending": {}, "sent": set(),
                                                        "chord": False, "x": 0, "y": 0,"quit":False,
                                                        "lt":False,"rt":False})
        if state.buttons&0x330==0x330:
            old["quit"]=True;old["chord"]=True;old["pending"].clear();old["sent"].clear()
            return 0
        if old["quit"]:
            if not state.buttons&0x330:old["quit"]=False;old["chord"]=False
            return 0
        mask = sum(1 << target for source, target in self._BUTTON_IDS.items() if state.buttons & source)
        for key, value, target in (("lt", getattr(state,"left_trigger",0), 12),
                                   ("rt", getattr(state,"right_trigger",0), 13)):
            old[key] = value > 85 if old[key] else value >= 128
            if old[key]:
                mask |= 1 << target
        old["x"] = self._direction(state.thumb_lx, old["x"])
        old["y"] = self._direction(state.thumb_ly, old["y"])
        if old["x"]:
            mask |= 1 << (7 if old["x"] > 0 else 6)
        if old["y"]:
            mask |= 1 << (4 if old["y"] > 0 else 5)
        special = state.buttons & 0x30
        if state.buttons & 0x0400:
            old["chord"] = True
            old["pending"].clear()
            old["sent"].clear()
            return 0
        if special == 0x30:
            old["chord"] = True
            old["pending"].clear()
            old["sent"].clear()
        elif old["chord"]:
            if not special:
                old["chord"] = False
        else:
            for source, target in self._SPECIAL_IDS.items():
                if state.buttons & source:
                    if source not in old["pending"] and source not in old["sent"]:
                        old["pending"][source] = now
                    if source in old["pending"] and now - old["pending"][source] >= self.chord_window:
                        old["pending"].pop(source)
                        old["sent"].add(source)
                    if source in old["sent"]:
                        mask |= 1 << target
                else:
                    if source in old["pending"]:
                        old["pending"].pop(source)
                        mask |= 1 << target
                    old["sent"].discard(source)
        return mask


class NavigationMapper:
    """Pure state machine, useful for deterministic tests and alternate inputs.

    Start/Back edges are held briefly so pressing the pair produces only one
    menu event, not a menu followed by Home. Chord events never repeat.
    """
    def __init__(self, *, repeat_delay=0.38, repeat_interval=0.10, chord_window=0.10,chord_hold=0,
                 press_threshold=0.52, release_threshold=0.34):
        if not (repeat_delay>=0 and repeat_interval>0 and chord_window>=0 and isinstance(chord_hold,(int,float)) and not isinstance(chord_hold,bool) and 0<=chord_hold<=5):
            raise ValueError("Invalid controller timing")
        if not 0<=release_threshold<press_threshold<=1:
            raise ValueError("Invalid stick thresholds")
        self.repeat_delay=repeat_delay;self.repeat_interval=repeat_interval
        self.chord_window=chord_window;self.press_threshold=press_threshold;self.release_threshold=release_threshold
        self.chord_hold=chord_hold
        self._controllers={}

    def reset(self,controller=None):
        if controller is None:self._controllers.clear()
        else:self._controllers.pop(controller,None)

    @staticmethod
    def _axis(value):return max(-1.0,min(1.0,value/(32767 if value>=0 else 32768)))

    def _direction(self,value,previous):
        value=self._axis(value)
        if previous==1 and value>self.release_threshold:return 1
        if previous==-1 and value<-self.release_threshold:return -1
        if value>=self.press_threshold:return 1
        if value<=-self.press_threshold:return -1
        return 0

    def update(self, controller: int, state: GamepadState | None, now: float) -> list[InputEvent]:
        old=self._controllers.get(controller)
        if state is None:
            if old is None:return []
            del self._controllers[controller]
            actions=old["held"]|old["special_sent"]|({"guide"} if old.get("guide") else set())
            return [InputEvent(action,False,controller) for action in sorted(actions)]
        if not 0<=controller<=3:raise ValueError("XInput controller index must be 0–3")
        if old is None:
            old={"held":set(),"next":{},"x":0,"y":0,"pending":{},"special_sent":set(),"chord":False,"guide":False,
                 "chord_started":None,"chord_emitted":False,"quit":False}
            self._controllers[controller]=old
        if state.buttons&0x330==0x330 or old["quit"]:
            pressed=not old["quit"]
            old["quit"]=bool(state.buttons&0x330)
            old["chord"]=True;old["chord_started"]=None;old["chord_emitted"]=True
            if not old["quit"]:old["chord"]=False;old["chord_emitted"]=False
            old["pending"].clear();old["special_sent"].clear()
            old["held"]={action for bit,action in _BUTTONS.items() if state.buttons&bit and bit not in {0x10,0x20}}
            old["next"]={};old["x"]=old["y"]=0
            return [InputEvent("quit_game",True,controller,chord=True)] if pressed else []
        guide=bool(state.buttons&0x0400)
        if guide:
            pressed=not old["guide"]
            old["guide"]=True;old["chord"]=True
            old["chord_started"]=None;old["chord_emitted"]=True
            old["pending"].clear();old["special_sent"].clear()
            old["held"]={action for bit,action in _BUTTONS.items() if state.buttons&bit and bit not in {0x10,0x20}}
            old["x"]=self._direction(state.thumb_lx,old["x"]);old["y"]=self._direction(state.thumb_ly,old["y"])
            if old["x"]:old["held"].add("right" if old["x"]>0 else "left")
            if old["y"]:old["held"].add("up" if old["y"]>0 else "down")
            old["next"]={action:now+self.repeat_delay for action in old["held"]}
            return [InputEvent("guide",True,controller)] if pressed else []
        guide_released=old["guide"];old["guide"]=False
        actions={action for bit,action in _BUTTONS.items() if state.buttons&bit and bit not in {0x10,0x20}}
        old["x"]=self._direction(state.thumb_lx,old["x"])
        old["y"]=self._direction(state.thumb_ly,old["y"])
        if old["x"]:actions.add("right" if old["x"]>0 else "left")
        if old["y"]:actions.add("up" if old["y"]>0 else "down")
        events=[InputEvent("guide",False,controller)] if guide_released else []
        start,back=bool(state.buttons&0x10),bool(state.buttons&0x20)
        if start and back:
            if not old["chord"]:
                old["chord_started"]=now;old["chord_emitted"]=False
            if not old["chord_emitted"] and old["chord_started"] is not None and now-old["chord_started"]>=self.chord_hold:
                events.append(InputEvent("menu",True,controller,chord=True));old["chord_emitted"]=True
            old["chord"]=True
            old["pending"].clear()
            old["special_sent"].clear()
        elif old["chord"]:
            # Require both controls released before considering another chord or
            # single-button action. Releasing only Start must not activate Home.
            old["chord_started"]=None
            if not start and not back:old["chord"]=False;old["chord_emitted"]=False
        else:
            special={"menu":start,"home":back}
            for action,pressed in special.items():
                pending=old["pending"]
                if pressed:
                    if action not in pending and action not in old["special_sent"]:pending[action]=now
                    if action in pending and now-pending[action]>=self.chord_window:
                        events.append(InputEvent(action,True,controller));old["special_sent"].add(action);del pending[action]
                else:
                    if action in pending:
                        events.extend((InputEvent(action,True,controller),InputEvent(action,False,controller)))
                        del pending[action]
                    elif action in old["special_sent"]:
                        events.append(InputEvent(action,False,controller));old["special_sent"].remove(action)
        for action in sorted(old["held"]-actions):
            events.append(InputEvent(action,False,controller));old["next"].pop(action,None)
        for action in sorted(actions-old["held"]):
            events.append(InputEvent(action,True,controller));old["next"][action]=now+self.repeat_delay
        for action in sorted(actions&old["held"]&_DIRECTIONS):
            if now>=old["next"].get(action,now+self.repeat_delay):
                events.append(InputEvent(action,True,controller,True))
                # One repeat per polling cycle; do not burst on a delayed frame.
                old["next"][action]=now+self.repeat_interval
        old["held"]=actions
        return events


class _XInputAPI:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        class Pad(c.Structure):
            _fields_=[("buttons",w.WORD),("left_trigger",c.c_ubyte),("right_trigger",c.c_ubyte),
                      ("lx",c.c_short),("ly",c.c_short),("rx",c.c_short),("ry",c.c_short)]
        class State(c.Structure):_fields_=[("packet",w.DWORD),("gamepad",Pad)]
        self.c,self.state_type=c,State
        self.get=None
        for name in ("xinput1_4.dll","xinput1_3.dll","xinput9_1_0.dll"):
            try:
                dll=c.WinDLL(name)
                fn=dll.XInputGetState;fn.argtypes=[w.DWORD,c.POINTER(State)];fn.restype=w.DWORD
                self.dll,self.get=dll,fn;break
            except (OSError,AttributeError):continue

    def state(self,index):
        if self.get is None:return None
        value=self.state_type()
        # XInput errors are returned directly, not through GetLastError.
        if self.get(index,self.c.byref(value))!=0:return None
        return GamepadState(value.gamepad.buttons,value.gamepad.lx,value.gamepad.ly,value.packet)


class XInputController:
    def __init__(self, *, mapper=None, api=None):
        self.mapper=mapper or NavigationMapper()
        self.api=api if api is not None else _XInputAPI() if os.name=="nt" else None
        self.connected_indices=()

    @property
    def available(self):return self.api is not None and getattr(self.api,"get",True) is not None

    def poll(self, now=None) -> list[InputEvent]:
        now=time.monotonic() if now is None else now
        result=[];connected=[]
        for index in range(4):
            state=self.api.state(index) if self.api is not None else None
            if state is not None:connected.append(index)
            result.extend(self.mapper.update(index,state,now))
        self.connected_indices=tuple(connected)
        return result

    def devices(self):
        return [{"slot":index,"name":f"XInput controller {index+1}","mapped":True,"has_guide":False} for index in self.connected_indices]

    def close(self):
        if self.api and hasattr(self.api,"close"):self.api.close()
        self.connected_indices=()


class ControllerManager(XInputController):
    """SDL3 discovery and normalized input, with a single XInput fallback."""
    def __init__(self,*,mapper=None,api=None,mapping_file=None):
        self.failure=None;self.backend="SDL3";self._closed=False
        if api is None:
            try:
                from .sdl_gamepads import SDLGamepadAPI
                api=SDLGamepadAPI(mapping_file=mapping_file)
            except (OSError,RuntimeError,ValueError,AttributeError) as error:
                self.failure=str(error);self.backend="XInput"
                api=_XInputAPI() if os.name=="nt" else None
        super().__init__(mapper=mapper or NavigationMapper(chord_hold=1.0),api=api)
        self._slot_ids={}

    def poll(self,now=None):
        if self._closed:return []
        if self.api and hasattr(self.api,"refresh"):self.api.refresh()
        if self.api and hasattr(self.api,"slot_ids"):
            slots=self.api.slot_ids()
            for slot,instance in slots.items():
                if slot in self._slot_ids and self._slot_ids[slot]!=instance:self.mapper.reset(slot)
            self._slot_ids=slots
        return super().poll(now)

    def devices(self):
        if self._closed:return []
        return self.api.devices() if self.api and hasattr(self.api,"devices") else super().devices()

    @property
    def available(self):return not self._closed and super().available

    def close(self):
        if self._closed:return
        super().close();self._closed=True


def create_controller(*,mapping_file=None):
    return ControllerManager(mapping_file=mapping_file)
