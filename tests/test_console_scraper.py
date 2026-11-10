"""Controller scraping, credential privacy and queued-work UI boundaries."""
import base64
import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from redux.console_ui import ConsoleApp
from redux.scraper_providers import provider_info
from redux.scraper_settings import ScraperSettings
from redux.scraping import ScrapeBatchResult
from redux.ui_model import FocusTarget, next_focus


class TestProtector:
    """No Windows account state is read or written by these harness tests."""
    def protect(self, value):
        return base64.b64encode(bytes(byte ^ 0xA5 for byte in value))

    def unprotect(self, value):
        return bytes(byte ^ 0xA5 for byte in base64.b64decode(value, validate=True))


class ForbiddenDialogs:
    def __getattr__(self, _name):
        raise AssertionError("Scraper account setup must use the controller UI")


class ConsoleScraperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library_root = self.root / "library"
        self.local_root = self.root / "local-user"
        self.protector = TestProtector()
        self.app = app = object.__new__(ConsoleApp)
        app.library = SimpleNamespace(root=self.library_root)
        app._scraper_store = ScraperSettings(self.library_root, self.local_root, protector=self.protector)
        app.root = SimpleNamespace(grab_current=lambda:None, clipboard_get=lambda:"")
        app.screen = "settings"; app.settings_section = "artwork"; app.focus = "scraper:source"
        app.overlay = None; app.busy = False; app.embedded = None; app.playback = None
        app.preferences = {}; app.text_prompt = None; app.selected_id = 1
        app.rows = [{"id":1,"title":"Selected game","system":"psx"}]
        app.all_games = list(app.rows); app._selected = app.rows[0]
        app.selected = lambda:app._selected
        app.targets = []; app.callbacks = {}; app.drawn = []; app.pages = {}
        app.held_keys = set(); app.events = queue.Queue(); app.cancelled = threading.Event()
        app.status = SimpleNamespace(set=Mock()); app.art = SimpleNamespace(clear=Mock())
        app.schedule_draw = Mock(); app.refresh = Mock(); app.navigate = Mock()
        app.favorite = Mock(); app.open_file_browser = Mock(side_effect=AssertionError("No file picker for account input"))
        app.task_calls = []
        app.task = lambda work,done,description,**kwargs:app.task_calls.append((work,done,description,kwargs))
        app.fit_text = lambda value,*args,**kwargs:str(value)
        app.panel = lambda *args,**kwargs:None
        app.rect = lambda *args,**kwargs:None
        app.icon = lambda *args,**kwargs:None
        app._controller_hint = lambda *args,**kwargs:None
        app.text = lambda x,y,value,*args,**kwargs:app.drawn.append(str(value))
        app.display = app.text
        def target(key,bounds,callback,label):
            app.targets.append(FocusTarget(key,bounds,label))
            app.callbacks[key] = callback
        app.target = target
        def button(key,label,x,y,w,h,callback,*args,**kwargs):
            app.drawn.append(label)
            app.target(key,(x,y,x+w,y+h),callback,label)
        app.button_canvas = button

    def choose(self, label_prefix):
        overlay = self.app.overlay
        matches = [(label, callback) for label, callback in overlay["items"] if label.startswith(label_prefix)]
        self.assertEqual(len(matches),1,(label_prefix,[label for label,_ in overlay["items"]]))
        self.app._overlay_run(overlay,matches[0][1])()

    def field(self, provider, key):
        return next(field for info in provider_info() if info["id"]==provider
                    for field in info["credentials"] if field["key"]==key)

    def render_editor(self):
        self.app.drawn.clear(); self.app.targets.clear(); self.app.callbacks.clear()
        self.app._text_editor()

    def test_all_six_services_are_reachable_from_controller_menu(self):
        app = self.app
        infos = provider_info()
        self.assertEqual({info["id"] for info in infos},
                         {"screenscraper","thegamesdb","hfsdb","igdb","arcadedb","libretro"})
        for info in infos:
            app.scraper_provider_menu()
            self.choose(info["name"])
            self.assertEqual(app.scraper_settings().load()["provider"],info["id"])
            self.assertIsNone(app.overlay)
        app.open_file_browser.assert_not_called()

    def test_accounts_are_visible_from_settings_even_with_libretro_selected(self):
        app = self.app
        app.scraper_settings().save({"provider":"libretro"})
        app._artwork_settings()
        self.assertEqual(next(target.label for target in app.targets if target.key=="scraper:account"),
                         "Accounts & API keys")
        app.focus="scraper:account";app.input_action("select")
        self.assertEqual(app.overlay["title"],"Accounts & API keys")
        self.assertEqual(len(app.overlay["items"]),len(provider_info())+1)
        self.assertIn("Libretro thumbnails",app.overlay["message"])
        self.choose("ScreenScraper")
        labels=[label for label,_ in app.overlay["items"]]
        self.assertEqual(app.overlay["title"],"ScreenScraper account")
        for field in ("Username", "Password", "Developer API ID", "Developer API password"):
            self.assertTrue(any(label.startswith(field+"  ·") for label in labels),field)
        self.assertIn("Use this artwork service",labels)
        self.assertEqual(app.scraper_settings().load()["provider"],"libretro")

    def test_saving_and_cancelling_other_service_account_does_not_switch_scraper(self):
        app=self.app
        app.scraper_settings().save({"provider":"libretro"})
        app.scraper_account_menu();self.choose("ScreenScraper");self.choose("Username")
        app.text_prompt["value"]="new-account-name"
        saved_prompt=app.text_prompt;app._accept_text()
        self.assertEqual(saved_prompt["value"],"")
        self.assertEqual(app.overlay["title"],"ScreenScraper account")
        self.assertEqual(app.scraper_settings().credentials("screenscraper")["username"],"new-account-name")
        self.assertEqual(app.scraper_settings().load()["provider"],"libretro")
        self.choose("Password")
        app.text_prompt["value"]="discard-password-draft"
        cancelled_prompt=app.text_prompt;app.input_action("back")
        self.assertEqual(cancelled_prompt["value"],"")
        self.assertEqual(app.overlay["title"],"ScreenScraper account")
        self.assertNotIn("password",app.scraper_settings().credentials("screenscraper"))
        self.assertEqual(app.scraper_settings().load()["provider"],"libretro")
        app.input_action("back")
        self.assertEqual(app.overlay["title"],"Accounts & API keys")
        self.choose("IGDB")
        self.assertEqual(app.overlay["title"],"IGDB account")
        self.choose("Back")
        self.assertEqual(app.overlay["title"],"Accounts & API keys")

    def test_account_chooser_status_does_not_reveal_values_and_labels_no_account_services(self):
        app=self.app
        store=app.scraper_settings()
        for field in ("username", "password", "developer_id", "developer_password"):
            store.set_credential("screenscraper",field,"private-value-for-"+field)
        store.set_credential("igdb","client_id","private-client-id")
        app.scraper_accounts_menu()
        labels=[label for label,_ in app.overlay["items"]]
        self.assertTrue(next(label for label in labels if label.startswith("ScreenScraper")).endswith("Saved"))
        self.assertTrue(next(label for label in labels if label.startswith("IGDB")).endswith("1/2 saved"))
        for name in ("ArcadeDB", "Libretro thumbnails"):
            self.assertIn("No account needed",next(label for label in labels if label.startswith(name)))
        self.assertNotIn("private-value",repr(app.overlay))
        self.assertNotIn("private-client-id",repr(app.overlay))

    def test_use_this_service_is_explicit_and_scope_links_to_all_accounts(self):
        app=self.app
        app.scraper_settings().save({"provider":"libretro"})
        app.scraper_scope_menu();self.choose("Accounts & API keys")
        self.assertEqual(app.overlay["title"],"Accounts & API keys")
        self.choose("TheGamesDB")
        self.assertEqual(app.scraper_settings().load()["provider"],"libretro")
        self.choose("API key")
        app.text_prompt["value"]="private-api-key";app._accept_text()
        self.assertEqual(app.overlay["title"],"TheGamesDB account")
        self.assertEqual(app.scraper_settings().load()["provider"],"libretro")
        self.choose("Use this artwork service")
        self.assertEqual(app.scraper_settings().load()["provider"],"thegamesdb")
        self.assertEqual(app.overlay["title"],"TheGamesDB account")
        self.assertNotIn("Use this artwork service",[label for label,_ in app.overlay["items"]])
        self.assertNotIn("private-api-key",repr(app.overlay))
        self.choose("Back")
        self.assertEqual(app.overlay["title"],"Accounts & API keys")
        app.input_action("back")
        self.assertIsNone(app.overlay)
        self.assertEqual(app.settings_section,"artwork")
        self.assertEqual(app.focus,"scraper:source")

    def test_every_required_credential_opens_controller_editor_without_native_dialog(self):
        app = self.app
        with patch("redux.console_ui.filedialog",ForbiddenDialogs()), \
             patch("redux.console_ui.messagebox",ForbiddenDialogs()):
            for info in provider_info():
                for field in info["credentials"]:
                    with self.subTest(provider=info["id"],field=field["key"]):
                        app.scraper_account_menu(info["id"])
                        self.choose(field["label"])
                        self.assertEqual(app.screen,"text")
                        self.assertTrue(app.text_prompt["credential"])
                        self.assertEqual(app.text_prompt["secret"],field["secret"])
                        self.assertEqual(app.text_prompt["value"],"")
                        self.render_editor()
                        self.assertIn("credential:PAGE",app.callbacks)
                        self.assertIn("credential:SAVE",app.callbacks)
                        app.input_action("back")
                        self.assertEqual(app.screen,"settings")
                        self.assertEqual(app.overlay["title"],info["name"]+" account")
        app.open_file_browser.assert_not_called()

    def test_secret_is_masked_everywhere_and_save_returns_to_account_and_clears_draft(self):
        app = self.app
        app.scraper_account_menu("screenscraper"); self.choose("Password")
        secret = "Private#Password-AbC019!"
        for character in secret:app._credential_edit(character)
        prompt = app.text_prompt
        self.render_editor()
        self.assertNotIn(secret,"\n".join(app.drawn))
        self.assertIn("\u2022"*len(secret)+" |",app.drawn)
        self.assertFalse(any(secret in target.label or secret in target.key for target in app.targets))
        app.callbacks["credential:SAVE"]()
        self.assertIsNone(app.text_prompt)
        self.assertEqual(prompt["value"],"")
        self.assertEqual(app.screen,"settings")
        self.assertEqual(app.overlay["title"],"ScreenScraper account")
        self.assertEqual(app.scraper_settings().credentials("screenscraper")["password"],secret)
        self.assertNotIn(secret,repr(app.overlay))
        self.assertNotIn(secret,json.dumps(app.preferences))
        self.assertFalse(any(secret.encode() in path.read_bytes() for path in self.library_root.rglob("*") if path.is_file()))

    def test_cancel_returns_to_same_account_without_saving_or_retaining_draft(self):
        app = self.app
        app.scraper_settings().set_credential("igdb","client_secret","existing-key")
        app.scraper_account_menu("igdb"); self.choose("Client secret")
        app.text_prompt["value"] = "discard-this-draft"
        prompt = app.text_prompt
        self.render_editor();app.callbacks["text:cancel"]()
        self.assertEqual(prompt["value"],"")
        self.assertIsNone(app.text_prompt)
        self.assertEqual(app.screen,"settings")
        self.assertEqual(app.overlay["title"],"IGDB account")
        self.assertEqual(app.scraper_settings().credentials("igdb")["client_secret"],"existing-key")

    def test_controller_pages_enter_case_sensitive_punctuation_and_remain_navigable(self):
        app = self.app
        app.edit_scraper_credential("screenscraper",self.field("screenscraper","password"))
        entered = ""
        for page,key in (("lower","a"),("upper","Z"),("symbols","#"),("symbols","\\"),("symbols","~")):
            for _ in range(3):
                if app.text_prompt["keyboard_page"]==page:break
                app._credential_edit("PAGE")
            self.assertEqual(app.text_prompt["keyboard_page"],page)
            self.render_editor()
            desired = "credential:key:"+str(ord(key))
            app.focus=desired;app.input_action("select")
            entered+=key
            self.assertEqual(app.text_prompt["value"],entered)
            # The page is usable by spatial controller navigation, not just a callback list.
            keys={target.key for target in app.targets};seen=set();pending=[app.targets[0].key]
            while pending:
                current=pending.pop()
                if current in seen:continue
                seen.add(current)
                pending.extend(next_focus(app.targets,current,direction) for direction in ("left","right","up","down"))
            self.assertEqual(seen,keys)
        app._credential_edit("SPACE");app._credential_edit("BACKSPACE")
        self.assertEqual(app.text_prompt["value"],entered)
        app._credential_edit("CLEAR");self.assertEqual(app.text_prompt["value"],"")

    def test_physical_typing_and_paste_preserve_text_and_enforce_the_4096_limit(self):
        app = self.app
        app.edit_scraper_credential("igdb",self.field("igdb","client_secret"))
        typed = "aAZ9#é🌙"
        for character in typed:
            self.assertEqual(app._key(SimpleNamespace(keysym=character,char=character,state=0)),"break")
        self.assertEqual(app.text_prompt["value"],typed)
        app.root.clipboard_get=lambda:"Quoted!API+=/\\"+"x"*5000+"\n\t"
        self.assertEqual(app._paste(),"break")
        self.assertEqual(len(app.text_prompt["value"]),4096)
        self.assertTrue(app.text_prompt["value"].startswith(typed+"Quoted!API+=/\\"))
        self.assertNotIn("\n",app.text_prompt["value"])
        app._key(SimpleNamespace(keysym="z",char="z",state=0))
        app._credential_edit("Z")
        self.assertEqual(len(app.text_prompt["value"]),4096)
        app.favorite.assert_not_called()
        self.assertEqual(app.preferences,{})
        self.assertFalse(self.library_root.exists())

    def test_active_modal_blocks_secret_typing_and_paste(self):
        app = self.app
        app.edit_scraper_credential("igdb",self.field("igdb","client_secret"))
        app.text_prompt["value"]="unchanged"
        app.menu("Could not complete",[("OK",lambda:None)])
        app.root.clipboard_get=lambda:"must-not-paste"
        app._key(SimpleNamespace(keysym="a",char="a",state=0));app._paste()
        self.assertEqual(app.text_prompt["value"],"unchanged")

    def test_batch_freezes_requested_ids_and_worker_queues_progress_without_touching_ui(self):
        app = self.app
        service = Mock()
        result=ScrapeBatchResult(updated=[1,2])
        def batch(identities,*,cancelled,progress):
            self.assertEqual(list(identities),[1,2])
            self.assertIs(cancelled,app.cancelled)
            progress(1,2,"First game")
            return result
        service.batch.side_effect=batch
        app.scraper_service=lambda:service
        identities=[1,2]
        app.scrape_game_ids(identities)
        identities[:]=[999]
        self.assertEqual(len(app.task_calls),1)
        work,done,_description,_kwargs=app.task_calls[0]
        output=[];errors=[]
        def worker():
            try:output.append(work())
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=worker);thread.start();thread.join(2)
        self.assertFalse(thread.is_alive())
        if errors:raise errors[0]
        self.assertEqual(output,[result])
        self.assertEqual(app.events.get_nowait(),("status","Artwork 1/2  ·  First game"))
        app.refresh.assert_not_called();app.schedule_draw.assert_not_called()
        app.art.clear.assert_not_called();app.status.set.assert_not_called()
        self.assertIsNone(app.overlay)
        done(result)
        app.refresh.assert_called_once();app.art.clear.assert_called_once()
        self.assertEqual(app.overlay["title"],"Artwork downloads")
        self.assertIn("2 updated",app.overlay["message"])

    def test_batch_scope_is_the_chosen_view_not_a_later_selection(self):
        app = self.app
        app.rows=[{"id":7},{"id":9}];app.all_games=[{"id":7},{"id":9},{"id":12}]
        service=Mock();service.batch.return_value=ScrapeBatchResult()
        app.scraper_service=lambda:service
        app.scraper_scope_menu();self.choose("Fill artwork in this view")
        app.rows[:]=[{"id":100}];app.selected_id=100
        work,done,_description,_kwargs=app.task_calls[-1];work()
        self.assertEqual(list(service.batch.call_args.args[0]),[7,9])

    def test_switching_library_reloads_portable_settings_but_keeps_local_account_scope(self):
        app = self.app
        app.scraper_settings().save({"provider":"igdb","region":"jp"})
        app.scraper_settings().set_credential("igdb","client_secret","same-local-account")
        old=app.scraper_settings();other=self.root/"another-library"
        def make(root):return ScraperSettings(root,self.local_root,protector=self.protector)
        with patch("redux.console_scraper.ScraperSettings",side_effect=make) as constructor:
            app.library=SimpleNamespace(root=other)
            current=app.scraper_settings()
            self.assertIsNot(current,old)
            constructor.assert_called_once_with(other)
            self.assertEqual(current.load()["provider"],"screenscraper")
            self.assertEqual(current.load()["region"],"us")
            self.assertEqual(current.credentials("igdb")["client_secret"],"same-local-account")
            self.assertIs(app.scraper_settings(),current)
        self.assertEqual(old.load()["provider"],"igdb")


if __name__=="__main__":unittest.main()
