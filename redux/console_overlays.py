"""Console modal presentation over the existing library and playback actions."""
from __future__ import annotations

from .library import SYSTEMS

WHITE="#f4f4f2"
MUTED="#aaaab1"
ACCENT="#ec407a"


def _title(game):
    return game.get("title","").split(" (USA)")[0].split(" (Europe)")[0].split(" (Japan)")[0]


def _game_action(label):
    lowered=label.casefold()
    if "resume" in lowered or "return to game" in lowered:return "Resume","play"
    if "save state" in lowered:return "Save State","storage"
    if "load state" in lowered:return "Load State","library"
    if "screenshot" in lowered:return "Screenshot","systems"
    if "reset" in lowered:return "Reset","back"
    if "back up" in lowered or "backup" in lowered:return "Backup","storage"
    if "stop" in lowered:return "Stop","home"
    return label,"settings"


class OverlayScreens:
    def _overlay_game(self,in_game=False):
        if in_game:
            game=getattr(self,"embedded_game",None)
            if getattr(self,"embedded",None) is not None and isinstance(game,dict):return game
            playback=getattr(self,"playback",None)
            if playback:
                active=playback.active()
                if active:
                    try:return self.library.game(active[0].game_id)
                    except (OSError,ValueError,KeyError):pass
        return self.selected()

    def _overlay_run(self,overlay,callback):
        def run():
            self.overlay=None
            self.focus=overlay.get("previous_focus","nav:home")
            callback()
            self.schedule_draw()
        return run

    def _overlay(self):
        overlay=self.overlay
        if not overlay:return
        self.targets=[];self.callbacks={}
        if overlay.get("kind")=="file_browser":self._file_browser_panel(overlay);return
        panel={"display_panel":self._display_panel,"state_slots":self._state_slots_panel,"controls_panel":self._controls_panel}.get(overlay.get("kind"))
        if panel:panel(overlay);return
        if overlay["title"] in {"Game paused","Game menu"}:
            self._game_menu_panel(overlay);return
        items=overlay["items"];count=len(items)
        try:selected=int(str(self.focus).split(":")[-1]) if str(self.focus).startswith("overlay:") else 0
        except ValueError:selected=0
        selected=max(0,min(selected,max(0,count-1)))
        start=max(0,min(selected-5,count-6))
        visible=items[start:start+6]
        self.targets=[];self.callbacks={}
        in_game=overlay["title"] in {"Game paused","Game menu"}
        if in_game:
            self.panel(0,0,1280,720,"#000000b0",0)
            game=self._overlay_game(True)
            self.display(100,311,"PAUSED" if overlay["title"]=="Game paused" else "GAME MENU",14,MUTED)
            heading=_title(game) if game else overlay["title"]
            self.display(100,342,self.fit_text(heading,1080,35),36,WHITE)
            message=overlay.get("message","")
            if message:self.display(100,393,self.fit_text(message,1080,17),18,MUTED)
            self.panel(100,420,1080,110,"#252528",0)
            step=1080/max(1,len(visible))
            for offset,(label,callback) in enumerate(visible):
                index=start+offset;x=100+offset*step;key=f"overlay:{index}"
                focused=self.focus==key
                if focused:self.panel(x+1,421,step-2,108,ACCENT,0,WHITE)
                text,icon=_game_action(label)
                self.icon(icon,x+step/2-16,436,32,WHITE)
                self.display(x+step/2,499,self.fit_text(text,step-20,20),20,WHITE,anchor="center")
                self.target(key,(x,420,x+step,530),self._overlay_run(overlay,callback),label)
            self._controller_hint("B","Resume",100,622,"#d75b78")
            self._controller_hint("A","Select",248,622,"#35a4c6")
            if count>6:self.display(1180,622,f"{start+1}–{min(start+6,count)} / {count}",16,MUTED,anchor="ne")
            return

        self.rect(0,0,1280,720,"#09090b")
        background=self.art.resource("dialog/background_default.png")
        if background:self.picture(background,0,0,790,720)
        self.rect(790,0,490,720,"#272729")
        self.display(68,65,overlay["title"],36,WHITE,width=650)
        message=str(overlay.get("message",""))
        if message:self.display(68,130,message[:1600],16 if len(message)>700 else 20,MUTED,width=650)
        if len(message)<220 and message.count("\n")<=3:
            disc=overlay.get("disc")
            game=None if disc else self._overlay_game()
            if disc:
                visual=disc.cover_path or self.art.path(disc.system,"tile")
                self.rect(68,275,245,245,"#171719")
                self.picture(visual,68,275,245,245,mode="contain")
                self.display(345,300,self.fit_text(disc.title,375,28),29,WHITE)
                self.display(345,351,SYSTEMS.get(disc.system,"Unidentified system"),20,MUTED,width=375)
                self.display(345,394," · ".join(filter(None,(disc.drive,disc.game_id))),17,"#92929a",width=375)
            if game:
                visual=self._game_visual(game)
                if not visual:visual=self.art.path(game["system"],"tile")
                self.rect(68,275,245,245,"#171719")
                self.picture(visual,68,275,245,245,mode="contain")
                self.display(345,300,self.fit_text(_title(game),375,28),29,WHITE)
                self.display(345,351,SYSTEMS.get(game["system"],game["system"]),20,MUTED,width=375)
                facts=[str(game["year"]) for _ in (0,) if game.get("year")]
                if game.get("publisher"):facts.append(game["publisher"])
                if facts:self.display(345,394," · ".join(facts),17,"#92929a",width=375)
        for offset,(label,callback) in enumerate(visible):
            index=start+offset;y=180+offset*62;key=f"overlay:{index}";focused=self.focus==key
            if focused:
                self.rect(818,y+7,3,35,ACCENT)
                self.rect(812,y-2,436,52,"",WHITE,1)
            self.display(837,y+8,self.fit_text(label,390,24 if focused else 22),25 if focused else 23,WHITE if focused else "#a3a3aa")
            self.target(key,(812,y-2,1248,y+50),self._overlay_run(overlay,callback),label)
        if count>6:self.display(1238,599,f"{start+1}–{min(start+6,count)} / {count}  ↑ ↓",16,"#8b8b93",anchor="ne")
        self._controller_hint("B","Back",826,652,"#d75b78")
        self._controller_hint("A","Select",1062,652,"#35a4c6")
