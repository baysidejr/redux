from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from redux.playback import (
    PlaybackError, PlaybackManager, assess_readiness, discover_backends,
    get_profile, profile_arguments,
)


class Process:
    pid=1234
    def __init__(self):self.code=None;self.killed=False
    def poll(self):return self.code
    def kill(self):self.killed=True;self.code=9
    def wait(self,timeout):
        if self.code is None:raise subprocess.TimeoutExpired("fake",timeout)
        return self.code


class Control:
    def __init__(self,process):self.process=process;self.pauses=0;self.resumes=0;self.closes=0;self.requests=0
    def pause(self):self.pauses+=1
    def resume(self):self.resumes+=1
    def request_close(self):self.requests+=1
    def close(self):self.closes+=1


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.clock=[10.0];self.process=Process();self.control=Control(self.process)
        self.manager=PlaybackManager(clock=lambda:self.clock[0],launcher=lambda *a,**k:self.process,
            control_factory=lambda process:self.control)
        self.session=self.manager.launch(7,"Mesen.exe",["{game}"],"Game.nes",title="A Game")

    def test_pause_is_idempotent_and_excluded_from_playtime(self):
        self.clock[0]=15;self.assertTrue(self.manager.pause(self.session.session_id))
        self.assertTrue(self.manager.pause(self.session.session_id));self.assertEqual(self.control.pauses,1)
        self.clock[0]=25
        paused=self.manager.active()[0]
        self.assertTrue(paused.paused);self.assertEqual(paused.play_seconds,5)
        self.assertEqual(json.loads(json.dumps(asdict(paused)))["profile_id"],"mesen")
        self.manager.resume(self.session.session_id);self.manager.resume(self.session.session_id)
        self.assertEqual(self.control.resumes,1)
        self.clock[0]=28;self.process.code=0
        result=self.manager.poll()[0]
        self.assertEqual(result.elapsed_seconds,18);self.assertEqual(result.play_seconds,8)
        self.assertEqual(result.duration_seconds,8);self.assertFalse(result.forced)
        self.assertEqual(self.control.closes,1);self.assertEqual(self.manager.poll(),[])

    def test_graceful_timeout_does_not_silently_force(self):
        self.assertFalse(self.manager.stop(self.session.session_id,grace_seconds=0))
        self.assertEqual(self.control.requests,1);self.assertFalse(self.process.killed)
        self.assertEqual(len(self.manager.active()),1)
        self.assertTrue(self.manager.stop(self.session.session_id,force=True,grace_seconds=0))
        self.assertTrue(self.manager.poll()[0].forced)

    def test_force_stop_can_recover_a_failed_resume(self):
        self.manager.pause(self.session.session_id)
        self.control.resume=lambda:(_ for _ in ()).throw(PlaybackError("failed resume"))
        self.assertTrue(self.manager.stop(self.session.session_id,force=True,grace_seconds=0))
        self.assertTrue(self.process.killed)

    def test_only_one_running_game_and_owned_sessions(self):
        with self.assertRaises(PlaybackError):self.manager.launch(8,"Mesen.exe",["{game}"],"Other.nes")
        with self.assertRaises(PlaybackError):self.manager.pause("not-owned")

    def test_detach_resumes_and_keeps_game_running(self):
        self.manager.pause(self.session.session_id);self.manager.close()
        self.assertEqual(self.control.resumes,1);self.assertEqual(self.control.closes,1)
        self.assertIsNone(self.process.poll());self.assertFalse(self.manager.active()[0].can_pause)

    def test_backend_pause_accounts_time_without_calling_native_controller(self):
        self.clock[0]=15;self.manager.set_backend_paused(self.session.session_id,True)
        self.manager.set_backend_paused(self.session.session_id,True)
        self.clock[0]=25;snapshot=self.manager.active()[0]
        self.assertTrue(snapshot.paused);self.assertEqual(snapshot.pause_source,"backend")
        self.assertEqual(snapshot.play_seconds,5)
        with self.assertRaises(PlaybackError):self.manager.pause(self.session.session_id)
        with self.assertRaises(PlaybackError):self.manager.resume(self.session.session_id)
        self.manager.set_backend_paused(self.session.session_id,False)
        self.assertEqual(self.control.pauses,0);self.assertEqual(self.control.resumes,0)
        self.clock[0]=28;self.process.code=0
        self.assertEqual(self.manager.poll()[0].play_seconds,8)

    def test_backend_accounting_cannot_clear_native_pause(self):
        self.manager.pause(self.session.session_id)
        for paused in (False,True):
            with self.subTest(paused=paused),self.assertRaises(PlaybackError):
                self.manager.set_backend_paused(self.session.session_id,paused)
        self.assertEqual(self.manager.active()[0].pause_source,"process")
        with self.assertRaises(TypeError):self.manager.set_backend_paused(self.session.session_id,"yes")


