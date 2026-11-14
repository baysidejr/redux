"""Secondary console screens, in 1280x720 space beside the persistent sidebar."""
from __future__ import annotations
from datetime import datetime
import shutil
from pathlib import Path
from .library import SYSTEMS

WHITE="#f4f4f2"
MUTED="#979ba6"
FAINT="#626875"
ACCENT="#ec407a"
LINE="#2b2d34"
PANEL="#14161c"


def _size(value):
    value=max(0,int(value or 0))
    for suffix in ("B","KB","MB","GB","TB"):
        if value<1024 or suffix=="TB":return f"{value:.1f} {suffix}" if suffix!="B" else f"{value} B"
        value/=1024


def _game_title(game):
    return game.get("title","").split(" (USA)")[0].split(" (Europe)")[0].split(" (Japan)")[0]


def _date(value):
    try:return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone().strftime("%b %d, %Y  ·  %I:%M %p").replace(" 0"," ")
    except (AttributeError,ValueError):return str(value or "Unknown date")


class SecondaryScreens:
    def _secondary_heading(self,eyebrow,title,caption=None):
        self.text(370,42,eyebrow.upper(),11,ACCENT,"bold")
        self.display(368,64,title,34,WHITE)
        if caption:self.text(370,112,caption,14,MUTED,width=870)

    def _secondary_pager(self,key,total,size,y=642):
        if total<=size:return
        page=self.pages.get(key,0);maximum=(total-1)//size
        self.text(370,y+10,f"{page+1} / {maximum+1}",12,MUTED)
        if page>0:self.button_canvas(f"{key}:prev","Previous",908,y,154,32,lambda:self._change_page(key,-1))
        if page<maximum:self.button_canvas(f"{key}:next","Next",1078,y,154,32,lambda:self._change_page(key,1))

    def _setting_row(self,key,title,detail,y,callback,*,icon="settings",value=None,toggle=None):
        x,w=getattr(self,"settings_frame",(618,630));right=x+w
        selected=self.focus==key
        self.panel(x,y,w,72,"#242128" if selected else "#17181c",3,WHITE if selected else None)
        self.icon(icon,x+17,y+24,23,ACCENT if selected else "#b6b9c4")
        self.text(x+58,y+14,title,15,WHITE,"bold")
        self.text(x+58,y+41,self.fit_text(str(detail),w-(260 if value else 106),13),13,MUTED)
        if toggle is not None:
            self.panel(right-78,y+24,48,26,ACCENT if toggle else "#424651",13)
            self.panel(right-53 if toggle else right-74,y+28,18,18,WHITE,9)
        elif value:
            self.text(right-43,y+27,self.fit_text(str(value),145,12),12,MUTED,anchor="ne")
            self.icon("chevron",right-24,y+28,14,WHITE if selected else FAINT)
        else:self.icon("chevron",right-28,y+28,16,WHITE if selected else FAINT)
        self.target(key,(x,y,right,y+72),callback,title)

    # The System menu: one horizontal row of icons over the dimmed screen, like a console's
    # system bar. The focused item names itself above the row; A opens it, B leaves.
    SYSTEM_ITEMS=(("home","Home","home","Return to Now Playing"),
                  ("storage","Storage","storage","Where Redux and your games live."),
                  ("input","Controllers","controller","Made to feel at home in your hands."),
                  ("saves","Saves","storage","Keep your progress close."),
                  ("windows","Windows","systems","Ready when you sit down to play."),
                  ("about","About","info","Version, folders and the screen."),
                  ("exit","Exit","power","Close Redux."))

    def _system_menu(self):
        self.panel(0,0,1280,720,"#000000b4",0)
        # Only the row is reachable while the menu is open; the screen behind it is dimmed.
        self.targets=[];self.callbacks={}
        items=self.SYSTEM_ITEMS
        if self.focus not in {f"system:{item[0]}" for item in items}:self.focus="system:home"
        focused=next(item for item in items if f"system:{item[0]}"==self.focus)
        self.display(140,318,"REDUX" if focused[0]=="home" else focused[1].upper(),46,WHITE,"bold")
        self.display(140,384,focused[3],18,"#c9c9ce")
        left,spacing=274,122
        for index,(section,label,icon,_) in enumerate(items):
            cx=left+index*spacing;key=f"system:{section}";active=key==self.focus
            color=WHITE if active else "#8a8a90"
            self.icon(icon,cx-14,448,28,color)
            self.display(cx,499,label,15,color,anchor="n")
            if active:self.rect(cx-50,520,100,3,ACCENT)
            if section=="home":callback=lambda:self.navigate("home",remember=False)
            elif section=="exit":callback=self.exit_app_menu
            else:callback=lambda s=section:self._set_settings(s)
            self.target(key,(cx-61,430,cx+61,536),callback,label)

    def _settings(self):
        if getattr(self,"settings_menu",True):self._system_menu();return
        section=self.settings_section
        item=next((entry for entry in self.SYSTEM_ITEMS if entry[0]==section and entry[0] not in {"home","exit"}),None)
        if item is None:
            self.settings_menu=True;self._system_menu();return
        # A section page keeps the menu's look: dimmed screen, one title, full-width rows, B back.
        self.panel(0,0,1280,720,"#000000c4",0)
        self.targets=[];self.callbacks={}
        self.settings_frame=(140,1000)
        X,W=self.settings_frame;R=X+W
        self.display(X,54,item[1].upper(),40,WHITE,"bold")
        self.display(X,108,item[3],17,"#c9c9ce")
        self.icon(item[2],R-40,62,32,"#8a8a90")
        if section=="storage":
            from .console_windows import application_directory
            # Internal storage: where Redux is installed and its library lives.
            self.text(X,156,"INTERNAL STORAGE",11,ACCENT,"bold")
            selected=self.focus=="storage:internal"
            self.panel(X,178,W,92,"#242128" if selected else "#17181c",3,WHITE if selected else None)
            self.icon("storage",X+17,200,26,ACCENT if selected else "#b6b9c4")
            self.text(X+58,190,"Internal storage",15,WHITE,"bold")
            self.text(X+58,214,self.fit_text("Redux at "+str(application_directory()),W-420,12),12,MUTED)
            self.text(X+58,236,self.fit_text("Library at "+str(self.library.root),W-420,12),12,MUTED)
            try:
                usage=shutil.disk_usage(self.library.root);ratio=min(1,max(0,usage.used/max(1,usage.total)))
                self.text(R-18,194,f"{_size(usage.free)} free of {_size(usage.total)}",12,WHITE,anchor="ne")
                self.panel(R-318,222,300,4,LINE,2)
                if ratio>0:self.panel(R-318,222,max(4,300*ratio),4,ACCENT,2)
                self.text(R-18,238,f"{len(self.all_games)} games • discs install {'to '+str(self.install_destination().anchor.rstrip(chr(92)+'/')) if self.install_destination() else 'here'}",12,MUTED,anchor="ne")
            except OSError:self.text(R-18,194,"Storage unavailable",12,MUTED,anchor="ne")
            self.target("storage:internal",(X,178,R,270),self.refresh,"Internal storage")
            # External storage: every data drive, plus the optical drive.
            self.text(X,296,"EXTERNAL STORAGE",11,ACCENT,"bold")
            drives=self.storage_drives()
            if not drives:
                self.panel(X,318,W,92,PANEL,3);self.icon("library",X+21,342,30,ACCENT)
                self.text(X+70,336,"Nothing connected",15,WHITE,"bold")
                self.text(X+70,362,"Connect an SD card, micro SD card or SSD caddy. Redux sets it up as extra storage and plays its games in place.",13,MUTED,width=W-100)
            for index,drive in enumerate(drives[:4]):
                y=318+index*82;letter=Path(drive["root"]).anchor.rstrip(chr(92)+"/");name=(drive["label"] or ("Optical drive" if drive["optical"] else "External drive"))
                if drive["optical"]:
                    self._setting_row(f"storage:{letter}",f"{name} ({letter})","Disc tools: inspect, capture or import a mounted image",y,self.disc_dialog,icon="disc");continue
                usage=drive.get("usage");value=f"{_size(usage[0])} free" if usage else None
                if drive["ignored"]:detail="Not used by Redux • A to use it as extra storage"
                elif drive["card"]:detail=f"Extra storage • {drive['games']} game{'' if drive['games']==1 else 's'}"+(" • discs install here" if drive["destination"] else "")
                else:detail="A sets it up as extra storage"
                self._setting_row(f"storage:{letter}",f"{name} ({letter}) · {drive['kind']}",detail,y,lambda root=drive["root"]:self.storage_menu(root),icon="library",value=value)
        elif section=="input":
            connected=getattr(self.controller,"connected_indices",()) if self.controller else ()
            self.panel(X,218,W,76,PANEL,3)
            self.icon("controller",X+18,240,33,ACCENT)
            self.text(X+73,233,"Game controllers",16,WHITE,"bold")
            self.text(X+73,261,f"{len(connected)} connected" if connected else "No controller connected",13,MUTED)
            bindings=[("D-pad / left stick","Navigate"),("A / B","Select / go back"),("X / Y","Favorite / manage game"),("Home / Guide","Open or close game menu"),("Select + Start (hold 1s)","Same game menu")]
            for index,(control,action) in enumerate(bindings):
                y=324+index*43
                self.text(X,y,control,14,WHITE,"bold");self.text(X+330,y,action,14,MUTED)
                if index<len(bindings)-1:self.rect(X,y+30,W,1,LINE)
            self.button_canvas("settings:controller-bind","Configure a controller",X,552,320,43,self.configure_controller)
            self.button_canvas("settings:controller-test","Test controllers",X+338,552,320,43,self.open_controller_test)
            self.button_canvas("settings:phone-controller","Phone controller",X+676,552,324,43,self.redux_controller_menu)
            phone_first,phone_second=self.redux_controller_status_lines()
            self.text(X,610,phone_first,13,WHITE,"bold")
            self.text(X,634,self.fit_text(phone_second,W,12),12,MUTED)
            self.text(X,658,"Known pads are mapped automatically. USB and Bluetooth use Windows drivers.",11,MUTED,width=W)
        elif section=="saves":
            game=self.selected();detail=_game_title(game) if game else "Choose a game from your library first"
            self._setting_row("settings:savegame","Game saves & backups",detail,218,lambda:self.navigate("saves"),icon="storage")
            self.panel(X,322,W,192,PANEL,3)
            self.icon("check",X+21,346,25,ACCENT)
            self.text(X+66,347,"Two ways to keep your progress",16,WHITE,"bold")
            self.text(X+66,391,"Save states return to a moment in a supported running game.",14,MUTED,width=W-120)
            self.text(X+66,446,"File backups preserve a chosen save directory for restoration later.",14,MUTED,width=W-120)
        elif section=="windows":self._windows_settings()
        else:
            from . import __version__
            self.panel(X,218,W,110,PANEL,3)
            self.display(X+22,234,"REDUX",33,WHITE);self.text(X+24,280,f"VERSION {__version__}",12,ACCENT,"bold")
            self.text(R-23,252,"WINDOWS",11,MUTED,"bold",anchor="ne")
            facts=[("Collection",f"{len(self.all_games)} games"),("Library folder",str(self.library.root)),
                   ("Firmware (BIOS) folder",str(self.firmware_directory())),("Media","Discs, game cards and optical drives"),
                   ("Connection","Local library · no account required")]
            for index,(label,value) in enumerate(facts):
                y=352+index*44;self.text(X,y,label,14,MUTED);self.text(R,y,self.fit_text(value,640,14),14,WHITE,anchor="ne")
                if index<len(facts)-1:self.rect(X,y+31,W,1,LINE)
            fullscreen=bool(self.root.attributes("-fullscreen"))
            self._setting_row("settings:fullscreen","Fullscreen","ON · A switches to windowed mode" if fullscreen else "OFF · A fills the display",584,self.fullscreen,icon="systems",toggle=fullscreen)

    def _collections(self):
        self._secondary_heading("Made for your collection","Playlists","Keep great games together.")
        self.button_canvas("collection:new","+  New playlist",1040,67,208,40,self.create_collection_dialog,True)
        # The Welcome tile leads Playlists as it leads Now Playing: box art, then the banner.
        focused=self.focus=="collection:welcome"
        self.panel(370,140,878,128,"#2c232a" if focused else PANEL,3,WHITE if focused else None)
        self.rect(372,142,128,124,"#111113")
        self.picture(self.art.welcome("box"),372,142,128,124,mode="contain")
        if not self.picture(self.art.welcome("hero"),502,142,744,124,shade=True,radius=3):self.rect(502,142,744,124,"#17121f")
        self.display(524,170,"WELCOME",14,"#c7c1d1","bold")
        self.display(524,194,"It's dangerous to go alone. Bring your games.",22,WHITE)
        self.target("collection:welcome",(370,140,1248,268),self._open_collection_view_all,"Welcome")
        # A playlist is an ordered list of installed games, nothing more.
        collections=self.library.collections();self.text(370,288,"YOUR PLAYLISTS",11,MUTED,"bold")
        self.text(1248,288,str(len(collections)),12,MUTED,anchor="ne")
        if not collections:
            self.panel(370,315,878,140,PANEL,3);self.icon("collections",396,348,31,ACCENT)
            self.display(451,342,"Play your games in your order",24,WHITE)
            self.text(451,383,"Create a playlist, then add games from each game's Manage menu. Move them up or down from Edit playlist.",14,MUTED,width=730)
        start,end=self._page_bounds("collections",len(collections),14)
        for offset,item in enumerate(collections[start:end]):
            x,y=370+(offset%2)*450,315+(offset//2)*47;key=f"custom:{item['id']}";focused=self.focus==key
            self.panel(x,y,428,40,"#2c232a" if focused else PANEL,2,WHITE if focused else None)
            self.icon("collections",x+13,y+12,16,ACCENT if focused else MUTED)
            self.text(x+42,y+12,self.fit_text(item["name"],322,13),13,WHITE)
            self.text(x+389,y+12,str(item["game_count"]),12,MUTED,anchor="ne")
            self.icon("chevron",x+400,y+14,12,FAINT)
            self.target(key,(x,y,x+428,y+40),lambda cid=item["id"]:self._open_custom_collection(cid),item["name"])
        self._secondary_pager("collections",len(collections),14)

    def _systems(self):
        self._secondary_heading("Every era. One collection.","Systems","Explore the hardware that made the games.")
        systems=[system for system in SYSTEMS if any(game["system"]==system for game in self.all_games)]
        self.text(1248,78,f"{len(systems)} SYSTEM"+("" if len(systems)==1 else "S"),12,MUTED,anchor="ne")
        if not systems:
            self.panel(370,182,878,357,PANEL,3);self.icon("systems",784,236,51,ACCENT)
            self.display(809,332,"Your first system is waiting",29,WHITE,anchor="center")
            self.text(809,378,"Insert a disc or connect a game card to begin.",14,MUTED,anchor="center")
            self.button_canvas("systems:storage","Storage",709,428,200,42,self._open_media,True);return
        start,end=self._page_bounds("systems",len(systems),6)
        for index,system in enumerate(systems[start:end]):
            x,y,key=370+(index%3)*300,165+(index//3)*230,f"system:{system}";focused=self.focus==key
            self.panel(x-2,y-2,282,205,"#2c232a" if focused else PANEL,3,WHITE if focused else None)
            if not self.picture(self.art.path(system,"tile"),x+5,y+5,268,142):self.icon("systems",x+116,y+43,43,ACCENT)
            self.text(x+12,y+161,self.fit_text(SYSTEMS[system],240,14),14,WHITE,"bold")
            count=sum(game["system"]==system for game in self.all_games);self.text(x+12,y+183,f"{count} game"+("" if count==1 else "s"),12,MUTED)
            self.icon("chevron",x+250,y+165,14,ACCENT if focused else FAINT)
            self.target(key,(x-2,y-2,x+280,y+203),lambda s=system:self._open_system(s),SYSTEMS[system])
        self._secondary_pager("systems",len(systems),6)

    def _secondary_keyboard(self,x=320,y=418,width=640,submit_label="Results"):
        # The original keyboard is a translucent lower-screen layer with bare
        # letters and a circular white focus marker, not a matrix of buttons.
        self.panel(0,390,1280,292,"#111216f2",0)
        self.rect(0,390,1280,1,"#ffffff")
        spacing=width/10
        for row,letters in enumerate(("QWERTYUIOP","ASDFGHJKL","ZXCVBNM")):
            offset=(width-len(letters)*spacing)/2
            for index,key in enumerate(letters):
                left=x+offset+index*spacing+(spacing-40)/2;top=y+row*54
                focused=self.focus==f"key:{key}"
                if focused:self.panel(left,top,40,40,WHITE,20)
                self.text(left+20,top+20,key,22,"#111216" if focused else WHITE,anchor="center")
                self.target(f"key:{key}",(left-8,top,left+48,top+40),lambda k=key:self._type_key(k),key)
        gap=20;control_width=(width-gap*3)/4
        for index,(key,label) in enumerate((("SPACE","Space"),("BACKSPACE","Delete"),("CLEAR","Clear"),("RESULTS",submit_label))):
            left=x+index*(control_width+gap);top=604;focused=self.focus==f"key:{key}"
            if focused:self.panel(left,top,control_width,37,"#ffffff14",2,WHITE)
            self.text(left+control_width/2,top+18,label,14,WHITE if focused else MUTED,anchor="center")
            action=self._secondary_keyboard_submit if key=="RESULTS" else lambda k=key:self._type_key(k)
            self.target(f"key:{key}",(left,top,left+control_width,top+37),action,label)
        self.text(80,641,"Type directly or use your controller.",12,MUTED,width=205)

    def _secondary_keyboard_submit(self):
        if self.screen=="search":self.keyboard_visible=False
        self._type_key("RESULTS")

    def _secondary_show_keyboard(self):
        self.keyboard_visible=True;self.pages["search"]=0;self.focus="key:Q";self.schedule_draw()

    def _search(self):
        keyboard=getattr(self,"keyboard_visible",True)
        scope=SYSTEMS.get(self.system_filter,"All Systems") if self.system_filter else "All Systems"
        self.display(80,46,scope+" / Search",38,WHITE)
        self.text(80,105,f"Search your collection  ·  {len(self.rows)} game"+("" if len(self.rows)==1 else "s"),15,MUTED)
        self.panel(80,143,1120,53,"#ffffff19",0)
        self.icon("search",99,160,21,WHITE)
        query=self.search_text or "Search titles, publishers and genres"
        self.text(140,160,self.fit_text(query+(" |" if self.search_text else ""),1032,20),20,WHITE if self.search_text else MUTED)
        if not keyboard:self.button_canvas("search:keyboard","On-screen keyboard",998,47,202,37,self._secondary_show_keyboard)
        if not self.rows:
            self.display(640,275,"No games found",27,WHITE,anchor="center")
            self.text(640,325,"Try another title or a shorter search.",15,MUTED,anchor="center")
        page_size=5 if keyboard else 10
        focused_index=next((i for i,game in enumerate(self.rows) if self.focus==f"game:{game['id']}"),None)
        if focused_index is not None:self.pages["search"]=focused_index//page_size
        start,end=self._page_bounds("search",len(self.rows),page_size)
        for index,game in enumerate(self.rows[start:end]):
            x=80+(index%5)*226.5;y=240+(index//5)*163
            self._card(game,x,y,213,149)
        if keyboard:self._secondary_keyboard()
        else:
            page=self.pages.get("search",0);maximum=max(0,(len(self.rows)-1)//page_size)
            self.text(80,656,f"{len(self.rows)} results",12,MUTED)
            if page>0:self.button_canvas("search:prev","Previous",850,648,154,28,lambda:self._change_page("search",-1))
            if page<maximum:self.button_canvas("search:next","Next",1020,648,180,28,lambda:self._change_page("search",1))

    def _saves(self):
        self._secondary_heading("Pick up where you left off","Saves & backups")
        game=self.selected()
        if not game:
            self.panel(370,181,878,357,PANEL,3);self.icon("storage",785,238,50,ACCENT)
            self.display(809,337,"Choose a game first",29,WHITE,anchor="center")
            self.text(809,383,"Open a game to manage its saved progress.",14,MUTED,anchor="center")
            self.button_canvas("saves:library","Browse library",697,433,224,42,lambda:self.navigate("library"),True);return
        self.text(370,124,self.fit_text(_game_title(game),590,17),17,WHITE,"bold")
        self.text(370,151,SYSTEMS.get(game["system"],game["system"]),13,MUTED)
        self.button_canvas("saves:create","+  Back up save directory",994,122,254,42,self.backup_saves,True)
        from .saves import SaveStore
        backups=SaveStore(self.library.root).list_backups(game["id"])
        self.text(370,199,"BACKUP HISTORY",11,MUTED,"bold");self.text(1248,199,f"{len(backups)} SNAPSHOT"+("" if len(backups)==1 else "S"),11,MUTED,anchor="ne")
        if not backups:
            self.panel(370,233,878,312,PANEL,3);self.icon("storage",785,278,50,ACCENT)
            self.display(809,372,"Your progress deserves a backup",28,WHITE,anchor="center")
            self.text(809,420,"Choose your emulator's save directory to create a snapshot.",14,MUTED,anchor="center")
        start,end=self._page_bounds("saves",len(backups),6)
        for offset,backup in enumerate(backups[start:end]):
            y,key=231+offset*55,f"backup:{start+offset}";focused=self.focus==key
            self.panel(370,y,878,47,"#2c232a" if focused else PANEL,2,WHITE if focused else None)
            self.icon("storage",387,y+14,19,ACCENT if focused else MUTED);self.text(427,y+14,_date(backup.created_at),14,WHITE)
            self.text(977,y+15,f"{backup.file_count} files",13,MUTED,anchor="ne");self.text(1203,y+15,_size(backup.total_bytes),13,MUTED,anchor="ne")
            self.icon("chevron",1226,y+17,14,ACCENT if focused else FAINT)
            self.target(key,(370,y,1248,y+47),lambda bid=backup.id:self.restore_backup(bid),_date(backup.created_at))
        self._secondary_pager("saves",len(backups),6)

    def _text_editor(self):
        prompt=self.text_prompt
        self.display(80,46,prompt["title"],38,WHITE)
        self.text(80,105,prompt["label"],15,MUTED)
        self.panel(80,143,1120,53,"#ffffff19",0)
        value=self._scraper_text_value(prompt) if prompt.get("credential") else prompt["value"]
        self.text(101,159,self.fit_text(value+" |",1077,21),21,WHITE)
        self.button_canvas("text:save","Save",980,47,100,37,self._accept_text,True)
        self.button_canvas("text:cancel","Cancel",1096,47,104,37,self._cancel_text)
        self.text(80,239,prompt.get("hint") or "Type a name, then save your changes.",16,MUTED,width=1120)
        if prompt.get("credential"):self._credential_keyboard()
        else:self._secondary_keyboard(submit_label="Save")
