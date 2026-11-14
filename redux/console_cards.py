"""Game cards in the console: a drive with a ``Redux Games`` folder is added on its own.

A drive connected while Redux runs is prepared first (marker folder, system folders and a
config folder per installed PC game), then read. Drives present at start-up are only read.
"""
from __future__ import annotations

from pathlib import Path
import queue
import threading


class ConsoleGameCards:
    def _start_card_watch(self):
        from .game_cards import find_cards, list_drives
        self.card_events=queue.Queue()
        self.card_roots=set()
        self.card_drives=None
        self.card_pending=[]
        self.card_stopping=threading.Event()
        def watch():
            seen=None
            while not self.card_stopping.is_set():
                try:
                    drives=[str(drive) for drive in list_drives()]
                    current=(frozenset(drives),frozenset(str(card) for card in find_cards(drives)))
                except OSError:current=seen
                if current is not None and current!=seen:
                    self.card_events.put(current);seen=current
                self.card_stopping.wait(3.0)
        self.card_watcher=threading.Thread(target=watch,name="game-cards",daemon=True)
        self.card_watcher.start()

    def _stop_card_watch(self):
        if hasattr(self,"card_stopping"):self.card_stopping.set()

    def _poll_cards(self):
        """UI-thread step: prepare drives that just arrived, queue cards, start one read when idle."""
        try:current=self.card_events.get_nowait()
        except (queue.Empty,AttributeError):current=None
        if current is not None:
            drives,cards=current
            arrived_drives=set() if self.card_drives is None else set(drives)-set(self.card_drives)
            self.card_drives=set(drives)
            cards=set(cards)
            for drive in sorted(arrived_drives):
                if drive not in {str(Path(card).parent) for card in cards}:
                    prepared=self._prepare_drive(drive)
                    if prepared is not None:cards.add(prepared)
            arrived=cards-self.card_roots;gone=self.card_roots-cards
            self.card_roots=cards
            for card in sorted(arrived):
                if card not in self.card_pending:self.card_pending.append(card)
            if gone:
                self.refresh()
                if not self.busy and not self.embedded and not self.overlay:self.status.set("Game card removed • its games are offline until it returns")
        if self.card_pending and self._cards_idle():self._sync_card(self.card_pending.pop(0))

    def _storage_ignored(self,drive):
        import os
        return os.path.normcase(str(drive)) in {os.path.normcase(str(root)) for root in self.preferences.get("storage_ignored",[])}

    def install_destination(self):
        """The extra-storage drive discs install to, when it is connected; None means internal storage."""
        value=self.preferences.get("install_destination","")
        if not value:return None
        from .game_cards import MARKER
        root=Path(value)
        return root if (root/MARKER).is_dir() and not self._storage_ignored(root) else None

    def storage_drives(self):
        """Every drive the Storage page lists, with what Redux knows about it."""
        import os
        from .game_cards import MARKER, can_prepare, drive_kind, drive_label, drive_usage, list_drives
        destination=os.path.normcase(str(self.preferences.get("install_destination","") or ""))
        result=[]
        for drive in list_drives():
            root=str(drive);kind=drive_kind(drive)
            if kind=="optical":
                result.append(dict(root=root,kind=kind,label=drive_label(drive),optical=True));continue
            if not can_prepare(drive):continue
            prefix=os.path.normcase(root)
            games=sum(1 for game in self.all_games if os.path.normcase(str(game.get("source_path") or game.get("path") or "")).startswith(prefix))
            result.append(dict(root=root,kind=kind,label=drive_label(drive),usage=drive_usage(drive),card=(Path(drive)/MARKER).is_dir(),
                               games=games,ignored=self._storage_ignored(drive),destination=prefix==destination and bool(destination),optical=False))
        return result

    def storage_menu(self,root):
        """What a connected drive can be for Redux."""
        from .game_cards import MARKER
        drive=Path(root);label=drive.anchor.rstrip(chr(92)+"/")
        is_card=(drive/MARKER).is_dir();ignored=self._storage_ignored(drive);destination=self.install_destination()
        items=[]
        if ignored:items.append(("Use as extra storage",lambda:self._use_storage(root)))
        elif not is_card:items.append(("Use as extra storage",lambda:self._use_storage(root)))
        else:
            if destination and str(destination)==str(drive):items.append(("Install discs to internal storage instead",lambda:self._set_install_destination(None)))
            else:items.append(("Install discs here",lambda:self._set_install_destination(root)))
            items.append(("Read its games again",lambda:self._use_storage(root)))
            items.append(("Stop using as extra storage",lambda:self._ignore_storage(root)))
        items.append(("Cancel",lambda:None))
        self.menu(f"External storage {label}",items,message="Extra storage holds games Redux plays in place. Stopping leaves every file where it is.")

    def _use_storage(self,root):
        ignored=[item for item in self.preferences.get("storage_ignored",[]) if str(item).casefold()!=str(root).casefold()]
        self.preferences["storage_ignored"]=ignored;self._save_preferences()
        prepared=self._prepare_drive(root,force=True)
        if prepared is None:self.notice("Drive unavailable","Redux could not write its folder on this drive.");return
        self.card_roots.add(prepared)
        if prepared not in self.card_pending:self.card_pending.insert(0,prepared)
        self.overlay=None;self._poll_cards();self.schedule_draw()

    def _ignore_storage(self,root):
        ignored=[item for item in self.preferences.get("storage_ignored",[]) if str(item).casefold()!=str(root).casefold()]+[str(root)]
        self.preferences["storage_ignored"]=ignored
        if str(self.preferences.get("install_destination","")).casefold()==str(root).casefold():self.preferences["install_destination"]=""
        self._save_preferences();self.status.set(f"{Path(root).anchor.rstrip(chr(92)+'/')} is no longer extra storage • its files stay where they are");self.schedule_draw()

    def _set_install_destination(self,root):
        self.preferences["install_destination"]="" if root is None else str(root)
        self._save_preferences()
        self.status.set("Discs install to internal storage" if root is None else f"Discs install to {Path(root).anchor.rstrip(chr(92)+'/')}")
        self.schedule_draw()

    def _prepare_drive(self,drive,force=False):
        """Write the marker structure on a newly connected data drive; None when it is not one."""
        from .game_cards import MARKER, can_prepare, prepare_card
        try:
            if not can_prepare(drive) or (not force and self._storage_ignored(drive)):return None
            prepare_card(drive)
            return str(Path(drive)/MARKER)
        except OSError:
            return None

    def _cards_idle(self):
        playing=bool(self.embedded) or bool(self.playback and self.playback.active())
        return not self.busy and not playing and not self.overlay and self.text_prompt is None

    def _sync_card(self,card):
        from .game_cards import sync_card, prepare_card, can_prepare
        library=self.library;catalogue=self.catalogue
        def progress(index,total,title):self.events.put(("status",f"Game card • adding {index}/{total} • {title}"))
        def work():
            drive=Path(card).parent
            prepared=[]
            try:
                if can_prepare(drive):prepared=prepare_card(drive)
            except OSError:prepared=[]
            result=sync_card(library,card,cancelled=self.cancelled,catalogue=catalogue,progress=progress)
            result.prepared=prepared
            return result
        def done(result):
            self.refresh();self.status.set(result.summary)
            if result.errors:self.notice("Some games on the card were skipped","\n".join(result.errors[:4]))
        self.task(work,done,f"Reading game card {Path(card).anchor.rstrip(chr(92)+'/')}…")

    def rescan_game_cards(self):
        """Prepare and read every connected data drive again, so new files and edited settings arrive."""
        if self.busy:return
        from .game_cards import MARKER, find_cards, list_drives, can_prepare
        drives=list_drives()
        cards={str(card) for card in find_cards(drives)}
        for drive in drives:
            if str(Path(drive)/MARKER) not in cards:
                prepared=self._prepare_drive(drive) if can_prepare(drive) else None
                if prepared:cards.add(prepared)
        cards={card for card in cards if not self._storage_ignored(Path(card).parent)}
        if not cards:
            self.notice("No game card connected","Connect an SD card or SSD. Redux creates its 'Redux Games' folder there, with one folder per system and a settings folder for each installed PC game it finds.")
            return
        self.card_roots|=cards
        self.card_pending=sorted(cards)+[card for card in self.card_pending if card not in cards]
        self._poll_cards()

    def game_card_summary(self):
        roots=sorted(getattr(self,"card_roots",()))
        if not roots:return "No extra storage connected • a connected SD card or SSD gets a 'Redux Games' folder and is read on its own"
        drives=", ".join(Path(root).anchor.rstrip(chr(92)+"/") for root in roots)
        return f"{len(roots)} extra storage drive{'' if len(roots)==1 else 's'} • {drives}"
