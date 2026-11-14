"""A live controller test: an Xbox-style diagram that lights up with whatever you press."""
from __future__ import annotations

import time

from .ui_theme import WHITE, MUTED, ACCENT

# XInput-style button bits, as the SDL and XInput backends both report them.
BUTTON_BITS = {"up":0x0001,"down":0x0002,"left":0x0004,"right":0x0008,"menu":0x0010,"view":0x0020,
               "l3":0x0040,"r3":0x0080,"lb":0x0100,"rb":0x0200,"guide":0x0400,
               "a":0x1000,"b":0x2000,"x":0x4000,"y":0x8000}
HOLD_TO_LEAVE = 1.5


def pressed_names(buttons):
    return [name for name, bit in BUTTON_BITS.items() if buttons & bit]


def axis_fraction(value):
    return max(-1.0, min(1.0, value / (32767 if value >= 0 else 32768)))


def trigger_fraction(value):
    return max(0.0, min(1.0, value / 255 if value <= 255 else value / 32767))


class ConsoleControllerTest:
    def open_controller_test(self):
        if self.busy:return
        if self.embedded or self.playback and self.playback.active():
            self.notice("A game is running","Return to the library before testing a controller.");return
        self.test_slot=None;self.test_states={};self.test_hold_since=None
        self.navigate("controller_test")
        self.focus="controller-test:back"
        self.schedule_draw()

    def _controller_test_devices(self):
        try:devices=self.controller.devices() if self.controller else []
        except (OSError,ValueError,RuntimeError):devices=[]
        return [device for device in devices if device.get("slot") is not None]

    def _poll_controller_test(self):
        """Every tick: read all four slots; the one that moves becomes the one on screen."""
        if self.screen!="controller_test" or self.overlay or self.busy:return
        api=getattr(self.controller,"api",None)
        if api is None:return
        changed=False
        for slot in range(4):
            try:state=api.state(slot)
            except (OSError,ValueError,RuntimeError):state=None
            previous=self.test_states.get(slot)
            snapshot=None if state is None else (state.buttons,state.thumb_lx,state.thumb_ly,state.thumb_rx,state.thumb_ry,state.left_trigger,state.right_trigger)
            if snapshot!=previous:
                changed=True
                if snapshot is not None and previous is not None and snapshot[0]!=previous[0]:self.test_slot=slot
                elif self.test_slot is None and snapshot is not None:self.test_slot=slot
            self.test_states[slot]=snapshot
        # Holding B for a moment leaves; a tap only lights the button up.
        active=self.test_states.get(self.test_slot) if self.test_slot is not None else None
        if active and active[0]&BUTTON_BITS["b"]:
            if self.test_hold_since is None:self.test_hold_since=time.monotonic()
            elif time.monotonic()-self.test_hold_since>=HOLD_TO_LEAVE:
                self.test_hold_since=None;self.back();return
            changed=True
        else:self.test_hold_since=None
        if changed:self.schedule_draw()

    def _oval(self,x,y,w,h,fill="",outline="",width=2):
        return self.canvas.create_oval(self.offset_x+x*self.scale,self.offset_y+y*self.scale,
            self.offset_x+(x+w)*self.scale,self.offset_y+(y+h)*self.scale,fill=fill,outline=outline,width=max(1,width*self.scale))

    def _controller_test(self):
        self.panel(0,0,1280,720,"#000000c4",0)
        self.targets=[];self.callbacks={}
        devices=self._controller_test_devices()
        self.display(140,54,"CONTROLLER TEST",40,WHITE,"bold")
        self.display(140,108,"Press anything on a controller: the diagram follows it. Hold B to go back.",17,"#c9c9ce")
        self.icon("controller",1100,62,32,"#8a8a90")
        if not devices:
            self.display(640,330,"No controller connected",30,WHITE,anchor="center")
            self.display(640,374,"Connect a USB pad, pair a Bluetooth controller or connect the phone controller.",18,"#97979e",anchor="center")
        for index,device in enumerate(devices):
            slot=device.get("slot");x=140+index*280;active=slot==self.test_slot
            self.panel(x,156,264,46,"#2c232a" if active else "#17181c",3,WHITE if active else None)
            self.display(x+14,164,self.fit_text(f"{slot+1} · "+(device.get('name') or 'Controller'),238,15),15,WHITE if active else "#b6b9c4")
            self.display(x+14,185,"mapped" if device.get("mapped") else "setup needed",11,ACCENT if device.get("mapped") else "#d6bb34")
            self.target(f"controller-test:slot:{slot}",(x,156,x+264,202),lambda s=slot:setattr(self,"test_slot",s) or self.schedule_draw(),device.get("name") or "Controller")
        state=self.test_states.get(self.test_slot) if self.test_slot is not None else None
        buttons,lx,ly,rx,ry,lt,rt=state if state else (0,0,0,0,0,0,0)
        idle,line,hot="#1c1d23","#5c5d66",ACCENT
        def on(name):return hot if buttons&BUTTON_BITS[name] else idle
        # Body and grips.
        self.panel(360,268,560,250,"#111216",80)
        self._oval(352,330,150,240,"#111216","#2a2b33",2);self._oval(778,330,150,240,"#111216","#2a2b33",2)
        self.panel(360,268,560,250,"",80,"#2a2b33")
        # Triggers and bumpers.
        for name,x,value in (("lb",420,lt),("rb",750,rt)):
            self.panel(x,214,110,18,idle,6,line)
            if value:self.panel(x,214,max(6,110*trigger_fraction(value)),18,hot,6)
            self.display(x+55,223,"LT" if name=="lb" else "RT",11,WHITE,anchor="center")
            self.panel(x,242,110,22,on(name),8,line)
            self.display(x+55,253,name.upper(),13,WHITE,anchor="center")
        # Left stick, d-pad, right stick.
        for name,cx,cy,ax,ay in (("l3",470,345,lx,ly),("r3",740,432,rx,ry)):
            self._oval(cx-40,cy-40,80,80,idle,hot if buttons&BUTTON_BITS[name] else line,2)
            dx,dy=axis_fraction(ax)*24,axis_fraction(ay)*24
            moving=abs(axis_fraction(ax))>0.08 or abs(axis_fraction(ay))>0.08
            self._oval(cx+dx-14,cy+dy-14,28,28,hot if moving or buttons&BUTTON_BITS[name] else "#8a8a90","")
        cx,cy=548,432
        for name,x,y,w,h in (("up",cx-10,cy-44,20,34),("down",cx-10,cy+10,20,34),("left",cx-44,cy-10,34,20),("right",cx+10,cy-10,34,20)):
            self.panel(x,y,w,h,on(name),4,line)
        self.panel(cx-10,cy-10,20,20,idle,2)
        # View, Guide, Menu.
        self._oval(596,336,28,20,on("view"),line,2);self._oval(656,336,28,20,on("menu"),line,2)
        self._oval(622,286,36,36,on("guide"),line,2)
        # Face buttons.
        for name,x,y,color in (("y",805,296,"#d6bb34"),("x",765,336,"#35a4c6"),("b",845,336,"#d75b78"),("a",805,376,"#5bb974")):
            self._oval(x,y,40,40,hot if buttons&BUTTON_BITS[name] else idle,color,2)
            self.display(x+20,y+20,name.upper(),16,WHITE,"bold",anchor="center")
        # Readout.
        names=pressed_names(buttons)
        self.display(140,560,"Pressed: "+(", ".join(name.upper() for name in names) if names else "nothing"),16,WHITE)
        self.display(140,590,f"Left stick {lx:>6} {ly:>6}    Right stick {rx:>6} {ry:>6}    Triggers {lt:>3} {rt:>3}",14,MUTED)
        if self.test_hold_since is not None:
            held=min(1.0,(time.monotonic()-self.test_hold_since)/HOLD_TO_LEAVE)
            self.panel(140,622,300,6,"#2a2b33",3);self.panel(140,622,max(6,300*held),6,ACCENT,3)
            self.display(452,614,"Keep holding B to go back",13,MUTED)
        self.button_canvas("controller-test:back","Back",1030,616,170,40,self.back)
