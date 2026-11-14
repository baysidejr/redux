"""PC shortcuts, controller UI, and real Windows child-process lifetime checks."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from redux.library import Library
from redux.pc_games import validate_executable,launch_pc_game,WindowsGameProcess
from redux.playback import PlaybackManager,PlaybackError
from redux.console_ui import ConsoleApp


class PCShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.library=Library(self.root/'portable'/'library')
        self.exe=self.root/'portable'/'GOG Games'/'My Game'/'Game.exe'
        self.exe.parent.mkdir(parents=True);self.exe.write_bytes(b'MZ'+b'fixture'*30)
    def tearDown(self):self.temp.cleanup()

    def test_link_is_portable_and_duplicate_does_not_reset_edits(self):
        game=self.library.add_pc_game(self.exe,title='My title')
        self.assertEqual((game['system'],game['storage'],game['file_count']),('pc','linked',0))
        self.assertTrue(game['available']);self.assertFalse(Path(game['path']).is_absolute())
        again=self.library.add_pc_game(self.exe,title='Different title')
        self.assertEqual((again['id'],again['title']),(game['id'],'My title'))
        self.assertFalse(list(self.library.media.iterdir()))
        copied=self.root/'moved';shutil.copytree(self.root/'portable',copied)
        moved=Library(copied/'library');new=moved.game(game['id'])
        self.assertTrue(new['available'])
        self.assertEqual(moved.path_for(new),copied/'GOG Games'/'My Game'/'Game.exe')

    def test_external_ssd_disconnect_and_relocation_preserve_history_and_identity(self):
        outside=self.root/'ssd'/'Game.exe';outside.parent.mkdir();outside.write_bytes(self.exe.read_bytes())
        game=self.library.add_pc_game(outside);self.library.record_session(game['id'],12)
        outside.unlink();self.assertFalse(self.library.game(game['id'])['available'])
        updated=self.library.add_pc_game(self.exe,game_id=game['id'])
        self.assertEqual(updated['bundle_hash'],game['bundle_hash'])
        self.assertEqual(updated['play_seconds'],12);self.assertTrue(updated['available'])
        self.assertEqual(self.library.add_pc_game(self.exe)['id'],game['id'])

    def test_same_exe_bytes_at_two_locations_are_separate_games(self):
        first=self.library.add_pc_game(self.exe)
        second=self.root/'another'/'Game.exe';second.parent.mkdir();second.write_bytes(self.exe.read_bytes())
        self.assertNotEqual(self.library.add_pc_game(second)['id'],first['id'])

    def test_remove_only_removes_shortcut(self):
        game=self.library.add_pc_game(self.exe);self.library.remove_game(game['id'])
        self.assertTrue(self.exe.is_file());self.assertEqual(self.library.games(),[])

    def test_launch_options_are_per_game_portable_and_argument_vector_validated(self):
        game=self.library.add_pc_game(self.exe)
        self.library.configure_pc_game(game['id'],['--flag','a & b'],self.exe.parent)
        game=self.library.game(game['id'])
        self.assertEqual(self.library.pc_game_config(game),(['--flag','a & b'],self.exe.parent))
        self.assertFalse(Path(game['pc_working_directory']).is_absolute())
        for invalid in ('--flag',[None],['bad\0value'],['x']*65):
            with self.assertRaises(ValueError):self.library.configure_pc_game(game['id'],invalid)
        self.library.configure_pc_game(game['id'],[])
        self.assertEqual(self.library.pc_game_config(self.library.game(game['id'])),([],self.exe.parent))

    def test_duplicate_relocation_does_not_merge_two_games(self):
        first=self.library.add_pc_game(self.exe)
        other=self.root/'other'/'Game.exe';other.parent.mkdir();other.write_bytes(self.exe.read_bytes())
        second=self.library.add_pc_game(other)
        with self.assertRaises(ValueError):self.library.add_pc_game(other,game_id=first['id'])
        self.assertEqual(self.library.path_for(self.library.game(first['id'])),self.exe)

    def test_launcher_passes_optional_arguments_as_literal_separate_values(self):
        with patch('redux.pc_games.WindowsGameProcess') as spawn:
            launch_pc_game(self.exe,['--config','file with spaces.ini','&'],self.exe)
        spawn.assert_called_once_with([str(self.exe),'--config','file with spaces.ini','&'],self.exe.parent)

    def test_bad_executable_and_platform_reclassification_rejected(self):
        for name,data in [('bad.exe',b'text'),('fake.lnk',b'MZ')]:
            path=self.root/name;path.write_bytes(data)
            with self.assertRaises(ValueError):self.library.add_pc_game(path)
        game=self.library.add_pc_game(self.exe)
        with self.assertRaises(ValueError):self.library.update_metadata(game['id'],system='psx')
        with self.assertRaises(ValueError):self.library.update_details(game['id'],title='x',system='psx')
        game['path']='../escape.exe'
        with self.assertRaises(ValueError):self.library.path_for(game)

    def test_launch_uses_explicit_exe_and_its_folder_without_shell_or_galaxy(self):
        with patch('redux.pc_games.WindowsGameProcess') as spawn:
            launch_pc_game(self.exe,[],self.exe)
        spawn.assert_called_once_with([str(self.exe)],self.exe.parent)
        with self.assertRaises(ValueError):launch_pc_game(self.exe,'&',self.exe)


class PCConsoleTests(unittest.TestCase):
    def bare(self):
        app=object.__new__(ConsoleApp);app.busy=False;app.preferences={};app.embedded=None
        app.root=SimpleNamespace(iconify=Mock(),deiconify=Mock(),lift=Mock(),focus_force=Mock())
        app.status=SimpleNamespace(set=Mock());app.overlay=None;app.bridges={};app.sessions={}
        app.focus='media:pc'
        app.schedule_draw=Mock();app.refresh=Mock();app.open_file_browser=Mock()
        app.task=lambda work,done,*a,**k:done(work())
        return app

    def test_missing_drive_explains_controller_relocation(self):
        app=self.bare();app._game_context=lambda:{'id':2,'system':'pc','available':False}
        app.notice=Mock();app.play()
        self.assertEqual(app.notice.call_args.args[0],'Connect your game drive')
        self.assertIn('PC game setup',app.notice.call_args.args[1])

    def test_pc_launch_bypasses_emulator_and_tracks_session(self):
        app=self.bare();game={'id':7,'system':'pc','available':True,'title':'My Game'}
        exe=Path('G:/Games/My Game/Game.exe');app._game_context=lambda:game
        app.library=SimpleNamespace(path_for=lambda g:exe,start_session=lambda g:99,pc_game_config=lambda g:([],exe.parent))
        app.playback=SimpleNamespace(active=lambda:[],launch=Mock(return_value=SimpleNamespace(session_id='native')))
        app.play();self.assertEqual(app.sessions,{'native':99})
        app.playback.launch.assert_called_once_with(7,exe,[],exe,profile_id='pc_game',title='My Game',cwd=exe.parent)
        app.root.iconify.assert_called_once()

    def test_pc_menu_does_not_pause_or_offer_emulator_states(self):
        app=self.bare();app.playback=SimpleNamespace(active=lambda:[SimpleNamespace(session_id='pc',profile_id='pc_game',title='Game')],pause=Mock())
        app.game_menu();app.playback.pause.assert_not_called()
        self.assertEqual(app.overlay['title'],'PC game menu')
        self.assertEqual(len(app.overlay['items']),2)
        self.assertIn('continues running',app.overlay['message'])

    def test_natural_game_exit_restores_frontend_and_finishes_history(self):
        app=self.bare();app.sessions={'pc':99}
        result=SimpleNamespace(session_id='pc',duration_seconds=9,exit_code=0)
        app.playback=SimpleNamespace(poll=lambda:[result]);app.library=SimpleNamespace(finish_session=Mock())
        app._harvest_sessions();app.root.deiconify.assert_called_once();app.root.focus_force.assert_called_once()
        app.library.finish_session.assert_called_once_with(99,duration_seconds=9,exit_code=0)

    def test_pc_capabilities_block_process_pause(self):
        process=SimpleNamespace(pid=1,poll=lambda:None)
        control=SimpleNamespace(pause=Mock(),close=Mock())
        manager=PlaybackManager(pc_launcher=lambda *a,**kw:process,pc_control_factory=lambda p:control)
        session=manager.launch(1,'Game.exe',[],'Game.exe',profile_id='pc_game')
        with self.assertRaises(PlaybackError):manager.pause(session.session_id)
        self.assertFalse(session.can_pause);control.pause.assert_not_called();manager.close()


@unittest.skipUnless(os.name=='nt','Windows process lifetime integration')
class WindowsPCProcessTests(unittest.TestCase):
    def test_real_child_handoff_keeps_session_alive_after_parent_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'child-ready'
            child="from pathlib import Path; import time; Path('child-ready').write_text('ok'); time.sleep(.8)"
            script="import subprocess,sys; subprocess.Popen([sys.executable,'-c',"+repr(child)+"] )"
            process=WindowsGameProcess([sys.executable,'-c',script],directory)
            try:
                deadline=time.monotonic()+5
                while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(marker.exists())
                code=process.w.DWORD(259)
                while code.value==259 and time.monotonic()<deadline:
                    process.exit_code(process.handle,process.c.byref(code));time.sleep(.01)
                self.assertEqual(code.value,0)
                self.assertIsNone(process.poll())
                self.assertTrue(process.active_pids())
                with self.assertRaises(subprocess.TimeoutExpired):process.wait(.01)
                self.assertEqual(process.wait(5),0)
            finally:
                if process.poll() is None:process.kill();process.wait(5)
                process.close()

    def test_working_folder_and_literal_arguments_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            script="import json,sys;from pathlib import Path;Path('result.json').write_text(json.dumps(sys.argv[1:]))"
            process=WindowsGameProcess([sys.executable,'-c',script,'a & b','space name'],directory)
            try:
                self.assertEqual(process.wait(5),0)
                self.assertEqual(json.loads((Path(directory)/'result.json').read_text()),['a & b','space name'])
            finally:process.close()

    def test_detaching_frontend_does_not_kill_game(self):
        with tempfile.TemporaryDirectory() as directory:
            script="import time;from pathlib import Path;time.sleep(.3);Path('survived').write_text('ok')"
            process=WindowsGameProcess([sys.executable,'-c',script],directory)
            k=process.kernel;w=process.w
            k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
            k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];k.WaitForSingleObject.restype=w.DWORD
            observer=k.OpenProcess(0x100000,False,process.pid)
            self.assertTrue(observer);process.close()
            deadline=time.monotonic()+5
            while not (Path(directory)/'survived').exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue((Path(directory)/'survived').exists())
            try:self.assertEqual(k.WaitForSingleObject(observer,5000),0)
            finally:process.close_handle(observer)
