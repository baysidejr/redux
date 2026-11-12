"""Controller-first desktop shell over the independent media-library services.

Screen composition and rendering are original. Optional local reference artwork
is read without requiring the vendor's executable or resource pack at runtime.
"""
from __future__ import annotations
from datetime import datetime
import json
import queue
from pathlib import Path
import sqlite3
import time
import tkinter as tk
from tkinter import filedialog,messagebox,ttk
from tkinter import font as tkfont

from .app import App as ToolsApp,config_path,human_size,describe_disc
from .library import Library,SYSTEMS,ImportCancelled,discover_media
from .ui_assets import Artwork,SYSTEM_ART,project_root
from .ui_model import FocusTarget,ScreenHistory,hit_test,next_focus,page_window
from .console_layout import ReferenceLayout
from .console_secondary import SecondaryScreens
from .console_overlays import OverlayScreens
from .console_editors import ConsoleEditors
from .console_media import ConsoleMedia
from .console_controllers import ConsoleControllers
from .console_pc_games import ConsolePCGames
from .console_ingame import ConsoleIngame
from .console_browser import ConsoleFileBrowser
from .console_windows import ConsoleWindows
from .console_scraper import ConsoleScraper
from . import __version__
from .ui_theme import Painter,ACCENT,BACKGROUND,SURFACE,RAISED,WHITE,SUBTLE,MUTED


def duration(seconds):
    seconds=max(0,int(seconds or 0))
    if seconds>=3600:return f"{seconds//3600}h {(seconds%3600)//60}m"
    return f"{seconds//60}m" if seconds>=60 else f"{seconds}s"


def display_title(game):
    title=game.get("title","")
    return title.split(" (USA)")[0].split(" (Europe)")[0].split(" (Japan)")[0] or title


from .console_cores import ConsoleCores
from .console_redux_controller import ConsoleReduxController

