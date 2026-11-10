"""Secondary console screens, in 1280x720 space beside the persistent sidebar."""
from __future__ import annotations
from datetime import datetime
import shutil
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
        selected=self.focus==key
        self.panel(618,y,630,72,"#242128" if selected else "#17181c",3,WHITE if selected else None)
        self.icon(icon,635,y+24,23,ACCENT if selected else "#b6b9c4")
        self.text(676,y+14,title,15,WHITE,"bold")
        self.text(676,y+41,self.fit_text(str(detail),370 if value else 524,13),13,MUTED)
        if toggle is not None:
            self.panel(1170,y+24,48,26,ACCENT if toggle else "#424651",13)
            self.panel(1195 if toggle else 1174,y+28,18,18,WHITE,9)
        elif value:
            self.text(1205,y+27,self.fit_text(str(value),145,12),12,MUTED,anchor="ne")
            self.icon("chevron",1224,y+28,14,WHITE if selected else FAINT)
        else:self.icon("chevron",1220,y+28,16,WHITE if selected else FAINT)
        self.target(key,(618,y,1248,y+72),callback,title)

    def _settings(self):
        self._secondary_heading("System","Settings")
        categories=[("general","Library & storage","storage"),("disc","Discs & imports","disc"),
                    ("emulators","Emulators & firmware","play"),("input","Controllers","controller"),
                    ("saves","Saves & backups","storage"),("display","Display & artwork","settings"),
                    ("artwork","Artwork & scraping","collections"),
                    ("windows","Windows & Xbox mode","systems"),
                    ("about","System information","home")]
        for index,(section,label,icon) in enumerate(categories):
            x,y,key=370,147+index*52,f"setting:{section}"
            focused,active=self.focus==key,self.settings_section==section
            if focused or active:self.panel(x,y,211,46,"#2b232a" if active else "#1d2028",2,WHITE if focused else None)
            if active:self.rect(x,y+9,3,28,ACCENT)
            self.icon(icon,x+14,y+15,18,WHITE if active else MUTED)
            self.text(x+45,y+15,label,13,WHITE if active or focused else MUTED,"bold" if active else "normal")
            self.target(key,(x,y,x+211,y+46),lambda s=section:self._set_settings(s),label)
        self.button_canvas("settings:exit","Exit app",370,630,211,40,self.exit_app_menu)
        self.rect(600,148,1,528,LINE)
        section=self.settings_section
        title=next(label for name,label,_ in categories if name==section)
        self.display(618,147,title,24,WHITE)
        captions={"general":"Your games, wherever you keep them.","disc":"Bring discs and images into one library.",
                  "emulators":"Choose how each system plays.","input":"Made to feel at home in your hands.",
                  "saves":"Keep your progress close.","display":"Make room for the games.",
                  "artwork":"Give every game its own cover, tile and background.",
                  "windows":"Ready when you sit down to play.","about":"An independent home for your collection."}
        self.text(618,181,captions[section],13,MUTED)
        if section=="general":
            self._setting_row("settings:folder","Library folder",str(self.library.root),218,self.choose_library,icon="storage")
            linked=self.linked.get()
            self._setting_row("settings:link","Import mode","Keep original files linked" if linked else "Copy complete media into this library",300,self.toggle_import_mode,icon="library",value="Link files" if linked else "Managed copy")
            self._setting_row("settings:refresh","Check media availability","Refresh connected storage and linked files",382,self.refresh,icon="check")
            try:
                usage=shutil.disk_usage(self.library.root);ratio=min(1,max(0,usage.used/max(1,usage.total)))
                self.text(618,501,"STORAGE",11,MUTED,"bold")
                self.text(1248,501,f"{_size(usage.free)} free / {_size(usage.total)}",12,WHITE,anchor="ne")
                self.panel(618,526,630,4,LINE,2)
                if ratio>0:self.panel(618,526,max(4,630*ratio),4,ACCENT,2)
                self.text(618,547,f"{len(self.all_games)} games in this library",13,MUTED)
            except OSError:self.text(618,515,"Connect the library storage to see free space.",13,MUTED)
        elif section=="disc":
            self._setting_row("settings:files","Add media files","Choose a game, disc image or complete image bundle",218,self.add_files,icon="plus")
            self._setting_row("settings:scan","Scan a folder","Find media on your SSD, SD card or local drive",300,self.scan_folder,icon="search")
            self._setting_row("settings:drives","Optical & mounted drives","Inspect a disc, import its source or choose a capture method",382,self.disc_dialog,icon="disc")
            self.text(618,501,"Original image bundles keep their referenced tracks together.",13,MUTED,width=600)
        elif section=="emulators":
            self._setting_row("settings:emulator","System playback","Assign an integrated core or external emulator",218,self.launcher_dialog,icon="play")
            backends=getattr(self,"detected_backends",())
            self._setting_row("settings:detect","Detect local backends","Look in local emulator folders",300,self._configure_detected_backends,icon="search",value=f"{len(backends)} detected")
            self._setting_row("settings:firmware","Firmware directory",str(self.firmware_directory()),382,self.choose_firmware,icon="storage")
            self._setting_row('settings:core-setup','Emulator setup','Download, repair or update integrated cores',464,self.core_setup_menu,icon='play')
        elif section=="input":
            connected=getattr(self.controller,"connected_indices",()) if self.controller else ()
            self.panel(618,218,630,76,PANEL,3)
            self.icon("controller",636,240,33,ACCENT)
            self.text(691,233,"Game controllers",16,WHITE,"bold")
            self.text(691,261,f"{len(connected)} connected" if connected else "No controller connected",13,MUTED)
            bindings=[("D-pad / left stick","Navigate"),("A / B","Select / go back"),("X / Y","Favorite / manage game"),("Home / Guide","Open or close game menu"),("Select + Start (hold 1s)","Same game menu")]
            for index,(control,action) in enumerate(bindings):
                y=324+index*43
                self.text(618,y,control,14,WHITE,"bold");self.text(845,y,action,14,MUTED)
                if index<len(bindings)-1:self.rect(618,y+30,630,1,LINE)
            self.button_canvas("settings:controller-bind","Configure a controller",618,552,294,43,self.configure_controller)
            self.text(618,614,"Known pads are mapped automatically. USB and Bluetooth use Windows drivers.",12,MUTED,width=620)
        elif section=="saves":
            game=self.selected();detail=_game_title(game) if game else "Choose a game from your library first"
            self._setting_row("settings:savegame","Game saves & backups",detail,218,lambda:self.navigate("saves"),icon="storage")
            self.panel(618,322,630,192,PANEL,3)
            self.icon("check",639,346,25,ACCENT)
            self.text(684,347,"Two ways to keep your progress",16,WHITE,"bold")
            self.text(684,391,"Save states return to a moment in a supported running game.",14,MUTED,width=525)
            self.text(684,446,"File backups preserve a chosen save directory for restoration later.",14,MUTED,width=525)
        elif section=="display":
            fullscreen=bool(self.root.attributes("-fullscreen"))
            self._setting_row("settings:fullscreen","Fullscreen","ON · A switches to windowed mode" if fullscreen else "OFF · A fills the display",218,self.fullscreen,icon="systems",toggle=fullscreen)
            self._setting_row("settings:artroot","Local artwork folder",self.preferences.get("reference_assets") or "Use available local system artwork",300,self.choose_reference_art,icon="collections")
            self.text(618,428,"Change game-specific cover art from each game's details.",14,MUTED,width=600)
        elif section=="artwork":self._artwork_settings()
        elif section=="windows":self._windows_settings()
        else:
            from . import __version__
            self.panel(618,218,630,110,PANEL,3)
            self.display(640,234,"REDUX",33,WHITE);self.text(642,280,f"VERSION {__version__}",12,ACCENT,"bold")
            self.text(1225,252,"WINDOWS",11,MUTED,"bold",anchor="ne")
            facts=[("Collection",f"{len(self.all_games)} games"),("Library format","Version 2"),("Media","PC storage & optical drives"),("Connection","Local library · no account required")]
            for index,(label,value) in enumerate(facts):
                y=360+index*48;self.text(618,y,label,14,MUTED);self.text(1248,y,value,14,WHITE,anchor="ne")
                if index<len(facts)-1:self.rect(618,y+31,630,1,LINE)

    def _collections(self):
        self._secondary_heading("Made for your collection","Playlists","Keep great games together.")
        self.button_canvas("collection:new","+  New playlist",1040,67,208,40,self.create_collection_dialog,True)
        presets=[("Favorites","favorites","star"),("Continue playing","continue","play"),("Wishlist","wishlist","plus"),("Completed","completed","check")]
        for index,(label,view,icon) in enumerate(presets):
            x,y,key=370+(index%2)*450,157+(index//2)*126,f"collection:{view}"
            focused=self.focus==key
            self.panel(x,y,428,108,"#2c232a" if focused else PANEL,3,WHITE if focused else None)
            self.icon(icon,x+20,y+22,28,ACCENT)
            self.display(x+69,y+19,label,23,WHITE)
            count=len(self.library.games(view=view));self.text(x+71,y+64,f"{count} game"+("" if count==1 else "s"),13,MUTED)
            self.icon("chevron",x+393,y+47,15,WHITE if focused else FAINT)
            self.target(key,(x,y,x+428,y+108),lambda v=view:self._open_collection_view(v),label)
        collections=self.library.collections();self.text(370,420,"YOUR PLAYLISTS",11,MUTED,"bold")
        self.text(1248,420,str(len(collections)),12,MUTED,anchor="ne")
        if not collections:
            self.panel(370,453,878,140,PANEL,3);self.icon("collections",396,486,31,ACCENT)
            self.display(451,480,"A place for your favorites",24,WHITE)
            self.text(451,521,"Create a playlist, then add games from their details.",14,MUTED,width=730)
        start,end=self._page_bounds("collections",len(collections),8)
        for offset,item in enumerate(collections[start:end]):
            x,y=370+(offset%2)*450,447+(offset//2)*47;key=f"custom:{item['id']}";focused=self.focus==key
            self.panel(x,y,428,40,"#2c232a" if focused else PANEL,2,WHITE if focused else None)
            self.icon("collections",x+13,y+12,16,ACCENT if focused else MUTED)
            self.text(x+42,y+12,self.fit_text(item["name"],322,13),13,WHITE)
            self.text(x+389,y+12,str(item["game_count"]),12,MUTED,anchor="ne")
            self.icon("chevron",x+400,y+14,12,FAINT)
            self.target(key,(x,y,x+428,y+40),lambda cid=item["id"]:self._open_custom_collection(cid),item["name"])
        self._secondary_pager("collections",len(collections),8)

    def _systems(self):
        self._secondary_heading("Every era. One collection.","Systems","Explore the hardware that made the games.")
        systems=[system for system in SYSTEMS if any(game["system"]==system for game in self.all_games)]
        self.text(1248,78,f"{len(systems)} SYSTEM"+("" if len(systems)==1 else "S"),12,MUTED,anchor="ne")
        if not systems:
            self.panel(370,182,878,357,PANEL,3);self.icon("systems",784,236,51,ACCENT)
            self.display(809,332,"Your first system is waiting",29,WHITE,anchor="center")
            self.text(809,378,"Add media to begin building your collection.",14,MUTED,anchor="center")
            self.button_canvas("systems:add","Add media",709,428,200,42,self.add_files,True);return
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
