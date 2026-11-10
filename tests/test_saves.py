import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from redux.saves import SaveCancelled, SaveError, SaveStore


class SaveStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        self.source=self.root/"emulator";self.source.mkdir()
        self.store=SaveStore(self.root/"library")
        (self.source/"card.mcd").write_bytes(b"memory-card-original")
        (self.source/"states").mkdir();(self.source/"states"/"slot.sstate0").write_bytes(b"state-original")

    def tearDown(self):self.temporary.cleanup()

    def test_verified_backup_restore_and_patterns_leave_unrelated_files(self):
        (self.source/"firmware.bin").write_bytes(b"not-a-save")
        backup=self.store.backup(7,self.source,label="Before boss",patterns=["*.mcd","*.sstate*"])
        self.assertEqual(backup.file_count,2);self.assertEqual(self.store.verify(backup.id).label,"Before boss")
        target=self.root/"restored";target.mkdir();(target/"unrelated.txt").write_text("keep me")
        result=self.store.restore(backup.id,target)
        self.assertEqual(result.restored_files,2);self.assertIsNone(result.preserved_backup_id)
        self.assertEqual((target/"card.mcd").read_bytes(),b"memory-card-original")
        self.assertEqual((target/"states"/"slot.sstate0").read_bytes(),b"state-original")
        self.assertFalse((target/"firmware.bin").exists());self.assertEqual((target/"unrelated.txt").read_text(),"keep me")
        self.assertEqual(len(self.store.list_backups()),1)

    def test_overwrite_requires_explicit_choice_and_preserves_verified_undo(self):
        backup=self.store.backup(7,self.source)
        (self.source/"card.mcd").write_bytes(b"newer-progress")
        with self.assertRaises(SaveError):self.store.restore(backup.id,self.source)
        self.assertEqual((self.source/"card.mcd").read_bytes(),b"newer-progress")
        result=self.store.restore(backup.id,self.source,overwrite=True)
        self.assertEqual((self.source/"card.mcd").read_bytes(),b"memory-card-original")
        self.assertIsNotNone(result.preserved_backup_id);self.store.verify(result.preserved_backup_id)
        undo_target=self.root/"undo";self.store.restore(result.preserved_backup_id,undo_target)
        self.assertEqual((undo_target/"card.mcd").read_bytes(),b"newer-progress")

    def test_failed_publish_rolls_back_every_original_and_retains_undo_archive(self):
        backup=self.store.backup(7,self.source)
        (self.source/"card.mcd").write_bytes(b"current-card")
        (self.source/"states"/"slot.sstate0").write_bytes(b"current-state")
        real_replace=os.replace
        def replacement(source,destination):
            if Path(source).parent.name=="states" and "new" in Path(source).parts:
                raise OSError("simulated second publication failure")
            return real_replace(source,destination)
        with patch("redux.saves.os.replace",side_effect=replacement):
            with self.assertRaisesRegex(OSError,"second publication"):self.store.restore(backup.id,self.source,overwrite=True)
        self.assertEqual((self.source/"card.mcd").read_bytes(),b"current-card")
        self.assertEqual((self.source/"states"/"slot.sstate0").read_bytes(),b"current-state")
        self.assertEqual(list(self.source.glob(".redux-restore-*")),[])
        self.assertEqual(len(self.store.list_backups(7)),2)
        for saved in self.store.list_backups(7):self.store.verify(saved.id)

    def test_cancelled_backup_and_restore_publish_nothing(self):
        cancelled=threading.Event();cancelled.set()
        with self.assertRaises(SaveCancelled):self.store.backup(7,self.source,cancelled=cancelled)
        self.assertEqual(self.store.list_backups(),[])
        self.assertEqual(list(self.store.root.rglob("*.tmp")),[])
        backup=self.store.backup(7,self.source);target=self.root/"cancelled-restore"
        with self.assertRaises(SaveCancelled):self.store.restore(backup.id,target,cancelled=cancelled)
        self.assertFalse(target.exists())

    def test_backup_detects_files_changing_during_read(self):
        class ChangingEvent:
            def __init__(self,path):self.path=path;self.calls=0
            def is_set(self):
                self.calls+=1
                if self.calls==2:self.path.write_bytes(b"changed-content")
                return False
        with self.assertRaisesRegex(SaveError,"changed during backup"):
            self.store.backup(7,self.source,patterns=["*.mcd"],cancelled=ChangingEvent(self.source/"card.mcd"))
        self.assertEqual(self.store.list_backups(),[])

    def _hostile(self,path_name="card.mcd",*,payload=b"evil",manifest_value=None,extra=None):
        backup_id="7-20260101T000000Z-123456abcdef"
        folder=self.store.root/"game-7";folder.mkdir(exist_ok=True);path=folder/(backup_id+".zip")
        manifest={"format":"redux-save-backup","version":1,"id":backup_id,"game_id":7,
            "created_at":"now","label":"hostile","files":[{"path":path_name,"size":len(payload),
                "sha256":hashlib.sha256(payload).hexdigest()}]}
        with zipfile.ZipFile(path,"w") as archive:
            archive.writestr("manifest.json",json.dumps(manifest if manifest_value is None else manifest_value))
            archive.writestr("files/"+path_name,payload)
            if extra:archive.writestr(*extra)
        return backup_id,path

    def test_untrusted_archive_paths_and_unlisted_entries_cannot_escape(self):
        target=self.root/"hostile-target"
        for name in ["../escape","/escape","folder/../../escape","C:/escape","folder\\escape","NUL","file:stream","file.","question?.mcd"]:
            with self.subTest(name=name):
                backup_id,_=self._hostile(name)
                with self.assertRaises(SaveError):self.store.restore(backup_id,target)
                self.assertFalse(target.exists())
        backup_id,_=self._hostile(extra=("files/../escape",b"hidden"))
        with self.assertRaises(SaveError):self.store.restore(backup_id,target)
        self.assertFalse((self.root/"escape").exists())

    def test_invalid_manifest_is_reported_as_unreadable_not_crash(self):
        for value in [[],["not-an-object"],"text",17]:
            with self.subTest(value=value):
                backup_id,_=self._hostile(manifest_value=value)
                self.assertFalse(self.store.list_backups(7)[0].valid)
                with self.assertRaises(SaveError):self.store.verify(backup_id)

    def test_corrupt_payload_is_rejected_before_destination_creation(self):
        backup=self.store.backup(7,self.source)
        path=Path(backup.path)
        with zipfile.ZipFile(path) as archive:
            entries={n:archive.read(n) for n in archive.namelist()}
        entries["files/card.mcd"]=b"X"*len(entries["files/card.mcd"])
        with zipfile.ZipFile(path,"w") as archive:
            for name,payload in entries.items():archive.writestr(name,payload)
        target=self.root/"corrupt-restore"
        with self.assertRaisesRegex(SaveError,"checksum"):self.store.restore(backup.id,target)
        self.assertFalse(target.exists())

    def test_backup_store_can_never_be_a_source_or_restore_target(self):
        with self.assertRaises(SaveError):self.store.backup(7,self.root)
        backup=self.store.backup(7,self.source)
        for target in [self.root,self.store.root,self.store.root/"nested"]:
            with self.subTest(target=target),self.assertRaises(SaveError):self.store.restore(backup.id,target)
        with self.assertRaises(ValueError):self.store.backup(7,self.source,patterns=[])
        for value in ["../escape","0-20260101T000000Z-123456abcdef",True]:
            with self.subTest(value=value),self.assertRaises(ValueError):self.store.verify(value)


if __name__=="__main__":unittest.main()
