import io
import queue
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

from redux import core_install as cores
from redux.console_cores import ConsoleCores, recommended_names
from redux.library import ImportCancelled


def archive(binary=None):
    binary=bytearray(128) if binary is None else bytearray(binary)
    if len(binary)==128:
        binary[:2]=b'MZ';struct.pack_into('<I',binary,60,64)
        binary[64:68]=b'PE\0\0';struct.pack_into('<H',binary,68,0x8664)
        struct.pack_into('<H',binary,86,0x2000)
    stream=io.BytesIO()
    with ZipFile(stream,'w') as zipped:zipped.writestr('mesen_libretro.dll',binary)
    return stream.getvalue()


class CoreInstallerTests(unittest.TestCase):
    def test_download_publishes_dll_license_and_provenance_without_python_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch=Mock(side_effect=[archive(),b'license = "GPLv3"',b'upstream license'])
            result=cores.install('mesen',tmp,downloader=fetch)
            self.assertTrue((Path(tmp)/'mesen_libretro.dll').is_file())
            self.assertEqual((Path(tmp)/'mesen_LICENSE.txt').read_bytes(),b'upstream license')
            self.assertEqual(len(result['dll_sha256']),64)
            fetch.reset_mock()
            self.assertTrue(cores.install('mesen',tmp,downloader=fetch)['already_installed'])
            fetch.assert_not_called()

    def test_failed_repair_keeps_existing_core(self):
        for replies in ([archive(),OSError('offline')],[archive(b'bad')]):
            with self.subTest(replies=len(replies)), tempfile.TemporaryDirectory() as tmp:
                target=Path(tmp)/'mesen_libretro.dll';target.write_bytes(b'original')
                with self.assertRaises((OSError,ValueError,RuntimeError)):
                    cores.install('mesen',tmp,replace=True,downloader=Mock(side_effect=replies))
                self.assertEqual(target.read_bytes(),b'original')

    def test_cancellation_and_unknown_core_do_not_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            cancel=threading.Event();cancel.set();fetch=Mock()
            with self.assertRaises(ImportCancelled):cores.install('mesen',tmp,cancelled=cancel,downloader=fetch)
            with self.assertRaises(ValueError):cores.install('../evil',tmp,downloader=fetch)
            fetch.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_unsafe_download_source_is_rejected_before_network(self):
        for url in ('file:///local','http://buildbot.libretro.com/a','https://evil.invalid/a','https://user@buildbot.libretro.com/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):cores.fetch(url,10)

    def test_recommended_set_is_unique_and_excludes_optional_backends(self):
        names=recommended_names()
        self.assertEqual(len(names),8)
        self.assertNotIn('mame',names);self.assertNotIn('mupen64plus_next',names)
        self.assertTrue(set(names)<=cores.CORES.keys())


class SetupUI(ConsoleCores):
    def __init__(self):
        self.busy=False;self.overlay=None;self.embedded=None;self.playback=None
        self.preferences={};self.cancelled=threading.Event();self.events=queue.Queue()
        self.menu=Mock();self.notice=Mock();self._save_preferences=Mock()
        self._configure_detected_backends=Mock();self.choose_firmware=Mock()
    def task(self,work,done,*args,**kwargs):done(work())


class CoreSetupFlowTests(unittest.TestCase):
    def test_first_launch_and_controller_download_continue(self):
        with tempfile.TemporaryDirectory() as tmp, patch('redux.console_cores.project_root',return_value=Path(tmp)):
            ui=SetupUI();ui.first_core_setup()
            self.assertEqual(ui.menu.call_args.args[0],'Set up supported systems')
            retry=Mock()
            ui.offer_core_download('nes',retry)
            with patch('redux.core_install.install',return_value={}) as install:
                ui.menu.call_args.args[1][0][1]()
            self.assertEqual(install.call_args.args[0],'mesen')
            self.assertTrue(ui.preferences['integrated']['nes'])
            retry.assert_called_once_with()
            self.assertTrue(ui.preferences['core_setup_seen'])

    def test_active_game_blocks_core_replacement(self):
        ui=SetupUI();ui.embedded=object()
        ui.download_cores(('mesen',),repair=True)
        self.assertEqual(ui.notice.call_args.args[0],'Close the game first')

    def test_deferred_first_launch_does_not_repeat(self):
        ui=SetupUI();ui._defer_core_setup();ui.first_core_setup();ui.menu.assert_not_called()
