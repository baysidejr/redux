"""Console presentation traced from the supplied visual references.

Storage, playback and input remain separate services. Layout coordinates use a
1280 by 720 television canvas; the local resource artwork is optional.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
import tkinter as tk
from tkinter import font as tkfont

from .library import SYSTEMS,ARTWORK_FIELDS
from .ui_model import FocusTarget,page_window
from .ui_theme import Painter,BACKGROUND,SURFACE,RAISED,WHITE,SUBTLE,MUTED,ACCENT
from . import __version__
from .brand import fuji_shapes

LANE=1280*520/1920
LANE_SCREENS=frozenset({"home","systems","collections","settings","saves","media"})
BADGES={"psx":("PS","#8e24aa"),"nes":("NES","#ec407a"),"snes":("SNES","#7e57c2"),
    "pc":("PC","#2779b0"),
    "genesis":("GEN","#1976d2"),"saturn":("SAT","#00a6af"),"neo_geo_cd":("NGCD","#65a641"),
    "pce":("PCE","#dc9d38"),"n64":("N64","#4f9a58")}


def title(game):
    return game.get("title","").split(" (USA)")[0].split(" (Europe)")[0].split(" (Japan)")[0]


class ReferenceLayout:
    def render(self):
        self.redraw_id=None
        if self.closing:return
        width=self.canvas.winfo_width();height=self.canvas.winfo_height()
        # A withdrawn startup window, minimization, or a TV mode change can
        # briefly report a 1x1 canvas. Wait for Configure instead of rasterizing
        # zero-pixel artwork and showing an error before the first real frame.
        if width<32 or height<18:return
        self.scale=min(width/1280,height/720)
        self.offset_x=(width-1280*self.scale)/2;self.offset_y=(height-720*self.scale)/2
        self.canvas.delete("all");self.targets=[];self.callbacks={};self.photos=[]
        self.painter.begin_frame(scale=self.scale,offset=(self.offset_x,self.offset_y))
        self.rect(0,0,1280,720,"#080909")
        if self.screen!="gameplay":
            lane=self.screen in LANE_SCREENS
            background=self.art.backdrop(round(1280*self.scale),round(720*self.scale),lane)
            self.photos.append(background)
            self.canvas.create_image(self.offset_x,self.offset_y,anchor="nw",image=background)
            if lane:self._header()
        screen={"home":self._home,"library":self._library,"details":self._game_details,
            "systems":self._systems,"collections":self._collections,"settings":self._settings,
            "search":self._search,"saves":self._saves,"gameplay":self._gameplay,
            "text":self._text_editor,"screenshots":self._screenshots,"screenshot":self._screenshot,
            "media":self._media,"metadata_editor":self._metadata_editor,"disc_info":self._disc_info,
            "controller_setup":self._controller_setup}.get(self.screen)
        if screen:screen()
        self._footer()
        if self.overlay:self._overlay()
        self._toast()
        if self.targets and self.focus not in self.callbacks:
            self.focus=self.targets[0].key;self.root.after_idle(self.schedule_draw)

    def text(self,x,y,value,size=16,color=WHITE,weight="normal",width=None,anchor="nw"):
        return self.canvas.create_text(self.offset_x+x*self.scale,self.offset_y+y*self.scale,text=value,
            anchor=anchor,fill=color,font=(self.body_font,-max(8,round(size*self.scale)),weight),
            width=round(width*self.scale) if width else 0)

    def display(self,x,y,value,size=36,color=WHITE,weight="normal",width=None,anchor="nw"):
        return self.canvas.create_text(self.offset_x+x*self.scale,self.offset_y+y*self.scale,text=value,
            anchor=anchor,fill=color,font=(self.display_font,-max(9,round(size*self.scale)),weight),
            width=round(width*self.scale) if width else 0)

    def fit_text(self,value,width,size=16,weight="normal"):
        value=str(value)
        key=(self.body_font,size,weight,round(self.scale,3))
        if key not in self.fonts:self.fonts[key]=tkfont.Font(self.root,family=self.body_font,size=-max(8,round(size*self.scale)),weight=weight)
        font=self.fonts[key];limit=width*self.scale
        if font.measure(value)<=limit:return value
        low,high=0,len(value)
        while low<high:
            middle=(low+high+1)//2
            if font.measure(value[:middle]+"…")<=limit:low=middle
            else:high=middle-1
        return value[:low]+"…"

    def panel(self,x,y,w,h,fill=SURFACE,radius=3,outline=None):
        return self.painter.rounded_rect(x,y,w,h,fill,radius=radius,outline=outline,stroke=1)

    def icon(self,name,x,y,size=20,color=WHITE,stroke=2,filled=False):
        return self.painter.icon(name,x,y,size,color,stroke,filled)

    def picture(self,path,x,y,w,h,shade=False,*,mode="cover",radius=0):
        photo=self.art.image(path,round(w*self.scale),round(h*self.scale),shade,mode=mode,radius=round(radius*self.scale))
        if photo:
            self.photos.append(photo)
            self.canvas.create_image(self.offset_x+x*self.scale,self.offset_y+y*self.scale,anchor="nw",image=photo)
            return True
        return False

    def button_canvas(self,key,label,x,y,w,h,callback,primary=False,active=False):
        focused=self.focus==key
        fill=ACCENT if primary or active else "#252629" if focused else "#191a1c"
        self.panel(x,y,w,h,fill,radius=2,outline=WHITE if focused else "#38393c" if not primary else None)
        self.display(x+w/2,y+h/2,self.fit_text(label,w-24,17),18,WHITE,"normal",anchor="center")
        self.target(key,(x,y,x+w,y+h),callback,label)

    def _header(self):
        search="nav:search"
        self.panel(64,47,64,64,"#853398",radius=12,outline=WHITE if self.focus==search else None)
        self.icon("search",82,65,28)
        self.target(search,(64,47,128,111),self.search_screen,"Search")
        entries=[("home","Now Playing",266),("library","Collection",342),("collections","Playlists",416),
            ("media","Media",490),("settings","System",564)]
        current="library" if self.screen=="systems" else "settings" if self.screen in {"saves","text"} else self.screen
        for screen,label,y in entries:
            key=f"nav:{screen}";focused=self.focus==key;active=current==screen
            if focused:self.rect(49,y-9,271,50,"#454547")
            self.display(64,y,label,30 if active else 23,WHITE if active or focused else "#929292")
            self.target(key,(48,y-12,326,y+45),lambda s=screen:self._nav(s),label)
        self.display(64,677,"REDUX",14,"#8f8f91","bold")
        self.text(122,678,__version__,11,"#77777b")

    def _identity(self,x=1080,y=34):
        self.display(x,y+9,"REDUX",16,"#c8c8c8","bold")
        self.panel(x+65,y,35,35,"#303033",radius=18)
        for color,points in fuji_shapes():
            self.canvas.create_polygon(*self._points([(x+71+px*.23,y+6+py*.23) for px,py in points]),fill=color,outline="")
        self.display(x+114,y+10,datetime.now().strftime("%I:%M%p").lstrip("0"),15,"#bababc","bold")

    def _points(self,points):
        return [coordinate for x,y in points for coordinate in (self.offset_x+x*self.scale,self.offset_y+y*self.scale)]

    def _top_shade(self,x,y,w,h):
        photo=self.art.scrim(round(w*self.scale),round(h*self.scale))
        self.photos.append(photo)
        self.canvas.create_image(self.offset_x+x*self.scale,self.offset_y+y*self.scale,image=photo,anchor="nw")

    def _badge(self,system,x,y):
        label,color=BADGES.get(system,(system.upper().replace("_"," ")[:6],"#586171"))
        w=max(32,len(label)*8+12)
        self.rect(x,y,w,20,color);self.display(x+w/2,y+10,label,14,WHITE,"bold",anchor="center")
        return w

    def _game_artwork(self,game,role):
        if not game or not game.get(ARTWORK_FIELDS[role]):return None
        try:
            custom=self.library.artwork_for(game) if role=="cover" else self.library.artwork_for(game,role=role)
            if custom and Path(custom).is_file():return custom
        except (OSError,ValueError):pass
        return None

    def _captured_game_screenshot(self,game):
        if not game:return None
        root=self.library.root/"game_data"/str(game["id"])
        folders=[root/game.get("bundle_hash","")/"screenshots",root/"screenshots"]
        for folder in folders:
            try:
                files=sorted((path for path in folder.glob("*.png") if path.is_file() and not path.is_symlink()),reverse=True)
                if files:return files[0]
            except OSError:pass
        return None

    def _game_screenshot(self,game):
        return self._game_artwork(game,"screenshot") or self._captured_game_screenshot(game)

    def _game_visual(self,game):
        return self._game_artwork(game,"cover") or self._game_artwork(game,"tile") or self._game_screenshot(game)

    def _game_hero(self,game):
        # A box cover is not a cinematic banner: retain its complete shape in
        # the cover panel and use purpose-specific art/gameplay above it.
        return self._game_artwork(game,"hero") or self._game_screenshot(game)

    def _home(self):
        game=self.selected();system=game["system"] if game else "psx"
        disc=self.inserted_disc()
        if disc:system=disc.system or "unknown"
        x=LANE;w=1280-x;h=w*437/1400
        image=(disc.hero_path if disc else self._game_hero(game)) or self.art.path(system,"hero")
        if not self.picture(image,x,0,w,h,shade=bool(disc)):
            self.rect(x,0,w,h,"#17121f")
            if not disc:self.display(x+54,100,SYSTEMS.get(system,"Your collection"),54,"#72707a")
        if disc:
            self._top_shade(x,0,w,h-29)
            has_cover=disc.cover_path and self.picture(disc.cover_path,x+22,20,168,h-66,mode="contain")
            tx=x+216 if has_cover else x+36
            self.display(tx,58,"DISC INSERTED" if not disc.is_virtual else "MOUNTED IMAGE",14,"#c7c1d1","bold")
            self.display(tx,92,disc.title.upper(),38,WHITE,"bold",width=1280-tx-30)
            facts=[SYSTEMS.get(system,"Unidentified system"),disc.drive]
            if disc.metadata.get("year"):facts.append(str(disc.metadata["year"]))
            if disc.game_id:facts.append(disc.game_id)
            self.display(tx,197,"  ·  ".join(facts),18,"#dedbe3")
            self.display(tx,227,"Play disc or install to your library",16,"#b4b0bb")
        self._identity(1090,27)
        strip_y=h-29
        self.rect(x,strip_y,w,29,"#080808")
        badge_width=self._badge(system,x+12,strip_y+4)
        self.display(x+max(65,24+badge_width),strip_y+7,self.fit_text(disc.title.upper() if disc else title(game).upper() if game else "ADD YOUR FIRST GAME",540,15),15,WHITE)
        self._controller_hint("A","Disc options" if disc else "Play" if game else "Add media",1042 if disc else 1080,strip_y+6,"#35a4c6")
        self._controller_hint("Y","Info" if disc else "Manage",1180 if disc else 1163,strip_y+6,"#d6bb34")
        self.target("hero:play",(x+2,2,1278,h-2),self.inserted_disc_options if disc else self.play if game else self.add_files,"Disc options" if disc else "Play selected game")
        self.target("hero:details",(1174 if disc else 1158,strip_y,1279,h),self.inserted_disc_information if disc else lambda:self.open_game(game["id"]) if game else self.add_files(),"Disc information" if disc else "Manage game")
        if self.focus in {"hero:play","hero:details"}:self.rect(x+1,1,w-3,h-2,"",WHITE,2)
        self.display(360,315,"Collection",21,"#a2a2a4")
        systems=[s for s in SYSTEMS if s!="unknown" and any(g["system"]==s for g in self.all_games)]
        if not systems:
            self._empty_home();return
        system_index=next((i for i,s in enumerate(systems) if f"home-system:{s}"==self.focus),0)
        start=max(0,min(system_index-3,len(systems)-4))
        for i,s in enumerate(systems[start:start+4]):
            sx=360+i*(213+13.5);key=f"home-system:{s}"
            self._system_tile(s,key,sx,348,213,149,lambda value=s:self._open_system(value))
        self.display(360,524,"Continue Playing" if any(g.get("last_played") for g in self.all_games) else "My Games",21,"#a2a2a4")
        games=sorted(self.all_games,key=lambda item:item.get("last_played") or "",reverse=True)
        self.home_rows=games
        index=next((i for i,g in enumerate(games) if f"game:{g['id']}"==self.focus),0)
        start=max(0,min(index-3,len(games)-4))
        for i,entry in enumerate(games[start:start+4]):self._card(entry,360+i*226.5,558,213,149)

    def _empty_home(self):
        self._add_tile("add:tile",360,348,213,149,self.add_files,"Add media")
        self.display(599,365,"Make room for your collection",30,WHITE)
        self.display(600,409,"Add a disc image or scan a media folder.\nYour games stay on your own storage.",20,"#a1a1a5",width=610)
        self.button_canvas("home:scan","Scan a folder",600,473,205,42,self.scan_folder)

    def _system_tile(self,system,key,x,y,w,h,callback):
        if not self.picture(self.art.path(system,"tile"),x,y,w,h):
            self.rect(x,y,w,h,"#25212e")
            self.display(x+w/2,y+h/2,SYSTEMS[system],28,WHITE,width=w-22,anchor="center")
        if self.focus==key:self.rect(x-3,y-3,w+6,h+6,"",WHITE,2)
        self.target(key,(x,y,x+w,y+h),callback,SYSTEMS[system])

    def _add_tile(self,key,x,y,w,h,callback,label):
        self.rect(x,y,w,h,"#1a1a1d")
        self.icon("plus",x+w/2-16,y+h/2-32,32,"#98989d")
        self.display(x+w/2,y+h/2+23,label,20,"#aaaab0",anchor="center")
        if self.focus==key:self.rect(x-3,y-3,w+6,h+6,"",WHITE,2)
        self.target(key,(x,y,x+w,y+h),callback,label)

    def _card(self,game,x,y,w,h):
        key=f"game:{game['id']}"
        self.rect(x,y,w,h,"#111113")
        rendered_role=None
        visuals=(("tile",self._game_artwork(game,"tile"),"cover"),
                 ("cover",self._game_artwork(game,"cover"),"contain"),
                 ("screenshot",self._game_screenshot(game),"contain"))
        for role,visual,mode in visuals:
            # The screenshot title has its own space, so it does not cover the
            # bottom of the game frame. Portrait box fronts are never cropped.
            if visual and self.picture(visual,x,y,w,h-35 if role=="screenshot" else h,mode=mode):
                rendered_role=role;break
        if rendered_role is None:
            self.picture(self.art.path(game["system"],"tile"),x,y,w,h)
        self._badge(game["system"],x+w-max(32,len(BADGES.get(game["system"],(game["system"],None))[0])*8+12),y)
        # Captured gameplay is not a supplied box cover. Its title remains a
        # separate, legible label rather than pretending a system logo is art.
        if rendered_role not in {"cover","tile"}:
            self.rect(x,y+h-35,w,35,"#080809")
            self.display(x+9,y+h-29,self.fit_text(title(game),w-18,20),20,WHITE)
        if game.get("favorite"):self.icon("star",x+8,y+8,18,"#f1ce83",filled=True)
        if not game.get("available"):
            self.rect(x,y+h-59,w,24,"#342322");self.display(x+9,y+h-56,"Media offline",15,"#edb7a7")
        if self.focus==key:self.rect(x-3,y-3,w+6,h+6,"",WHITE,2)
        self.target(key,(x,y,x+w,y+h),lambda identity=game["id"]:self.open_game(identity),game["title"])

    def _library(self):
        game_system=self.system_filter
        collection=next((c["name"] for c in self.library.collections() if c["id"]==self.collection_id),None)
        heading=collection or SYSTEMS.get(game_system,"Collection")
        if self.view!="all":heading={"favorites":"Favorites","continue":"Continue Playing","wishlist":"Wishlist","completed":"Completed Games"}.get(self.view,heading)
        focused_game=self.selected() if str(self.focus).startswith("game:") else None
        if focused_game:heading=title(focused_game).upper()
        self.display(80,43,heading,40,WHITE,"bold")
        self.display(80,101,(focused_game.get("publisher") or SYSTEMS.get(focused_game["system"],"")) if focused_game else f"Showing {len(self.rows)} Games",20,WHITE)
        self._identity(1080,46)
        self.rect(0,143,1280,53,"#202020")
        self.icon("search",106,156,26,"#8b8b8b")
        self.target("library:search",(80,146,154,192),self.search_screen,"Search")
        entries=[("A–Z","title"),("Genre","genre"),("Players","players"),("Year","year"),("Rating","rating"),("System","system")]
        selected=getattr(self,"facet_tab","system")
        for i,(label,kind) in enumerate(entries):
            x=188+i*113.5;key=f"facet:{kind}"
            self.display(x+45,155,label,27,WHITE if selected==kind or self.focus==key else "#88888b",anchor="n")
            if selected==kind:self.rect(x,192,90,3,ACCENT)
            if self.focus==key:self.rect(x,146,90,1,WHITE)
            self.target(key,(x,146,x+110,195),lambda value=kind:self.facet_menu(value),label)
        self.display(80,226,SYSTEMS.get(game_system,"All Systems"),27,"#a0a0a4")
        self.target("library:back",(28,42,66,92),self.back,"Back")
        self.icon("back",31,53,26,"#94949a")
        self.target("library:views",(80,218,420,262),self.library_views,"Library views")
        if collection:self.button_canvas("collection:manage","Edit playlist",1030,220,170,35,self.manage_collection)
        if not self.rows:
            self.display(80,325,"No games in this view",34,WHITE)
            self.display(80,378,"Choose a different filter or add your media.",21,"#8e8e94")
            self.button_canvas("empty:import","Add media",80,440,213,45,self.add_files,True);return
        index=next((i for i,g in enumerate(self.rows) if f"game:{g['id']}"==self.focus),0)
        start,end=page_window(len(self.rows),index,5,2)
        for i,game in enumerate(self.rows[start:end]):self._card(game,80+(i%5)*226.5,285+(i//5)*163,213,149)
        self.display(1200,661,f"{start+1}–{end} / {len(self.rows)}",15,"#8b8b91",anchor="ne")

    def library_views(self):
        self.menu("Collection",[(label,lambda value=view:self._filter(value)) for view,label in
            [("all","All Games"),("continue","Continue Playing"),("favorites","Favorites"),("wishlist","Wishlist"),("completed","Completed Games")]])

    def facet_menu(self,kind):
        self.facet_tab=kind
        if kind=="title":self.sort_menu();return
        if kind=="system":
            values=[("All Systems",None)]+[(SYSTEMS[s],s) for s in SYSTEMS if any(g["system"]==s for g in self.all_games)]
            self.menu("System",[(label,lambda system=value:self._set_system_filter(system)) for label,value in values]);return
        if kind=="rating":
            self.menu("Rating",[("All Ratings",lambda:self._set_facet("rating",None))]+[("★"*n,lambda rating=n:self._set_facet("rating",rating)) for n in range(5,0,-1)]+[("Not Rated",lambda:self._set_facet("rating",0))]);return
        if kind=="players":
            self.menu("Players",[("All Games",lambda:self._set_facet("players",None))],message="Player counts have not been assigned to these library entries.");return
        values=sorted({g.get(kind) for g in self.all_games if g.get(kind)},key=str)
        self.menu(kind.title(),[("All "+("Years" if kind=="year" else "Genres"),lambda:self._set_facet(kind,None))]+[(str(value),lambda item=value:self._set_facet(kind,item)) for value in values])

    def _set_facet(self,key,value):
        if value is None:self.facet_filters.pop(key,None)
        else:self.facet_filters[key]=value
        self.refresh()

    def _set_system_filter(self,system):self.system_filter=system;self.refresh()

    def _game_details(self):
        game=self.selected()
        if not game:self._empty("Choose a game from Collection");return
        visual=self._game_visual(game);hero=self._game_hero(game) or self.art.path(game["system"],"hero")
        if not self.picture(hero,0,0,1280,388):self.rect(0,0,1280,388,"#18121e")
        self._top_shade(0,0,1280,145)
        self._identity(1080,46)
        self.rect(0,388,1280,54,"#212121")
        self.rect(0,442,1280,278,"#080909")
        # The cartridge collage is a separate lower layer, matching the
        # reference's cinematic banner / cover / information composition.
        lower=self.art.resource("details/real_bg.png")
        if lower:self.picture(lower,0,442,1280,278)
        self.target("details:back",(24,25,78,80),self.back,"Back")
        if self.focus=="details:back":self.rect(24,25,54,55,"",WHITE,1)
        self.icon("back",39,41,25)
        self.rect(99,366,248,244,"#171719")
        if not self.picture(visual,99,366,248,244,mode="contain"):
            self.picture(self.art.path(game["system"],"tile"),99,366,248,244)
        if not game.get("artwork_path"):
            self.rect(99,553,248,57,"#080809")
            self.display(112,563,self.fit_text(title(game),222,28),29,WHITE,"bold")
        current=getattr(self,"details_tab","play")
        for i,(tab,label) in enumerate([("play","Play"),("manage","Manage"),("rate","Rate")]):
            x=374+i*150;key=f"details:{tab}"
            self.display(x+72,401,label,28,WHITE if current==tab or self.focus==key else "#88888a",anchor="n")
            if self.focus==key:self.rect(x,389,130,53,"",WHITE,1)
            if current==tab:self.rect(x,439,122,3,ACCENT)
            self.target(key,(x,389,x+130,442),self.play if tab=="play" and current=="play" else lambda value=tab:self._detail_tab(value),label)
        self.display(403,495,self.fit_text(title(game).upper(),790,39,"bold"),40,WHITE,"bold")
        badge=self._badge(game["system"],404,551)
        if game.get("year"):
            self.panel(421+badge,551,48,22,"#0c0c0c",radius=1,outline="#b5b5b9")
            self.display(445+badge,562,str(game["year"]),17,WHITE,anchor="center")
        self.display(515+badge,553,game.get("publisher") or SYSTEMS.get(game["system"],""),18,"#a8a8ae")
        if current=="manage":
            actions=[("details:metadata","Game information",self.metadata_dialog),("details:art","Artwork",self.game_artwork_menu),
                ("details:collection","Add to playlist",self.collection_menu),("details:saves","Saves & backups",lambda:self.navigate("saves")),
                ("details:more","More options",self.game_actions)]
            if game["system"]=="pc":actions[3]=("details:saves","PC game setup",self.pc_game_setup)
            for i,(key,label,callback) in enumerate(actions):
                self.button_canvas(key,label,403+(i%3)*258,597+(i//3)*44,245,36,callback)
        elif current=="rate":
            rating=self._rating(game)
            self.display(404,600,"Your rating",21,"#aaaab0")
            for n in range(1,6):
                key=f"rating:{n}";x=555+(n-1)*48
                self.icon("star",x,596,31,ACCENT if rating and n<=rating else "#65656c",filled=bool(rating and n<=rating))
                if self.focus==key:self.rect(x-3,591,39,43,"",WHITE,1)
                self.target(key,(x-3,591,x+36,634),lambda value=n:self._set_rating(value),f"{n} stars")
            self.button_canvas("rating:clear","Clear rating",855,595,150,35,lambda:self._set_rating(None))
        else:
            description=game.get("description") or "Your installed game is ready to play. Press A to start, or choose Manage for game information, playlists and save backups."
            self.display(404,604,description[:210]+("…" if len(description)>210 else ""),23,"#b4b4b7",width=790)
            self.target("details:launch",(403,491,1210,673),self.play,"Play game")
            if self.focus=="details:launch":self.rect(395,481,823,196,"",WHITE,1)

    def _detail_tab(self,tab):self.details_tab=tab;self.focus=f"details:{tab}";self.schedule_draw()

    def _rating(self,game):
        from .game_preferences import GamePreferences
        try:return GamePreferences(self.library.root,game["id"],game["bundle_hash"]).rating()
        except (OSError,ValueError):return None

    def _set_rating(self,value):
        from .game_preferences import GamePreferences
        game=self.selected()
        GamePreferences(self.library.root,game["id"],game["bundle_hash"]).set_rating(value)
        self.status.set("Rating saved");self.schedule_draw()

    def _media(self):
        self.display(370,49,"Media",40,WHITE,"bold")
        self.display(370,107,"Add games from your own discs and storage.",21,"#a6a6ad")
        entries=[("media:files","disc","Add media files","Import a complete disc image or cartridge file.",self.add_files),
            ("media:pc","play","Add PC game","Link an installed GOG or other Windows game EXE.",self.add_pc_game),
            ("media:scan","storage","Scan a folder","Find game images on your SSD, SD card or local drive.",self.scan_folder),
            ("media:disc","disc","Optical drives","Inspect a mounted image or capture a supported CD.",self.disc_dialog),
            ("media:library","library","Library location",str(self.library.root),self.choose_library)]
        for i,(key,icon,label,description,callback) in enumerate(entries):
            y=160+i*96;focused=self.focus==key
            self.rect(370,y,865,89,"#272727" if focused else "#161618")
            if focused:self.rect(370,y,3,89,ACCENT)
            self.icon(icon,393,y+25,31,"#c6c6cc")
            self.display(446,y+14,label,25,WHITE)
            self.display(447,y+49,self.fit_text(description,710,17),18,"#97979e")
            self.icon("chevron",1193,y+31,23,"#777780")
            self.target(key,(370,y,1235,y+89),callback,label)

    def _empty(self,message):self.display(800,340,message,30,"#aaaab2",width=810,anchor="center")

    def _controller_hint(self,key,label,x,y,color="#9a9aa2"):
        self.panel(x,y,19,19,"#29292c",radius=10)
        self.display(x+9.5,y+9.5,key,15,color,"bold",anchor="center")
        self.display(x+25,y+1,label,17,"#a5a5aa")

    def _footer(self):
        if self.screen!="home":
            self.rect(0,684,1280,36,"#09090a")
            left=370 if self.screen in LANE_SCREENS else 80
            if self.screen=="gameplay" and not self.overlay:
                self.display(80,694,"Home / Guide or Select + Start (hold 1s) opens the game menu",15,"#9898a1")
            else:
                self._controller_hint("A","Select",left,693,"#35a4c6")
                self._controller_hint("B","Back",left+110,693,"#d75b78")
            self.display(1200,694,"Fullscreen: Pause → Display" if self.screen=="gameplay" else "Fullscreen: System → Display",15,"#777780",anchor="ne")
        status=self.status.get()
        if self.busy:
            self.panel(860,103,380,43,"#222225",radius=2)
            self.display(872,109,self.fit_text(status,356,16),16,"#d0d0d5")
            self.rect(872,137,356,3,"#414146")
            position=(time.monotonic()*180)%285
            self.rect(872+position,137,71,3,ACCENT)
            if getattr(self,"task_cancellable",True):self.button_canvas("task:cancel","Cancel",1128,155,110,32,self.cancelled.set)

    def _toast(self):
        status=self.status.get()
        if not self.busy and status and status!="Ready" and not status.startswith("Local emulator scan") and time.monotonic()-getattr(self,"status_changed_at",0)<4:
            self.panel(860,103,380,32,"#222225",radius=2)
            self.display(872,109,self.fit_text(status,356,16),16,"#c6c6cb")


    def _gameplay(self):
        if not self.embedded:return
        from PIL import Image,ImageTk
        from .video_filters import render_frame,fit_size
        frame=self.embedded.host.latest_frame()
        self.rect(0,0,1280,684,"#000000")
        if frame:
            kind=self.overlay.get("kind") if self.overlay else None
            preview=kind in {"display_panel","controls_panel","state_slots"}
            bounds_width,bounds_height=round((1120 if preview else 1210)*self.scale),round((145 if kind=="state_slots" else 235 if preview else 648)*self.scale)
            profile=getattr(self,"game_display",None)
            shape=profile.shape if profile else "auto"
            aspect=frame.width/frame.height if shape=="pixel" else 4/3 if shape=="4:3" else bounds_width/bounds_height if shape=="stretch" else getattr(self.embedded.host,"display_aspect",4/3)
            width,height=fit_size(round(aspect*1000),1000,bounds_width,bounds_height)
            zoom=profile.zoom/100 if profile else 1
            width,height=max(1,round(width*zoom)),max(1,round(height*zoom))
            preset=profile.filter_id if profile else getattr(self,"game_filter","sharp")
            key=(frame.sequence,width,height,bounds_width,bounds_height,profile,preset)
            if self.game_photo is None or getattr(self,"game_photo_key",None)!=key:
                image=render_frame(frame.rgb,frame.width,frame.height,width,height,preset,
                                   scanline_strength=profile.scanlines if profile else 40,brightness=profile.brightness if profile else 100)
                surface=Image.new("RGB",(bounds_width,bounds_height),"black")
                ox=round(profile.offset_x*self.scale) if profile else 0
                oy=round(profile.offset_y*self.scale) if profile else 0
                surface.paste(image,((bounds_width-width)//2+ox,(bounds_height-height)//2+oy))
                self.game_photo=ImageTk.PhotoImage(surface,master=self.canvas)
                self.game_photo_key=key
            self.photos.append(self.game_photo)
            self.canvas.create_image(self.offset_x+640*self.scale,self.offset_y+(100 if kind=="state_slots" else 145 if preview else 342)*self.scale,image=self.game_photo,anchor="center")
        self.button_canvas("gameplay:menu","Pause / Menu",1050,642,180,32,self.game_menu)

    def _pager(self,key,total,size,y=641):
        if total<=size:return
        page=self.pages.get(key,0);maximum=(total-1)//size
        y=min(y,638)
        if page>0:self.button_canvas(f"{key}:prev","← Previous",870,y,165,32,lambda:self._change_page(key,-1))
        if page<maximum:self.button_canvas(f"{key}:next","Next →",1052,y,165,32,lambda:self._change_page(key,1))
        self.display(370,y+9,f"Page {page+1} of {maximum+1}",15,"#93939b")