class ConsoleApp(ConsoleReduxController,ConsoleCores,ConsolePCGames,ConsoleScraper,ConsoleWindows,ConsoleFileBrowser,ConsoleIngame,ConsoleControllers,ConsoleMedia,ConsoleEditors,OverlayScreens,ReferenceLayout,SecondaryScreens,ToolsApp):
    def __init__(self,root,library_root=None,*,activation=None):
        from .windows_activation import parse_activation
        from .windows_gaming import WindowsGamingExperience
        self.activation=activation or parse_activation([])
        self.home_activation=self.activation.home
        self.windows_experience=WindowsGamingExperience()
        self.windows_events=queue.Queue()
        self.windows_registration=None
        self.preferences={}
        try:self.preferences=json.loads(config_path().read_text(encoding="utf-8"))
        except (OSError,ValueError):pass
        if not self.preferences.get("firmware_dir"):
            try:self.firmware_directory().mkdir(parents=True,exist_ok=True)
            except OSError:pass  # A custom writable location remains selectable.
        self.screen="home"
        self.view="all"
        self.system_filter=None
        self.collection_id=None
        self.sort="title"
        self.facet_filters={}
        self.facet_tab="system"
        self.details_tab="play"
        self.keyboard_visible=True
        self.search_text=""
        self.history=ScreenHistory()
        self.focus="nav:home"
        self.targets=[]
        self.callbacks={}
        self.rows=[]
        self.all_games=[]
        self.selected_id=None
        self.settings_section="general"
        self.overlay=None
        self.overlay_selection=0
        self.photos=[]
        self.redraw_id=None
        self.playback=None
        self.controller=None
        self.sessions={}
        self.bridges={}
        self.bridge_events=queue.Queue()
        self.embedded=None
        self.embedded_record=None
        self.embedded_game=None
        self.embedded_started=0
        self.embedded_paused_at=None
        self.embedded_pause_seconds=0
        self.frame_sequence=-1
        self.game_photo=None
        self.game_photo_key=None
        self.game_filter="sharp"
        self.game_display=None
        self.controller_move_started=None
        self.input_release_gate=False
        self.pending_controller_disconnect=None
        self.held_keys=set()
        self.fonts={}
        from .ui_fonts import register_fonts
        self.display_font=register_fonts()
        self.body_font=self.display_font
        self.status_changed_at=0
        self.toast_timer=None
        self.pages={"systems":0,"collections":0,"screenshots":0,"saves":0,"search":0}
        self.text_prompt=None
        self.close_after_task=False
        self.screenshot_selection=None
        self.session_snapshot={}
        self.last_playback_poll=0
        self.last_clock_minute=None
        self.diagnostic_text=""
        self.catalogue=None
        self.catalogue_loading=False
        self.catalogue_queue=queue.Queue()
        self.keyboard_keys=list("QWERTYUIOPASDFGHJKLZXCVBNM")+["SPACE","BACKSPACE","CLEAR","RESULTS"]
        from .storage_paths import library_directory
        library_root=library_directory(self.preferences,project_root(),library_root)
        super().__init__(root,library_root)
        root.title(f"Redux {__version__} • Console")
        from .ui_fonts import font_directory
        icon_path=font_directory().parent/"redux.ico"
        if icon_path.is_file():root.iconbitmap(str(icon_path))
        root.geometry("1280x720")
        root.minsize(960,540)
        root.configure(bg=BACKGROUND)
        root.bind("<KeyPress>",self._key)
        root.bind("<KeyRelease>",self._key_release)
        root.bind("<FocusOut>",lambda _:self.held_keys.clear())
        root.bind("<F11>",lambda _:self.fullscreen())
        root.bind("<Alt-F4>",lambda _:self.close())
        root.bind("<Control-f>",lambda _:self.search_screen())
        root.bind("<Control-F>",lambda _:self.search_screen())
        root.bind("<Control-v>",self._paste)
        root.bind("<Control-V>",self._paste)
        root.protocol("WM_DELETE_WINDOW",self.close)
        from .controllers import create_controller,GameplayInputMapper
        from .playback import PlaybackManager
        self.controller=create_controller()
        mappings=config_path().parent/"controller-mappings.txt"
        if mappings.is_file() and hasattr(self.controller.api,"load_mappings"):
            try:self.controller.api.load_mappings(mappings)
            except (OSError,ValueError,RuntimeError):pass
        self.gameplay_input=GameplayInputMapper()
        self.playback=PlaybackManager()
        try:self._redux_controller_startup()
        except (OSError,ValueError,RuntimeError):pass
        self.status.trace_add("write",self._status_changed)
        if self.activation.fullscreen or self.preferences.get("fullscreen",True):root.attributes("-fullscreen",True)
        try:self.windows_registration=self.windows_experience.subscribe(lambda state:self.windows_events.put(state))
        except OSError:pass
        self._configure_detected_backends()
        self._load_catalogue()
        self._start_media_watch()
        if self.all_games:self.focus="hero:play"
        root.after(40,self._tick)
        root.after(500,self.first_core_setup)

    def _build(self):
        self.canvas=tk.Canvas(self.root,bg=BACKGROUND,highlightthickness=0,bd=0,takefocus=True)
        self.canvas.pack(fill="both",expand=True)
        self.canvas.bind("<Configure>",lambda _:self.schedule_draw())
        self.canvas.bind("<Motion>",self._motion)
        self.canvas.bind("<Button-1>",self._click)
        self.canvas.bind("<MouseWheel>",self._wheel)
        self.canvas.focus_set()
        self.art=Artwork(self.canvas,self.preferences.get("reference_assets"))
        self.painter=Painter(self.canvas)
        self.status=tk.StringVar(value="Ready")
        # The inherited worker/import service uses these controls only to change
        # state; all visible controls are canvas targets with the same busy guard.
        self.root_label=tk.Label(self.root)
        self.count=tk.Label(self.root)
        self.progress=ttk.Progressbar(self.root)
        self.cancel_button=ttk.Button(self.root,command=self.cancelled.set)
        self.linked=tk.BooleanVar(value=self.preferences.get("link_files",False))
        self.action_buttons=[]

    def _save_preferences(self):
        from .storage_paths import library_setting
        self.preferences.update(library_root=library_setting(self.library.root,project_root()),link_files=self.linked.get(),
                                fullscreen=bool(self.root.attributes("-fullscreen")))
        file=config_path()
        file.parent.mkdir(parents=True,exist_ok=True)
        temporary=file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.preferences,indent=2),encoding="utf-8")
        temporary.replace(file)

    def _status_changed(self,*_):
        self.status_changed_at=time.monotonic()
        if self.toast_timer is not None:self.root.after_cancel(self.toast_timer)
        self.toast_timer=self.root.after(4100,self.schedule_draw)
        self.schedule_draw()

    def refresh(self):
        if not hasattr(self,"library"):return
        self.all_games=self.library.games()
        view=self.view if self.screen not in {"systems","settings","collections","saves"} else "all"
        self.rows=self.library.games(self.search_text if self.screen=="search" else "",self.system_filter,
                                    view=view,collection_id=self.collection_id,sort=self.sort)
        if self.screen=="library":
            for field,value in self.facet_filters.items():
                self.rows=[game for game in self.rows if (self._rating(game) or 0)==value] if field=="rating" else [game for game in self.rows if game.get(field)==value]
        if not self.rows and self.screen=="home" and self.view=="continue":
            self.rows=self.library.games(system=self.system_filter,sort="recent")
        ids={game["id"] for game in self.all_games}
        if self.selected_id not in ids:
            self.selected_id=self.rows[0]["id"] if self.rows else self.all_games[0]["id"] if self.all_games else None
        self.root_label.configure(text=str(self.library.root))
        self.count.configure(text=str(len(self.rows)))
        self.schedule_draw()

    def selected(self):
        if self.selected_id is None:return None
        try:return self.library.game(self.selected_id)
        except KeyError:return None

    def select(self):self.schedule_draw()
    def _details(self,text):
        self.diagnostic_text=text
        self.schedule_draw()

    def schedule_draw(self):
        if self.closing or self.redraw_id is not None:return
        self.redraw_id=self.root.after_idle(self.render)

    def _coordinates(self,event):
        return (event.x-self.offset_x)/self.scale,(event.y-self.offset_y)/self.scale

    def _motion(self,event):
        if getattr(self,"controller_navigation",False):
            point=(event.x_root,event.y_root)
            previous=getattr(self,"controller_pointer",None)
            if previous and abs(point[0]-previous[0])+abs(point[1]-previous[1])<4:return
            self._navigation_pointer(False)
        if not hasattr(self,"scale") or self.overlay:return
        key=hit_test(self.targets,*self._coordinates(event))
        if key and key!=self.focus:
            self.focus=key
            if key.startswith("game:"):
                self.selected_id=int(key.split(":")[1])
            self.schedule_draw()

    def _click(self,event):
        self._navigation_pointer(False)
        self.canvas.focus_set()
        if not hasattr(self,"scale"):return
        key=hit_test(self.targets,*self._coordinates(event))
        if key:
            self.focus=key
            self.activate()

    def _wheel(self,event):
        if self.overlay and self.overlay.get("kind")=="file_browser":
            self.browser_input("page_up" if event.delta>0 else "page_down");return
        if self.screen in {"library","home","search"} and self.rows:
            rows=getattr(self,"home_rows",self.rows) if self.screen=="home" else self.rows
            current=next((i for i,g in enumerate(rows) if f"game:{g['id']}"==self.focus),0)
            current=max(0,min(len(rows)-1,current+(-5 if event.delta>0 else 5)))
            self.selected_id=rows[current]["id"]
            self.focus=f"game:{self.selected_id}"
            self.schedule_draw()

    def _key(self,event):
        if self.root.grab_current() is not None:return
        if self.embedded and not self.overlay:
            if event.keysym in {"Escape","F10"}:self.game_menu()
            else:self.held_keys.add(event.keysym.casefold())
            return "break"
        mapping={"Left":"left","Right":"right","Up":"up","Down":"down",
                 "Return":"select","KP_Enter":"select","Escape":"back","BackSpace":"back",
                 "Tab":"next","Home":"home"}
        if not self.overlay and self.screen=="text" and event.keysym in {"Return","KP_Enter"}:self._accept_text();return "break"
        if not self.overlay and self.screen=="search" and event.keysym in {"Return","KP_Enter"}:self._type_key("RESULTS");return "break"
        if not self.overlay and self.screen in {"search","text"} and event.char and event.char.isprintable() and not event.state&4:
            if self.screen=="text":
                value=self.text_prompt["value"]+event.char
                self.text_prompt["value"]=value[:4096] if self.text_prompt.get("credential") else value
                self.schedule_draw()
            else:self.search_text+=event.char;self.pages["search"]=0;self.refresh()
            return "break"
        if not self.overlay and self.screen in {"search","text"} and event.keysym=="BackSpace":
            if self.screen=="text":self.text_prompt["value"]=self.text_prompt["value"][:-1];self.schedule_draw()
            else:self.search_text=self.search_text[:-1];self.refresh()
            return "break"
        if event.keysym=="F10":self.game_menu();return "break"
        if event.keysym in mapping:
            self.input_action(mapping[event.keysym]);return "break"
        if event.char.casefold()=="f":self.favorite()
        elif event.char.casefold()=="x":self.favorite()

    def _key_release(self,event):
        self.held_keys.discard(event.keysym.casefold())
        if event.keysym=="Right":self.controller_move_started=None

    def _paste(self,event=None):
        if self.root.grab_current() is not None or self.overlay or self.screen not in {"text","search"}:return
        try:value=self.root.clipboard_get()
        except tk.TclError:return "break"
        value="".join(char for char in value if char.isprintable())[:4096]
        if self.screen=="text":
            combined=self.text_prompt["value"]+value
            self.text_prompt["value"]=combined[:4096] if self.text_prompt.get("credential") else combined
            self.schedule_draw()
        else:self.search_text+=value;self.pages["search"]=0;self.refresh()
        return "break"

    def input_action(self,action):
        if self.busy:
            if action in {"back","b"} and getattr(self,"task_cancellable",True):
                self.cancelled.set();self.status.set("Cancelling after the current read…")
            return
        if self.overlay and self.overlay.get("kind")=="file_browser":
            self.browser_input(action);return
        if action=="guide":
            self._request_game_menu();return
        if self.overlay and self.overlay.get("kind")=="display_panel" and action in {"left","right"}:
            self._adjust_display_focused(-1 if action=="left" else 1);return
        if self.overlay and self.overlay.get("kind")=="controls_panel" and action=="right":
            self._hold_move_player();return
        if self.overlay:
            if action in {"menu","start"} and self.overlay.get("title") in {"Game paused","Game menu"}:self.back();return
            if action not in {"left","right","up","down","select","a","confirm","back","b","next"}:return
        if self.screen=="text" and action not in {"left","right","up","down","select","a","confirm","back","b","next"}:return
        if not self.overlay and self.screen=="details" and self._details_navigation(action):return
        if not self.overlay and self.screen=="search" and self.keyboard_visible:
            if action in {"search","y","menu","start"}:self._type_key("RESULTS");return
            if action in {"details","favorite","x"}:self._type_key("BACKSPACE");return
            if action in {"home","shoulder_left","shoulder_right","lb","rb","previous_tab","next_tab"}:return
        if action in {"left","right","up","down"}:
            if self.overlay and action in {"up","down"}:
                current=int(self.focus.split(":")[-1]) if str(self.focus).startswith("overlay:") else 0
                self.focus=f"overlay:{max(0,min(len(self.overlay['items'])-1,current+(-1 if action=='up' else 1)))}"
                self.schedule_draw();return
            if self.focus and self.focus.startswith("game:") and self.screen in {"home","library","search"}:
                rows=getattr(self,"home_rows",self.rows) if self.screen=="home" else self.rows
                index=next((i for i,g in enumerate(rows) if f"game:{g['id']}"==self.focus),0)
                delta={"left":-1,"right":1,"down":5,"up":-5}[action]
                if self.screen=="home" and action in {"up","down"}:delta=0
                if self.screen=="search" and self.keyboard_visible and action in {"up","down"}:delta=0
                if delta and 0<=index+delta<len(rows):
                    self.selected_id=rows[index+delta]["id"];self.focus=f"game:{self.selected_id}"
                    self.schedule_draw();return
            self.focus=next_focus(self.targets,self.focus,action)
            if self.focus and self.focus.startswith("game:"):
                self.selected_id=int(self.focus.split(":")[1])
            self.schedule_draw()
        elif action in {"select","a","confirm"}:self.activate()
        elif action in {"back","b"}:self.back()
        elif action in {"favorite","x","details"}:self.favorite()
        elif action in {"search","y"}:
            if self.screen=="home" and str(self.focus).startswith("hero:") and self.inserted_disc():
                self.inserted_disc_information();return
            if self.screen in {"home","library","details"}:
                if self._game_context():self.open_game(self.selected_id,tab="manage")
            else:self.search_screen()
        elif action in {"menu","start"}:
            if self.embedded or self.playback and self.playback.active():self.game_menu()
            elif self.screen=="home" and str(self.focus).startswith("hero:") and self.inserted_disc():self.inserted_disc_options()
            elif self.screen in {"home","library","details"}:self.play()
            else:self.navigate("settings")
        elif action=="home":self.navigate("home",remember=False)
        elif action=="next":
            keys=[target.key for target in self.targets]
            self.focus=keys[(keys.index(self.focus)+1)%len(keys)] if self.focus in keys else keys[0] if keys else None
            self.schedule_draw()
        elif action in {"shoulder_left","shoulder_right","lb","rb","previous_tab","next_tab"}:
            systems=[None,*[s for s in SYSTEMS if any(g["system"]==s for g in self.all_games)]]
            current=systems.index(self.system_filter) if self.system_filter in systems else 0
            self.system_filter=systems[(current+(-1 if action in {"lb","shoulder_left","previous_tab"} else 1))%len(systems)]
            self.refresh()

    def activate(self):
        if self.busy:
            if self.focus=="task:cancel":self.cancelled.set()
            return
        if not self.overlay and self.screen=="home" and str(self.focus).startswith("hero:") and self.inserted_disc():
            if self.focus=="hero:details":self.inserted_disc_information()
            else:self.inserted_disc_options()
            return
        callback=self.callbacks.get(self.focus)
        if callback:
            try:callback()
            except (OSError,ValueError,RuntimeError,sqlite3.Error,KeyError) as error:
                self.notice("Could not complete",str(error))

    def navigate(self,screen,*,view=None,system=None,collection=None,remember=True,reset_scope=False):
        if self.embedded and screen!="gameplay":
            self.game_menu();return
        if remember:
            self.history.push(dict(screen=self.screen,view=self.view,system=self.system_filter,
                                   collection=self.collection_id,focus=self.focus,search=self.search_text,
                                   facets=dict(self.facet_filters),facet_tab=self.facet_tab,sort=self.sort,
                                   selected_id=self.selected_id,details_tab=self.details_tab,
                                   keyboard_visible=self.keyboard_visible,pages=dict(self.pages)))
        self.screen=screen
        self.overlay=None
        if reset_scope:
            self.view="all";self.system_filter=None;self.collection_id=None;self.search_text="";self.facet_filters.clear()
        if view is not None:self.view=view
        if system is not None:self.system_filter=system
        if collection is not None:self.collection_id=collection
        if screen in {"home","systems","collections","settings"}:
            self.collection_id=None
            self.view="all"
        self.focus=f"nav:{screen}" if screen in {"home","library","collections","systems","settings"} else "details:play"
        self.refresh()
        if screen=="home":self.focus="hero:play" if self.all_games else "add:tile"
        elif screen=="library":
            self.focus=f"game:{self.rows[0]['id']}" if self.rows else "facet:system"
            if self.rows:self.selected_id=self.rows[0]["id"]
        elif screen=="media":self.focus="media:files"
        elif screen=="settings":self.focus=f"setting:{self.settings_section}"
        elif screen=="collections":self.focus="collection:favorites"
        elif screen=="saves":self.focus="saves:create"

    def back(self):
        if self.busy:
            if getattr(self,"task_cancellable",True):self.cancelled.set();self.status.set("Cancelling after the current read…")
            return
        if self.overlay and self.overlay.get("kind")=="file_browser":self.browser_back();return
        if self.overlay:
            if self.overlay.get("back_action"):
                callback=self.overlay["back_action"];self.overlay=None;callback();return
            if self.overlay.get("kind") in {"display_panel","controls_panel","state_slots"}:
                self.game_menu();return
            if self.embedded:
                self._resume_embedded();return
            resume=self.overlay.get("resume_id")
            if resume:self._resume(resume)
            self.overlay=None;self.schedule_draw();return
        if self.screen=="text":self._cancel_text();return
        previous=self.history.pop()
        if previous:
            self.screen=previous["screen"];self.view=previous["view"];self.system_filter=previous["system"]
            self.collection_id=previous["collection"];self.focus=previous["focus"];self.search_text=previous["search"]
            self.facet_filters=dict(previous.get("facets",{}));self.facet_tab=previous.get("facet_tab","system")
            self.sort=previous.get("sort","title");self.selected_id=previous.get("selected_id",self.selected_id)
            self.details_tab=previous.get("details_tab","play");self.keyboard_visible=previous.get("keyboard_visible",True)
            self.pages=dict(previous.get("pages",self.pages))
            self.refresh()
        elif self.screen!="home":self.navigate("home",remember=False)
        else:self.exit_menu()

    def open_game(self,game_id,tab="play"):
        self.selected_id=game_id
        self.details_tab=tab
        self.navigate("details")
        self.focus=f"details:{tab}"
        self.schedule_draw()

    def _details_navigation(self,action):
        """Keep tabs and their content reachable independently of panel geometry."""
        tabs=("play","manage","rate")
        tab=getattr(self,"details_tab","play")
        if tab not in tabs:tab="play"
        tab_keys={f"details:{value}" for value in tabs}
        left={"shoulder_left","lb","previous_tab"}
        right={"shoulder_right","rb","next_tab"}
        if action in left|right or (action in {"left","right"} and self.focus in tab_keys|{"details:launch"}):
            step=-1 if action in left|{"left"} else 1
            self._detail_tab(tabs[(tabs.index(tab)+step)%len(tabs)])
            return True
        if self.focus=="details:back" and action in {"down","right"}:
            self.focus=f"details:{tab}"
        elif self.focus in tab_keys and action=="up":self.focus="details:back"
        elif self.focus in tab_keys and action=="down":
            self.focus={"play":"details:launch","manage":"details:metadata","rate":"rating:1"}[tab]
        elif action=="up" and self.focus in {"details:launch","details:metadata","details:art","details:collection","rating:1","rating:2","rating:3","rating:4","rating:5","rating:clear"}:
            self.focus=f"details:{tab}"
        else:return False
        self.schedule_draw();return True

    def _navigation_pointer(self,controller):
        self.controller_navigation=controller
        canvas=getattr(self,"canvas",None)
        if canvas is not None:
            if controller:self.controller_pointer=canvas.winfo_pointerxy()
            canvas.configure(cursor="none" if controller else "")

    def search_screen(self):
        if self.overlay or self.busy:return
        self.navigate("search",reset_scope=True)
        self.keyboard_visible=True
        self.pages["search"]=0
        self.focus="key:Q"
        self.schedule_draw()

    def favorite(self):
        if self.overlay:return
        game=self._game_context()
        if not game:return
        self.library.toggle_favorite(game["id"])
        self.refresh()

    def _game_context(self):
        if self.screen not in {"home","library","search","details"}:return None
        if self.screen=="home" and str(self.focus).startswith("hero:") and self.inserted_disc():return None
        if self.screen in {"library","search"} and not any(game["id"]==self.selected_id for game in self.rows):return None
        return self.selected()

    def fullscreen(self):
        display_panel=bool(getattr(self,"overlay",None) and self.overlay.get("kind")=="display_panel")
        focus=self.focus
        self.root.attributes("-fullscreen",not bool(self.root.attributes("-fullscreen")))
        self._save_preferences()
        if display_panel:
            self.video_filter_menu();self.focus=focus
        self.schedule_draw()


    def rect(self,x,y,w,h,color,outline=None,width=1):
        return self.canvas.create_rectangle(self.offset_x+x*self.scale,self.offset_y+y*self.scale,
            self.offset_x+(x+w)*self.scale,self.offset_y+(y+h)*self.scale,
            fill=color,outline=outline or color,width=max(1,width*self.scale))


    def target(self,key,bounds,callback,label=""):
        self.targets.append(FocusTarget(key,bounds,label));self.callbacks[key]=callback


    def _nav(self,screen):
        self.navigate(screen,reset_scope=True)


    def _filter(self,view):
        self.view=view
        if self.collection_id is not None and view!="all":self.collection_id=None
        self.focus=f"view:{view}";self.refresh()

    def sort_menu(self):
        self.menu("Sort library",[("Title",lambda:self._sort("title")),("Recently played",lambda:self._sort("recent")),
            ("Recently imported",lambda:self._sort("added")),("Most played",lambda:self._sort("playtime")),("Release year",lambda:self._sort("year"))])

    def _sort(self,sort):self.sort=sort;self.refresh()


    def _open_system(self,system):
        self.navigate("library",system=system,view="all",reset_scope=True)


    def _page_bounds(self,key,total,size):
        page=min(self.pages.get(key,0),max(0,(total-1)//size));self.pages[key]=page
        return page*size,min(total,(page+1)*size)


    def _change_page(self,key,delta):
        self.pages[key]=max(0,self.pages.get(key,0)+delta);self.schedule_draw()

    def _open_collection_view(self,view):self.navigate("library",view=view,reset_scope=True)
    def _open_custom_collection(self,collection):
        self.navigate("library",view="all",collection=collection,reset_scope=True)


    def _set_settings(self,section):self.settings_section=section;self.focus=f"setting:{section}";self.schedule_draw()


    def _type_key(self,key):
        if self.screen=="text":
            value=self.text_prompt["value"]
            if key=="RESULTS":self._accept_text();return
            self.text_prompt["value"]="" if key=="CLEAR" else value[:-1] if key=="BACKSPACE" else value+(" " if key=="SPACE" else key)
            self.schedule_draw();return
        if key=="SPACE":self.search_text+=" "
        elif key=="BACKSPACE":self.search_text=self.search_text[:-1]
        elif key=="CLEAR":self.search_text=""
        elif key=="RESULTS":
            self.keyboard_visible=False
            if self.rows:self.selected_id=self.rows[0]["id"]
            self.focus=f"game:{self.rows[0]['id']}" if self.rows else "key:Q"
        else:self.search_text+=key
        self.pages["search"]=0
        self.refresh()


    def notice(self,title,message):
        self.menu(title,[("OK",lambda:None)],message=message)

    def menu(self,title,items,message=""):
        self.overlay={"title":title,"items":items,"message":message,"previous_focus":self.focus}
        self.focus="overlay:0"
        self.schedule_draw()

    def add_files(self):
        if self.busy:return
        from .library import MEDIA_EXTENSIONS
        self.open_file_browser("Add media",mode="files",on_select=self._add_selected_files,
            extensions=MEDIA_EXTENSIONS,multiple=True,initialdir=self.preferences.get("media_source_dir"))

    def _add_selected_files(self,files):
        if self.busy or not files:return
        files=[Path(path) for path in files]
        self.preferences["media_source_dir"]=str(files[0].parent)
        def work():
            from .import_selection import prepare_media_selection
            try:return prepare_media_selection(files,cancelled=self.cancelled)
            except ImportCancelled:return None
        def done(paths):
            if paths is None:self.status.set("Media selection cancelled")
            elif paths:self.import_files(paths)
            else:self.notice("No supported media","Choose a supported game or disc-image entry file.")
        self.task(work,done,"Preparing selected media…")

    def scan_folder(self):
        if self.busy:return
        self.open_file_browser("Scan a folder",mode="folder",on_select=self._scan_selected_folder,
            initialdir=self.preferences.get("media_source_dir"))

    def _scan_selected_folder(self,folder):
        if self.busy:return
        folder=Path(folder);self.preferences["media_source_dir"]=str(folder)
        def work():
            try:return discover_media(folder,cancelled=self.cancelled)
            except ImportCancelled:return None
        def done(files):
            if files is None:self.status.set("Folder scan cancelled")
            elif files:self.import_files(files)
            else:self.notice("No supported media", "This folder and its subfolders contain no supported game media.")
        self.task(work,done,"Scanning selected folder and subfolders…")


    def choose_library(self):
        if self.embedded or self.playback.active():self.notice("A game is running","Stop the current game before changing libraries.");return
        def selected(path):
            self.library=Library(path)
            self.selected_id=None
            self._save_preferences();self.refresh();self.status.set("Library opened")
        self.open_file_browser("Choose library storage",mode="folder",on_select=selected,initialdir=self.library.root)

    def toggle_import_mode(self):self.linked.set(not self.linked.get());self._save_preferences();self.schedule_draw()

    def import_files(self,files,*,system="auto"):
        files=[Path(path) for path in files]
        if not files:self.status.set("No supported media found");return
        library=self.library;linked=self.linked.get();catalogue=self.catalogue
        artwork_imports=[]
        def work():
            from .importing import import_identified
            added=duplicates=0;errors=[];last=None
            last_progress=0;copied=0
            def progress(count):
                nonlocal last_progress,copied
                copied+=count
                now=time.monotonic()
                if now-last_progress>.2:
                    self.events.put(("status",f"Copying media • {human_size(copied)}"))
                    last_progress=now
            for index,path in enumerate(files):
                if self.cancelled.is_set():break
                self.events.put(("status",f"Importing {index+1}/{len(files)} • {path.name}"))
                try:
                    game,created,identity=import_identified(library,path,system=system,linked=linked,cancelled=self.cancelled,progress=progress,catalogue=catalogue)
                    added+=int(created);duplicates+=int(not created);last=game["id"]
                    if created:artwork_imports.append(game["id"])
                except ImportCancelled:break
                except (OSError,ValueError) as error:errors.append(f"{path.name}: {error}")
            return added,duplicates,errors,last,self.cancelled.is_set()
        def done(result):
            added,duplicates,errors,last,cancelled=result
            if last:self.selected_id=last
            self.refresh()
            self.status.set(f"{'Cancelled • ' if cancelled else ''}{added} added • {duplicates} existing • {len(errors)} errors")
            if errors:self.notice("Some media could not be imported","\n".join(errors[:4]))
            elif artwork_imports and not cancelled and self.scraper_settings().load()["automatic"]:
                self.scrape_game_ids(artwork_imports)
        self.task(work,done,"Identifying and importing media…")

    def choose_reference_art(self):
        def selected(path):
            self.preferences["reference_assets"]=str(path)
            self.art.reference_root=Path(path)
            self.art.clear();self._save_preferences();self.schedule_draw()
        self.open_file_browser("Choose artwork folder",mode="folder",on_select=selected,
            initialdir=self.preferences.get("reference_assets"))

    def choose_firmware(self):
        def selected(path):
            self.preferences["firmware_dir"]=str(path);self._save_preferences();self.status.set("Firmware folder configured")
        self.open_file_browser("Choose BIOS folder",mode="folder",on_select=selected,initialdir=self.firmware_directory())

    def launcher_dialog(self):
        game=self.selected()
        systems=list(SYSTEMS)
        if game:systems.remove(game["system"]);systems.insert(0,game["system"])
        self.menu("Choose a system",[(SYSTEMS[s],lambda system=s:self._backend_menu(system)) for s in systems]+[("Cancel",lambda:None)])

    def _backend_menu(self,system):
        from .playback import discover_backends
        self.detected_backends=discover_backends(project_root())
        items=[(f"Use {b.name}",lambda b=b:self._assign_backend(system,b)) for b in self.detected_backends if system in b.systems]
        from .embedded import find_core,VALIDATED_DEFAULTS,HOSTED_DEFAULTS
        if find_core(project_root(),system):
            items.insert(0,("Use integrated core"+(" (compatibility testing)" if system not in VALIDATED_DEFAULTS else ""),lambda:self._integrated_setting(system,True)))
        else:
            from .core_profiles import PROFILES
            if system in PROFILES:items.insert(0,('Download integrated core',lambda:self.offer_core_download(system,lambda:self._backend_menu(system))))
        items.extend([("Advanced executable and arguments",lambda:self._advanced_launcher(system)),("Cancel",lambda:None)])
        self.menu(SYSTEMS[system]+" playback",items,message="Choose an installed backend or configure your own executable.")

    def _integrated_setting(self,system,enabled):
        self.preferences.setdefault("integrated",{})[system]=enabled
        self._save_preferences();self.status.set("Integrated playback enabled");self.schedule_draw()

    def _assign_backend(self,system,backend):
        self.library.configure_launcher(system,backend.executable,list(backend.arguments))
        self.preferences.setdefault("integrated",{})[system]=False
        self._save_preferences();self.status.set(f"{SYSTEMS[system]} uses {backend.name}")

    def _advanced_launcher(self,system):
        from .launch import build_command
        dialog,frame=self._dialog(SYSTEMS[system]+" emulator")
        executable=tk.StringVar();arguments=tk.StringVar(value='["{game}"]')
        try:
            current=self.library.launcher(system);executable.set(current["executable"]);arguments.set(json.dumps(current["arguments"]))
        except ValueError:pass
        ttk.Label(frame,text="Emulator executable").grid(row=0,column=0,sticky="w")
        ttk.Entry(frame,textvariable=executable,width=65).grid(row=1,column=0,pady=10)
        def browse():
            path=filedialog.askopenfilename(parent=dialog,title="Choose emulator executable",filetypes=[("Windows programs","*.exe")])
            if path:
                from .playback import profile_for_executable,profile_arguments
                executable.set(path);arguments.set(json.dumps(profile_arguments(profile_for_executable(path).id,path)))
        ttk.Button(frame,text="Browse",command=browse).grid(row=1,column=1,padx=10)
        ttk.Label(frame,text="Arguments (JSON array); placeholders: {game}, {game_dir}").grid(row=2,column=0,columnspan=2,sticky="w")
        ttk.Entry(frame,textvariable=arguments,width=75).grid(row=3,column=0,columnspan=2,pady=10,sticky="ew")
        def save():
            try:
                args=json.loads(arguments.get());build_command(executable.get(),args,self.library.db)
                self.library.configure_launcher(system,executable.get(),args)
                self.preferences.setdefault("integrated",{})[system]=False;self._save_preferences()
                dialog.destroy();self.status.set("Emulator configured")
            except (OSError,ValueError,TypeError) as error:messagebox.showerror("Emulator",str(error),parent=dialog)
        ttk.Button(frame,text="Save emulator",command=save,style="Accent.TButton").grid(row=4,column=1,pady=10)

    def create_collection_dialog(self):
        self.text_input("New collection","Collection name",lambda name:self._create_collection(name))

    def _create_collection(self,name):self.library.create_collection(name);self.refresh()

    def text_input(self,title,label,callback,value="",*,secret=False,credential=False,hint=None,return_callback=None):
        self.text_prompt=dict(title=title,label=label,callback=callback,value=value,screen=self.screen,focus=self.focus,
                              secret=secret,credential=credential,hint=hint,return_callback=return_callback,keyboard_page="lower")
        self.screen="text";self.focus="credential:key:49" if credential else "key:Q";self.schedule_draw()


    def _accept_text(self):
        prompt=self.text_prompt
        if not prompt:return
        prompt["callback"](prompt["value"])
        self.screen=prompt["screen"];self.focus=prompt["focus"];self.text_prompt=None;self.refresh()
        if prompt.get("credential"):prompt["value"]=""
        if prompt.get("return_callback"):prompt["return_callback"]()

    def _cancel_text(self):
        prompt=self.text_prompt
        if not prompt:return
        self.screen=prompt["screen"];self.focus=prompt["focus"];self.text_prompt=None;self.refresh()
        if prompt.get("credential"):prompt["value"]=""
        if prompt.get("return_callback"):prompt["return_callback"]()

    def collection_menu(self):
        game=self.selected()
        if not game:return
        memberships={c["id"] for c in self.library.game_collections(game["id"])}
        items=[]
        for collection in self.library.collections():
            cid=collection["id"]
            items.append((f"{'✓ ' if cid in memberships else '+ '}{collection['name']}",
                lambda c=cid,existing=cid in memberships:self._membership(c,game["id"],existing)))
        items.append(("Create a new collection",self.create_collection_dialog))
        items.append(("Cancel",lambda:None))
        self.menu("Add to collection",items)

    def _membership(self,collection,game,remove):
        if remove:self.library.remove_from_collection(collection,game)
        else:self.library.add_to_collection(collection,game)
        self.refresh()

    def game_actions(self):
        game=self.selected()
        if not game:return
        if game["system"]=="pc":
            self.menu("PC game options",[("Edit title / metadata",self.metadata_dialog),
                ("PC game setup",self.pc_game_setup),("Artwork",self.game_artwork_menu),
                ("Add to playlist",self.collection_menu),
                ("Mark unfinished" if game.get("completed") else "Mark completed",lambda:self._flag("completed",not game.get("completed"))),
                ("Remove library entry",self.remove_entry)])
            return
        self.menu("Game options",[("Edit title / system / metadata",self.metadata_dialog),
            ("Find game artwork",self.scrape_selected_game),
            ("Choose cover artwork",self.choose_cover),("Verify every media file",self.verify),
            ("Mark unfinished" if game.get("completed") else "Mark completed",lambda:self._flag("completed",not game.get("completed"))),
            ("Remove from wishlist" if game.get("wishlist") else "Add to wishlist",lambda:self._flag("wishlist",not game.get("wishlist"))),
            ("Saves & backups",lambda:self.navigate("saves")),("Screenshot gallery",lambda:self.navigate("screenshots")),("Remove library entry",self.remove_entry)])

    def manage_collection(self):
        item=next((c for c in self.library.collections() if c["id"]==self.collection_id),None)
        if not item:return
        self.menu(item["name"],[("Rename collection",lambda:self.text_input("Rename collection","Collection name",lambda name:self.library.rename_collection(item["id"],name),item["name"])),
            ("Delete collection",lambda:self._confirm_collection_delete(item)),("Cancel",lambda:None)])

    def _confirm_collection_delete(self,item):
        self.menu("Delete this collection?",[("Keep collection",lambda:None),("Delete collection",lambda:self._delete_collection(item["id"]))],message="All games and media files stay in the library.")

    def _delete_collection(self,identity):
        self.library.delete_collection(identity);self.collection_id=None;self.navigate("collections",remember=False)

    def _screenshot_files(self):
        game=self.selected()
        if not game:return []
        base=self.library.root/"game_data"/str(game["id"])/game["bundle_hash"]/"screenshots"
        if not base.resolve().is_relative_to(self.library.root):return []
        return sorted((p for p in base.glob("*.png") if p.is_file() and not p.is_symlink()),reverse=True)

    def _screenshots(self):
        self.display(80,44,"Screenshots",40,WHITE,"bold")
        files=self._screenshot_files()
        if not files:self._empty("Take a screenshot from the in-game menu");return
        start,end=self._page_bounds("screenshots",len(files),6)
        for i,path in enumerate(files[start:end]):
            x=80+(i%3)*380;y=140+(i//3)*243;key=f"shot:{start+i}"
            self.rect(x-3,y-3,366,223,WHITE if self.focus==key else SURFACE)
            self.picture(path,x,y,360,190)
            self.display(x+12,y+199,path.stem,16,SUBTLE)
            self.target(key,(x,y,x+360,y+217),lambda p=path:self._open_screenshot(p),path.name)
        self._pager("screenshots",len(files),6,638)

    def _open_screenshot(self,path):self.screenshot_selection=path;self.navigate("screenshot")

    def _screenshot(self):
        path=self.screenshot_selection
        if not path or not path.is_file():self._empty("Screenshot unavailable");return
        from PIL import Image,ImageOps,ImageTk
        with Image.open(path) as source:
            if source.width*source.height>16_000_000:raise ValueError("Screenshot is too large")
            image=ImageOps.contain(source.convert("RGB"),(round(1180*self.scale),round(550*self.scale)))
        photo=ImageTk.PhotoImage(image,master=self.canvas);self.photos.append(photo)
        self.canvas.create_image(self.offset_x+640*self.scale,self.offset_y+330*self.scale,image=photo,anchor="center")
        self.display(80,643,path.name,17,SUBTLE)
        self.button_canvas("screenshot:back","Back to gallery",980,636,220,35,self.back)

    def _flag(self,flag,value):self.library.set_flags(self.selected_id,**{flag:value});self.refresh()

    def choose_cover(self):
        game=self.selected()
        if not game:return
        def selected(path):
            self.task(lambda:self.library.set_artwork(game["id"],path),lambda _:self._cover_selected(),"Saving game artwork…")
        self.open_file_browser("Choose game cover",mode="files",on_select=selected,
            extensions={".png",".jpg",".jpeg",".webp",".gif"},multiple=False)

    def _cover_selected(self):self.art.clear();self.refresh()


    def remove_entry(self):
        game=self.selected()
        if not game:return
        self.menu("Remove this library entry?",[("Keep entry",lambda:None),
            ("Remove entry — keep all media files",lambda:self._remove_entry(game["id"]))],
            message="Original and managed media files are preserved. Only the library entry, collection links and recorded history are removed.")

    def _remove_entry(self,game):
        self.library.remove_game(game);self.selected_id=None
        self.navigate("library",remember=False);self.refresh()

    def verify(self):
        game=self.selected()
        if not game:return
        library=self.library
        def done(result):
            good=sum(r["status"]=="ok" for r in result)
            self.refresh()
            self.notice("Media verification",f"{good} of {len(result)} files match their recorded hashes.\n\n"+"\n".join(f"{r['status'].upper()}: {r['path']}" for r in result[:12]))
        self.task(lambda:library.verify(game["id"],self.cancelled),done,"Verifying media integrity…")

    def backup_saves(self):
        game=self.selected()
        if not game:return
        def selected(directory):
            from .saves import SaveStore
            store=SaveStore(self.library.root)
            self.task(lambda:store.backup(game["id"],directory),lambda _:self.refresh(),"Creating verified save backup…")
        self.open_file_browser("Choose saves to back up",mode="folder",on_select=selected,
            initialdir=self.library.root/"game_data")

    def restore_backup(self,identity):
        if self.embedded or self.playback.active():
            self.notice("Close the game first","Stop the emulator before restoring files that it may have open.")
            return
        def selected(destination):
            self.menu("Restore save files",[("Restore into empty paths",lambda:self._restore_files(identity,destination,False)),
                ("Replace matching files and preserve an undo backup",lambda:self._restore_files(identity,destination,True)),("Cancel",lambda:None)],
                message=f"Destination: {destination}\nAn overwrite operation first preserves the existing matching files in a separate verified backup.")
        self.open_file_browser("Choose save restore folder",mode="folder",on_select=selected,
            initialdir=self.library.root/"game_data")

    def _restore_files(self,identity,destination,overwrite):
        from .saves import SaveStore
        store=SaveStore(self.library.root)
        self.task(lambda:store.restore(identity,destination,overwrite=overwrite,cancelled=self.cancelled),
                  lambda result:self.notice("Backup restored",f"{result.restored_files} files restored."+(" Previous files have an undo backup." if result.preserved_backup_id else "")),"Restoring save backup…")

    def _configure_detected_backends(self):
        from .playback import discover_backends
        detected=discover_backends(project_root())
        self.detected_backends=detected
        assigned=0
        for backend in detected:
            for system in backend.systems:
                if system not in SYSTEMS:continue
                try:self.library.launcher(system)
                except ValueError:
                    self.library.configure_launcher(system,backend.executable,list(backend.arguments))
                    assigned+=1
                else:
                    configured=self.library.launcher(system)
                    if backend.profile_id=="pcsx_redux" and Path(configured["executable"]).name.casefold()=="pcsx-redux.exe" and Path(configured["executable"]).parent==Path(backend.executable).parent:
                        self.library.configure_launcher(system,backend.executable,list(backend.arguments))
        self.status.set(f"Local emulator scan complete • {assigned} system profiles configured")

    def play(self):
        if self.busy:return
        game=self._game_context()
        if not game:return
        if not game.get("available"):
            self.notice("Connect your game drive" if game["system"]=="pc" else "Media unavailable",
                "Connect the SSD containing this game. If its drive letter or folder changed, use Manage → PC game setup to locate its EXE." if game["system"]=="pc" else "Connect the original storage device or verify the complete image bundle before playing.")
            return
        if self.embedded or self.playback.active():
            self.game_menu();return
        if game["system"]=="pc":self._play_pc_game(game);return
        if game['system'] in {'jaguar','jaguar_cd'}:
            from .bigpemu import executable,EXTENSIONS
            if not executable(project_root()).is_file():
                self.bigpemu_setup_menu(retry=self.play);return
            if self.library.path_for(game).suffix.casefold() not in EXTENSIONS:
                self.notice('Unsupported BigPEmu image','Use a supported Jaguar cartridge, CUE/CDI or BigPImage. BigPEmu does not accept CHD files.');return
            try:
                configured=self.library.launcher(game['system'])
                if Path(configured['executable']).name.casefold()=='bigpemu.exe' and not Path(configured['executable']).is_file():
                    from .playback import profile_arguments
                    exe=executable(project_root())
                    self.library.configure_launcher(game['system'],str(exe),profile_arguments('bigpemu',exe))
            except ValueError:
                from .playback import profile_arguments
                exe=executable(project_root())
                self.library.configure_launcher(game['system'],str(exe),profile_arguments('bigpemu',exe))
        from .embedded import find_core,VALIDATED_DEFAULTS,HOSTED_DEFAULTS
        core=find_core(project_root(),game["system"])
        if not core and self.preferences.get('integrated',{}).get(game['system'],game['system'] in HOSTED_DEFAULTS):
            self.offer_core_download(game['system'],self.play);return
        if core and self.preferences.get("integrated",{}).get(game["system"],game["system"] in HOSTED_DEFAULTS):
            self._start_integrated_game(game,core);return
        from .playback import assess_readiness
        try:launcher=self.library.launcher(game["system"])
        except ValueError:
            self.notice("Configure an emulator",f"No emulator is assigned to {SYSTEMS.get(game['system'],game['system'])}. Open Settings → Emulators & firmware.")
            return
        readiness=assess_readiness(game["system"],launcher["executable"],firmware_dir=self.firmware_directory())
        if not readiness.ready:
            self.notice("Playback requirements","\n".join(readiness.issues))
            return
        arguments=list(launcher["arguments"]);bridge=None
        if readiness.profile_id=="pcsx_redux":
            from .backend_rpc import PcsxReduxBridge
            bridge=PcsxReduxBridge(self.library.root,game["id"],game_key=game["bundle_hash"])
            arguments.extend(bridge.launch_arguments)
        try:session=self.playback.launch(game["id"],launcher["executable"],arguments,self.library.path_for(game),profile_id=readiness.profile_id,title=display_title(game))
        except Exception:
            if bridge:bridge.close()
            raise
        sid=getattr(session,"session_id",getattr(session,"id",None))
        if sid is None:raise RuntimeError("Playback session did not return an identity")
        self.sessions[sid]=self.library.start_session(game["id"])
        self._redux_controller_send_profile(game)
        if bridge:
            self.bridges[sid]=bridge
            import threading
            threading.Thread(target=lambda:self.bridge_events.put((sid,bridge.wait_ready(12))),daemon=True).start()
        self.status.set(f"Playing {display_title(game)} • Home or Select + Start held 1 second opens the game menu")
        self.root.iconify()
        self.refresh()

    def _start_integrated_game(self,game,core,media_lease=None):
        library=self.library;firmware=self.firmware_directory()
        if game.get("system")=="saturn":
            region=str(game.get("region") or "").casefold()
            names=("mpr-17933.bin",) if region in {"u","usa","north america","e","europe"} else ("sega_101.bin","mpr-17933.bin")
            if not firmware or not any((Path(firmware)/name).is_file() for name in names):
                retained=""
                if media_lease:
                    released=media_lease.close()
                    if released is False or getattr(media_lease,"closed",True) is False:
                        self.retained_disc_leases=getattr(self,"retained_disc_leases",[])+[media_lease]
                        retained="\nTemporary disc buffer retained: "+str(media_lease.entry_path)
                self.menu("Saturn firmware needed",[("Choose firmware folder",self.choose_firmware),("Back",lambda:None)],
                          message="Add compatible Saturn BIOS firmware ("+" or ".join(names)+") to "+str(firmware)+" and retry Play, or choose another firmware folder. Disc installation does not require firmware."+retained)
                return
        def work():
            from .embedded import EmbeddedGame
            from .disc_session import DiscEmbeddedGame
            session=None
            try:
                session=DiscEmbeddedGame(library,game,core,media_lease=media_lease,firmware=firmware,audio=not self.preferences.get("mute",False)) if media_lease else EmbeddedGame(library,game,core,firmware=firmware,audio=not self.preferences.get("mute",False))
                return session.start()
            except BaseException:
                if session is None and media_lease:media_lease.close()
                raise
        def started(session):
            try:record=library.start_session(game["id"]) if not game.get("transient_disc") else None
            except Exception:
                session.stop();raise
            self.embedded=session;self.embedded_game=game;self.frame_sequence=-1;self.held_keys.clear()
            self.gameplay_input.reset();self.input_release_gate=True;self.game_photo_key=None
            from .game_preferences import GamePreferences
            try:self.game_filter=GamePreferences(library.root,game["id"],game["bundle_hash"]).video_filter()
            except (OSError,ValueError,RuntimeError):self.game_filter="sharp"
            from .display_preferences import DisplayPreferences
            self.game_display=DisplayPreferences(library.root).get(game["system"])
            self.embedded_record=record;self.screen="gameplay";self.overlay=None;self.focus="gameplay:menu"
            self.status.set("Playing from temporary disc buffer" if game.get("transient_disc") else "Playing • Home or Select + Start held 1 second opens the game menu")
            self.refresh()
        self.task(work,started,"Starting integrated playback…",cancellable=False)

    def _request_game_menu(self):
        if self.busy:return
        if self.overlay and self.overlay.get("kind")=="file_browser":self.browser_input("guide");return
        if self.embedded:
            if self.overlay:self._resume_embedded()
            else:self.game_menu()
            return
        active=self.playback.active() if self.playback else []
        if active:
            sid=active[0].session_id
            if self.overlay and self.overlay.get("resume_id")==sid:
                self._resume(sid)
            else:self.game_menu()
        elif not self.overlay and self.screen!="text":self.navigate("home",remember=False)

    def game_menu(self):
        if self.busy:return
        if self.embedded:
            self.held_keys.clear()
            self.input_release_gate=True
            self.embedded.pause()
            items=[("Resume game",self._resume_embedded),("Save state",lambda:self.state_menu("save")),
                ("Load state",lambda:self.state_menu("load")),("Display",self.video_filter_menu),
                ("Controls",self.game_controls_menu),("Screenshot",lambda:self.state_operation("capture")),
                ("Reset game",self._reset_embedded)]
            buffered=bool(getattr(self.embedded,"game",{}).get("transient_disc") and getattr(self.embedded,"media_lease",None))
            installed=getattr(self.embedded,"installed_game",None)
            if buffered and not installed:items.insert(5,("Install to library",self._install_buffered_game))
            items.append(("Stop game and return to library",self._stop_embedded))
            self.menu("Game paused",items,
                message=f"{display_title(self.embedded_game)} • {duration(self.embedded.play_seconds)} played"+(" • Installed to library" if installed else ""))
            return
        active=self.playback.active()
        if not active:return
        session=active[0]
        sid=getattr(session,"session_id",getattr(session,"id",None))
        if getattr(session,"profile_id",None)=="pc_game":
            self.root.deiconify();self.root.lift();self.root.focus_force()
            self.menu("PC game menu",[("Return to game",lambda:self._resume(sid)),
                ("Quit game and return to library",lambda:self._pc_quit_menu(sid))],
                message=session.title+"\nThe game continues running. Pause and save using its own menu.")
            self.overlay["resume_id"]=sid
            return
        paused=bool(getattr(session,"paused",False))
        bridge=self.bridges.get(sid)
        if bridge and bridge.ready and not paused:
            def pause_done(_):self.playback.set_backend_paused(sid,True);self.game_menu()
            self.task(bridge.pause,pause_done,"Pausing game…",cancellable=False);return
        if not paused and not bridge:
            try:self.playback.pause(sid);paused=True
            except (OSError,RuntimeError):pass
        self.root.deiconify();self.root.lift();self.root.focus_force()
        items=[("Resume game",lambda:self._resume(sid))]
        if bridge and bridge.ready:
            items.extend([("Save state",lambda:self.state_menu("save",sid)),("Load state",lambda:self.state_menu("load",sid)),("Take screenshot",lambda:self.state_operation("capture",sid=sid))])
        if getattr(session,'profile_id',None)!='bigpemu':items.append(("Back up current save files",lambda:self._backup_current(sid)))
        items.append(("Stop game and return to library",lambda:self._stop(sid)))
        self.menu("Game menu",items,message="Paused" if paused else "Running in the backend window. Game-menu controls are connecting." if bridge else "Running in the backend window.")
        self.overlay["resume_id"]=sid

    def _install_buffered_game(self):
        if self.busy or not self.embedded:return
        session=self.embedded
        if not getattr(session,"game",{}).get("transient_disc") or not getattr(session,"media_lease",None):return
        if getattr(session,"installed_game",None):self.game_menu();return
        session.pause()
        copied=0
        def progress(count):
            nonlocal copied
            copied+=count
            total=max(1,int(session.game.get("size",0)))
            self.events.put(("status",f"Installing from buffer • {min(100,copied*100//total)}%"))
        def work():
            from .library import ImportCancelled
            from .buffer_install import BufferInstallError
            try:return session.install_to_library(cancelled=self.cancelled,progress=progress)
            except ImportCancelled:
                if getattr(session,"installed_game",None):raise
                return None
            except BufferInstallError as error:
                if error.installed_game:return dict(error.installed_game,_install_warning=str(error))
                raise
        def done(game):
            if game:
                self.selected_id=game["id"]
                if self.embedded_record is None:self.embedded_record=self.library.start_session(game["id"])
                self.refresh();self.game_menu();self.status.set("Installed to library • the current game is ready to resume")
                if game.get("_install_warning"):
                    self.notice("Game installed • saves need attention",game["_install_warning"])
                    self.overlay["back_action"]=self.game_menu
            else:self.game_menu();self.status.set("Installation cancelled • buffered game is ready to resume")
        self.task(work,done,"Installing from the existing disc buffer…")

    def _resume(self,sid):
        bridge=self.bridges.get(sid)
        snapshot=next((s for s in self.playback.active() if s.session_id==sid),None)
        if snapshot and snapshot.pause_source=="backend" and bridge and bridge.ready:
            self.task(bridge.resume,lambda _:self._resumed(sid),"Resuming game…",cancellable=False)
        else:
            self.playback.resume(sid);self.overlay=None;self.status.set("Game resumed");self.root.iconify()
            if snapshot and getattr(snapshot,"profile_id",None) in {"pc_game","bigpemu"}:self.playback.foreground(sid)

    def _resumed(self,sid):
        self.playback.set_backend_paused(sid,False);self.overlay=None;self.status.set("Game resumed");self.root.iconify();self.schedule_draw()

    def state_menu(self,operation,sid=None):
        backend=self.embedded or self.bridges.get(sid)
        if not backend:raise RuntimeError("This backend has no state interface")
        from .state_presentation import cards,format_saved_at
        slot_cards=cards(backend,4)
        items=[]
        for card in slot_cards:
            try:stamp=datetime.fromisoformat(card.saved_at.replace("Z","+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
            except (ValueError,AttributeError):stamp=format_saved_at(card.saved_at)
            label=f"Slot {card.slot+1}  •  "+(stamp if card.exists else "Empty")
            items.append((label,lambda selected=card:self._request_state_operation(operation,selected,sid)))
        items.append(("Back to game menu",self.game_menu))
        self.menu("Save state" if operation=="save" else "Load state",items,message="Choose a slot. Saving replaces that slot's previous state." if operation=="save" else "Choose a state to restore. Loading replaces your current in-memory game progress.")
        self.overlay.update(kind="state_slots",cards=slot_cards,operation=operation)
        if sid:self.overlay["resume_id"]=sid

    def _request_state_operation(self,operation,card,sid=None):
        if operation=="load" and (not card.exists or not card.compatible):
            self.notice("State unavailable","This slot is empty." if not card.exists else "OLD • This state was made by another core build." if card.old_version else card.error or "This state cannot be loaded.")
            if sid:self.overlay["resume_id"]=sid
            self.overlay["back_action"]=lambda:self.state_menu(operation,sid)
            return
        if operation=="load" or card.exists:
            self.menu("Load this state?" if operation=="load" else "Replace this state?",
                      [("Cancel",lambda:self.state_menu(operation,sid)),("Load state" if operation=="load" else "Replace state",lambda:self.state_operation(operation,card.slot,sid))],
                      message="Loading rewinds your current game progress and may also rewind in-game saves." if operation=="load" else f"Replace the existing state in slot {card.slot+1}?")
            if sid:self.overlay["resume_id"]=sid
            self.overlay["back_action"]=lambda:self.state_menu(operation,sid)
        else:self.state_operation(operation,card.slot,sid)

    def state_operation(self,operation,slot=0,sid=None):
        backend=self.embedded or self.bridges.get(sid)
        if not backend:raise RuntimeError("This backend has no state interface")
        def work():return backend.capture() if operation=="capture" else getattr(backend,operation)(slot)
        def done(result):
            self.status.set(f"Screenshot saved: {result}" if operation=="capture" else f"State {operation} completed • slot {slot+1}")
            self.game_menu()
        self.task(work,done,"Capturing game frame…" if operation=="capture" else f"{operation.title()} state • slot {slot+1}…",cancellable=False)

    def _backup_current(self,sid):
        from .saves import SaveStore
        session=next((s for s in self.playback.active() if s.session_id==sid),None)
        if not session:return
        game=self.library.game(session.game_id)
        launcher=self.library.launcher(game["system"])
        source=Path(launcher["executable"]).parent
        patterns=["*.mcd","*.sstate*"] if session.profile_id=="pcsx_redux" else ["*.sav","*.srm","*.state","*.mss"]
        self.task(lambda:SaveStore(self.library.root).backup(game["id"],source,label="Game-menu backup",patterns=patterns,cancelled=self.cancelled),lambda _:self.game_menu(),"Backing up save files…")

    def _stop(self,sid):
        def done(closed):
            if not closed:
                self.menu("The emulator is still open",[("Return to game",lambda:self._resume(sid)),
                    ("Force stop — unsaved progress may be lost",lambda:self._force_stop(sid))],message="The backend may need a confirmation in its own window.")
            self.status.set("Game closed • play history saved" if closed else "Waiting for emulator to close")
        self.task(lambda:self.playback.stop(sid),done,"Closing game…",cancellable=False)

    def _force_stop(self,sid):self.task(lambda:self.playback.stop(sid,force=True),lambda _:self.refresh(),"Stopping emulator…",cancellable=False)

    def _resume_embedded(self):
        self.input_release_gate=True
        self.gameplay_input.reset()
        self.gameplay_input.block_until_release()
        self.held_keys.clear()
        if self.embedded:self.embedded.resume()
        self.overlay=None;self.focus="gameplay:menu";self.schedule_draw()

    def video_filter_menu(self):
        if not self.embedded:return
        from .display_preferences import DisplayPreferences
        self.game_display=DisplayPreferences(self.library.root).get(self.embedded_game["system"])
        profile=self.game_display
        values=[("FILTER",profile.filter_id.upper()),("SCANLINES","—" if profile.filter_id=="clean" else f"{profile.scanlines}%"),
                ("SHAPE",profile.shape.upper()),("ZOOM",f"{profile.zoom}%"),("MOVE LEFT / RIGHT",f"{profile.offset_x:+}"),
                ("MOVE UP / DOWN",f"{profile.offset_y:+}"),("BRIGHTNESS",f"{profile.brightness}%")]
        fields=("filter_id","scanlines","shape","zoom","offset_x","offset_y","brightness")
        items=[(f"{label}   ·   {value}",lambda field=field:self._adjust_display(field,1)) for (label,value),field in zip(values,fields)]
        fullscreen_index=len(fields)
        fullscreen=bool(self.root.attributes("-fullscreen")) if getattr(self,"root",None) else False
        def toggle_fullscreen():
            self.fullscreen()
            if not self.overlay or self.overlay.get("kind")!="display_panel":self.video_filter_menu()
            self.focus=f"overlay:{fullscreen_index}"
        items.append(("FULLSCREEN   ·   "+("ON" if fullscreen else "OFF"),toggle_fullscreen))
        items.extend([("RESET TO DEFAULTS",self._reset_game_display),("Back to game menu",self.game_menu)])
        self.menu("Display",items,message="Picture settings saved per system • A or D-pad left / right changes a setting")
        self.overlay.update(kind="display_panel",fields=(*fields,"fullscreen"))

    def _adjust_display_focused(self,delta):
        try:index=int(str(self.focus).split(":")[-1])
        except ValueError:return
        fields=self.overlay.get("fields",())
        if 0<=index<len(fields):
            if fields[index]=="fullscreen":self.overlay["items"][index][1]()
            else:self._adjust_display(fields[index],delta)

    def _adjust_display(self,field,delta):
        if not self.embedded:return
        from .display_preferences import DisplayPreferences
        profile=self.game_display;value=getattr(profile,field)
        choices={"filter_id":("clean","scanlines","crt","composite"),"shape":("auto","4:3","pixel","stretch")}
        if field in choices:
            options=choices[field];value=options[(options.index(value)+delta)%len(options)]
        else:
            if field=="scanlines" and profile.filter_id=="clean":return
            low,high,step={"scanlines":(0,100,10),"zoom":(50,150,5),"offset_x":(-200,200,5),"offset_y":(-200,200,5),"brightness":(25,200,5)}[field]
            value=max(low,min(high,value+step*delta))
        focus=f"overlay:{('filter_id','scanlines','shape','zoom','offset_x','offset_y','brightness').index(field)}"
        self.game_display=DisplayPreferences(self.library.root).set(self.embedded_game["system"],**{field:value})
        self.game_photo_key=None;self.video_filter_menu();self.focus=focus

    def _reset_game_display(self):
        from .display_preferences import DisplayPreferences
        self.game_display=DisplayPreferences(self.library.root).reset(self.embedded_game["system"])
        self.game_photo_key=None;self.video_filter_menu()

    def _select_video_filter(self,value):
        from .game_preferences import GamePreferences
        game=self.embedded_game
        GamePreferences(self.library.root,game["id"],game["bundle_hash"]).set_video_filter(value)
        self.game_filter=value;self.game_photo_key=None
        self.video_filter_menu()

    def game_controls_menu(self):
        if not self.embedded:return
        system=self.embedded_game["system"]
        swap=self.preferences.get("system_controls",{}).get(system,{}).get("swap_ab",False)
        devices=self.controller.devices() if self.controller else []
        pads=[device for device in devices if device.get("slot") is not None]
        items=[("SWAP A AND B   ·   "+("ON" if swap else "OFF"),self._toggle_game_ab)]
        for device in pads:
            items.append((f"PLAYER {device['slot']+1}   ·   {device['name']}",lambda pad=device:self._move_game_player(pad)))
        items.append(("Back to game menu",self.game_menu))
        layout=('X → A · B → B · A → C · LB/RB → L/R\nBack → Stop · Start → Play/Pause' if system=='3do' else 'A/B · Console A/B   |   X/Y · Console Y/X')
        self.menu("Controls",items,message=layout+"\nD-pad / left stick · Directions\nHome or Select + Start held 1s · Menu\nHold Right for 2 seconds on a pad to move it to the next player.")
        self.overlay.update(kind="controls_panel",pads=pads)

    def _toggle_game_ab(self):
        system=self.embedded_game["system"]
        settings=self.preferences.setdefault("system_controls",{}).setdefault(system,{})
        settings["swap_ab"]=not settings.get("swap_ab",False)
        self._save_preferences();self.game_controls_menu()

    def _hold_move_player(self):
        try:index=int(str(self.focus).split(":")[-1])-1
        except ValueError:return
        pads=self.overlay.get("pads",())
        if not 0<=index<len(pads):return
        marker=getattr(self,"controller_move_started",None)
        if marker is None or marker[0]!=self.focus:self.controller_move_started=(self.focus,time.monotonic());return
        if time.monotonic()-marker[1]>=2:
            self._move_game_player(pads[index]);self.controller_move_started=(self.focus,float("inf"))

    def _move_game_player(self,pad):
        assign=getattr(self.controller.api,"assign_slot",None)
        if not assign:return
        assign(pad["instance_id"],(pad["slot"]+1)%4)
        self.gameplay_input.block_until_release();self.input_release_gate=True
        self.game_controls_menu()
        self.focus=f"overlay:{next((i+1 for i,device in enumerate(self.overlay['pads']) if device['instance_id']==pad['instance_id']),0)}"

    def _reset_embedded(self):
        self.menu("Reset this game?",[("Keep current game",self.game_menu),("Reset console",lambda:self._perform_reset())],message="Unsaved in-memory progress will be lost. Existing save slots and battery saves remain stored.")

    def _perform_reset(self):
        self.embedded.host.reset();self.game_menu()

    def _stop_embedded(self,exit_after=False,failure=None):
        if not self.embedded:return
        session=self.embedded;record=self.embedded_record
        def done(_):
            try:
                if record is not None:self.library.finish_session(record,duration_seconds=session.play_seconds,exit_code=1 if failure else 0)
            finally:
                self.embedded=None;self.embedded_record=None;self.game_photo=None;self.held_keys.clear()
                installed=getattr(session,"installed_game",None)
                if installed:self.selected_id=installed["id"];self.embedded_game=installed
                self.screen="home" if getattr(session,"game",{}).get("transient_disc") and not installed else "details";self.overlay=None;self.focus="hero:play" if self.screen=="home" else "details:play";self.refresh()
            transient=bool(getattr(session,"game",{}).get("transient_disc"))
            retained=bool(getattr(session,"lease_retained",False))
            if retained:
                self.retained_disc_sessions=getattr(self,"retained_disc_sessions",[])+[session]
                message="Game closed • temporary disc buffer retained: "+str(session.media_lease.entry_path)
            else:
                message="Game closed • saves stored; temporary disc buffer released" if transient else "Game closed • battery saves and play history stored"
            self.status.set("Playback stopped: "+failure if failure else message)
            if failure:self.notice("Playback stopped",failure)
            if exit_after:self._close_now()
        self.task(session.stop,done,"Saving battery memory and closing game…",cancellable=False)


    def _embedded_input(self):
        from .console_input import embedded_input
        return embedded_input(self)

    def _tick(self):
        self.poll_windows_experience()
        if self.closing:return
        try:
            self._poll_media()
            previous_pads=self._controller_connections()
            controller_events=self.controller.poll() if self.controller else []
            disconnected=set(previous_pads)-set(self._controller_connections())
            if disconnected and (self.embedded or self.playback and self.playback.active()):
                self.pending_controller_disconnect=min(previous_pads[device] for device in disconnected)
            if self.screen=="controller_setup":self._poll_controller_setup()
            elif self.controller and self.root.grab_current() is None:
                self._route_controller_events(controller_events)
            if self.pending_controller_disconnect is not None and not self.busy:
                self._pause_for_controller_disconnect()
            if not self.busy:
                try:self._catalogue_ready(self.catalogue_queue.get_nowait())
                except queue.Empty:pass
            self._harvest_sessions()
            try:
                sid,ready=self.bridge_events.get_nowait()
                if sid in self.bridges:self.status.set("Game controls connected • save states and screenshots available" if ready else "Game is running • backend game controls did not connect")
            except queue.Empty:pass
            if self.embedded:
                self._embedded_input()
                frame=self.embedded.host.latest_frame()
                if frame and frame.sequence!=self.frame_sequence:
                    self.frame_sequence=frame.sequence;self.schedule_draw()
                if self.embedded.host.error and not self.busy:
                    self._stop_embedded(failure=str(self.embedded.host.error))
            if self.busy:self.schedule_draw()
            elif self.close_after_task:
                self.close_after_task=False;self.root.after_idle(self.close)
            minute=datetime.now().strftime("%H:%M")
            if minute!=self.last_clock_minute:self.last_clock_minute=minute;self.schedule_draw()
        except (OSError,ValueError,RuntimeError,sqlite3.Error) as error:
            self.status.set(str(error))
        self.root.after(16 if self.embedded and not self.overlay else 40,self._tick)

    def _route_controller_events(self,events):
        if any(getattr(event,"pressed",True) for event in events):self._navigation_pointer(True)
        if self.busy:
            if any(getattr(event,"pressed",True) and event.action in {"back","b"} for event in events):self.input_action("back")
            return
        # Reserved menu input takes precedence over all navigation from this
        # frame, so an A press cannot select a just-opened menu accidentally.
        if any(getattr(event,"pressed",True) and event.action=="quit_game" for event in events):
            if not self.busy:
                if self.embedded:self._stop_embedded()
                elif self.playback and self.playback.active():self._stop(self.playback.active()[0].session_id)
            return
        for event in events:
            if getattr(event,"pressed",True) and (event.action=="guide" or event.action=="menu" and getattr(event,"chord",False)):
                self._request_game_menu();return
        active=bool(self.embedded or self.playback and self.playback.active())
        for event in events:
            if not getattr(event,"pressed",True):
                if event.action=="right":self.controller_move_started=None
                continue
            if active and not self.overlay:continue
            self.input_action(event.action)

    def _controller_connections(self):
        if not self.controller:return {}
        devices=getattr(self.controller,"devices",None)
        if callable(devices):
            return {device.get("instance_id",f"xinput:{device['slot']}"):device["slot"] for device in devices() if device.get("slot") is not None}
        return {f"xinput:{slot}":slot for slot in getattr(self.controller,"connected_indices",())}

    def _pause_for_controller_disconnect(self):
        port=self.pending_controller_disconnect;self.pending_controller_disconnect=None
        if not (self.embedded or self.playback and self.playback.active()):return
        if not self.overlay:self.game_menu()
        if self.overlay:
            self.overlay["message"]=f"CONTROLLER DISCONNECTED • PLAYER {port+1}\nReconnect your controller to continue."
            self.schedule_draw()
        self.status.set("CONTROLLER DISCONNECTED")

    def _harvest_sessions(self):
        if not self.playback:return
        for result in self.playback.poll():
            sid=result.session_id;library_sid=self.sessions.pop(sid,None)
            bridge=self.bridges.pop(sid,None)
            if bridge:
                try:bridge.close()
                except OSError:pass
            if library_sid is not None:self.library.finish_session(library_sid,duration_seconds=result.duration_seconds,exit_code=result.exit_code)
            self.status.set("Game closed • play history saved")
            self._redux_controller_send_profile(None)
            self.overlay=None;self.root.deiconify();self.root.lift();self.root.focus_force();self.refresh()

    def task(self,work,done,description,*,cancellable=True):
        if self.busy:return
        self.task_cancellable=cancellable
        super().task(work,done,description,cancellable=cancellable)

    def _task_error(self,message):
        if self.embedded and getattr(self.embedded,"finished_seconds",None) is not None:
            self.menu("Save transfer needs attention",[("Retry finishing save transfer",self._stop_embedded),
                ("Keep files and return to library",self._return_after_retained_disc)],message=str(message))
            self.overlay["back_action"]=self._return_after_retained_disc
            return
        self.notice("Task could not complete",str(message))
        if self.embedded:self.overlay["back_action"]=self.game_menu

    def _return_after_retained_disc(self):
        session=self.embedded
        if not session or getattr(session,"finished_seconds",None) is None:return
        self.retained_disc_sessions=getattr(self,"retained_disc_sessions",[])+[session]
        if self.embedded_record is not None:self.library.finish_session(self.embedded_record,duration_seconds=session.play_seconds,exit_code=1)
        installed=getattr(session,"installed_game",None)
        if installed:self.selected_id=installed["id"];self.embedded_game=installed
        self.embedded=None;self.embedded_record=None;self.game_photo=None;self.held_keys.clear()
        self.screen="details" if installed else "home";self.overlay=None
        self.focus="details:play" if installed else "hero:play"
        self.refresh();self.status.set("Game closed • saved files and disc buffer retained for recovery")

    def _callback_error(self,kind,error,traceback):
        if not self.closing:
            self.status.set("Operation stopped • see error details")
            self._task_error(str(error))

    def disc_dialog(self):
        self.menu("Discs & images",[("Inspect / import mounted source",self.optical_dialog),
            ("Acquire a data volume",self.acquire_volume),("Capture supported physical CD tracks",self.raw_disc_dialog),("Add existing image files",self.add_files),
            ("Cancel",lambda:None)],message="Original mounted image files are preferred so raw tracks remain intact.")

    def raw_disc_dialog(self):
        from .optical import list_drives
        drives=list_drives()
        if not drives:self.notice("No optical drive","Connect a standard PC optical drive.");return
        self.menu("Choose a CD drive",[(drive.letter,lambda letter=drive.letter:self._raw_plan(letter)) for drive in drives]+[("Cancel",lambda:None)])

    def _raw_plan(self,drive):
        selected=self.inserted_disc()
        if selected and selected.drive!=drive:selected=None
        def work():
            from .mounted_images import mounted_image_source
            source=mounted_image_source(drive)
            if source:return source,None
            from .raw_acquisition import plan_raw_capture
            return None,plan_raw_capture(drive,cancelled=self.cancelled)
        def done(result):
            if selected and not self._disc_current(selected.drive,selected.fingerprint):return
            source,plan=result
            if source:
                self.menu("Install disc",[("Install to library",lambda:self.install_disc_from_drive(drive,disc=selected)),("Cancel",lambda:None)],message="The original mounted image will be copied into your library.")
                if selected:self.overlay["disc"]=selected
                return
            self.menu("Install disc",[("Install to library",lambda:self.install_disc_from_drive(drive,disc=selected)),("Cancel",lambda:None)],
                message=f"{len(plan.tracks)} tracks • {human_size(plan.bytes_to_capture)}\nBIN/CUE main-channel capture; no subchannels, hidden pregaps or protection reconstruction. Unsupported layouts are rejected.")
            if selected:self.overlay["disc"]=selected
        self.task(work,done,"Checking physical CD layout…")

    def _raw_destination(self,drive):
        self.install_disc_from_drive(drive)

    def acquire_volume(self):
        from .optical import list_drives
        drives=list_drives()
        if not drives:self.notice("No optical drive","Connect a USB/internal drive or mount an image.");return
        self.menu("Choose a source drive",[(drive.letter,lambda d=drive.letter:self._acquire(d)) for drive in drives]+[("Cancel",lambda:None)])

    def _acquire(self,drive):
        if self.busy:return
        def work():
            from .acquisition import acquisition_plan,capture_logical_volume
            import uuid
            plan=acquisition_plan(drive)
            if plan.source_image:return plan.source_image
            if plan.mode!="logical_volume":raise ValueError("\n".join(plan.warnings))
            target=self.library.root/"exports"/uuid.uuid4().hex/"disc.iso"
            target.parent.mkdir(parents=True)
            def progress(done,total,phase):self.events.put(("status",f"{phase}: {done*100//max(1,total)}%"))
            try:
                capture_logical_volume(drive,target,cancelled=self.cancelled,progress=progress)
                return target
            except Exception as error:
                if getattr(error,"reason",None)=="cancelled":return None
                raise
        def done(path):
            if path:self.import_files([path])
            else:self.status.set("Data export cancelled")
        self.task(work,done,"Exporting data volume into the library…")

    def _load_catalogue(self):
        path=project_root()/"analysis/formats/decoded/data/catalogue.rbxhdb"
        if not path.is_file() or self.catalogue_loading:return
        self.catalogue_loading=True
        def worker():
            try:
                from .metadata import Catalogue
                catalogue=Catalogue(path,asset_root=project_root()/"analysis/formats/decoded")
                self.catalogue_queue.put(catalogue)
            except (OSError,ValueError,ImportError):
                self.catalogue_loading=False
        import threading
        threading.Thread(target=worker,daemon=True).start()

    def _catalogue_ready(self,catalogue):
        self.catalogue=catalogue;self.catalogue_loading=False
        disc=self.inserted_disc()
        if disc:self._enrich_disc(disc.identity)
        for game in self.all_games:
            match=catalogue.match(game["title"],game["system"])
            if match:
                allowed={key:match[key] for key in ("description","year","publisher","developer","region","genre") if match.get(key) and not game.get(key)}
                if allowed:self.library.update_metadata(game["id"],**allowed)
        self.refresh()

    def exit_menu(self):
        if self.is_gaming_home():
            self.menu("Xbox home",[("Keep browsing",lambda:None),("Windows Xbox mode settings",self.open_xbox_settings)],message="Use the Xbox mode menu to switch to the Windows desktop.")
            return
        self.exit_app_menu()

    def exit_app_menu(self):
        self.menu("Exit Redux?",[("Keep browsing",lambda:None),("Exit app",self.close)],
                  message="Close Redux. Your library and artwork stay saved.")

    def close(self):
        if self.busy:
            self.close_after_task=True
            if getattr(self,"task_cancellable",True):self.cancelled.set()
            self.status.set("Finishing the current operation before closing…");return
        if self.embedded:
            self.held_keys.clear();self.embedded.pause()
            self.menu("A game is still running",[("Resume game",self._resume_embedded),("Save battery memory and exit",lambda:self._stop_embedded(True))],message="Save a state from the game menu if you want to return to the exact current moment.")
        elif self.playback and self.playback.active():
            self.menu("A game is still running",[("Return to library",lambda:None),
                ("Stop game and exit",self._stop_and_close)],message="Finish or save your current game before closing its emulator.")
        else:self._close_now()

    def _stop_and_close(self):
        def work():return [snapshot.session_id for snapshot in self.playback.active() if not self.playback.stop(snapshot.session_id)]
        def done(remaining):
            self._harvest_sessions()
            if remaining:self.menu("The emulator is still open",[("Keep library open",lambda:None),("Force stop and exit",lambda:self._force_close(remaining))],message="Force stopping may lose unsaved game progress.")
            else:self._close_now()
        self.task(work,done,"Closing running games…",cancellable=False)

    def _force_close(self,sessions):
        self.task(lambda:[self.playback.stop(sid,force=True) for sid in sessions],lambda _:self._close_now(),"Stopping owned games…",cancellable=False)

    def _close_now(self):
        registration=getattr(self,"windows_registration",None)
        if registration:registration.close();self.windows_registration=None
        self._stop_media_watch()
        if self.controller and hasattr(self.controller,"close"):self.controller.close()
        self._harvest_sessions()
        if self.playback:self.playback.close()
        self._save_preferences()
        super().close()
