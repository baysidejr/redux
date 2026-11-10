"""Automatic media presentation; native reads and artwork stay off Tk's thread."""
from __future__ import annotations

import queue
import threading
from pathlib import Path

from .library import SYSTEMS


def catalogue_disc_match(catalogue, identity):
    if catalogue is None or not identity.system:return None
    details=identity.details or {}
    region=details.get("area_symbols") or details.get("region")
    tag={"U":"USA","J":"Japan","E":"Europe","USA":"USA","Japan":"Japan","Europe":"Europe","North America":"USA"}.get(region)
    if identity.system=="saturn" and not tag:return None
    name=identity.title+(f" ({tag})" if tag else "")
    return catalogue.match(name,identity.system)


class ConsoleMedia:
    def firmware_directory(self):
        from .firmware import firmware_directory
        from .ui_assets import project_root
        return firmware_directory(self.preferences,project_root())

    def _start_media_watch(self):
        from .media_watch import MediaWatcher
        from .now_playing import DiscRegistry
        self.discs=DiscRegistry()
        self.media_results=queue.Queue()
        self.media_requests=queue.Queue()
        self.media_versions={}
        self.media_stopping=threading.Event()
        def enrich():
            from .disc_artwork import fetch_disc_artwork
            while not self.media_stopping.is_set():
                request=self.media_requests.get()
                if request is None:return
                version,identity,catalogue,cache=request
                try:
                    metadata=catalogue_disc_match(catalogue,identity) or {}
                    artwork=fetch_disc_artwork(cache,identity,metadata)
                    self.media_results.put((version,identity,metadata,artwork))
                except (OSError,ValueError,RuntimeError,ImportError):
                    # Detection remains useful without optional metadata/artwork.
                    self.media_results.put((version,identity,{},{}))
        self.media_worker=threading.Thread(target=enrich,name="disc-artwork",daemon=True)
        self.media_worker.start()
        self.media_watcher=MediaWatcher().start()

    def _enrich_disc(self,identity):
        version=self.media_versions.get(identity.drive,0)+1
        self.media_versions[identity.drive]=version
        self.media_requests.put((version,identity,self.catalogue,self.library.root/"cache"/"disc_artwork"))

    def _poll_media(self):
        for event in self.media_watcher.poll_events():
            changed=self.discs.apply(event)
            if event.kind in {"inserted","changed"} and event.identity:
                self._enrich_disc(event.identity)
            elif event.kind=="removed":
                self.media_versions[event.drive]=self.media_versions.get(event.drive,0)+1
            if changed:
                disc=self.discs.active
                if not self.busy and not self.embedded and not self.overlay:
                    self.status.set(f"{disc.title} • {disc.drive} detected" if disc else "Disc removed")
                self.schedule_draw()
        for _ in range(16):
            try:version,identity,metadata,artwork=self.media_results.get_nowait()
            except queue.Empty:break
            if self.media_versions.get(identity.drive)!=version:continue
            if self.discs.update(identity.drive,identity.fingerprint,metadata=metadata,artwork=artwork):
                self.schedule_draw()

    def _stop_media_watch(self):
        if hasattr(self,"media_stopping"):
            self.media_stopping.set();self.media_requests.put(None)
        if hasattr(self,"media_watcher"):self.media_watcher.close(timeout=.25)

    def inserted_disc(self):
        registry=getattr(self,"discs",None)
        return registry.active if registry else None

    def _disc_current(self,drive,fingerprint):
        disc=self.inserted_disc()
        if disc and (disc.drive,disc.fingerprint)==(drive,fingerprint):return disc
        self.notice("Disc changed","The selected disc is no longer active. Choose the current disc from Now Playing.")
        return None

    def inserted_disc_options(self):
        if self.busy:return
        disc=self.inserted_disc()
        if not disc:return
        drive,fingerprint=disc.drive,disc.fingerprint
        def current(action):
            if self._disc_current(drive,fingerprint):action()
        items=[("Play disc",lambda:current(self.play_inserted_disc)),("Install disc to library",lambda:current(self.install_inserted_disc)),
               ("Inspect disc tracks",lambda:current(lambda:self._inspect_console_drive(drive))),
               ("Game information",lambda:current(self.inserted_disc_information))]
        if disc.system:
            items.append(("Configure playback",lambda:current(lambda:self._disc_playback_menu(disc))))
        items.append(("Back",lambda:None))
        kind="Mounted image" if disc.is_virtual else "Disc inserted"
        self.menu(disc.title,items,message=f"{kind} • {drive} • {SYSTEMS.get(disc.system,'Unidentified system')}\nPlay uses a temporary disk buffer. Install keeps a library copy.")
        self.overlay["disc"]=disc

    def install_inserted_disc(self):
        disc=self.inserted_disc()
        if disc:self.install_disc_from_drive(disc.drive,disc=disc)

    def install_disc_from_drive(self,drive,*,disc=None):
        """Install directly into managed storage; destination choice is automatic."""
        if self.busy:return
        if self.embedded or self.playback and self.playback.active():self.game_menu();return
        if disc is None:
            current=self.inserted_disc()
            if current and current.drive==drive:disc=current
        library=self.library
        def work():
            from .disc_buffer import buffer_disc,DiscBufferCancelled
            from .disc_session import prepare_disc_game
            from .buffer_install import install_buffered_disc
            from .buffer_install import BufferInstallError
            from .raw_acquisition import RawCaptureCancelled
            from .library import ImportCancelled
            lease=None;installed=False
            def capture_progress(done,total,phase):
                self.events.put(("status",f"Installing disc • Reading and checking • {done*100//max(1,total)}%"))
            copied=0
            def copy_progress(count):
                nonlocal copied
                copied+=count
                self.events.put(("status",f"Installing disc • Saving to library • {copied//(1024*1024)} MiB"))
            try:
                lease=buffer_disc(drive,library.root/"cache"/"disc-install",cancelled=self.cancelled,
                    progress=capture_progress,expected_fingerprint=disc.fingerprint if disc else None)
                metadata=disc.metadata if disc else catalogue_disc_match(self.catalogue,lease.identity)
                game=prepare_disc_game(library,lease,metadata=metadata,cancelled=self.cancelled)
                result=install_buffered_disc(library,lease,game,cancelled=self.cancelled,progress=copy_progress)
                installed=True
                return result
            except (ImportCancelled,DiscBufferCancelled,RawCaptureCancelled):
                if lease and getattr(lease,"_redux_installed_game",None):raise
                return None
            except BufferInstallError as error:
                if error.installed_game:return dict(error.installed_game,_install_warning=str(error))
                raise
            finally:
                if lease:
                    if not installed and getattr(lease,"_redux_installed_game",None):
                        self.retained_disc_leases=getattr(self,"retained_disc_leases",[])+[lease]
                    elif lease.close() is False:
                        self.retained_disc_leases=getattr(self,"retained_disc_leases",[])+[lease]
                        self.events.put(("status","Temporary disc buffer retained: "+str(lease.entry_path)))
        def done(game):
            if game:
                self.selected_id=game["id"]
                self.refresh();self.status.set("Installed to library • ready to play")
                if game.get("_install_warning"):self.notice("Game installed • saves need attention",game["_install_warning"])
            else:self.status.set("Disc installation cancelled")
        self.task(work,done,"Installing disc to library…")

    def play_inserted_disc(self):
        if self.busy:return
        if self.embedded or self.playback and self.playback.active():self.game_menu();return
        disc=self.inserted_disc()
        if not disc:return
        from .embedded import find_core
        from .ui_assets import project_root
        core=find_core(project_root(),disc.system)
        if not core:
            self.offer_core_download(disc.system,self.play_inserted_disc);return
        if disc.system=="saturn":
            firmware=self.firmware_directory()
            names=("mpr-17933.bin",) if (disc.identity.details or {}).get("area_symbols") in {"U","E"} else ("sega_101.bin","mpr-17933.bin")
            if not firmware or not any((Path(firmware)/name).is_file() for name in names):
                self.menu("Saturn firmware needed",[("Choose firmware folder",self.choose_firmware),("Back",self.inserted_disc_options)],
                          message="Add compatible Saturn BIOS firmware ("+" or ".join(names)+") to "+str(firmware)+" and retry Play, or choose another firmware folder. Disc installation does not require firmware.")
                self.overlay["disc"]=disc;return
        def work():
            from .disc_buffer import buffer_disc
            from .disc_session import prepare_disc_game
            def progress(done,total,phase):self.events.put(("status",f"Preparing disc • {phase} • {done*100//max(1,total)}%"))
            lease=buffer_disc(disc.drive,self.library.root/"cache"/"disc-play",cancelled=self.cancelled,progress=progress,expected_fingerprint=disc.fingerprint)
            try:game=prepare_disc_game(self.library,lease,metadata=disc.metadata,cancelled=self.cancelled)
            except BaseException:
                lease.close();raise
            return lease,game
        def done(result):
            lease,game=result
            if self.cancelled.is_set() or getattr(self,"close_after_task",False):
                lease.close();self.status.set("Disc playback preparation cancelled");return
            self._start_integrated_game(game,core,media_lease=lease)
        self.task(work,done,"Buffering disc for playback…")

    def _disc_playback_menu(self,disc):
        self._backend_menu(disc.system)
        self.overlay["disc"]=disc

    def inserted_disc_information(self):
        disc=self.inserted_disc()
        if not disc:return
        details=disc.identity.details or {};metadata=disc.metadata
        facts=[SYSTEMS.get(disc.system,"Unidentified system"),f"Drive {disc.drive}"]
        if disc.game_id:facts.append(disc.game_id)
        region=metadata.get("region") or details.get("area_symbols") or details.get("region")
        if region:facts.append(str(region))
        if metadata.get("year"):facts.append(str(metadata["year"]))
        description=metadata.get("description") or "Identified directly from the inserted disc header."
        self.menu(disc.title,[("Back to disc options",self.inserted_disc_options)],message=" • ".join(facts)+"\n\n"+description)
        self.overlay["disc"]=disc
