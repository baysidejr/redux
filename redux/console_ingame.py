"""Model-driven game panels over the already rendered, real paused frame.

The session/controller services supply actions and slot metadata. Presentation
never fabricates states, previews, controller devices or backend capabilities.
"""
from __future__ import annotations

from .state_presentation import format_saved_at

WHITE="#f4f4f2"
MUTED="#aaaab1"
DIM="#777780"
ACCENT="#ec407a"


def _selected(focus,count):
    try:index=int(str(focus).split(":")[-1]) if str(focus).startswith("overlay:") else 0
    except ValueError:index=0
    return max(0,min(index,max(0,count-1)))


class ConsoleIngame:
    def _ingame_panel_grid(self,overlay,heading):
        # The gameplay renderer has already drawn the genuine latest frame.
        # Cover only the lower part; do not substitute cover art for that frame.
        self.panel(0,292,1280,428,"#171719",0)
        self.rect(0,292,1280,2,"#404047")
        self.display(80,313,heading,30,WHITE,"bold")
        message=str(overlay.get("message","")).replace("\n","  ·  ")
        if message:self.display(80,352,self.fit_text(message,1120,16),16,MUTED)
        items=overlay.get("items",())
        selected=_selected(self.focus,len(items));start=(selected//10)*10
        for offset,(label,callback) in enumerate(items[start:start+10]):
            index=start+offset;column,row=divmod(offset,5)
            x=80+column*580;y=379+row*50;key=f"overlay:{index}"
            focused=self.focus==key
            self.panel(x,y,540,44,"#303036" if focused else "#202023",2,WHITE if focused else None)
            if focused:self.rect(x,y+8,3,28,ACCENT)
            self.display(x+16,y+11,self.fit_text(label,505,21),21,WHITE if focused else MUTED)
            self.target(key,(x,y,x+540,y+44),self._overlay_run(overlay,callback),label)
        if len(items)>10:self.display(1200,644,f"{start+1}–{min(start+10,len(items))} / {len(items)}",15,DIM,anchor="ne")
        self._controller_hint("B","Back",80,675,"#d75b78")
        self._controller_hint("A","Select",245,675,"#35a4c6")

    def _display_panel(self,overlay):
        self._ingame_panel_grid(overlay,"DISPLAY")

    def _controls_panel(self,overlay):
        self.panel(0,292,1280,428,"#171719",0)
        self.rect(0,292,1280,2,"#404047")
        self.display(80,313,"CONTROLLERS",30,WHITE,"bold")
        self.display(80,375,str(overlay.get("message","")),19,MUTED,width=520)
        items=overlay.get("items",());selected=_selected(self.focus,len(items));start=(selected//6)*6
        for offset,(label,callback) in enumerate(items[start:start+6]):
            index=start+offset;x=660;y=376+offset*47;key=f"overlay:{index}";focused=self.focus==key
            self.panel(x,y,540,41,"#303036" if focused else "#202023",2,WHITE if focused else None)
            if focused:self.rect(x,y+7,3,27,ACCENT)
            self.display(x+16,y+10,self.fit_text(label,505,20),20,WHITE if focused else MUTED)
            self.target(key,(x,y,x+540,y+41),self._overlay_run(overlay,callback),label)
        self._controller_hint("B","Back",80,675,"#d75b78")
        self._controller_hint("A","Select",245,675,"#35a4c6")

    def _state_slots_panel(self,overlay):
        self.panel(0,190,1280,530,"#111113",0)
        self.rect(0,190,1280,2,"#404047")
        operation=overlay.get("operation","load")
        self.display(80,213,"SAVE STATE" if operation=="save" else "LOAD STATE",30,WHITE,"bold")
        slot_cards=tuple(overlay.get("cards",()))[:4]
        items=overlay.get("items",())
        for index,card in enumerate(slot_cards):
            x=80+285*index;y=270;key=f"overlay:{index}";focused=self.focus==key
            self.panel(x-3,y-3,266,312,"#2b2b31" if focused else "#1b1b1e",2,WHITE if focused else None)
            self.rect(x,y,260,220,"#080809")
            shown=card.thumbnail_path and self.picture(card.thumbnail_path,x,y,260,220,mode="contain")
            if not shown:
                empty="Empty slot" if not card.exists else "Preview unavailable" if card.compatible or card.old_version else "Unavailable"
                self.display(x+130,y+110,empty,21,DIM,width=234,anchor="center")
            if card.old_version:
                self.rect(x+8,y+8,112,23,"#8b3548")
                self.display(x+64,y+19,"OLD VERSION",13,WHITE,"bold",anchor="center")
            elif card.exists and not card.compatible:
                self.rect(x+8,y+8,112,23,"#55412d")
                self.display(x+64,y+19,"UNAVAILABLE",13,WHITE,"bold",anchor="center")
            self.display(x+13,507,f"SLOT {card.slot+1}",20,WHITE,"bold")
            stamp=format_saved_at(card.saved_at) if card.exists else "No saved state"
            self.display(x+13,540,self.fit_text(stamp,234,17),17,MUTED)
            if index<len(items):
                label,callback=items[index]
                self.target(key,(x-3,y-3,x+263,y+309),self._overlay_run(overlay,callback),label)
        if not slot_cards:self.display(80,330,"No save slots are available for this session.",23,MUTED)
        for offset,(label,callback) in enumerate(items[len(slot_cards):len(slot_cards)+4]):
            index=len(slot_cards)+offset;x=80+285*offset
            self.button_canvas(f"overlay:{index}",label,x,618,260,42,self._overlay_run(overlay,callback))
        message=str(overlay.get("message","")).replace("\n","  ·  ")
        if message:self.display(80,589,self.fit_text(message,1120,16),16,MUTED)
        self._controller_hint("B","Back",80,686,"#d75b78")
        self._controller_hint("A","Select",245,686,"#35a4c6")

    def _game_menu_panel(self,overlay):
        items=overlay.get("items",());selected=_selected(self.focus,len(items))
        start=max(0,min(selected-7,max(0,len(items)-8)))
        game=self._overlay_game(True)
        if not getattr(self,"embedded",None):self.rect(0,0,844,720,"#101113")
        else:self.panel(0,0,844,720,"#00000070",0)
        self.rect(844,0,436,720,"#242427")
        self.display(80,355,"PAUSED" if overlay.get("title")=="Game paused" else "GAME MENU",15,MUTED,"bold")
        heading=game.get("title","") if game else "Game menu"
        self.display(80,391,self.fit_text(heading,685,36,"bold"),36,WHITE,"bold")
        message=str(overlay.get("message","")).replace("\n","  ·  ")
        if message:self.display(80,506,self.fit_text(message,685,18),18,MUTED)
        for offset,(label,callback) in enumerate(items[start:start+8]):
            index=start+offset;y=159+offset*54;key=f"overlay:{index}";focused=self.focus==key
            if focused:
                self.rect(866,y,390,46,"",WHITE,1)
                self.rect(872,y+8,3,30,ACCENT)
            self.display(890,y+11,self.fit_text(label,345,22),22,WHITE if focused else MUTED)
            self.target(key,(866,y,1256,y+46),self._overlay_run(overlay,callback),label)
        if len(items)>8:self.display(1256,615,f"{start+1}–{min(start+8,len(items))} / {len(items)}",15,DIM,anchor="ne")
        self._controller_hint("B","Resume",870,675,"#d75b78")
        self._controller_hint("A","Select",1090,675,"#35a4c6")
