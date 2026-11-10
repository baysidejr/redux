"""Verified save-file archives with reversible, staged restore.

This backs up selected emulator save files. It does not fabricate emulator save
states, capture screenshots, or assume all emulators share a state format.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import threading
import uuid
import zipfile


class SaveError(RuntimeError):
    pass


class SaveCancelled(SaveError):
    pass


@dataclass(frozen=True)
class SaveBackup:
    id: str
    path: str
    game_id: int
    created_at: str
    label: str
    file_count: int
    total_bytes: int
    valid: bool = True
    error: str | None = None


@dataclass(frozen=True)
class RestoreResult:
    backup_id: str
    destination: str
    restored_files: int
    preserved_backup_id: str | None
    total_bytes: int


_ID = re.compile(r"([1-9][0-9]*)-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\Z")
_RESERVED = re.compile(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z")
_MAX_FILES = 10000
_MAX_BYTES = 8 * 1024**3
_MANIFEST_LIMIT = 4 * 1024**2


def _cancel(event):
    if event and event.is_set():raise SaveCancelled("Save-file operation cancelled")


def _relative(name):
    if not isinstance(name,str) or not name or "\\" in name or "\0" in name:
        raise SaveError("Invalid archive file path")
    parts=name.split("/")
    if any(p in {"",".",".."} or any(c in p for c in ':<>"|?*') or p.endswith((" ",".")) or
           _RESERVED.fullmatch(p) or any(ord(c)<32 for c in p) for p in parts):
        raise SaveError("Archive file path is unsafe or ambiguous")
    path=PurePosixPath(name)
    if path.is_absolute():raise SaveError("Archive paths must be relative")
    return path


def _inside(base,relative):
    path=base.joinpath(*_relative(relative).parts)
    if not path.resolve().is_relative_to(base.resolve()):
        raise SaveError("Save-file path leaves its selected directory")
    current=base
    for part in _relative(relative).parts:
        current=current/part
        if current.is_symlink() or (hasattr(current,"is_junction") and current.is_junction()):
            raise SaveError("Save paths through symlinks or junctions are unsupported")
    return path


def _cleanup(stage,parent):
    # Verify the fully resolved target before recursive cleanup on Windows.
    stage,parent=stage.resolve(),parent.resolve()
    if stage.parent!=parent or not stage.name.startswith(".redux-restore-"):
        raise SaveError("Refusing restore cleanup outside its staging directory")
    if stage.exists():shutil.rmtree(stage)


class SaveStore:
    def __init__(self,library_root):
        self.library_root=Path(library_root).expanduser().resolve()
        self.library_root.mkdir(parents=True,exist_ok=True)
        self.root=self.library_root/"save_backups"
        self.root.mkdir(exist_ok=True)
        if self.root.resolve().parent!=self.library_root:
            raise SaveError("Save backup directory must stay within this library")
        self._lock=threading.RLock()

    def _folder(self,game_id):
        if not isinstance(game_id,int) or isinstance(game_id,bool) or game_id<=0:
            raise ValueError("A positive game ID is required")
        folder=self.root/f"game-{game_id}"
        folder.mkdir(exist_ok=True)
        if folder.resolve().parent!=self.root.resolve():
            raise SaveError("Backup folder leaves the library")
        return folder

    def _archive_path(self,backup_id):
        if isinstance(backup_id,SaveBackup):backup_id=backup_id.id
        if not isinstance(backup_id,str) or not (match:=_ID.fullmatch(backup_id)):
            raise ValueError("Invalid save backup ID")
        path=self._folder(int(match.group(1)))/(backup_id+".zip")
        if path.is_symlink() or path.resolve().parent!=self._folder(int(match.group(1))).resolve():
            raise SaveError("Backup archive leaves its library folder")
        return path

    def _files(self,source,patterns):
        files=[]
        for directory,folders,names in os.walk(source,followlinks=False):
            parent=Path(directory)
            for name in folders:
                path=parent/name
                if path.is_symlink() or (hasattr(path,"is_junction") and path.is_junction()):
                    raise SaveError("Save folders containing links must be selected more narrowly")
            folders[:]=[n for n in folders if not n.startswith(".redux-restore-")]
            for name in names:
                path=parent/name
                relative=path.relative_to(source).as_posix()
                if patterns and not any(fnmatch.fnmatchcase(name.casefold(),p.casefold()) or
                    fnmatch.fnmatchcase(relative.casefold(),p.casefold()) for p in patterns):continue
                checked=_inside(source,relative)
                if not stat.S_ISREG(checked.stat(follow_symlinks=False).st_mode):
                    raise SaveError("Only regular save files can be backed up")
                files.append(checked)
        return sorted(files,key=lambda p:p.relative_to(source).as_posix().casefold())

    def backup(self,game_id,source_directory,*,label="",patterns=None,cancelled=None):
        """Archive selected files; patterns are filename/relative-path globs.

        For a portable PCSX-Redux root select ['*.mcd','*.sstate*'], not its
        executable/firmware folder wholesale. Other save folders can use None.
        """
        source=Path(source_directory).expanduser().resolve(strict=True)
        if not source.is_dir():raise SaveError("Select an emulator save directory")
        if self.root.resolve().is_relative_to(source):
            raise SaveError("The backup source cannot contain this library's backup store")
        if patterns is not None and (not isinstance(patterns,(list,tuple)) or not patterns or not all(isinstance(p,str) and p for p in patterns)):
            raise ValueError("Save patterns must be a list of nonempty strings")
        with self._lock:
            return self._backup_files(game_id,source,self._files(source,patterns),label,cancelled)

    def _backup_files(self,game_id,source,files,label,cancelled):
        if not files:raise SaveError("No matching save files were found")
        if len(files)>_MAX_FILES:raise SaveError("Too many save files for one backup")
        folder=self._folder(game_id)
        now=datetime.now(timezone.utc)
        backup_id=f"{game_id}-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
        destination=folder/(backup_id+".zip")
        temporary=folder/("."+backup_id+".tmp")
        records=[];seen=set();total=0
        try:
            with zipfile.ZipFile(temporary,"x",zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
                for path in files:
                    _cancel(cancelled)
                    relative=path.relative_to(source).as_posix();path=_inside(source,relative)
                    if relative.casefold() in seen:raise SaveError("Case-ambiguous save filenames are unsupported")
                    seen.add(relative.casefold())
                    before=path.stat();digest=hashlib.sha256();written=0
                    with path.open("rb") as reader,archive.open("files/"+relative,"w",force_zip64=True) as writer:
                        while chunk:=reader.read(1024*1024):
                            _cancel(cancelled);written+=len(chunk);total+=len(chunk)
                            if total>_MAX_BYTES:raise SaveError("Save backup exceeds the 8 GiB limit")
                            writer.write(chunk);digest.update(chunk)
                    after=path.stat()
                    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or written!=before.st_size:
                        raise SaveError("A save changed during backup; retry after stopping the emulator")
                    records.append({"path":relative,"size":written,"sha256":digest.hexdigest()})
                manifest={"format":"redux-save-backup","version":1,"id":backup_id,"game_id":game_id,
                    "created_at":now.isoformat(),"label":str(label).strip()[:120],"source_directory":str(source),"files":records}
                archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
            _cancel(cancelled)
            self._validate(temporary,verify=True)
            # Windows _commit requires a writable descriptor, even when the ZIP
            # writer has already closed its completed file.
            with temporary.open("r+b") as saved:os.fsync(saved.fileno())
            os.replace(temporary,destination)
            return self._describe(destination,manifest)
        finally:
            if temporary.exists():temporary.unlink()

    def _validate(self,path,*,verify=False,cancelled=None):
        try:
            with zipfile.ZipFile(path) as archive:
                infos=archive.infolist()
                if len(infos)>_MAX_FILES+1:raise SaveError("Archive contains too many entries")
                names=[i.filename for i in infos]
                if len(names)!=len(set(n.casefold() for n in names)):
                    raise SaveError("Archive has duplicate or case-ambiguous paths")
                if "manifest.json" not in names:raise SaveError("Archive has no Redux save manifest")
                if archive.getinfo("manifest.json").file_size>_MANIFEST_LIMIT:raise SaveError("Backup manifest is too large")
                manifest=json.loads(archive.read("manifest.json"))
                if not isinstance(manifest,dict):raise SaveError("Invalid save archive manifest")
                if manifest.get("format")!="redux-save-backup" or manifest.get("version")!=1:
                    raise SaveError("Unsupported save archive format")
                if not isinstance(manifest.get("game_id"),int) or isinstance(manifest["game_id"],bool) or manifest["game_id"]<=0:
                    raise SaveError("Invalid game ID in save archive")
                if not isinstance(manifest.get("id"),str) or not _ID.fullmatch(manifest["id"]):
                    raise SaveError("Invalid identity in save archive")
                if int(manifest["id"].split("-",1)[0])!=manifest["game_id"]:
                    raise SaveError("Save archive identity does not match its game")
                records=manifest.get("files")
                if not isinstance(records,list) or not 0<len(records)<=_MAX_FILES:raise SaveError("Invalid save-file manifest")
                expected={"manifest.json"};seen=set();total=0
                for record in records:
                    if not isinstance(record,dict):raise SaveError("Invalid save-file record")
                    relative=record.get("path");_relative(relative)
                    if relative.casefold() in seen:raise SaveError("Duplicate save-file manifest path")
                    seen.add(relative.casefold())
                    size=record.get("size");digest=record.get("sha256")
                    if not isinstance(size,int) or isinstance(size,bool) or size<0 or not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest):
                        raise SaveError("Invalid save-file checksum or size")
                    total+=size
                    if total>_MAX_BYTES:raise SaveError("Archive exceeds the save-size limit")
                    name="files/"+relative;expected.add(name)
                    try:info=archive.getinfo(name)
                    except KeyError as error:raise SaveError("Save-file payload is missing") from error
                    if info.is_dir() or info.file_size!=size or (info.external_attr>>16)&0o170000==stat.S_IFLNK:
                        raise SaveError("Save payload metadata does not match the manifest")
                    if verify:
                        actual=hashlib.sha256();read=0
                        with archive.open(info) as stream:
                            while chunk:=stream.read(1024*1024):
                                _cancel(cancelled);read+=len(chunk);actual.update(chunk)
                        if read!=size or actual.hexdigest()!=digest:raise SaveError("Save payload checksum mismatch")
                if set(names)!=expected:raise SaveError("Archive contains unlisted entries")
                return manifest
        except (zipfile.BadZipFile,json.JSONDecodeError,UnicodeError,KeyError,TypeError,ValueError) as error:
            if isinstance(error,SaveError):raise
            raise SaveError(f"Invalid save archive: {error}") from error

    @staticmethod
    def _describe(path,manifest):
        return SaveBackup(manifest["id"],str(path),manifest["game_id"],str(manifest.get("created_at","")),
            str(manifest.get("label","")),len(manifest["files"]),sum(r["size"] for r in manifest["files"]))

    def list_backups(self,game_id=None):
        folders=[self._folder(game_id)] if game_id is not None else [p for p in self.root.glob("game-*") if p.is_dir()]
        result=[]
        for folder in folders:
            if folder.resolve().parent!=self.root.resolve() or not re.fullmatch(r"game-[1-9][0-9]*",folder.name):continue
            for path in folder.glob("*.zip"):
                if path.is_symlink() or not _ID.fullmatch(path.stem):continue
                try:
                    manifest=self._validate(path)
                    if manifest["id"]!=path.stem or manifest["game_id"]!=int(folder.name[5:]):
                        raise SaveError("Backup filename and identity disagree")
                    result.append(self._describe(path,manifest))
                except (OSError,SaveError) as error:
                    result.append(SaveBackup(path.stem,str(path),int(folder.name[5:]),"","Unreadable backup",0,0,False,str(error)))
        return sorted(result,key=lambda b:b.id,reverse=True)

    def verify(self,backup_id):
        path=self._archive_path(backup_id)
        manifest=self._validate(path,verify=True)
        if manifest["id"]!=path.stem:raise SaveError("Backup filename and identity disagree")
        return self._describe(path,manifest)

    def restore(self,backup_id,destination,*,overwrite=False,cancelled=None):
        """Restore only listed files; unrelated destination files are retained.

        Stop the emulator first. overwrite=True explicitly permits replacement;
        existing matching files are verified into a separate undo backup first.
        A failed multi-file publish rolls all touched paths back before cleanup.
        """
        with self._lock:
            archive_path=self._archive_path(backup_id)
            manifest=self._validate(archive_path,verify=True,cancelled=cancelled)
            if manifest["id"]!=archive_path.stem:raise SaveError("Backup identity mismatch")
            target=Path(destination).expanduser().resolve()
            if target.is_relative_to(self.root.resolve()) or self.root.resolve().is_relative_to(target):
                raise SaveError("Restore destination cannot contain or be inside the backup store")
            target.mkdir(parents=True,exist_ok=True)
            if not target.is_dir():raise SaveError("Restore destination must be a directory")
            records=manifest["files"]
            old=[]
            for record in records:
                path=_inside(target,record["path"])
                if path.exists():
                    if not path.is_file():raise SaveError("A destination path is not a regular file")
                    old.append(path)
            if old and not overwrite:raise SaveError("Destination already has matching save files; explicit overwrite is required")
            stage=target/(".redux-restore-"+uuid.uuid4().hex)
            stage.mkdir();published=[];moved_old=[];can_cleanup=True;preserved=None
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    for record in records:
                        _cancel(cancelled)
                        staged=_inside(stage/"new",record["path"]);staged.parent.mkdir(parents=True,exist_ok=True)
                        with archive.open("files/"+record["path"]) as reader,staged.open("xb") as writer:
                            while chunk:=reader.read(1024*1024):_cancel(cancelled);writer.write(chunk)
                            writer.flush();os.fsync(writer.fileno())
                if old:
                    preserved=self._backup_files(manifest["game_id"],target,old,"Before restore "+manifest["id"],cancelled)
                _cancel(cancelled)
                try:
                    for record in records:
                        path=_inside(target,record["path"])
                        path.parent.mkdir(parents=True,exist_ok=True)
                        if path.exists():
                            previous=_inside(stage/"old",record["path"]);previous.parent.mkdir(parents=True,exist_ok=True)
                            os.replace(path,previous);moved_old.append((path,previous))
                        os.replace(_inside(stage/"new",record["path"]),path);published.append(path)
                except BaseException:
                    try:
                        for path in reversed(published):
                            if path.exists():path.unlink()
                        for path,previous in reversed(moved_old):os.replace(previous,path)
                    except OSError as error:
                        can_cleanup=False
                        raise SaveError(f"Restore rollback needs recovery from {stage}: {error}") from error
                    raise
                return RestoreResult(manifest["id"],str(target),len(records),preserved.id if preserved else None,
                    sum(r["size"] for r in records))
            finally:
                if can_cleanup:_cleanup(stage,target)
