"""Controller-friendly metadata editing and optical-media inspection pages."""
from __future__ import annotations

from pathlib import Path
from .library import SYSTEMS

WHITE="#f4f4f2"
MUTED="#a3a3ab"
ACCENT="#ec407a"

_METADATA_FIELDS=("title","system","year","publisher","developer","genre","region","description")


def _size(value):
    value=max(0,int(value or 0))
    for suffix in ("B","KB","MB","GB","TB"):
        if value<1024 or suffix=="TB":return f"{value:.1f} {suffix}" if suffix!="B" else f"{value} B"
        value/=1024


def _track_time(sectors):
    minutes,seconds=divmod(max(0,int(sectors))//75,60)
    return f"{minutes}:{seconds:02}"


class ConsoleEditors:
    def metadata_dialog(self):
        game=self.selected()
        if not game:
            self.notice("Choose a game","Open a game from Collection to edit its information.")
            return
        self.metadata_game_id=game["id"]
        self.metadata_draft={key:game.get(key) for key in _METADATA_FIELDS}
        self.navigate("metadata_editor")
        self.focus="metadata:title";self.schedule_draw()

    def _metadata_field(self,name,label):
        if name=="system":
            self.menu("Choose a system",[(value,lambda system=key:self._metadata_set("system",system))
                for key,value in SYSTEMS.items()]+[("Cancel",lambda:None)])
        else:
            value=self.metadata_draft.get(name)
            self.text_input("Edit "+label.lower(),label,lambda text:self._metadata_set(name,text),
                            "" if value is None else str(value))

    def _metadata_set(self,name,value):
        self.metadata_draft[name]=value
        self.schedule_draw()

    def _metadata_save(self):
        fields={name:self.metadata_draft.get(name) for name in _METADATA_FIELDS}
        for name in _METADATA_FIELDS:
            if name not in {"system","year"} and fields[name] is None:fields[name]=""
        if fields["year"] in (None,""):fields["year"]=None
        # The library validates all fields before committing this one update.
        self.library.update_metadata(self.metadata_game_id,**fields)
        self.status.set("Game information saved")
        self.back();self.refresh()

    def _metadata_editor(self):
        draft=getattr(self,"metadata_draft",None)
        self.display(80,47,"Game Information",38,WHITE)
        if draft is None:
            self.display(80,131,"Choose a game from Collection first.",22,MUTED)
            self.button_canvas("metadata:cancel","Back",1094,49,106,38,self.back)
            return
        self.display(80,106,"Edit a field, then save your changes.",19,MUTED)
        self.button_canvas("metadata:save","Save",972,49,106,38,self._metadata_save,True)
        self.button_canvas("metadata:cancel","Cancel",1094,49,106,38,self.back)
        columns=((("title","Title"),("year","Year"),("developer","Developer"),("region","Region")),
                 (("system","System"),("publisher","Publisher"),("genre","Genre"),("description","Description")))
        for column,fields in enumerate(columns):
            for row,(name,label) in enumerate(fields):
                x,y,key=80+column*580,163+row*83,f"metadata:{name}"
                focused=self.focus==key
                self.panel(x,y,540,72,"#ffffff16" if focused else "#ffffff07",0,WHITE if focused else None)
                if focused:self.rect(x,y+11,3,50,ACCENT)
                self.display(x+17,y+10,label.upper(),12,ACCENT if focused else "#898991")
                value=draft.get(name)
                if name=="system":value=SYSTEMS.get(value,"Unassigned")
                empty=value is None or str(value).strip()==""
                summary="Add a description" if empty and name=="description" else "Not set" if empty else str(value).replace("\n"," ")
                self.display(x+17,y+34,self.fit_text(summary,490,21),22,MUTED if empty else WHITE)
                self.icon("chevron",x+505,y+30,17,"#d6d6dc" if focused else "#6b6b74")
                self.target(key,(x,y,x+540,y+72),lambda field=name,title=label:self._metadata_field(field,title),label)
        self.display(80,558,"Changes stay in this draft until you choose Save.",16,"#85858e")

    def optical_dialog(self):
        from .optical import list_drives
        try:drives=list_drives()
        except (OSError,ValueError,RuntimeError) as error:
            self.notice("Could not list drives",str(error));return
        if not drives:
            self.notice("No optical drives","Connect a USB or internal PC optical drive, or mount an image.")
            return
        self.menu("Optical drives",[(f"{drive.letter}  ·  Inspect disc",lambda letter=drive.letter:self._inspect_console_drive(letter))
                  for drive in drives]+[("Cancel",lambda:None)],message="Choose a drive to inspect its current media.")

    def _inspect_console_drive(self,letter):
        def work():
            from .optical import inspect_disc
            from .mounted_images import mounted_image_source
            try:
                info=inspect_disc(letter)
                try:source=mounted_image_source(letter)
                except (OSError,ValueError,RuntimeError):source=None
                return info,source,None
            except (OSError,ValueError,RuntimeError) as error:return None,None,str(error)
        def done(result):
            info,source,error=result
            if error:self.notice("Could not inspect this drive",error);return
            self.disc_inspection=(info,source)
            self.navigate("disc_info")
            self.focus="disc:import" if source else "disc:refresh";self.schedule_draw()
        self.task(work,done,f"Inspecting {letter}…",cancellable=False)

    def _import_console_disc_source(self):
        inspection=getattr(self,"disc_inspection",None)
        if not inspection or not inspection[1]:
            self.notice("Original image unavailable","Choose the original image from Media, or select a supported capture method.")
            return
        info,source=inspection
        def work():
            from .mounted_images import mounted_image_source
            try:
                current=mounted_image_source(info.drive.letter)
                if current is None or Path(current).resolve()!=Path(source).resolve():
                    return None,"The mounted image changed. Inspect the drive again before importing."
                return Path(current),None
            except (OSError,ValueError,RuntimeError) as error:return None,str(error)
        def done(result):
            path,error=result
            if error:self.notice("Mounted source changed",error)
            else:self.import_files([path])
        self.task(work,done,"Checking the mounted source…",cancellable=False)

    def _disc_info(self):
        inspection=getattr(self,"disc_inspection",None)
        if not inspection:
            self.display(80,47,"Optical Media",38,WHITE)
            self.display(80,131,"Choose a drive to inspect its current media.",22,MUTED)
            self.button_canvas("disc:other","Choose a drive",80,199,216,42,self.optical_dialog,True)
            return
        info,source=inspection
        volume=info.volume
        self.display(80,47,"Drive "+info.drive.letter,38,WHITE)
        kind="Mounted image" if source else "Optical media"
        subtitle="Readable data volume" if info.inspection_kind=="iso9660_volume" else info.disc_type.replace("_"," ").title()
        self.display(80,105,kind+"  /  "+subtitle,21,MUTED)
        self.icon("disc",80,165,42,ACCENT)
        self.display(141,162,volume.system_id.strip() if volume and volume.system_id.strip() else subtitle,27,WHITE)
        self.display(142,201,"Read-only inspection",15,"#85858e")

        facts=[(80,239,"ORIGINAL IMAGE",Path(source).name if source else "Not exposed by this drive"),
               (660,239,"VOLUME",(volume.volume_id.strip() or "Unlabeled") if volume else "No supported data volume"),
               (80,311,"READABLE DATA",_size(volume.volume_bytes) if volume else "Not available"),
               (660,311,"TRACK LAYOUT",f"{len(info.tracks)} track"+("" if len(info.tracks)==1 else "s") if info.tracks else "Not available from this drive")]
        for x,y,label,value in facts:
            self.display(x,y,label,12,"#85858e")
            self.display(x,y+24,self.fit_text(value,532,21),22,WHITE)
        self.rect(80,379,1120,1,"#333338")
        self.display(80,401,"Disc tracks",21,WHITE)
        self.display(660,401,"Inspection notes",21,WHITE)
        if info.tracks:
            for index,track in enumerate(info.tracks[:4]):
                y=444+index*34
                self.display(80,y,f"{track.number:02}   "+("Data" if track.is_data else "Audio"),17,MUTED)
                self.display(604,y,_track_time(track.sector_count),17,MUTED,anchor="ne")
            if len(info.tracks)>4:self.display(80,581,f"{len(info.tracks)-4} additional tracks reported.",15,"#85858e")
        else:
            self.display(80,444,"Track positions are unavailable. The readable data volume is shown above.",17,MUTED,width=528)
        notes=" ".join(str(note) for note in info.notes)
        self.display(660,444,notes[:600] if notes else "The drive reported its media layout without additional notes.",16,MUTED,width=540)

        actions=[("disc:back","Back",self.back),("disc:other","Other drive",self.optical_dialog),
                 ("disc:refresh","Refresh",lambda:self._inspect_console_drive(info.drive.letter))]
        if source:actions.append(("disc:import","Import source",self._import_console_disc_source))
        actions.extend([("disc:volume","Export data",lambda:self._acquire(info.drive.letter)),
                        ("disc:raw","Capture CD tracks",lambda:self._raw_plan(info.drive.letter))])
        for index,(key,label,callback) in enumerate(actions):
            self.button_canvas(key,label,80+index*190,621,170,40,callback,key=="disc:import")
