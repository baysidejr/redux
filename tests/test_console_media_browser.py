"""Console media routing with real file fixtures and no Tk/native dialogs."""
from pathlib import Path
from queue import Queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from redux.app import App as ToolsApp
from redux.console_ui import ConsoleApp
from redux.file_browser import BrowserEntry
from redux.library import Library,MEDIA_EXTENSIONS,discover_media
from redux.ui_model import ScreenHistory


class ConsoleMediaBrowserTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.media=self.root/"Media with spaces";self.media.mkdir()
        self.nested=self.media/"Saturn";self.nested.mkdir()
        self.cue=self.nested/"disc.cue"
        self.bin=self.nested/"track.bin"
        self.cue.write_text('FILE "track.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n')
        self.bin.write_bytes(bytes(2352*2))
        self.rom=self.media/"Homebrew.nes";self.rom.write_bytes(b"NES\x1a"+bytes(32))
        (self.media/"notes.txt").write_text("Not game media")
        self.empty=self.root/"No games";self.empty.mkdir()
        (self.empty/"notes.txt").write_text("Not game media")
        self.app=self.bare()
        for name in ("askopenfilename","askopenfilenames","askdirectory","asksaveasfilename"):
            patcher=patch("tkinter.filedialog."+name,side_effect=AssertionError("Native picker must not open"))
            patcher.start();self.addCleanup(patcher.stop)

    def bare(self):
        app=object.__new__(ConsoleApp)
        app.root=SimpleNamespace(grab_current=lambda:None,after=lambda *args:None)
        app.busy=False;app.embedded=None;app.playback=SimpleNamespace(active=lambda:[])
        app.screen="media";app.overlay=None;app.focus="media:files"
        app.history=ScreenHistory();app.preferences={};app.rows=[];app.all_games=[]
        app.targets=[];app.callbacks={};app.selected_id=None
        app.library=Library(self.root/"library")
        app.cancelled=threading.Event();app.events=Queue()
        app.status=SimpleNamespace(set=Mock());app.notice=Mock()
        app.schedule_draw=Mock();app.refresh=Mock()
        app.import_files=Mock();app.task=Mock()
        app.open_file_browser=Mock()
        return app

    def selection(self):
        self.app.open_file_browser.assert_called_once()
        return self.app.open_file_browser.call_args.kwargs["on_select"]

    def run_scan_worker(self):
        self.app.task.assert_called_once()
        work,done,*_=self.app.task.call_args.args
        result=[];errors=[];thread_ids=[]
        def worker():
            thread_ids.append(threading.get_ident())
            try:result.append(work())
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=worker);thread.start();thread.join(5)
        self.assertFalse(thread.is_alive(),"Fixture scan must finish")
        if errors:raise errors[0]
        self.assertNotEqual(thread_ids,[threading.get_ident()])
        done(result[0])

    def real_browser(self):
        del self.app.open_file_browser
        self.app.closing=False
        self.browser_workers=[]
        self.app._browser_start_worker=self.browser_workers.append
        self.app._browser_list_locations=lambda *_:(BrowserEntry(self.media,"Fixture media",True),)

    def finish_browser_reads(self):
        while self.browser_workers:
            callback=self.browser_workers.pop(0)
            worker=threading.Thread(target=callback);worker.start();worker.join(5)
            self.assertFalse(worker.is_alive())
            self.app._browser_poll_results()

    def focus_path(self,path):
        state=self.app.overlay["browser"]
        index=next(index for index,entry in enumerate(state.entries) if entry.path==path)
        state.focus_index=index;self.app.focus=f"browser:row:{index}"

    def test_normal_library_bios_and_cover_selectors_use_the_controller_browser(self):
        self.app._save_preferences=Mock()
        self.app.choose_library()
        self.assertEqual(self.app.open_file_browser.call_args.kwargs["mode"],"folder")
        self.selection()(self.empty)
        self.assertEqual(self.app.library.root,self.empty)
        self.app.open_file_browser.reset_mock()
        self.app.choose_firmware()
        self.assertEqual(self.app.open_file_browser.call_args.kwargs["mode"],"folder")
        self.selection()(self.media)
        self.assertEqual(Path(self.app.preferences["firmware_dir"]),self.media)
        self.app.open_file_browser.reset_mock()
        self.app.selected=Mock(return_value={"id":1,"title":"Fixture game"})
        self.app.choose_cover()
        kwargs=self.app.open_file_browser.call_args.kwargs
        self.assertEqual(kwargs["mode"],"files")
        self.assertFalse(kwargs["multiple"])
        self.assertIn(".png",kwargs["extensions"])
        self.assertNotIn(".txt",kwargs["extensions"])


if __name__=="__main__":unittest.main()
