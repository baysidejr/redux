from pathlib import Path
import shutil
import tempfile
import unittest

from redux.storage_paths import library_directory,library_setting
from redux.library import Library


class PortableStorageTests(unittest.TestCase):
    def test_default_and_relative_setting_follow_copied_application_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);original=root/"original";copied=root/"copied"
            original.mkdir()
            source=original/"demo.nes";source.write_bytes(b"NES\x1a"+bytes(28))
            library=Library(library_directory({},original))
            game,_=library.import_game(source,system="nes")
            setting=library_setting(library.root,original)
            self.assertEqual(setting,"library")
            shutil.copytree(original,copied)
            for preferences in ({},{"library_root":setting}):
                moved=Library(library_directory(preferences,copied))
                row=moved.game(game["id"])
                self.assertTrue(moved.path_for(row).is_relative_to(copied))
                self.assertEqual(moved.path_for(row).read_bytes(),source.read_bytes())
                self.assertTrue(all(item["status"]=="ok" for item in moved.verify(row["id"])))

    def test_explicit_external_storage_and_command_line_override_remain_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);app=root/"app";external=root/"external";override=root/"override"
            self.assertEqual(library_directory({"library_root":str(external)},app),external)
            self.assertEqual(library_directory({"library_root":str(external)},app,override),override)
            self.assertEqual(library_setting(external,app),str(external))
