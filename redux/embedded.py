"""Per-game storage and timed sessions over an independent libretro host."""
from datetime import datetime,timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .backend_rpc import StateSlot


from .core_profiles import PROFILES,default_options
CORE_FILES={system:profile.filename for system,profile in PROFILES.items()}
VALIDATED_DEFAULTS=frozenset({"nes","psx"})
# Software cores use the common host without individual emulator setup. These
# choices are separate from the systems with recorded game acceptance above.
HOSTED_DEFAULTS=frozenset({"nes","psx","snes","genesis","sms","game_gear","sega_cd","saturn","pce","pce_cd","supergrafx","gb","gbc","32x"})
_MAX_STATE_FILE=512*1024*1024+16384+13


def _slot_number(number):
    if isinstance(number,bool) or not isinstance(number,int) or not 0<=number<10:
        raise ValueError("State slot must be 0–9")
    return number


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError("Duplicate manifest field")
        result[key]=value
    return result


def _serialized(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self._lock:return method(self,*args,**kwargs)
    return call


def find_core(root,system):
    name=CORE_FILES.get(system)
    path=Path(root)/"tools"/"cores"/name if name else None
    return path if path and path.is_file() else None


class EmbeddedGame:
    def __init__(self,library,game,core,*,firmware=None,audio=True,_clock=None):
        from .libretro_host import EmbeddedSession
        key=game["bundle_hash"]
        if not isinstance(key,str) or not re.fullmatch(r"[a-f0-9]{64}",key):raise ValueError("Invalid game identity")
        if isinstance(game["id"],bool) or not isinstance(game["id"],int) or game["id"]<1:raise ValueError("Invalid game ID")
        self.game=dict(game);self.core=Path(core).resolve()
        with self.core.open("rb") as source:self.core_hash=hashlib.file_digest(source,"sha256").hexdigest()
        root=Path(library.root).resolve()
        self.directory=root/"game_data"/str(game["id"])/key
        if not self.directory.resolve().is_relative_to(root):raise ValueError("Game data resolves outside library")
        self.states=self.directory/"states"/self.core.stem
        self.screenshots=self.directory/"screenshots"
        for folder in (self.states,self.screenshots):
            if not folder.resolve().is_relative_to(self.directory):raise ValueError("Game output resolves outside its directory")
            folder.mkdir(parents=True,exist_ok=True)
        self.host=EmbeddedSession(self.core,library.path_for(game),firmware or self.directory/"system",self.directory/"battery"/self.core.stem,audio=audio,core_options=default_options(game.get("system")))
        self.started=None;self.paused_at=None;self.pause_seconds=0;self.finished_seconds=None
        self._clock=_clock or time.monotonic
        self._lock=threading.RLock()

    @_serialized
    def start(self):
        if self.started is not None or self.finished_seconds is not None:raise RuntimeError("An embedded game can only be started once")
        self.host.start();self.started=self._clock();return self

    @property
    @_serialized
    def play_seconds(self):
        if self.finished_seconds is not None:return self.finished_seconds
        if self.started is None:return 0
        endpoint=self.paused_at if self.paused_at is not None else self._clock()
        return max(0,endpoint-self.started-self.pause_seconds)

    @_serialized
    def pause(self):
        if self.started is None or self.finished_seconds is not None:raise RuntimeError("No embedded game is running")
        if self.paused_at is None:
            self.host.pause();self.paused_at=self._clock()

    @_serialized
    def resume(self):
        if self.started is None or self.finished_seconds is not None:raise RuntimeError("No embedded game is running")
        if self.paused_at is not None:
            self.host.resume();self.pause_seconds+=max(0,self._clock()-self.paused_at);self.paused_at=None

    @_serialized
    def stop(self):
        if self.finished_seconds is not None:return
        elapsed=self.play_seconds
        self.host.stop();self.finished_seconds=elapsed

    @_serialized
    def slot(self,number,verify=False):
        number=_slot_number(number)
        manifest=self.states/f"slot-{number}.json"
        if not manifest.exists():return StateSlot(number,False)
        try:
            if manifest.is_symlink():raise ValueError("Manifest is not a regular file")
            with manifest.open("rb") as source:data=source.read(8193)
            if len(data)>8192:raise ValueError("Manifest exceeds the size limit")
            item=json.loads(data,object_pairs_hook=_unique_object)
            digest=item["sha256"]
            if not isinstance(digest,str) or not re.fullmatch(r"[a-f0-9]{64}",digest):raise ValueError("Invalid state digest")
            size=item["size"]
            if (isinstance(size,bool) or not isinstance(size,int) or not 0<size<=_MAX_STATE_FILE or
                    type(item["schema"]) is not int or item["schema"]!=1 or
                    type(item["slot"]) is not int or item["slot"]!=number):
                raise ValueError("Invalid state manifest")
            if datetime.fromisoformat(item["saved_at"]).tzinfo is None:raise ValueError("State time has no time zone")
            path=self.states/(digest+".state")
            if item["game"]!=self.game["bundle_hash"] or item["core"]!=self.core_hash:raise ValueError("State belongs to another game or core version")
            if path.is_symlink() or not path.is_file() or path.stat().st_size!=item["size"]:raise ValueError("State data is incomplete")
            if verify:
                with path.open("rb") as source:
                    if hashlib.file_digest(source,"sha256").hexdigest()!=digest:raise ValueError("State checksum changed")
            return StateSlot(number,True,path,item["size"],item["saved_at"],digest)
        except (KeyError,OSError,TypeError,ValueError) as error:raise RuntimeError(f"Slot {number+1} is unavailable: {error}") from error

    @_serialized
    def slots(self):return [self.slot(n) for n in range(10)]

    @_serialized
    def save(self,number=0):
        number=_slot_number(number)
        temporary=self.states/(uuid.uuid4().hex+".state")
        staging=self.states/(uuid.uuid4().hex+".json.tmp")
        thumbnail_staging=self.states/(".thumb-"+uuid.uuid4().hex+".png")
        try:
            self.host.save_state(temporary)
            if temporary.is_symlink() or not temporary.is_file() or not 0<temporary.stat().st_size<=_MAX_STATE_FILE:
                raise RuntimeError("The core did not produce a supported state file")
            with temporary.open("rb") as source:digest=hashlib.file_digest(source,"sha256").hexdigest()
            destination=self.states/(digest+".state")
            if destination.exists():
                if destination.is_symlink():raise RuntimeError("An existing state blob is not a regular file")
                with destination.open("rb") as source:
                    if hashlib.file_digest(source,"sha256").hexdigest()!=digest:raise RuntimeError("An existing state blob has changed")
            else:temporary.replace(destination)
            item={"schema":1,"slot":number,"game":self.game["bundle_hash"],"core":self.core_hash,"sha256":digest,"size":destination.stat().st_size,"saved_at":datetime.now(timezone.utc).isoformat()}
            try:
                from PIL import Image
                self.host.save_screenshot(thumbnail_staging)
                with Image.open(thumbnail_staging) as image:
                    if image.format!="PNG" or image.width*image.height>4194304:raise ValueError("Invalid thumbnail")
                    image.verify()
                thumb_hash=hashlib.sha256(thumbnail_staging.read_bytes()).hexdigest()
                thumbnail=self.states/(thumb_hash+".png")
                if thumbnail.exists():
                    if thumbnail.is_symlink() or hashlib.sha256(thumbnail.read_bytes()).hexdigest()!=thumb_hash:raise ValueError("Thumbnail changed")
                else:thumbnail_staging.replace(thumbnail)
                item.update(thumbnail=thumbnail.name,thumbnail_sha256=thumb_hash)
            except (OSError,ValueError,RuntimeError):pass
            manifest=self.states/f"slot-{number}.json"
            with staging.open("x",encoding="utf-8") as output:
                json.dump(item,output,indent=2);output.flush();os.fsync(output.fileno())
            staging.replace(manifest)
            return self.slot(number)
        finally:
            temporary.unlink(missing_ok=True);staging.unlink(missing_ok=True);thumbnail_staging.unlink(missing_ok=True)

    @_serialized
    def load(self,number=0):
        slot=self.slot(number)
        if not slot.exists:raise ValueError("That save slot is empty")
        # Verify a copied snapshot, then load that exact snapshot so a changing
        # slot file cannot race the host's native deserialization.
        snapshot=self.states/(".load-"+uuid.uuid4().hex+".state")
        try:
            digest=hashlib.sha256();size=0
            with slot.path.open("rb") as source,snapshot.open("xb") as target:
                while chunk:=source.read(1024*1024):
                    size+=len(chunk)
                    if size>slot.size:raise RuntimeError("State changed before loading")
                    digest.update(chunk);target.write(chunk)
            if size!=slot.size or digest.hexdigest()!=slot.sha256:raise RuntimeError("State checksum changed before loading")
            self.host.load_state(snapshot);return slot
        finally:snapshot.unlink(missing_ok=True)

    @_serialized
    def capture(self):
        path=self.screenshots/(datetime.now().strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:8]+".png")
        return self.host.save_screenshot(path)
