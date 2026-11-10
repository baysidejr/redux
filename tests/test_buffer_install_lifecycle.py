"""Independent paused-buffer install lifecycle; real files, no hardware/DLLs."""
import ctypes
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux.disc_buffer import _new_lease, _validate_capture
from redux.disc_identity import DiscIdentity
from redux.disc_session import DiscEmbeddedGame, prepare_disc_game
from redux.embedded import EmbeddedGame
from redux.library import ImportCancelled, Library
from redux.libretro_host import EmbeddedSession
from redux.raw_acquisition import capture_raw_cd
from tests.test_raw_acquisition import Device


class FakeHost(EmbeddedSession):
    """Uses the real state-file format/hash checks without loading native code."""
    def __init__(self,core,game,firmware,saves,**kwargs):
        self.core_path=Path(core);self.game_path=Path(game)
        self.system_directory=Path(firmware);self.save_directory=Path(saves)
        self.save_directory.mkdir(parents=True,exist_ok=True)
        self._core_hash=hashlib.sha256(self.core_path.read_bytes()).hexdigest()
        self._game_hash=hashlib.sha256(self.game_path.read_bytes()).hexdigest()
        self._sram_path=self.save_directory/(self._core_hash[:12]+"-"+self._game_hash+".srm")
        self.core_info={"name":"Lifecycle fake core","version":"1"}
        self._sequence=1;self._paused=False;self.worker_alive=False
        self._thread=SimpleNamespace(is_alive=lambda:self.worker_alive)
        self.payload=b"before-state-000";self.loaded_state=None
        self.battery=b"before-battery";self.loaded_battery=None
        self.fail_stop=False;self.calls=[]
        def serialize(buffer,size):
            ctypes.memmove(buffer,self.payload,size)
            return True
        def unserialize(buffer,size):
            self.loaded_state=ctypes.string_at(buffer,size)
            return True
        self._core=SimpleNamespace(retro_serialize_size=lambda:16,
                                   retro_serialize=serialize,retro_unserialize=unserialize)

    def start(self):
        self.worker_alive=True;self.calls.append("start")
        if self._sram_path.exists():self.loaded_battery=self._sram_path.read_bytes()
        return self
    def pause(self):self._paused=True;self.calls.append("pause")
    def resume(self):self._paused=False;self.calls.append("resume")
    def flush_saves(self):self.calls.append("flush");self._save_sram()
    def _save_sram(self):self._sram_path.write_bytes(self.battery)
    def _flush_audio(self):pass
    def save_state(self,path):return self._save_state(Path(path))
    def load_state(self,path):return self._load_state(Path(path))
    def save_screenshot(self,path):raise RuntimeError("No video fixture needed")
    def stop(self):
        self.calls.append("stop")
        if self.fail_stop:raise RuntimeError("Native shutdown timed out")
        self._save_sram();self.worker_alive=False


class BufferInstallLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.library=Library(self.root/"library")
        self.identity=DiscIdentity("G:","Lifecycle Saturn","saturn","MK-TEST",
            "Saturn CD","header",(),{"region":"USA"},"a"*64)
        self.lease=_new_lease(self.library.root/"cache"/"disc-play",self.identity)
        result=capture_raw_cd("E:",self.lease.directory/"capture",device_factory=lambda _:Device())
        _validate_capture(result,self.lease)
        self.game=prepare_disc_game(self.library,self.lease)
        self.core=self.root/"fake-core.dll";self.core.write_bytes(b"Never loaded")
        patcher=patch("redux.libretro_host.EmbeddedSession",side_effect=FakeHost)
        patcher.start();self.addCleanup(patcher.stop)
        self.session=DiscEmbeddedGame(self.library,self.game,self.core,media_lease=self.lease,audio=False)
        self.session.start();self.session.pause()

    def target(self,game):
        return self.library.root/"game_data"/str(game["id"])/game["bundle_hash"]

    def test_later_states_and_battery_survive_install_stop_cleanup_and_managed_relaunch(self):
        old_directory=self.session.directory
        old_media=self.session.host.game_path
        old_save_directory=self.session.host.save_directory
        before=self.session.save(0)
        installed=self.session.install_to_library()
        self.assertNotEqual(installed["id"],self.game["id"])
        self.assertEqual(installed["bundle_hash"],self.game["bundle_hash"])
        self.assertEqual(self.session.directory,old_directory)
        self.assertEqual(self.session.host.game_path,old_media)
        self.assertEqual(self.session.host.save_directory,old_save_directory)
        self.assertEqual(self.session.game["id"],self.game["id"])
        self.assertTrue(self.session.host.paused)
        self.assertIn("flush",self.session.host.calls)
        initial_target=self.target(installed)/"states"/self.core.stem/"slot-0.json"
        self.assertEqual(initial_target.read_bytes(),(self.session.states/"slot-0.json").read_bytes())
        self.session.resume()
        self.session.host.payload=b"after--state-000"
        self.session.host.battery=b"after-battery-from-game"
        self.session.pause()
        after=self.session.save(0);self.session.save(1)
        self.assertNotEqual(before.sha256,after.sha256)
        self.session.stop()
        self.assertTrue(self.lease.closed)
        self.assertFalse(old_media.exists())
        self.assertTrue(after.path.exists())
        self.assertTrue(self.library.path_for(installed).exists())
        managed=EmbeddedGame(self.library,installed,self.core,audio=False)
        managed.start()
        try:
            self.assertEqual(managed.host.loaded_battery,b"after-battery-from-game")
            self.assertTrue(managed.slot(1).exists)
            managed.load(0)
            self.assertEqual(managed.host.loaded_state,b"after--state-000")
            self.assertEqual(managed.host._game_hash,self.session.host._game_hash)
            self.assertEqual(managed.host._core_hash,self.session.host._core_hash)
        finally:managed.stop()

    def test_cancelled_media_copy_keeps_paused_core_source_and_saves(self):
        self.session.save(0)
        event=threading.Event()
        def cancel(*_):event.set()
        with self.assertRaises((ImportCancelled,RuntimeError)):
            self.session.install_to_library(cancelled=event,progress=cancel)
        self.assertTrue(self.session.host.paused)
        self.assertFalse(self.lease.closed)
        self.assertTrue(self.lease.entry_path.is_file())
        self.assertTrue(self.session.slot(0,verify=True).exists)
        self.assertEqual(self.library.games(),[])
        self.session.stop()

    def test_native_stop_failure_retains_buffer_and_original_saves_for_retry(self):
        self.session.save(0)
        installed=self.session.install_to_library()
        self.session.host.battery=b"new-progress"
        self.session.host.fail_stop=True
        with self.assertRaisesRegex(RuntimeError,"timed out"):self.session.stop()
        self.assertFalse(self.lease.closed)
        self.assertTrue(self.lease.entry_path.is_file())
        self.assertTrue(self.session.slot(0,verify=True).exists)
        self.assertTrue(self.target(installed).is_dir())
        self.session.host.fail_stop=False
        self.session.stop()
        self.assertTrue(self.lease.closed)
        target_battery=self.target(installed)/"battery"/self.core.stem/self.session.host._sram_path.name
        self.assertEqual(target_battery.read_bytes(),b"new-progress")

    def test_old_core_state_metadata_is_copied_unchanged_and_remains_incompatible(self):
        saved=self.session.save(0)
        original_manifest=(self.session.states/"slot-0.json").read_bytes()
        original_state=saved.path.read_bytes()
        installed=self.session.install_to_library()
        self.session.stop()
        target_states=self.target(installed)/"states"/self.core.stem
        self.assertEqual((target_states/"slot-0.json").read_bytes(),original_manifest)
        self.assertEqual((target_states/saved.path.name).read_bytes(),original_state)
        self.core.write_bytes(b"A different fake core build; never loaded")
        managed=EmbeddedGame(self.library,installed,self.core,audio=False)
        with self.assertRaisesRegex(RuntimeError,"another game or core"):
            managed.load(0)
        self.assertIsNone(managed.host.loaded_state)
        self.assertEqual((target_states/"slot-0.json").read_bytes(),original_manifest)

    def test_destination_save_conflict_preserves_both_versions_and_retains_buffer(self):
        self.session.save(0)
        installed=self.session.install_to_library()
        target_battery=self.target(installed)/"battery"/self.core.stem/self.session.host._sram_path.name
        baseline=target_battery.read_bytes()
        target_battery.write_bytes(b"independent-managed-save")
        self.session.host.battery=b"active-disc-session-save"
        try:self.session.stop()
        except RuntimeError:pass
        self.assertEqual(target_battery.read_bytes(),b"independent-managed-save")
        self.assertEqual(self.session.host._sram_path.read_bytes(),b"active-disc-session-save")
        self.assertFalse(self.lease.closed)
        self.assertTrue(self.lease.entry_path.exists())
        self.assertTrue(self.session.lease_cleanup_error)
        # Resolving the conflict restores guarded sync eligibility; retry must
        # not stop the native host a second time or lose its final save.
        target_battery.write_bytes(baseline)
        stops=self.session.host.calls.count("stop")
        self.session.stop()
        self.assertTrue(self.lease.closed)
        self.assertEqual(self.session.host.calls.count("stop"),stops)
        self.assertEqual(target_battery.read_bytes(),b"active-disc-session-save")


if __name__=="__main__":unittest.main()
