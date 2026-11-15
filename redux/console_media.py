"""Automatic media presentation; native reads and database lookups stay off Tk's thread."""
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
            # A disc is named by its header (and a workspace catalogue when one is configured).
            # The shipped database is a place to browse, not something a disc borrows from.
            while not self.media_stopping.is_set():
                request=self.media_requests.get()
                if request is None:return
                version,identity,catalogue=request
                try:
                    metadata=catalogue_disc_match(catalogue,identity) or {}
                    self.media_results.put((version,identity,metadata,{}))
                except (OSError,ValueError,RuntimeError,ImportError,TypeError,AttributeError):
                    # Detection remains useful without optional metadata/artwork.
                    self.media_results.put((version,identity,{},{}))
        self.media_worker=threading.Thread(target=enrich,name="disc-artwork",daemon=True)
        self.media_worker.start()
        self.media_watcher=MediaWatcher().start()

    def _enrich_disc(self,identity):
        version=self.media_versions.get(identity.drive,0)+1
        self.media_versions[identity.drive]=version
        self.media_requests.put((version,identity,self.catalogue))

    def _poll_media(self):
        for event in self.media_watcher.poll_events():
            changed=self.discs.apply(event)
            if event.kind in {"inserted","changed"} and event.identity:
                self._enrich_disc(event.identity);self.note_disc_insert(event)
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
        self.notice("Disc changed","The selected disc is no longer active. Choose the current disc from My Collection.")
        return None

    def inserted_disc_options(self):
        if self.busy:return
        disc=self.inserted_disc()
        if not disc:return
        drive,fingerprint=disc.drive,disc.fingerprint
        def current(action):
            if self._disc_current(drive,fingerprint):action()
        items=[("Play",lambda:current(self.play_disc)),("Game information",lambda:current(self.inserted_disc_information))]
        if disc.system:
            items.append(("Configure playback",lambda:current(lambda:self._disc_playback_menu(disc))))
        items.append(("Back",lambda:None))
        kind="Mounted image" if disc.is_virtual else "Disc inserted"
        state="Installed • Play starts it" if self.installed_disc_game(disc) else "Not installed • Play installs it first, then starts it"
        self.menu(disc.title,items,message=f"{kind} • {drive} • {SYSTEMS.get(disc.system,'Unidentified system')}\n{state}")
        self.overlay["disc"]=disc

    def install_disc_from_drive(self,drive,*,disc=None,then_play=False):
        """Install directly into managed storage (or the chosen extra storage); with ``then_play`` the
        game starts as soon as the install finishes. The disc's fingerprint is recorded on the game."""
        if self.busy:return
        if self.embedded or self.playback and self.playback.active():self.game_menu();return
        if disc is None:
            current=self.inserted_disc()
            if current and current.drive==drive:disc=current
        library=self.library;destination=self.install_destination()
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
                result=install_buffered_disc(library,lease,game,cancelled=self.cancelled,progress=copy_progress,destination=destination)
                installed=True
                fingerprint=disc.fingerprint if disc else getattr(getattr(lease,"identity",None),"fingerprint",None)
                if fingerprint and result:
                    try:result=library.set_disc_fingerprint(result["id"],fingerprint)
                    except (OSError,ValueError,AttributeError,KeyError):pass
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
                self.selected_id=game["id"];self.__dict__.pop("_disc_installed",None)
                self.refresh();self.status.set("Installed to library • ready to play")
                if game.get("_install_warning"):self.notice("Game installed • saves need attention",game["_install_warning"])
                elif then_play:self.play_installed(game)
            else:self.status.set("Disc installation cancelled")
        self.task(work,done,"Installing disc • it starts when the copy is done…" if then_play else "Installing disc to library…")

    # ------------------------------------------------------------------ one button: Play
    def installed_disc_game(self,disc):
        """The library game this inserted disc was installed as, or None.

        A disc installed before fingerprints were kept is matched by system and title, and
        the fingerprint is recorded then, so the next insert is exact."""
        if not disc:return None
        cache=self.__dict__.setdefault("_disc_installed",{})
        key=(disc.fingerprint,len(getattr(self,"all_games",()) or ()))
        if key in cache:
            identity=cache[key]
            if identity is None:return None
            try:return self.library.game(identity)
            except (KeyError,ValueError,OSError,AttributeError):cache.pop(key,None)
        try:game,exact=self.library.find_disc_game(disc.fingerprint,system=disc.system,title=disc.title)
        except (OSError,ValueError,AttributeError,TypeError):game,exact=None,False
        if game and not exact:
            try:game=self.library.set_disc_fingerprint(game["id"],disc.fingerprint)
            except (OSError,ValueError,AttributeError):pass
        cache.clear();cache[key]=game["id"] if game else None
        return game

    def play_disc(self):
        """A on an inserted disc: start the installed copy, or install the disc first and then start it."""
        if self.busy:return
        if self.embedded or self.playback and self.playback.active():self.game_menu();return
        disc=self.inserted_disc()
        if not disc:return
        installed=self.installed_disc_game(disc)
        if installed:
            self.overlay=None
            self.play_installed(installed);return
        self.install_disc_from_drive(disc.drive,disc=disc,then_play=True)

    def play_installed(self,game):
        """Start an installed game through the ordinary Play path: cores, firmware and the disc check."""
        self.selected_id=game["id"]
        self.play(game)

    def disc_requirement(self,game):
        """Why an installed disc game cannot start now, or None.

        A game installed from a disc plays only with that disc in the drive, checked once at
        Play. Games on cards and PC games never pass through here. A managed CD game from
        before fingerprints were kept takes the matching disc by system and title and records it."""
        from .game_cards import CD_SYSTEMS
        from .library import loose_title
        fingerprint=str(game.get("disc_fingerprint") or "")
        legacy=not fingerprint and game.get("storage")=="managed" and game.get("system") in CD_SYSTEMS
        if not fingerprint and not legacy:return None
        disc=self.inserted_disc();title=game.get("title") or "this game"
        if not disc:return f"Put the {title} disc in the drive to play it."
        if fingerprint:
            return None if disc.fingerprint==fingerprint else f"The disc in the drive is {disc.title}. Put the {title} disc in to play it."
        if disc.system==game.get("system") and loose_title(disc.title)==loose_title(title):
            try:self.library.set_disc_fingerprint(game["id"],disc.fingerprint)
            except (OSError,ValueError,AttributeError):pass
            return None
        return f"The disc in the drive is {disc.title}. Put the {title} disc in to play it."

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
