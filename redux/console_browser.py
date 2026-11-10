"""Controller file/folder selection with asynchronous, stale-safe listings.

No filesystem query runs in drawing or controller callbacks. A closed browser
invalidates pending results; directory workers never call Tk. Tests may inject
``_browser_list_locations``, ``_browser_list_directory`` and
``_browser_start_worker(callback)`` on the application instance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import queue
import threading
from typing import Callable


_ROWS = 10
_BUTTONS = ("browser:locations", "browser:cancel", "browser:use")
_CONFIRM = frozenset({"select", "a", "confirm"})
_BACK = frozenset({"back", "b"})
_TOGGLE = frozenset({"favorite", "details", "x"})
_USE = frozenset({"search", "y"})
_PAGE_PREVIOUS = frozenset({"page_up", "shoulder_left", "lb", "previous_tab"})
_PAGE_NEXT = frozenset({"page_down", "shoulder_right", "rb", "next_tab"})


def _file_size(size):
    if size is None:
        return ""
    value = max(0, size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
        value /= 1024


@dataclass
class BrowserState:
    title: str
    mode: str
    on_select: Callable
    multiple: bool
    extensions: tuple | None
    previous_screen: str
    previous_focus: str | None
    previous_overlay: object
    path: Path | None = None
    parent: Path | None = None
    entries: tuple = ()
    selected: dict[str, Path] = field(default_factory=dict)
    focus_index: int = 0
    loading: bool = False
    error: str | None = None
    truncated: bool = False
    generation: int = 0


class ConsoleFileBrowser:
    def open_file_browser(self, title, mode="files", on_select=None, initialdir=None,
                          extensions=None, multiple=False):
        """Open the browser; callbacks receive Path, or list[Path] for multiple.

        Folder mode selects the directory currently being viewed. A single-file
        browser selects a file with A or Y. Multiple-file selection survives
        folder navigation and is submitted with Y or the visible Add button.
        """
        if mode not in {"files", "folder"} or not callable(on_select):
            raise ValueError("A browser requires files/folder mode and a selection callback")
        if getattr(self, "busy", False):
            return
        previous = self._browser_active()
        if previous is not None:
            self._browser_restore(previous)
        state = BrowserState(str(title), mode, on_select, bool(multiple and mode == "files"),
            (extensions,) if isinstance(extensions,str) else tuple(extensions) if extensions is not None else None,
            getattr(self, "screen", "home"), getattr(self, "focus", None), getattr(self, "overlay", None))
        self._browser_state = state
        if not hasattr(self, "_browser_results"):
            self._browser_results = queue.Queue()
        self.overlay = {"kind": "file_browser", "title": state.title, "browser": state}
        self._browser_request(state, Path(initialdir).expanduser() if initialdir else None)

    def _browser_active(self):
        overlay = getattr(self, "overlay", None)
        state = getattr(self, "_browser_state", None)
        return state if state is not None and overlay and overlay.get("kind") == "file_browser" and overlay.get("browser") is state else None

    def _browser_start_worker(self, callback):
        threading.Thread(target=callback, name="Redux file browser", daemon=True).start()

    def _browser_request(self, state, path, focus_path=None):
        if self._browser_active() is not state:
            return
        self._browser_generation = getattr(self, "_browser_generation", 0) + 1
        state.generation = self._browser_generation
        generation = state.generation
        state.path = Path(path) if path is not None else None
        state.parent = state.path.parent if state.path is not None and state.path.parent != state.path else None
        state.entries, state.focus_index = (), 0
        state.loading, state.error, state.truncated = True, None, False
        self.focus = "browser:row:0"
        requested = state.path
        def work():
            try:
                if requested is None:
                    from .file_browser import list_locations
                    from .ui_assets import project_root
                    lister = getattr(self, "_browser_list_locations", list_locations)
                    result = lister(self.library.root, project_root())
                else:
                    from .file_browser import list_directory
                    lister = getattr(self, "_browser_list_directory", list_directory)
                    result = lister(requested, extensions=state.extensions, folders_only=state.mode == "folder")
                error = None
            except Exception as exc:
                result, error = None, str(exc)[:2048] or type(exc).__name__
            self._browser_results.put((state, generation, requested, focus_path, result, error))
        self._browser_start_worker(work)
        self._browser_schedule_poll()
        self.schedule_draw()

    def _browser_schedule_poll(self):
        if not getattr(self, "_browser_poll_pending", False):
            self._browser_poll_pending = True
            self.root.after(30, self._browser_poll_results)

    def _browser_poll_results(self):
        self._browser_poll_pending = False
        if getattr(self, "closing", False):
            return
        active = self._browser_active()
        if active is None:
            return
        for _ in range(64):
            try:
                state, generation, requested, focus_path, result, error = self._browser_results.get_nowait()
            except queue.Empty:
                break
            if state is not active or generation != active.generation:
                continue
            active.loading = False
            if requested is None:
                active.entries = tuple(result or ())[:10000] if not error else ()
                active.error = error
            elif not error:
                active.path = Path(result.path)
                active.parent = result.parent
                active.entries = tuple(result.entries)[:10000]
                active.error, active.truncated = result.error, result.truncated
            else:
                active.error, active.entries = error, ()
            active.focus_index = next((index for index, entry in enumerate(active.entries)
                if focus_path is not None and entry.path == focus_path), 0)
            if self.focus not in _BUTTONS:
                self.focus = f"browser:row:{active.focus_index}" if active.entries else "browser:locations"
            self.schedule_draw()
        if active.loading:
            self._browser_schedule_poll()

    def _browser_restore(self, state):
        self._browser_generation = getattr(self, "_browser_generation", 0) + 1
        state.generation = self._browser_generation
        self._browser_state = None
        self.screen, self.focus, self.overlay = state.previous_screen, state.previous_focus, state.previous_overlay
        self.schedule_draw()

    def _browser_cancel(self):
        state = self._browser_active()
        if state is not None:
            self._browser_restore(state)

    def browser_back(self):
        state = self._browser_active()
        if state is None:
            return False
        if state.path is None:
            self._browser_restore(state)
        else:
            old = state.path
            self._browser_request(state, state.parent, focus_path=old)
        return True

    def _browser_row(self, state):
        return state.entries[state.focus_index] if 0 <= state.focus_index < len(state.entries) else None

    def _browser_can_use(self, state):
        if state.mode == "folder":
            return state.path is not None and not state.loading and not state.error
        if state.multiple:
            return bool(state.selected)
        entry = self._browser_row(state)
        return entry is not None and not entry.is_directory and not state.loading

    def _browser_finish(self):
        state = self._browser_active()
        if state is None or not self._browser_can_use(state):
            return
        selection = (state.path if state.mode == "folder" else list(state.selected.values()) if state.multiple
                     else self._browser_row(state).path)
        callback = state.on_select
        self._browser_restore(state)
        callback(selection)

    def _browser_toggle(self):
        state = self._browser_active()
        if state is None or not state.multiple or not str(self.focus).startswith("browser:row:"):
            return
        entry = self._browser_row(state)
        if entry is None or entry.is_directory or state.loading:
            return
        key = os.path.normcase(str(entry.path))
        if key in state.selected:
            del state.selected[key]
        else:
            state.selected[key] = entry.path
        self.schedule_draw()

    def _browser_activate_row(self, index, generation=None):
        state = self._browser_active()
        if state is None or state.loading or (generation is not None and generation != state.generation):
            return
        if not 0 <= index < len(state.entries):
            return
        state.focus_index = index
        self.focus = f"browser:row:{index}"
        entry = state.entries[index]
        if entry.is_directory:
            self._browser_request(state, entry.path)
        elif state.multiple:
            self._browser_toggle()
        else:
            self._browser_finish()

    def browser_input(self, action):
        state = self._browser_active()
        if state is None:
            return False
        if action in {"guide", "home"}:
            self._browser_restore(state)
        elif action in _BACK:
            self.browser_back()
        elif action in _USE:
            self._browser_finish()
        elif action in _TOGGLE:
            self._browser_toggle()
        elif action in _PAGE_PREVIOUS | _PAGE_NEXT:
            if state.entries:
                state.focus_index = max(0, min(len(state.entries) - 1,
                    state.focus_index + (-_ROWS if action in _PAGE_PREVIOUS else _ROWS)))
                self.focus = f"browser:row:{state.focus_index}"
                self.schedule_draw()
        elif action in _CONFIRM:
            if self.focus == "browser:locations":
                self._browser_request(state, None)
            elif self.focus == "browser:cancel":
                self._browser_restore(state)
            elif self.focus == "browser:use":
                self._browser_finish()
            else:
                self._browser_activate_row(state.focus_index)
        elif action in {"up", "down", "left", "right", "next"}:
            on_row = str(self.focus).startswith("browser:row:")
            if on_row and action in {"up", "down"}:
                index = state.focus_index + (-1 if action == "up" else 1)
                if 0 <= index < len(state.entries):
                    state.focus_index = index
                    self.focus = f"browser:row:{index}"
                else:
                    self.focus = "browser:locations" if action == "up" else "browser:use"
            elif on_row:
                self.focus = "browser:locations" if action == "left" else "browser:use"
            elif action == "up" and state.entries:
                self.focus = f"browser:row:{state.focus_index}"
            else:
                index = _BUTTONS.index(self.focus) if self.focus in _BUTTONS else 0
                self.focus = _BUTTONS[(index + (-1 if action == "left" else 1)) % len(_BUTTONS)]
            self.schedule_draw()
        else:
            # Consume ordinary menu actions so the screen under this browser
            # cannot navigate or launch a game while a selection is pending.
            return action in {"menu", "start", "quit_game"}
        return True

    def _file_browser_panel(self, overlay=None):
        state = self._browser_active()
        if state is None:
            return
        self.targets, self.callbacks = [], {}
        self.rect(0,0,1280,720,"#0b0c10")
        self.panel(40,30,1200,660,"#15161b",2)
        self.display(80,52,state.title,30,"#f4f4f2","bold")
        path = "Locations" if state.path is None else str(state.path)
        self.display(80,101,self.fit_text(path,1120,18),18,"#b5b7c2")
        if state.loading:
            self.display(80,138,"Reading folder… You can still go back or choose Locations.",17,"#a3a5b0")
        elif state.error:
            self.display(80,138,self.fit_text("Cannot read this location: "+state.error.replace("\n"," "),1120,17),17,"#e5b3b3")
        else:
            count=len(state.entries)
            summary=f"{count} item"+("s" if count!=1 else "")
            if state.multiple:summary+=f"   ·   {len(state.selected)} selected"
            if state.truncated:summary+="   ·   Folder limit reached"
            self.display(80,138,summary,17,"#a3a5b0")
        start=(state.focus_index//_ROWS)*_ROWS
        if state.loading and not state.entries:
            key="browser:row:0"
            self.panel(80,174,1120,36,"#343039" if self.focus==key else "#202127",2,"#f4f4f2" if self.focus==key else None)
            self.display(126,180,"Loading…",21,"#a3a5b0")
            self.target(key,(80,174,1200,210),lambda:None,"Loading folder")
        for index,entry in enumerate(state.entries[start:start+_ROWS],start):
            y=174+(index-start)*40
            key=f"browser:row:{index}"
            selected=os.path.normcase(str(entry.path)) in state.selected
            focused=self.focus==key
            self.panel(80,y,1120,36,"#343039" if focused else "#202127",2,"#f4f4f2" if focused else None)
            mark="✓" if selected else "›" if entry.is_directory else "·"
            self.display(94,y+6,mark,21,"#ec407a" if selected else "#c5c7d2")
            self.display(126,y+6,self.fit_text(entry.label,880,21),21,"#f4f4f2" if focused else "#c5c7d2")
            if entry.is_directory:self.display(1180,y+9,"Folder" if entry.kind not in {"drive","location"} else "Open",15,"#969aa5",anchor="ne")
            else:self.display(1180,y+9,_file_size(entry.size),15,"#969aa5",anchor="ne")
            self.target(key,(80,y,1200,y+36),lambda i=index,g=state.generation:self._browser_activate_row(i,g),entry.label)
        if not state.entries and not state.loading and not state.error:
            self.display(640,330,"No folders here" if state.mode=="folder" else "No matching files here",24,"#969aa5",anchor="center")
        if len(state.entries)>_ROWS:
            self.display(1200,586,f"{start+1}–{min(start+_ROWS,len(state.entries))} / {len(state.entries)}   ·   LB / RB Page",16,"#969aa5",anchor="ne")
        confirm=("Use this folder" if state.mode=="folder" else f"Add {len(state.selected)} "+("file" if len(state.selected)==1 else "files") if state.multiple else "Use selected file")
        for key,label,x,width,callback in (
            ("browser:locations","Locations",80,200,lambda:self._browser_request(state,None)),
            ("browser:cancel","Cancel",296,180,self._browser_cancel),
            ("browser:use",confirm,920,280,self._browser_finish)):
            focused=self.focus==key
            self.panel(x,618,width,40,"#343039" if focused else "#24262d",2,"#f4f4f2" if focused else None)
            self.display(x+width/2,628,label,20,"#f4f4f2" if key!="browser:use" or self._browser_can_use(state) else "#777c89",anchor="n")
            self.target(key,(x,618,x+width,658),callback,label)
        self._controller_hint("A","Open / Select",80,672,"#35a4c6")
        self._controller_hint("B","Back",306,672,"#d75b78")
        if state.multiple:self._controller_hint("X","Select",470,672,"#8599ed")
        self._controller_hint("Y","Use folder" if state.mode=="folder" else "Add selected" if state.multiple else "Use file",746,672,"#d6bb34")
