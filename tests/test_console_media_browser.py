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

    def test_console_resolves_its_own_add_and_scan_methods_instead_of_native_tools_dialogs(self):
        self.assertIsNot(ConsoleApp.add_files,ToolsApp.add_files)
        self.assertIsNot(ConsoleApp.scan_folder,ToolsApp.scan_folder)
        self.app.add_files()
        self.assertEqual(self.app.open_file_browser.call_args.kwargs.get("mode"),"files")
        self.assertTrue(self.app.open_file_browser.call_args.kwargs.get("multiple"))
        extensions=self.app.open_file_browser.call_args.kwargs.get("extensions")
        if extensions is not None:self.assertTrue(set(MEDIA_EXTENSIONS).issubset(extensions))
        self.app.open_file_browser.reset_mock()
        self.app.scan_folder()
        self.assertEqual(self.app.open_file_browser.call_args.kwargs.get("mode"),"folder")

    def test_add_media_imports_multiple_selected_roots_without_cue_track_duplicates(self):
        self.app.add_files()
        selected=self.selection()
        self.app.import_files.assert_not_called()
        selected([self.cue,self.bin,self.rom,self.cue])
        self.app.import_files.assert_not_called()
        self.run_scan_worker()
        self.app.import_files.assert_called_once()
        paths=[Path(p) for p in self.app.import_files.call_args.args[0]]
        self.assertEqual(len(paths),2)
        self.assertEqual(set(paths),{self.cue,self.rom})

    def test_scan_selection_defers_recursive_discovery_to_worker_and_deduplicates_cue_bins(self):
        self.app.scan_folder()
        selected=self.selection()
        with patch("redux.console_ui.discover_media",wraps=discover_media) as discover:
            selected(self.media)
            discover.assert_not_called()
            self.app.import_files.assert_not_called()
            self.run_scan_worker()
            discover.assert_called_once_with(self.media,cancelled=self.app.cancelled)
        paths=[Path(p) for p in self.app.import_files.call_args.args[0]]
        self.assertEqual(set(paths),{self.cue,self.rom})
        self.assertEqual(len(paths),2)

    def test_empty_folder_uses_console_notice_without_import_or_native_popup(self):
        self.app.scan_folder()
        self.selection()(self.empty)
        self.run_scan_worker()
        self.app.import_files.assert_not_called()
        self.app.notice.assert_called_once()

    def test_cancelled_add_and_scan_workers_do_not_import_or_show_empty_folder_notices(self):
        for action,selection in (("add_files",[self.cue,self.bin,self.rom]),
                                 ("scan_folder",self.media)):
            self.app=self.bare()
            getattr(self.app,action)()
            self.selection()(selection)
            self.app.cancelled.set()
            self.app.status.set.reset_mock()
            self.run_scan_worker()
            with self.subTest(action=action):
                self.app.import_files.assert_not_called()
                self.app.notice.assert_not_called()
                self.assertIn("cancelled",self.app.status.set.call_args.args[0].casefold())

    def test_busy_add_and_scan_do_not_open_any_picker_or_start_import(self):
        self.app.busy=True
        self.app.add_files();self.app.scan_folder()
        self.app.open_file_browser.assert_not_called()
        self.app.task.assert_not_called();self.app.import_files.assert_not_called()

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

    def test_controller_a_b_y_adds_selected_media_across_folders_without_track_duplicates(self):
        self.real_browser()
        self.app.preferences["media_source_dir"]=str(self.media)
        self.app.add_files();self.finish_browser_reads()
        self.focus_path(self.nested);self.app.input_action("a");self.finish_browser_reads()
        self.assertEqual(self.app.overlay["browser"].path,self.nested)
        for path in (self.cue,self.bin):
            self.focus_path(path);self.app.input_action("a")
        self.app.input_action("b");self.finish_browser_reads()
        self.assertEqual(self.app.overlay["browser"].path,self.media)
        self.focus_path(self.rom);self.app.input_action("a")
        self.assertEqual(len(self.app.overlay["browser"].selected),3)
        self.app.input_action("y")
        self.assertIsNone(self.app.overlay)
        self.app.import_files.assert_not_called()
        self.run_scan_worker()
        self.assertEqual(set(self.app.import_files.call_args.args[0]),{self.cue,self.rom})

    def test_controller_folder_navigation_a_back_b_and_use_y_starts_recursive_scan(self):
        self.real_browser()
        self.app.preferences["media_source_dir"]=str(self.media)
        self.app.scan_folder();self.finish_browser_reads()
        self.focus_path(self.nested);self.app.input_action("a");self.finish_browser_reads()
        self.app.input_action("b");self.finish_browser_reads()
        self.assertEqual(self.app.overlay["browser"].path,self.media)
        self.app.input_action("y")
        self.run_scan_worker()
        self.assertEqual(set(self.app.import_files.call_args.args[0]),{self.cue,self.rom})

    def test_controller_back_from_locations_cancels_without_import_or_selection_worker(self):
        self.real_browser()
        self.app.add_files();self.finish_browser_reads()
        self.assertIsNone(self.app.overlay["browser"].path)
        self.app.input_action("b")
        self.assertIsNone(self.app.overlay)
        self.assertEqual((self.app.screen,self.app.focus),("media","media:files"))
        self.app.task.assert_not_called();self.app.import_files.assert_not_called()

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
