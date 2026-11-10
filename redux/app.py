"""Original desktop frontend for an independent, portable media library."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .library import Library, SYSTEMS, MEDIA_EXTENSIONS, ImportCancelled, discover_media
from .launch import build_command, launch_game
from .optical import list_drives, inspect_disc

BG, PANEL, CARD, TEXT, MUTED, ACCENT = "#0c1420", "#111f2f", "#172a3d", "#edf4fc", "#99aec3", "#63d4b2"


def human_size(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024


def describe_disc(info, source=None):
    lines=[f"Drive: {info.drive.letter}"]
    if info.inspection_kind=="iso9660_volume":
        lines.append("Readable ISO9660 data volume • track layout unavailable")
    else:
        lines.append(f"Disc layout: {info.disc_type.replace('_',' ')}")
        for track in info.tracks:
            lines.append(f"Track {track.number:02}: {track.kind}, LBA {track.start_lba:,}–{track.end_lba:,}")
    if info.volume:
        volume=info.volume
        lines.extend([f"System marker: {volume.system_id or '(not specified)'}",
                      f"Logical volume: {human_size(volume.volume_bytes)} • {volume.volume_blocks:,} × {volume.logical_block_size:,} bytes"])
        if volume.device_bytes is not None:
            lines.append(f"Driver-reported capacity: {human_size(volume.device_bytes)}")
    if source:
        lines.extend(["",f"Mounted source: {source}","Import the source image to preserve its original format and referenced tracks."])
    lines.append("")
    lines.extend(info.notes)
    return "\n".join(lines)


def config_path():
    from .windows_paths import redux_user_data
    return redux_user_data()/"settings.json"


class App:
    def __init__(self, root: tk.Tk, library_root=None):
        self.root = root
        root.title(f"Redux Media Library  •  {__version__}")
        root.geometry("1180x760")
        root.minsize(1000,740)
        root.configure(bg=BG)
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.busy = self.closing = False
        self.action_buttons = []
        self.children = []
        settings = config_path()
        if library_root is None:
            try:
                library_root = json.loads(settings.read_text())["library_root"]
            except (OSError, ValueError, KeyError):
                library_root = settings.parent / "library"
        self.library = Library(library_root)
        root.report_callback_exception=self._callback_error
        self._style()
        self._build()
        self.refresh()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100,self._poll)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI",10), background=PANEL, foreground=TEXT)
        style.configure("TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("TButton", background=CARD, foreground=TEXT, borderwidth=0, padding=(12,9))
        style.map("TButton", background=[("active","#23435b"),("disabled","#152331")],
                  foreground=[("disabled","#60778b")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#102421")
        style.map("Accent.TButton",background=[("active","#8be3c8"),("disabled","#244239")])
        style.configure("TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT, padding=7)
        style.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=TEXT, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly",CARD)],foreground=[("readonly",TEXT)])
        style.configure("TCheckbutton",background=PANEL,foreground=MUTED,padding=4)
        style.map("TCheckbutton",background=[("active",PANEL)])
        style.configure("Treeview",background=PANEL,fieldbackground=PANEL,foreground=TEXT,
                        rowheight=38,borderwidth=0)
        style.configure("Treeview.Heading",background=CARD,foreground=MUTED,padding=(10,10),borderwidth=0)
        style.map("Treeview", background=[("selected","#234b5b")],foreground=[("selected","#ffffff")])
        style.configure("Horizontal.TProgressbar",background=ACCENT,troughcolor=CARD,borderwidth=0)

    def button(self,parent,text,command,accent=False,**pack):
        button=ttk.Button(parent,text=text,command=command,style="Accent.TButton" if accent else "TButton")
        button.pack(**pack)
        self.action_buttons.append(button)
        return button

    def _build(self):
        shell=tk.Frame(self.root,bg=BG)
        shell.pack(fill="both",expand=True,padx=18,pady=18)
        sidebar=tk.Frame(shell,bg=PANEL,width=238)
        sidebar.pack(side="left",fill="y",padx=(0,16))
        sidebar.pack_propagate(False)
        tk.Label(sidebar,text="R /",font=("Segoe UI",28,"bold"),fg=ACCENT,bg=PANEL).pack(anchor="w",padx=20,pady=(18,0))
        tk.Label(sidebar,text="REDUX",font=("Segoe UI",22,"bold"),fg=TEXT,bg=PANEL).pack(anchor="w",padx=20)
        tk.Label(sidebar,text="MEDIA LIBRARY",font=("Segoe UI",9,"bold"),fg=MUTED,bg=PANEL).pack(anchor="w",padx=22,pady=(0,24))
        tk.Label(sidebar,text="YOUR LIBRARY",font=("Segoe UI",9,"bold"),fg=ACCENT,bg=PANEL).pack(anchor="w",padx=20)
        self.root_label=tk.Label(sidebar,text="",wraplength=198,justify="left",font=("Segoe UI",9),fg=MUTED,bg=PANEL)
        self.root_label.pack(anchor="w",padx=20,pady=(8,12))
        self.button(sidebar,"Choose library folder",self.choose_library,fill="x",padx=16,pady=4)
        self.button(sidebar,"Add media files",self.add_files,True,fill="x",padx=16,pady=(18,4))
        self.button(sidebar,"Scan a folder",self.scan_folder,fill="x",padx=16,pady=4)
        self.linked=tk.BooleanVar(value=False)
        ttk.Checkbutton(sidebar,text="Link files in place",variable=self.linked).pack(anchor="w",padx=16,pady=8)
        tk.Label(sidebar,text="Default: copy complete media bundles\ninto this library. Sources stay intact.",
                 fg=MUTED,bg=PANEL,font=("Segoe UI",9),justify="left",wraplength=198).pack(anchor="w",padx=20,pady=(0,20))
        self.button(sidebar,"Emulator setup",self.launcher_dialog,fill="x",padx=16,pady=4)
        self.button(sidebar,"Optical drives",self.optical_dialog,fill="x",padx=16,pady=4)
        tk.Label(sidebar,text="USB / INTERNAL DRIVES\nSSD / SD / LOCAL STORAGE",font=("Segoe UI",8,"bold"),
                 fg=MUTED,bg=PANEL,justify="left").pack(side="bottom",anchor="w",padx=20,pady=22)
        main=tk.Frame(shell,bg=BG)
        main.pack(side="left",fill="both",expand=True)
        tk.Label(main,text="Your games. Your storage.",font=("Segoe UI",24,"bold"),fg=TEXT,bg=BG).pack(anchor="w")
        tk.Label(main,text="A local library for your own media and separately installed emulators.",
                 font=("Segoe UI",10),fg=MUTED,bg=BG).pack(anchor="w",pady=(5,18))
        filters=tk.Frame(main,bg=BG)
        filters.pack(fill="x",pady=(0,12))
        self.search=tk.StringVar()
        tk.Label(filters,text="Search titles",bg=BG,fg=MUTED,font=("Segoe UI",9)).pack(side="left",padx=(0,10))
        ttk.Entry(filters,textvariable=self.search).pack(side="left",fill="x",expand=True,padx=(0,10))
        self.system=tk.StringVar(value="All systems")
        combo=ttk.Combobox(filters,textvariable=self.system,values=["All systems",*SYSTEMS.values()],state="readonly",width=24)
        combo.pack(side="left")
        self.search.trace_add("write",lambda *_:self.refresh())
        combo.bind("<<ComboboxSelected>>",lambda _:self.refresh())
        tableframe=ttk.Frame(main)
        tableframe.pack(fill="both",expand=True)
        self.table=ttk.Treeview(tableframe,columns=("title","system","storage","size","status"),show="headings",selectmode="browse",height=4)
        for key,label,width in [("title","TITLE",280),("system","SYSTEM",150),("storage","STORAGE",85),("size","SIZE",90),("status","MEDIA",85)]:
            self.table.heading(key,text=label,anchor="w")
            self.table.column(key,width=width,minwidth=70,stretch=key in {"title","system"})
        scroll=ttk.Scrollbar(tableframe,orient="vertical",command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right",fill="y")
        self.table.pack(fill="both",expand=True)
        self.table.bind("<<TreeviewSelect>>",lambda _:self.select())
        self.table.bind("<Double-1>",lambda _:self.play())
        controls=tk.Frame(main,bg=BG)
        controls.pack(fill="x",pady=12)
        self.button(controls,"Play selected",self.play,True,side="left",padx=(0,8))
        self.button(controls,"Verify files",self.verify,side="left",padx=(0,8))
        self.button(controls,"Edit details",self.edit_dialog,side="left")
        self.count=tk.Label(controls,text="",fg=MUTED,bg=BG,font=("Segoe UI",9))
        self.count.pack(side="right")
        self.details=tk.Text(main,height=5,wrap="word",bg=PANEL,fg=MUTED,font=("Segoe UI",9),
                             relief="flat",padx=14,pady=12,state="disabled")
        self.details.pack(fill="x")
        self.status=tk.StringVar(value="Ready • media sources are never modified")
        ttk.Label(main,textvariable=self.status,style="Muted.TLabel").pack(anchor="w",pady=(12,6))
        footer=tk.Frame(main,bg=BG)
        footer.pack(fill="x")
        self.progress=ttk.Progressbar(footer,mode="determinate",value=0)
        self.progress.pack(side="left",fill="x",expand=True)
        self.cancel_button=ttk.Button(footer,text="Cancel task",command=self.cancelled.set,state="disabled")
        self.cancel_button.pack(side="right",padx=(10,0))

    def _details(self,text):
        self.details.configure(state="normal")
        self.details.delete("1.0","end")
        self.details.insert("1.0",text)
        self.details.configure(state="disabled")

    def refresh(self):
        selected=self.table.selection() if hasattr(self,"table") else ()
        reverse={v:k for k,v in SYSTEMS.items()}
        system=reverse.get(self.system.get())
        rows=self.library.games(self.search.get(),system)
        self.table.delete(*self.table.get_children())
        for game in rows:
            available=self.library.path_for(game).is_file()
            self.table.insert("","end",iid=str(game["id"]),values=(game["title"],SYSTEMS.get(game["system"],game["system"]),
                "Linked" if game["storage"]=="linked" else "Library",human_size(game["size"]),"Available" if available else "Offline"))
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected)
        self.count.configure(text=f"{len(rows)} shown / {len(self.library.games())} total")
        self.root_label.configure(text=str(self.library.root))
        if not rows:
            self._details("No matching media. Add your own ROMs or disc images, or scan an SSD / SD-card folder.\n"
                          "CUE and M3U imports retain their referenced files. Disc systems can be assigned in Edit details.\n"
                          "Emulators and console firmware are configured separately.")
        elif not self.table.selection():
            self.table.selection_set(str(rows[0]["id"]))
            self.select()

    def selected(self):
        rows=self.table.selection()
        if not rows:
            return None
        return self.library.game(int(rows[0]))

    def select(self):
        game=self.selected()
        if game:
            self._details(f"{game['title']}  •  {SYSTEMS.get(game['system'],game['system'])}\n"
                          f"Entry: {self.library.path_for(game)}\n"
                          f"Bundle SHA-256: {game['bundle_hash']}\n"
                          "Available means the entry file exists. Use Verify files to check every track and hash.")

    def choose_library(self):
        path=filedialog.askdirectory(parent=self.root,title="Choose a library folder on SSD, SD card or local disk")
        if path:
            try:
                library=Library(path)
                config_path().parent.mkdir(parents=True,exist_ok=True)
                config_path().write_text(json.dumps({"library_root":str(library.root)},indent=2),encoding="utf-8")
                self.library=library
                self.refresh()
                self.status.set("Library opened • managed media paths remain relative to this folder")
            except (OSError,ValueError,sqlite3.Error) as error:
                messagebox.showerror("Cannot open library",str(error),parent=self.root)

    def add_files(self):
        files=filedialog.askopenfilenames(parent=self.root,title="Add your own media files",
            filetypes=[("Game media"," ".join("*"+ext for ext in sorted(MEDIA_EXTENSIONS))),("All files","*.*")])
        if files:
            self.import_files([Path(p) for p in files])

    def scan_folder(self):
        folder=filedialog.askdirectory(parent=self.root,title="Scan a media folder (including subfolders)")
        if folder:
            self.task(lambda:discover_media(folder),self.import_files,"Scanning media folder…")

    def import_files(self,files,*,system="auto"):
        if not files:
            self.status.set("No supported media found in that folder")
            return
        linked=self.linked.get()
        library=self.library
        def work():
            added=duplicates=promoted=0
            errors=[]
            for index,path in enumerate(files):
                if self.cancelled.is_set():
                    break
                self.events.put(("status",f"Importing {index+1}/{len(files)} • {path.name}"))
                try:
                    game,created=library.import_game(path,system=system,linked=linked,cancelled=self.cancelled)
                    added+=int(created)
                    promoted+=int(game.get("import_action")=="promoted")
                    duplicates+=int(game.get("import_action")=="duplicate")
                except ImportCancelled:
                    break
                except (OSError,ValueError) as error:
                    errors.append(f"{path.name}: {error}")
            return added,duplicates,promoted,errors,self.cancelled.is_set()
        def done(result):
            added,duplicates,promoted,errors,cancelled=result
            self.refresh()
            self.status.set(f"{'Cancelled • ' if cancelled else ''}{added} added • {promoted} copied from links • {duplicates} duplicates • {len(errors)} errors")
            if errors:
                messagebox.showwarning("Some files could not be imported","\n\n".join(errors[:8]),parent=self.root)
        self.task(work,done,"Preparing media import…")

    def task(self,work,done,description,*,cancellable=True):
        if self.busy:
            return
        self.busy=True
        self.cancelled.clear()
        self.status.set(description)
        for button in self.action_buttons:
            button.configure(state="disabled")
        self.cancel_button.configure(state="normal" if cancellable else "disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        def run():
            try:
                self.events.put(("done",done,work()))
            except Exception as error:
                self.events.put(("error",str(error)))
        threading.Thread(target=run,daemon=False).start()

    def _poll(self):
        try:
            while True:
                event=self.events.get_nowait()
                if event[0]=="status":
                    self.status.set(event[1])
                    continue
                self.busy=False
                self.progress.stop()
                self.progress.configure(mode="determinate",value=0)
                self.cancel_button.configure(state="disabled")
                for button in self.action_buttons:
                    button.configure(state="normal")
                if not self.closing:
                    if event[0]=="done":
                        event[1](event[2])
                    else:
                        self.status.set("Task stopped • see error details")
                        self._task_error(event[1])
        except queue.Empty:
            pass
        except Exception as error:
            # A completed worker can fail while updating the UI/database too.
            # Preserve event-loop liveness so another task or a safe close can
            # still run after that failure.
            self.busy=False
            self._callback_error(type(error),error,error.__traceback__)
        if self.closing and not self.busy:
            self.root.destroy()
            return
        self.root.after(100,self._poll)

    def _task_error(self,message):
        messagebox.showerror("Task could not complete",message,parent=self.root)

    def verify(self):
        game=self.selected()
        if game:
            library=self.library
            def done(rows):
                failed=[r for r in rows if r["status"]!="ok"]
                self.status.set(f"Verification: {len(rows)-len(failed)}/{len(rows)} files match their import hashes")
                self._details("\n".join(f"{r['status'].upper()}  {r['path']}" for r in rows))
                self.refresh()
            self.task(lambda:library.verify(game["id"],self.cancelled),done,"Verifying every file in the media bundle…")
        else:
            self.status.set("Select a library entry first")

    def play(self):
        if self.busy:
            return
        game=self.selected()
        if not game:
            self.status.set("Select a library entry first")
            return
        try:
            launcher=self.library.launcher(game["system"])
            child=launch_game(launcher["executable"],launcher["arguments"],self.library.path_for(game))
            self.children.append(child)
            self.status.set(f"Started {Path(launcher['executable']).name} • process {child.pid}")
        except (OSError,ValueError,sqlite3.Error) as error:
            messagebox.showerror("Cannot launch media",str(error),parent=self.root)

    def _dialog(self,title):
        dialog=tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=PANEL)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.bind("<Escape>",lambda _:dialog.destroy())
        frame=ttk.Frame(dialog,padding=22)
        frame.pack(fill="both",expand=True)
        return dialog,frame

    def edit_dialog(self):
        game=self.selected()
        if not game:
            self.status.set("Select a library entry first")
            return
        dialog,frame=self._dialog("Edit media details")
        title=tk.StringVar(value=game["title"])
        system=tk.StringVar(value=SYSTEMS.get(game["system"],"Unassigned"))
        ttk.Label(frame,text="Title").pack(anchor="w")
        ttk.Entry(frame,textvariable=title,width=54).pack(fill="x",pady=(5,14))
        ttk.Label(frame,text="System • disc-image systems are assigned explicitly").pack(anchor="w")
        ttk.Combobox(frame,textvariable=system,values=list(SYSTEMS.values()),state="readonly",width=45).pack(fill="x",pady=(5,18))
        def save():
            try:
                self.library.update_details(game["id"],title=title.get(),system={v:k for k,v in SYSTEMS.items()}[system.get()])
                dialog.destroy()
                self.refresh()
                self.select()
            except (OSError,ValueError,sqlite3.Error) as error:
                messagebox.showerror("Cannot save details",str(error),parent=dialog)
        ttk.Button(frame,text="Save details",command=save,style="Accent.TButton").pack(anchor="e")

    def launcher_dialog(self):
        dialog,frame=self._dialog("Emulator setup")
        frame.columnconfigure(0,weight=1)
        game=self.selected()
        system=tk.StringVar(value=SYSTEMS.get(game["system"],"NES") if game else "NES")
        executable=tk.StringVar()
        arguments=tk.StringVar(value='["{game}"]')
        ttk.Label(frame,text="Use a separately installed emulator for each system.",style="Muted.TLabel").grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,18))
        ttk.Label(frame,text="System").grid(row=1,column=0,sticky="w")
        combo=ttk.Combobox(frame,textvariable=system,values=list(SYSTEMS.values()),state="readonly",width=42)
        combo.grid(row=2,column=0,columnspan=2,sticky="ew",pady=(5,14))
        ttk.Label(frame,text="Emulator executable").grid(row=3,column=0,sticky="w")
        ttk.Entry(frame,textvariable=executable,width=62).grid(row=4,column=0,sticky="ew",pady=(5,14))
        def browse():
            path=filedialog.askopenfilename(parent=dialog,title="Choose emulator executable")
            if path: executable.set(path)
        ttk.Button(frame,text="Browse",command=browse).grid(row=4,column=1,padx=(8,0),pady=(5,14))
        ttk.Label(frame,text="Arguments • JSON array, one string per argument").grid(row=5,column=0,columnspan=2,sticky="w")
        ttk.Entry(frame,textvariable=arguments,width=62).grid(row=6,column=0,columnspan=2,sticky="ew",pady=(5,8))
        ttk.Label(frame,text='Example: ["--fullscreen", "{game}"]\nSupported placeholders: {game}, {game_dir}\nImage and firmware support depend on the selected emulator.',style="Muted.TLabel").grid(row=7,column=0,columnspan=2,sticky="w",pady=(0,18))
        def load(_=None):
            try:
                item=self.library.launcher({v:k for k,v in SYSTEMS.items()}[system.get()])
                executable.set(item["executable"])
                arguments.set(json.dumps(item["arguments"]))
            except ValueError:
                executable.set("")
                arguments.set('["{game}"]')
        combo.bind("<<ComboboxSelected>>",load)
        load()
        def save():
            try:
                args=json.loads(arguments.get())
                build_command(executable.get(),args,self.library.db)
                self.library.configure_launcher({v:k for k,v in SYSTEMS.items()}[system.get()],executable.get(),args)
                dialog.destroy()
                self.status.set("Emulator configured")
            except (OSError,ValueError,TypeError,sqlite3.Error) as error:
                messagebox.showerror("Cannot save emulator",str(error),parent=dialog)
        ttk.Button(frame,text="Save emulator",command=save,style="Accent.TButton").grid(row=8,column=1,sticky="e")

    def optical_dialog(self):
        dialog,frame=self._dialog("Standard optical drives")
        ttk.Label(frame,text="USB and internal PC optical drives",font=("Segoe UI",15,"bold")).pack(anchor="w")
        ttk.Label(frame,text="Read-only disc and mounted-image inspection. Source media stays unchanged.",style="Muted.TLabel").pack(anchor="w",pady=(6,16))
        drives=list_drives()
        if not drives:
            ttk.Label(frame,text="No optical drives are currently attached.").pack(anchor="w",pady=10)
        choice=tk.StringVar(value=drives[0].letter if drives else "")
        if drives:
            ttk.Combobox(frame,textvariable=choice,values=[d.letter for d in drives],state="readonly").pack(anchor="w")
        output=tk.Text(frame,width=82,height=18,wrap="word",bg=CARD,fg=TEXT,font=("Consolas",10),relief="flat",padx=12,pady=12)
        output.pack(fill="both",expand=True,pady=16)
        output.insert("1.0","Inspect a disc or mounted image.\n\nMounted ISO/CUE images can test volume detection and existing-image import.\nUse Import mounted image to preserve a discoverable original image and all referenced tracks.\n\nThe disc menu also offers supported physical CD BIN/CUE capture and restricted data-volume export. Physical-drive compatibility still requires real-drive testing; subchannel and protection reconstruction are unsupported.")
        output.configure(state="disabled")
        def inspect():
            def work():
                from .mounted_images import mounted_image_source
                info=inspect_disc(choice.get())
                source=mounted_image_source(choice.get())
                return info,source
            def done(result):
                info,source=result
                if not dialog.winfo_exists(): return
                output.configure(state="normal")
                output.delete("1.0","end")
                output.insert("1.0",describe_disc(info,source))
                output.configure(state="disabled")
                self.status.set("Read-only disc inspection complete")
            self.task(work,done,"Inspecting disc / logical volume…",cancellable=False)
        def import_mounted():
            def work():
                from .mounted_images import mounted_image_source
                letter=choice.get()
                source=mounted_image_source(letter)
                if source is None:
                    return None,"auto"
                suggested="auto"
                try:
                    info=inspect_disc(letter)
                    if info.volume and info.volume.system_id.strip().upper()=="PLAYSTATION":
                        suggested="psx"
                except RuntimeError:
                    # Metadata is optional; a valid source bundle can still be
                    # preserved without inventing a disc/system identification.
                    pass
                if mounted_image_source(letter)!=source:
                    raise ValueError("The mounted source changed during inspection. Try again.")
                return source,suggested
            def done(result):
                source,system=result
                if source is None:
                    messagebox.showinfo("Select the original image","This drive does not expose a supported source-image path. Use Add media files to select its original ISO or CUE file, or choose a supported physical-media acquisition route from the disc menu.",parent=dialog)
                    return
                dialog.destroy()
                self.import_files([source],system=system)
            self.task(work,done,"Finding the mounted source image…",cancellable=False)
        if drives:
            actions=ttk.Frame(frame)
            actions.pack(fill="x")
            ttk.Button(actions,text="Inspect disc",command=inspect).pack(side="left")
            ttk.Button(actions,text="Import mounted image",command=import_mounted,style="Accent.TButton").pack(side="right")

    def close(self):
        self.closing=True
        self.cancelled.set()
        self.status.set("Finishing the current file operation before closing…")
        if not self.busy:
            self.root.destroy()

    def _callback_error(self,kind,error,traceback):
        if not self.closing:
            self.status.set("Operation stopped • see error details")
            messagebox.showerror("Operation could not complete",str(error),parent=self.root)


def main(library_root=None,*,activation=None):
    from .windows_activation import SingleInstance,ActivationError,parse_activation,append_activation_log
    from .windows_gaming import WindowsGamingExperience,gaming_configuration
    activation=activation or parse_activation([])
    experience=WindowsGamingExperience().query()
    configured_home=gaming_configuration().home_app or ""
    # Let sihost activate the identity-bearing home package, rather than taking
    # its single-instance mutex first through the ordinary desktop Run entry.
    if activation.startup and experience.active and configured_home.startswith("Redux.MediaLibrary_"):
        append_activation_log(activation,fse_active=True,outcome="startup skipped; Windows activates Redux home")
        return
    instance=SingleInstance()
    try:
        if not instance.acquire(activation):return
    except ActivationError as error:
        # A failed repeat activation must not start another frontend or expose a
        # bootloader traceback over the existing living-room session.
        append_activation_log(activation,fse_active=experience.active,outcome="activation failed: "+str(error))
        instance.close()
        return
    try:root=tk.Tk()
    except BaseException:
        instance.close();raise
    root.withdraw()
    try:
        from .console_ui import ConsoleApp
        app=ConsoleApp(root,library_root,activation=activation)
        from .windows_diagnostics import capture_requested_launch
        capture_requested_launch(app)
    except (OSError,ValueError,sqlite3.Error) as error:
        messagebox.showerror("Cannot start library",str(error),parent=root)
        root.destroy()
        instance.close()
        return
    def record(item):
        from .windows_gaming import current_package_full_name
        append_activation_log(item,package=current_package_full_name(),fse_active=app.windows_experience.query().active)
    record(activation)
    def poll_activation():
        while True:
            try:item=instance.activations.get_nowait()
            except queue.Empty:break
            record(item);app.handle_activation(item)
        if not app.closing:root.after(100,poll_activation)
    root.after(100,poll_activation)
    root.deiconify()
    if activation.startup and experience.active and not activation.home:root.iconify()
    try:root.mainloop()
    finally:instance.close()