class ProfileTests(unittest.TestCase):
    def test_local_discovery_and_openbios_readiness(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);backend=root/"tools"/"emulators"/"PCSXRedux";backend.mkdir(parents=True)
            (backend/"PCSXReduxCore.exe").write_bytes(b"core");(backend/"pcsx-redux.exe").write_bytes(b"wrapper")
            (backend/"openbios.bin").write_bytes(bytes(524288))
            found=discover_backends(root)
            self.assertEqual(len(found),1);self.assertEqual(Path(found[0].executable).name,"PCSXReduxCore.exe")
            ready=assess_readiness("psx",found[0].executable)
            self.assertTrue(ready.ready);self.assertEqual(len(ready.firmware_files),1)
            args=profile_arguments("pcsx_redux",found[0].executable)
            self.assertEqual(args[args.index("-bios")+1],str(backend/"openbios.bin"))
            self.assertEqual(args[args.index("-portable")+1],str(backend))
            self.assertFalse(assess_readiness("nes",found[0].executable).ready)
            (backend/"openbios.bin").unlink()
            self.assertFalse(assess_readiness("psx",found[0].executable).ready)

    def test_firmware_checks_only_explicit_known_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);exe=root/"duckstation.exe";exe.touch()
            (root/"other-user-stuff").mkdir();(root/"other-user-stuff"/"scph5501.bin").write_bytes(bytes(524288))
            self.assertFalse(assess_readiness("psx",exe).ready)
            self.assertTrue(assess_readiness("psx",exe,firmware_dir=root/"other-user-stuff").ready)
            self.assertFalse(get_profile("mesen").capabilities.frontend_save_states)


class NativeLifecycleTests(unittest.TestCase):
    def test_owned_harmless_child_really_pauses_and_resumes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);helper=root/"heartbeat.py";game=root/"test.nes";game.touch()
            heartbeat=root/"heartbeat.bin"
            helper.write_text("import pathlib,time\np=pathlib.Path(__file__).with_name('heartbeat.bin')\nf=p.open('ab',buffering=0)\nwhile True:\n f.write(b'x')\n time.sleep(.015)\n",encoding="utf-8")
            def launcher(*args,**kwargs):
                flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
                # Windows venv python.exe is a redirector that spawns the real
                # interpreter. The manager intentionally owns one process, so
                # test that actual process rather than a waiting wrapper.
                executable=getattr(sys,"_base_executable",sys.executable)
                return subprocess.Popen([executable,str(helper)],creationflags=flags)
            manager=PlaybackManager(launcher=launcher);session=manager.launch(1,sys.executable,["{game}"],game)
            try:
                deadline=time.monotonic()+5
                while (not heartbeat.exists() or heartbeat.stat().st_size<3) and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(session.can_pause);self.assertGreaterEqual(heartbeat.stat().st_size,3)
                manager.pause(session.session_id);size=heartbeat.stat().st_size
                time.sleep(.12);self.assertEqual(heartbeat.stat().st_size,size)
                manager.resume(session.session_id);time.sleep(.12)
                self.assertGreater(heartbeat.stat().st_size,size)
                manager.pause(session.session_id)
                self.assertTrue(manager.stop(session.session_id,force=True,grace_seconds=2))
                self.assertTrue(manager.poll()[0].forced)
            finally:
                for active in manager.active():manager.stop(active.session_id,force=True,grace_seconds=2)
                manager.poll();manager.close()


if __name__=="__main__":unittest.main()
