"""Video choices are portable, durable and independent of local star ratings."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from redux.console_ui import ConsoleApp
from redux.game_preferences import GamePreferences
from redux.video_filters import FILTERS


class GameFilterPreferenceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)/"library"
        self.preferences=GamePreferences(self.root,4,"b"*64)

    def test_default_filter_read_does_not_create_preferences_or_library(self):
        self.assertEqual(self.preferences.video_filter(),"sharp")
        self.assertFalse(self.root.exists())

    def test_filter_survives_new_object_and_preserves_rating_and_unrelated_choices(self):
        self.preferences.directory.mkdir(parents=True)
        original={"rating":4,"controller":{"layout":"日本語","ports":[0,1]},"favorite":True}
        self.preferences.path.write_text(json.dumps(original),encoding="utf-8")
        self.preferences.set_video_filter("scanlines")
        reopened=GamePreferences(self.root,4,"b"*64)
        self.assertEqual(reopened.video_filter(),"scanlines");self.assertEqual(reopened.rating(),4)
        self.assertEqual(json.loads(self.preferences.path.read_text(encoding="utf-8")),dict(original,video_filter="scanlines"))
        reopened.set_rating(2)
        self.assertEqual(GamePreferences(self.root,4,"b"*64).video_filter(),"scanlines")

    def test_reused_database_id_with_different_bundle_does_not_inherit_filter(self):
        self.preferences.set_video_filter("smooth")
        other=GamePreferences(self.root,4,"c"*64)
        self.assertEqual(other.video_filter(),"sharp")
        other.set_video_filter("scanlines")
        self.assertEqual(self.preferences.video_filter(),"smooth")

    def test_invalid_filter_is_rejected_without_replacing_existing_preference_file(self):
        self.preferences.set_video_filter("smooth");original=self.preferences.path.read_bytes()
        for value in (None,True,1,[],{},"crt-custom","SHARP"):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.preferences.set_video_filter(value)
            self.assertEqual(self.preferences.path.read_bytes(),original)

    def test_malformed_or_unknown_saved_choice_is_reported_without_rewriting(self):
        self.preferences.directory.mkdir(parents=True)
        for data in (b'{"video_filter":"unknown"}',b'{"video_filter":true}',b'{"video_filter":"sharp","video_filter":"smooth"}',b'{broken'):
            with self.subTest(data=data):
                self.preferences.path.write_bytes(data)
                with self.assertRaises(ValueError):self.preferences.video_filter()
                self.assertEqual(self.preferences.path.read_bytes(),data)

    def test_failed_atomic_publication_preserves_prior_filter_rating_and_removes_private_stage(self):
        self.preferences.set_rating(5);self.preferences.set_video_filter("sharp")
        original=self.preferences.path.read_bytes()
        with patch("redux.game_preferences.os.replace",side_effect=OSError("storage removed")):
            with self.assertRaisesRegex(OSError,"storage removed"):
                self.preferences.set_video_filter("scanlines")
        self.assertEqual(self.preferences.path.read_bytes(),original)
        self.assertEqual(list(self.preferences.directory.glob("*.tmp")),[])

    def test_filter_update_does_not_overwrite_an_external_preference_change(self):
        self.preferences.set_rating(3);original_read=self.preferences._read_bytes;reads=0
        def changed_read():
            nonlocal reads
            reads+=1
            if reads==2:self.preferences.path.write_text('{"rating":2,"video_filter":"smooth","changed":true}',encoding="utf-8")
            return original_read()
        with patch.object(self.preferences,"_read_bytes",side_effect=changed_read):
            with self.assertRaisesRegex(RuntimeError,"changed"):
                self.preferences.set_video_filter("scanlines")
        self.assertEqual(json.loads(self.preferences.path.read_text(encoding="utf-8")),{"rating":2,"video_filter":"smooth","changed":True})

    def test_display_menu_selection_persists_for_system_without_changing_game_rating(self):
        from redux.display_preferences import DisplayPreferences
        self.preferences.set_rating(4)
        original_preferences=self.preferences.path.read_bytes()
        app=object.__new__(ConsoleApp)
        app.embedded=object();app.embedded_game={"id":4,"bundle_hash":"b"*64,"system":"nes"}
        app.library=SimpleNamespace(root=self.root);app.game_filter="sharp";app.game_photo_key=("old-frame",)
        app.focus="overlay:0";app.overlay=None;app.schedule_draw=Mock()
        app.video_filter_menu()
        previous=app.game_display.filter_id
        label,callback=app.overlay["items"][0]
        self.assertTrue(label.startswith("FILTER"));callback()
        self.assertNotEqual(app.game_display.filter_id,previous);self.assertIsNone(app.game_photo_key)
        self.assertEqual(DisplayPreferences(self.root).get("nes").filter_id,app.game_display.filter_id)
        self.assertEqual(self.preferences.rating(),4)
        self.assertEqual(self.preferences.path.read_bytes(),original_preferences)
        self.assertEqual(app.overlay["title"],"Display")
        self.assertEqual(app.overlay["kind"],"display_panel")
        self.assertIn(app.game_display.filter_id.upper(),app.overlay["items"][0][0])


if __name__=="__main__":unittest.main()
