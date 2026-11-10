"""Read real device inventory and test SDL input with a process-local virtual pad.

The virtual joystick exists only in this process's SDL instance. It installs no
driver, sends no OS keys, and does not control another app or a physical pad.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

import ctypes as C
from dataclasses import asdict
from datetime import datetime,timezone
import json
from redux.controllers import create_controller


class Descriptor(C.Structure):
    _fields_=[("version",C.c_uint32),("type",C.c_uint16),("padding",C.c_uint16),
              ("vendor_id",C.c_uint16),("product_id",C.c_uint16),
              ("naxes",C.c_uint16),("nbuttons",C.c_uint16),("nballs",C.c_uint16),("nhats",C.c_uint16),
              ("ntouchpads",C.c_uint16),("nsensors",C.c_uint16),("padding2",C.c_uint16*2),
              ("button_mask",C.c_uint32),("axis_mask",C.c_uint32),("name",C.c_char_p),
              ("touchpads",C.c_void_p),("sensors",C.c_void_p),("userdata",C.c_void_p),
              ("Update",C.c_void_p),("SetPlayerIndex",C.c_void_p),("Rumble",C.c_void_p),
              ("RumbleTriggers",C.c_void_p),("SetLED",C.c_void_p),("SendEffect",C.c_void_p),
              ("SetSensorsEnabled",C.c_void_p),("Cleanup",C.c_void_p)]


def main():
    controller=create_controller();instance=0
    try:
        if controller.backend!="SDL3":raise RuntimeError(controller.failure)
        controller.poll(0)
        inventory=controller.devices()
        n=controller.api._native
        attach=n.lib.SDL_AttachVirtualJoystick;attach.argtypes=[C.POINTER(Descriptor)];attach.restype=C.c_uint32
        detach=n.lib.SDL_DetachVirtualJoystick;detach.argtypes=[C.c_uint32];detach.restype=C.c_bool
        set_button=n.lib.SDL_SetJoystickVirtualButton;set_button.argtypes=[C.c_void_p,C.c_int,C.c_bool];set_button.restype=C.c_bool
        descriptor=Descriptor();descriptor.version=C.sizeof(Descriptor);descriptor.type=1
        descriptor.naxes=6;descriptor.nbuttons=15;descriptor.button_mask=(1<<15)-1;descriptor.axis_mask=(1<<6)-1
        descriptor.name=b"Redux process-local verification pad"
        instance=int(attach(C.byref(descriptor)))
        if not instance:raise RuntimeError(n.error())
        controller.poll(.1)
        device=next(d for d in controller.devices() if d["instance_id"]==instance)
        joystick=n.GetGamepadJoystick(controller.api._gamepads[instance].handle)
        slot=device["slot"]
        if slot is None:raise RuntimeError("All four player slots are already occupied")
        def events(now):return [asdict(e) for e in controller.poll(now) if e.controller==slot and e.pressed]
        def set_buttons(buttons):
            for button in range(15):
                if not set_button(joystick,button,button in buttons):raise RuntimeError(n.error())
        set_buttons({5});guide=events(.2)
        assert [e["action"] for e in guide]==["guide"],guide
        assert events(.3)==[],"Held Guide repeated"
        set_buttons(set());events(.4)
        set_buttons({4,6});assert events(1)==[],"Short chord fired"
        assert events(1.99)==[],"Chord fired before one second"
        chord=events(2)
        assert [e["action"] for e in chord]==["menu"] and chord[0]["chord"],chord
        set_buttons(set());events(2.1)
        set_buttons({4,6,9,10});quit_events=events(3)
        assert [e["action"] for e in quit_events]==["quit_game"],quit_events
        set_buttons(set());events(3.1)
        assert detach(instance),n.error()
        instance=0;controller.poll(3.2)
        record={"observed_at_utc":datetime.now(timezone.utc).isoformat(),"sdl_version":controller.api.version,
                "backend":controller.backend,"physical_or_os_devices":inventory,
                "process_local_virtual_pad":{"guide_events":guide,"select_start_one_second_events":chord,"quit_events":quit_events,
                                             "held_guide_repeated":False,"removed_cleanly":all(d["instance_id"]!=device["instance_id"] for d in controller.devices())},
                "physical_button_press_tested":False,"os_driver_installed":False,"external_input_injected":False}
        output=ROOT/"analysis/controller_menu/native-input-observation.json"
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(record,indent=2),encoding="utf-8")
        print(json.dumps(record,indent=2))
    finally:
        if instance:detach(instance)
        controller.close()


if __name__=="__main__":main()
