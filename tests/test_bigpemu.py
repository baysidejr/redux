import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
import zipfile

from redux import bigpemu
from redux.library import Library,ImportCancelled
from redux.playback import discover_backends,get_profile,profile_arguments


def fixture(extra=None):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        for name in ('BigPEmu.exe','ReadMe.txt','Plugins/BigPEmu_Input_DirectInput.dll'):
            archive.writestr(name,b'original fixture')
        if extra:archive.writestr(extra,b'unsafe')
    return output.getvalue()


class BigPEmuTests(unittest.TestCase):
    def test_discovery_has_image_first_and_portable_defaults_for_both_systems(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe=bigpemu.executable(tmp);exe.parent.mkdir(parents=True);exe.write_bytes(b'MZ')
            backend=discover_backends(tmp)[0]
            self.assertEqual(backend.profile_id,'bigpemu')
            self.assertEqual(backend.systems,('jaguar','jaguar_cd'))
            args=profile_arguments('bigpemu',exe)
            self.assertEqual(args[0],'{game}');self.assertIn('-localdata',args)
            self.assertFalse(get_profile('bigpemu').capabilities.frontend_save_states)

    def test_verified_extraction_keeps_readme_and_plugins_intact(self):
        data=fixture()
        with tempfile.TemporaryDirectory() as tmp,patch.object(bigpemu,'SIZE',len(data)),patch.object(bigpemu,'SHA256',hashlib.sha256(data).hexdigest()):
            bigpemu.install(tmp,downloader=lambda _:data)
            self.assertEqual((bigpemu.directory(tmp)/'ReadMe.txt').read_bytes(),b'original fixture')
            fetch=Mock()
            self.assertTrue(bigpemu.install(tmp,downloader=fetch)['already_installed'])
            fetch.assert_not_called()

    def test_hash_mismatch_and_unsafe_zip_never_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):bigpemu.install(tmp,downloader=lambda _:b'corrupt')
            self.assertFalse(bigpemu.executable(tmp).exists())
        for bad in ('../escape.exe','C:/escape.exe','Plugins/evil.dll:stream','Plugins/trailing. '):
            data=fixture(bad)
            with self.subTest(bad=bad),tempfile.TemporaryDirectory() as tmp,patch.object(bigpemu,'SIZE',len(data)),patch.object(bigpemu,'SHA256',hashlib.sha256(data).hexdigest()):
                with self.assertRaises(ValueError):bigpemu.install(tmp,downloader=lambda _:data)
                self.assertFalse(bigpemu.executable(tmp).exists())

    def test_cancel_does_not_publish(self):
        data=fixture();cancel=threading.Event();cancel.set()
        with tempfile.TemporaryDirectory() as tmp,patch.object(bigpemu,'SIZE',len(data)),patch.object(bigpemu,'SHA256',hashlib.sha256(data).hexdigest()):
            with self.assertRaises(ImportCancelled):bigpemu.install(tmp,cancelled=cancel,downloader=lambda _:data)
            self.assertFalse(bigpemu.executable(tmp).exists())

    def test_jaguar_import_and_explicit_cd_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rom=root/'test.j64';rom.write_bytes(b'original sample')
            library=Library(root/'library')
            game,_=library.import_game(rom)
            self.assertEqual(game['system'],'jaguar')
            disc=root/'test.bigpimg';disc.write_bytes(b'original disc sample')
            game,_=library.import_game(disc)
            self.assertEqual(game['system'],'jaguar_cd')

    def test_recommended_first_launch_includes_standalone_download(self):
        from tests.test_core_install import SetupUI
        from redux.console_cores import recommended_names
        with tempfile.TemporaryDirectory() as tmp, patch('redux.console_cores.project_root',return_value=Path(tmp)):
            ui=SetupUI();ui.first_core_setup()
            action=ui.menu.call_args.args[1][0][1]
            with patch('redux.core_install.install',return_value={}) as core, patch('redux.bigpemu.install',return_value={}) as standalone:
                action()
            self.assertEqual(core.call_count,len(recommended_names()))
            standalone.assert_called_once_with(Path(tmp),cancelled=ui.cancelled)
