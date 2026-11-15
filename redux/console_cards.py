"""Game cards in the console: a prepared drive is read on its own; preparing one is the player's choice.

A drive that arrives with no ``Redux Games`` folder gets one question: console cartridge, GOG
cartridge, extra storage, or not now. Nothing is written to it before the answer, and Not now
keeps Redux from asking about that drive again. A drive that carries a ``bios`` folder with
known firmware files gets a second question, to copy them (console_cores.py). Drives present
at start-up are only read; System → Storage offers the same choice for any drive at any time.
"""
from __future__ import annotations

from pathlib import Path
import queue
import threading


def _letter(root):
    return Path(root).anchor.rstrip(chr(92)+"/") or str(root)


class ConsoleGameCards:
    def _start_card_watch(self):
        from .game_cards import find_cards, list_drives
        self.card_events=queue.Queue()
        self.card_roots=set()
        self.card_drives=None
        self.card_pending=[]
        self.card_offers=[]
        self.firmware_offers=[]
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
        """UI-thread step: queue prepared cards for reading, offer the choice for new bare drives, start one thing when idle."""
        from .game_cards import can_prepare
        try:current=self.card_events.get_nowait()
        except (queue.Empty,AttributeError):current=None
        if current is not None:
            drives,cards=current
            arrived_drives=set() if self.card_drives is None else set(drives)-set(self.card_drives)
            self.card_drives=set(drives)
            cards=set(cards)
            card_drives={str(Path(card).parent) for card in cards}
            from .firmware_files import find_firmware_folder, known_files_in
            for drive in sorted(arrived_drives):
                try:
                    bare=drive not in card_drives and can_prepare(drive) and not self._storage_ignored(drive)
                    firmware=find_firmware_folder(drive) if can_prepare(drive) and not self._firmware_drive_ignored(drive) else None
                except OSError:bare,firmware=False,None
                if bare and drive not in self.card_offers:self.card_offers.append(drive)
                if firmware and all(offer[0]!=drive for offer in getattr(self,"firmware_offers",[])):
                    self.firmware_offers.append((drive,firmware,known_files_in(firmware)))
            self.card_offers=[drive for drive in self.card_offers if drive in self.card_drives]
            self.firmware_offers=[offer for offer in getattr(self,"firmware_offers",[]) if offer[0] in self.card_drives]
            arrived=cards-self.card_roots;gone=self.card_roots-cards
            self.card_roots=cards
            for card in sorted(arrived):
                if card not in self.card_pending:self.card_pending.append(card)
            self._storage_cache=None
            if gone:
                self.refresh()
                if not self.busy and not self.embedded and not self.overlay:self.status.set("Game card removed • its games are offline until it returns")
        if not self._cards_idle():return
        if self.card_pending:self._sync_card(self.card_pending.pop(0))
        elif self.card_offers:self.offer_drive(self.card_offers.pop(0))
        elif getattr(self,"firmware_offers",None):self.offer_firmware(*self.firmware_offers.pop(0))

    # ------------------------------------------------------------------ the one question
    def offer_drive(self,root):
        """Ask what a newly connected bare drive is for. Nothing is written until the player answers."""
        from .game_cards import KIND_LABELS, drive_label
        if not Path(root).exists():return
        label=drive_label(root);name=f"{label} ({_letter(root)})" if label else f"Drive {_letter(root)}"
        items=[(KIND_LABELS[kind],lambda kind=kind:self._use_storage(root,kind)) for kind in ("console","gog","storage")]
        items.append(("Not now",lambda:self._ignore_storage(root,quiet=True)))
        self.menu("New drive • "+name,items,message="Console cartridge: folders for cartridge games you copy on. GOG cartridge: the GOG games installed on this drive. "
                  "Extra storage: where discs install. Nothing is written until you choose; Not now leaves the drive alone (System → Storage can set it up later).")

    def _storage_ignored(self,drive):
        import os
        from .game_cards import drive_identity
        ignored={os.path.normcase(str(item)) for item in self.preferences.get("storage_ignored",[])}
        return os.path.normcase(str(drive)) in ignored or os.path.normcase(drive_identity(drive)) in ignored

    def install_destination(self):
        """The extra-storage drive discs install to, when it is connected; None means internal storage."""
        value=self.preferences.get("install_destination","")
        if not value:return None
        from .game_cards import MARKER, card_kind
        root=Path(value)
        return root if card_kind(root/MARKER) in ("storage","legacy") and not self._storage_ignored(root) else None

    def storage_drives(self,*,max_age=3.0):
        """Every drive the Storage page lists, with what Redux knows about it.

        Drive labels and free space are Windows calls that take tens of
        milliseconds each; the answer is kept for a few seconds so moving the
        focus on the Storage page stays instant. Card events refresh it.
        """
        import os,time
        cached=getattr(self,"_storage_cache",None)
        if cached and time.monotonic()-cached[0]<max_age and cached[1]==(len(self.all_games),str(self.preferences.get("install_destination","")),tuple(self.preferences.get("storage_ignored",()) or ())):
            return [dict(item) for item in cached[2]]
        result=self._storage_drives_now()
        self._storage_cache=(time.monotonic(),(len(self.all_games),str(self.preferences.get("install_destination","")),tuple(self.preferences.get("storage_ignored",()) or ())),[dict(item) for item in result])
        return result

    def _storage_drives_now(self):
        import os
        from .game_cards import MARKER, can_prepare, card_kind, drive_kind, drive_label, drive_usage, list_drives
        destination=os.path.normcase(str(self.preferences.get("install_destination","") or ""))
        result=[]
        for drive in list_drives():
            root=str(drive);kind=drive_kind(drive)
            if kind=="optical":
                result.append(dict(root=root,kind=kind,label=drive_label(drive),optical=True));continue
            if not can_prepare(drive):continue
            prefix=os.path.normcase(root)
            games=sum(1 for game in self.all_games if os.path.normcase(str(game.get("source_path") or game.get("path") or "")).startswith(prefix))
            card=card_kind(Path(drive)/MARKER)
            result.append(dict(root=root,kind=kind,label=drive_label(drive),usage=drive_usage(drive),card=card is not None,card_kind=card,
                               games=games,ignored=self._storage_ignored(drive),destination=prefix==destination and bool(destination),optical=False))
        return result

    def storage_menu(self,root):
        """What a connected drive is, or can become, for Redux."""
        from .game_cards import MARKER, KIND_LABELS, card_kind
        drive=Path(root);letter=_letter(root)
        kind=card_kind(drive/MARKER);ignored=self._storage_ignored(drive);destination=self.install_destination()
        if kind is None:
            items=[(KIND_LABELS[choice],lambda choice=choice:self._use_storage(root,choice)) for choice in ("console","gog","storage")]+[("Cancel",lambda:None)]
            self.menu(f"Set up drive {letter}",items,message="Console cartridge: folders for cartridge games you copy on. GOG cartridge: the GOG games installed on this drive. "
                      "Extra storage: where discs install. Redux writes its 'Redux Games' folder only for the kind you choose.")
            return
        title=f"{KIND_LABELS.get(kind,'Game card')} {letter}"
        if ignored:
            self.menu(title,[("Use this drive again",lambda:self._use_storage(root,kind)),("Cancel",lambda:None)],
                      message="Redux is leaving this drive alone. Using it again reads its games; nothing on it changes.")
            return
        items=[]
        if kind in ("storage","legacy"):
            if destination and str(destination)==str(drive):items.append(("Install discs to internal storage instead",lambda:self._set_install_destination(None)))
            else:items.append(("Install discs here",lambda:self._set_install_destination(root)))
        items.append(("Read its games again",lambda:self._use_storage(root,kind)))
        items.append(("Stop using this drive",lambda:self._ignore_storage(root)))
        items.append(("Cancel",lambda:None))
        messages={"console":"A console cartridge holds cartridge games in one folder per system; Redux plays them in place.",
                  "gog":"A GOG cartridge holds installed GOG games; reading it again picks up new installs and edited settings.",
                  "storage":"Extra storage holds the discs you install; Redux plays them in place."}
        self.menu(title,items,message=messages.get(kind,"This card was prepared by an earlier Redux and is read as before.")+" Stopping leaves every file where it is.")

    def _use_storage(self,root,kind=None):
        """Prepare a bare drive as ``kind`` (or just read a prepared one again) and queue it for reading."""
        from .game_cards import MARKER, KINDS, KIND_LABELS, drive_identity
        keep={str(root).casefold(),drive_identity(root).casefold()}
        self.preferences["storage_ignored"]=[item for item in self.preferences.get("storage_ignored",[]) if str(item).casefold() not in keep]
        self._save_preferences()
        if (Path(root)/MARKER).is_dir():prepared=str(Path(root)/MARKER)
        else:
            if kind not in KINDS:return
            prepared=self._prepare_drive(root,kind)
            if prepared is None:self.notice("Drive unavailable","Redux could not write its folder on this drive.");return
            self.status.set(f"{_letter(root)} set up as {KIND_LABELS[kind].lower()}")
        self.card_roots.add(prepared)
        if prepared not in self.card_pending:self.card_pending.insert(0,prepared)
        self.overlay=None;self._poll_cards();self.schedule_draw()

    def _ignore_storage(self,root,quiet=False):
        from .game_cards import drive_identity
        identity=drive_identity(root)
        ignored=[item for item in self.preferences.get("storage_ignored",[]) if str(item).casefold() not in {str(root).casefold(),identity.casefold()}]+[identity]
        self.preferences["storage_ignored"]=ignored
        if str(self.preferences.get("install_destination","")).casefold()==str(root).casefold():self.preferences["install_destination"]=""
        self._save_preferences()
        self.status.set(f"{_letter(root)} left alone • System → Storage can set it up later" if quiet else f"{_letter(root)} is no longer used by Redux • its files stay where they are")
        self.schedule_draw()

    def _set_install_destination(self,root):
        self.preferences["install_destination"]="" if root is None else str(root)
        self._save_preferences()
        self.status.set("Discs install to internal storage" if root is None else f"Discs install to {_letter(root)}")
        self.schedule_draw()

    def _prepare_drive(self,drive,kind):
        """Write the card structure of one kind on a data drive the player chose; None when it cannot be written."""
        from .game_cards import MARKER, can_prepare, prepare_card
        try:
            if not can_prepare(drive):return None
            prepare_card(drive,kind)
            return str(Path(drive)/MARKER)
        except (OSError,ValueError):
            return None

    def _cards_idle(self):
        playing=bool(self.embedded) or bool(self.playback and self.playback.active())
        return not self.busy and not playing and not self.overlay and self.text_prompt is None

    def _sync_card(self,card):
        """Read a prepared card. Only a GOG cartridge is written to, and only to add settings for new installs."""
        from .game_cards import sync_card, write_pc_configs, card_kind
        library=self.library;catalogue=self.catalogue
        def progress(index,total,title):self.events.put(("status",f"Game card • adding {index}/{total} • {title}"))
        def work():
            prepared=[]
            if card_kind(card)=="gog":
                try:prepared=write_pc_configs(Path(card).parent)
                except OSError:prepared=[]
            result=sync_card(library,card,cancelled=self.cancelled,catalogue=catalogue,progress=progress)
            result.prepared=prepared
            return result
        def done(result):
            self.refresh();self.status.set(result.summary)
            if result.errors:self.notice("Some games on the card were skipped","\n".join(result.errors[:4]))
        self.task(work,done,f"Reading game card {_letter(card)}…")

    def rescan_game_cards(self):
        """Read every prepared, connected drive again, so new files and edited settings arrive."""
        if self.busy:return
        from .game_cards import find_cards, list_drives
        cards={str(card) for card in find_cards(list_drives()) if not self._storage_ignored(Path(card).parent)}
        if not cards:
            self.notice("No game card connected","Connect an SD card or SSD and choose what it is for: a console cartridge, a GOG cartridge or extra storage. System → Storage offers the same choice for any drive.")
            return
        self.card_roots|=cards
        self.card_pending=sorted(cards)+[card for card in self.card_pending if card not in cards]
        self._poll_cards()

    def game_card_summary(self):
        roots=sorted(getattr(self,"card_roots",()))
        if not roots:return "No game card connected • a connected SD card or SSD is offered as a console cartridge, GOG cartridge or extra storage"
        drives=", ".join(_letter(root) for root in roots)
        return f"{len(roots)} game card{'' if len(roots)==1 else 's'} • {drives}"
