"""Save-card inspection uses real wrapper metadata, never a native emulator."""
from dataclasses import FrozenInstanceError
from datetime import timedelta,timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from PIL import Image

from redux.backend_rpc import StateSlot
from redux.embedded import EmbeddedGame
from redux.state_presentation import cards,format_saved_at


class StatePresentationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name)/"states";self.directory.mkdir()
        self.backend=object.__new__(EmbeddedGame)
        self.backend.states=self.directory;self.backend._lock=threading.RLock()
        self.backend.game={"bundle_hash":"a"*64};self.backend.core_hash="b"*64

    def write_state(self,number=0,*,core=None,game=None,**extra):
        payload=b"independent test-state bytes"+bytes((number,))
        digest=hashlib.sha256(payload).hexdigest()
        (self.directory/(digest+".state")).write_bytes(payload)
        item={"schema":1,"slot":number,"game":game or self.backend.game["bundle_hash"],
              "core":core or self.backend.core_hash,"sha256":digest,"size":len(payload),
              "saved_at":"2026-11-08T21:00:00+00:00"}
        item.update(extra)
        (self.directory/f"slot-{number}.json").write_text(json.dumps(item),encoding="utf-8")
        return item

    def write_thumbnail(self):
        path=self.directory/"fixture-thumbnail.png"
        Image.new("RGB",(64,48),(45,86,120)).save(path)
        return path,hashlib.sha256(path.read_bytes()).hexdigest()

    def test_four_empty_immutable_cards_do_not_write_state_data(self):
        result=cards(self.backend)
        self.assertIsInstance(result,tuple);self.assertEqual([item.slot for item in result],[0,1,2,3])
        self.assertTrue(all(not item.exists for item in result))
        self.assertEqual(list(self.directory.iterdir()),[])
        with self.assertRaises(FrozenInstanceError):result[0].exists=True

    def test_existing_slot_retains_actual_date_and_has_no_invented_thumbnail(self):
        item=self.write_state(2)
        screenshot_dir=self.directory.parent/"screenshots";screenshot_dir.mkdir()
        Image.new("RGB",(64,48)).save(screenshot_dir/"latest.png")
        result=cards(self.backend)
        self.assertTrue(result[2].exists);self.assertTrue(result[2].compatible)
        self.assertEqual(result[2].saved_at,item["saved_at"])
        self.assertIsNone(result[2].thumbnail_path)
        self.assertFalse(result[2].old_version)

    def test_corrupt_slot_does_not_hide_other_valid_cards(self):
        self.write_state(0);self.write_state(2)
        (self.directory/"slot-1.json").write_text('{broken')
        result=cards(self.backend)
        self.assertTrue(result[0].compatible);self.assertTrue(result[2].compatible)
        self.assertTrue(result[1].exists);self.assertFalse(result[1].compatible)
        self.assertTrue(result[1].error);self.assertFalse(result[1].old_version)

    def test_old_core_same_game_is_visible_but_does_not_bypass_backend_compatibility(self):
        original=self.write_state(0,core="c"*64)
        result=cards(self.backend)[0]
        self.assertTrue(result.exists);self.assertTrue(result.old_version);self.assertFalse(result.compatible)
        self.assertEqual(result.saved_at,original["saved_at"])
        with self.assertRaisesRegex(RuntimeError,"another game or core version"):self.backend.slot(0)
        self.assertEqual(json.loads((self.directory/"slot-0.json").read_text()),original)

    def test_wrong_game_or_missing_blob_is_not_mislabeled_as_old_version(self):
        self.write_state(0,core="c"*64,game="d"*64)
        item=self.write_state(1,core="c"*64)
        (self.directory/(item["sha256"]+".state")).unlink()
        result=cards(self.backend)
        self.assertFalse(result[0].old_version);self.assertFalse(result[1].old_version)
        self.assertFalse(result[0].compatible);self.assertFalse(result[1].compatible)

    def test_generic_backend_without_recorded_core_identity_never_guesses_old_version(self):
        self.write_state(0,core="c"*64)
        backend=SimpleNamespace(state_directory=self.directory,
            slot=lambda number:StateSlot(number,number==0,saved_at="2026-11-08T21:00:00+00:00"))
        result=cards(backend)
        self.assertTrue(result[0].compatible);self.assertFalse(result[0].old_version)

    def test_explicit_hashed_thumbnail_is_used_for_this_slot_only(self):
        thumbnail,digest=self.write_thumbnail()
        self.write_state(1,thumbnail=thumbnail.name,thumbnail_sha256=digest)
        result=cards(self.backend)
        self.assertEqual(result[1].thumbnail_path,thumbnail)
        self.assertIsNone(result[0].thumbnail_path);self.assertIsNone(result[2].thumbnail_path)

    def test_invalid_thumbnail_cannot_escape_directory_or_invalidate_real_state(self):
        thumbnail,digest=self.write_thumbnail()
        for name,sha in (("../fixture-thumbnail.png",digest),(str(thumbnail),digest),
                         (thumbnail.name,"e"*64),("missing.png",digest)):
            with self.subTest(name=name,sha=sha):
                self.write_state(0,thumbnail=name,thumbnail_sha256=sha)
                result=cards(self.backend)[0]
                self.assertTrue(result.compatible);self.assertTrue(result.exists)
                self.assertIsNone(result.thumbnail_path)

    def test_invalid_image_bytes_are_not_exposed_as_thumbnail(self):
        bad=self.directory/"bad.png";bad.write_bytes(b"not an image")
        self.write_state(0,thumbnail=bad.name,thumbnail_sha256=hashlib.sha256(bad.read_bytes()).hexdigest())
        result=cards(self.backend)[0]
        self.assertIsNone(result.thumbnail_path);self.assertTrue(result.compatible)

    def test_oversized_or_duplicate_manifest_cannot_establish_old_version(self):
        item=self.write_state(0,core="c"*64)
        manifest=self.directory/"slot-0.json"
        for data in (b" "*8193,json.dumps(item).replace('"schema": 1','"schema": 1, "schema": 1').encode()):
            with self.subTest(length=len(data)):
                manifest.write_bytes(data)
                result=cards(self.backend)[0]
                self.assertFalse(result.compatible);self.assertFalse(result.old_version)

    def test_invalid_timestamps_never_receive_a_fabricated_date(self):
        for value in (None,"broken","2026-11-08T21:00:00"):
            self.assertEqual(format_saved_at(value),"Date unavailable")
        self.assertEqual(format_saved_at("2026-11-08T21:00:00Z",tz=timezone(timedelta(hours=-5))),
                         "Nov 08, 2026 · 04:00 PM")

    def test_card_count_and_backend_capability_are_validated(self):
        for count in (True,0,11,2.0,"4"):
            with self.subTest(count=count),self.assertRaises(ValueError):cards(self.backend,count)
        with self.assertRaisesRegex(ValueError,"no state interface"):cards(object())
        self.assertEqual(len(cards(self.backend,10)),10)


if __name__=="__main__":unittest.main()
