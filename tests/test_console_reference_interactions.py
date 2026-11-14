"""Interaction-context regressions for the reference-style console shell.

No Tk windows, emulator processes, screenshots or pixel snapshots are needed.
"""
from types import SimpleNamespace
import unittest

from redux.console_ui import ConsoleApp
from redux.ui_model import FocusTarget,ScreenHistory


class ReferenceInteractionTests(unittest.TestCase):
    def bare(self, screen="text"):
        app=object.__new__(ConsoleApp)
        app.root=SimpleNamespace(grab_current=lambda:None,clipboard_get=lambda:"pasted text",iconify=lambda:None)
        app.busy=False;app.embedded=None;app.playback=SimpleNamespace(active=lambda:[])
        app.screen=screen;app.overlay=None;app.focus="nav:home";app.targets=[];app.callbacks={}
        app.text_prompt={"value":"Existing name"};app.search_text="Existing query"
        app.pages={"search":0};app.rows=[];app.all_games=[];app.facet_filters={}
        app.selected_id=1;app.system_filter=None;app.keyboard_visible=True
        app.schedule_draw=lambda:None;app.refresh=lambda:None
        return app

    def event(self,keysym,char="",state=0):return SimpleNamespace(keysym=keysym,char=char,state=state)

    def test_controller_details_tabs_work_from_launch_panel_and_preserve_game_scope(self):
        app=self.bare("details");app.details_tab="play";app.focus="details:launch"
        app.system_filter="sega_cd"
        for action,expected in (("right","manage"),("rb","rate"),("lb","manage"),("left","play")):
            app._route_controller_events([SimpleNamespace(action=action,pressed=True)])
            self.assertEqual((app.details_tab,app.focus),(expected,f"details:{expected}"))
            self.assertEqual((app.selected_id,app.system_filter),(1,"sega_cd"))

    def test_details_content_back_and_tabs_are_reachable_without_mouse(self):
        for tab,first in (("play","details:launch"),("manage","details:metadata"),("rate","rating:1")):
            app=self.bare("details");app.details_tab=tab;app.focus=f"details:{tab}"
            for action,expected in (("down",first),("up",f"details:{tab}"),("up","details:back"),("down",f"details:{tab}")):
                app.input_action(action);self.assertEqual(app.focus,expected)

    def test_details_controller_can_activate_manage_action_and_return(self):
        app=self.bare("details");app.details_tab="play";app.focus="details:play";calls=[]
        app.callbacks={"details:metadata":lambda:calls.append("metadata")}
        for action in ("right","down","select"):
            app._route_controller_events([SimpleNamespace(action=action,pressed=True)])
        self.assertEqual(calls,["metadata"])
        app.back=lambda:calls.append("back")
        app._route_controller_events([SimpleNamespace(action="back",pressed=True)])
        self.assertEqual(calls,["metadata","back"])

    def test_controller_hides_pointer_and_stationary_motion_cannot_steal_focus(self):
        app=self.bare("details");changes=[]
        app.canvas=SimpleNamespace(winfo_pointerxy=lambda:(150,200),configure=lambda **kw:changes.append(kw))
        app.focus="details:play";app.details_tab="play"
        app.targets=[FocusTarget("details:launch",(0,0,300,300))]
        app.scale=1;app.offset_x=app.offset_y=0
        app._route_controller_events([SimpleNamespace(action="right",pressed=True)])
        self.assertEqual(changes[-1],{"cursor":"none"})
        app._motion(SimpleNamespace(x=150,y=200,x_root=150,y_root=200))
        self.assertEqual(app.focus,"details:manage")
        app._motion(SimpleNamespace(x=160,y=200,x_root=160,y_root=200))
        self.assertEqual(changes[-1],{"cursor":""})
        self.assertEqual(app.focus,"details:launch")

    def test_controller_releases_do_not_hide_pointer_or_navigate(self):
        app=self.bare("details");app.details_tab="play";app.focus="details:play"
        app._route_controller_events([SimpleNamespace(action="right",pressed=False)])
        self.assertFalse(getattr(app,"controller_navigation",False))
        self.assertEqual(app.focus,"details:play")

    def test_details_tab_shortcuts_do_not_escape_overlay(self):
        app=self.bare("details");app.details_tab="play";app.focus="overlay:0"
        app.overlay={"title":"Information","items":[("OK",lambda:None)]}
        app.input_action("rb")
        self.assertEqual((app.details_tab,app.focus),("play","overlay:0"))

    def navigation_app(self):
        app=self.bare("home")
        app.history=ScreenHistory();app.view="all";app.collection_id=None
        app.facet_tab="system";app.sort="title";app.details_tab="play";app.settings_section="general"
        return app

    def test_entering_media_and_collection_focuses_their_content(self):
        app=self.navigation_app()
        app.navigate("media")
        self.assertEqual(app.focus,"shot:0")
        app.rows=[{"id":4}]
        app.navigate("library")
        self.assertEqual((app.focus,app.selected_id),("game:4",4))

    def test_back_from_global_search_restores_collection_and_facet_scope(self):
        app=self.navigation_app()
        app.screen="library";app.system_filter="psx";app.collection_id=3;app.view="collection"
        app.facet_filters={"genre":"Action"};app.focus="game:1";app.sort="playtime"
        app.search_screen()
        self.assertIsNone(app.system_filter);self.assertIsNone(app.collection_id)
        self.assertEqual(app.facet_filters,{})
        app.back()
        self.assertEqual((app.screen,app.system_filter,app.collection_id),("library","psx",3))
        self.assertEqual(app.facet_filters,{"genre":"Action"})
        self.assertEqual((app.focus,app.selected_id,app.sort),("game:1",1,"playtime"))

    def test_enter_in_editor_notice_activates_modal_without_resubmitting_name(self):
        app=self.bare();accepted=[];modal=[]
        app._accept_text=lambda:accepted.append(True)
        app.overlay={"title":"Could not complete","items":[("OK",lambda:None)]}
        app.focus="overlay:0";app.callbacks={"overlay:0":lambda:modal.append(True)}
        app._key(self.event("Return","\r"))
        self.assertEqual(accepted,[])
        self.assertEqual(modal,[True])

    def test_editor_and_search_modal_do_not_accept_text_paste_or_backspace(self):
        for screen in ("text","search"):
            with self.subTest(screen=screen):
                app=self.bare(screen)
                app.overlay={"title":"Notice","items":[("OK",lambda:None)]}
                app._key(self.event("a","a"));app._paste()
                self.assertEqual(app.text_prompt["value"],"Existing name")
                self.assertEqual(app.search_text,"Existing query")
                app._key(self.event("BackSpace","\b"))
                self.assertIsNone(app.overlay)
                self.assertEqual(app.text_prompt["value"],"Existing name")
                self.assertEqual(app.search_text,"Existing query")

    def test_controller_shortcuts_cannot_navigate_or_mutate_behind_modal(self):
        app=self.bare("library");calls=[]
        app.overlay={"title":"Choose a rating","items":[("OK",lambda:None)]}
        app.favorite=lambda:calls.append("favorite")
        app.search_screen=lambda:calls.append("search")
        app.navigate=lambda *a,**k:calls.append("navigate")
        app.play=lambda:calls.append("play")
        for action in ("x","y","details","search","home","previous_tab","next_tab","menu","start"):
            app.input_action(action)
        self.assertEqual(calls,[])
        self.assertEqual(app.system_filter,None)

    def test_running_game_menu_start_returns_to_game_and_horizontal_focus_stays_modal(self):
        app=self.bare("home");backs=[]
        app.overlay={"title":"Game menu","items":[("Resume",lambda:None),("Save",lambda:None)]}
        app.focus="overlay:0"
        app.targets=[FocusTarget("overlay:0",(100,420,280,530)),FocusTarget("overlay:1",(280,420,460,530))]
        app.input_action("right");self.assertEqual(app.focus,"overlay:1")
        app.input_action("left");self.assertEqual(app.focus,"overlay:0")
        app.back=lambda:backs.append(True)
        app.input_action("menu");self.assertEqual(backs,[True])
        self.assertEqual(app.selected_id,1)

    def test_filtered_out_selection_cannot_play_or_change_its_favorite(self):
        app=self.bare("library");changed=[]
        app.selected=lambda:{"id":1,"available":True,"system":"nes"}
        app.library=SimpleNamespace(toggle_favorite=lambda identity:changed.append(identity))
        app.rows=[{"id":2}]
        self.assertIsNone(app._game_context())
        app.play();app.favorite()
        self.assertEqual(changed,[])
        app.rows=[{"id":1}]
        app.favorite()
        self.assertEqual(changed,[1])

    def test_submitting_search_results_selects_the_visibly_focused_game(self):
        app=self.bare("search")
        app.rows=[{"id":2},{"id":1}];app.selected_id=1
        app._type_key("RESULTS")
        self.assertEqual(app.focus,"game:2")
        self.assertEqual(app.selected_id,2,"Favorite and manage must act on the visibly focused result")

    def test_non_game_screens_do_not_expose_global_selection_to_favorite_shortcut(self):
        for screen in ("text","settings","media","collections","saves"):
            with self.subTest(screen=screen):
                app=self.bare(screen);changed=[]
                app.selected=lambda:{"id":1,"available":True,"system":"nes"}
                app.library=SimpleNamespace(toggle_favorite=lambda identity:changed.append(identity))
                app.favorite()
                self.assertEqual(changed,[],"A controller shortcut must not mutate a game absent from this screen")

    def test_controller_global_shortcuts_do_not_abandon_name_editor(self):
        app=self.bare("text");calls=[]
        app.favorite=lambda:calls.append("favorite")
        app.search_screen=lambda:calls.append("search")
        app.navigate=lambda *a,**k:calls.append("navigate")
        app.play=lambda:calls.append("play")
        for action in ("x","y","home","menu","next_tab","previous_tab"):
            app.input_action(action)
        self.assertEqual(calls,[])
        self.assertEqual(app.screen,"text")
        self.assertEqual(app.text_prompt["value"],"Existing name")

    def test_async_external_resume_blocks_other_callbacks_until_backend_acknowledges(self):
        app=self.bare("home");leaked=[];queued=[];paused=[]
        bridge=SimpleNamespace(ready=True,resume=lambda:None)
        app.bridges={"owned-session":bridge}
        app.playback=SimpleNamespace(active=lambda:[SimpleNamespace(session_id="owned-session",pause_source="backend")],
                                     set_backend_paused=lambda identity,value:paused.append((identity,value)))
        app.status=SimpleNamespace(set=lambda value:None)
        def task(work,done,description,**kwargs):app.busy=True;queued.append((work,done))
        app.task=task
        overlay={"title":"Game menu","items":[],"previous_focus":"game:1"}
        app.overlay=overlay;app.callbacks={"game:1":lambda:leaked.append(True)}
        app._overlay_run(overlay,lambda:app._resume("owned-session"))()
        app.activate()
        self.assertEqual(leaked,[])
        self.assertTrue(app.busy)
        self.assertEqual(len(queued),1)
        queued[0][0]();app.busy=False;queued[0][1](None)
        self.assertEqual(paused,[("owned-session",False)])

    def test_visible_search_results_are_not_under_keyboard_and_targets_stay_onscreen(self):
        app=self.bare("search");panels=[]
        app.rows=[{"id":number} for number in range(1,13)]
        app.focus="key:Q";app.search_text=""
        app.panel=lambda x,y,w,h,*a,**k:panels.append((x,y,x+w,y+h))
        app.rect=lambda *a,**k:None;app.text=lambda *a,**k:None;app.display=lambda *a,**k:None
        app.icon=lambda *a,**k:None;app.fit_text=lambda value,*a,**k:value
        def card(game,x,y,w,h):app.target(f"game:{game['id']}",(x,y,x+w,y+h),lambda:None)
        app._card=card
        app._search()
        backdrop=next(bounds for bounds in panels if bounds[0]==0 and bounds[2]==1280)
        cards=[target for target in app.targets if target.key.startswith("game:")]
        self.assertTrue(cards)
        self.assertLess(len(cards),len(app.rows))
        self.assertTrue(all(target.bounds[3]<=backdrop[1] for target in cards),"Keyboard must not cover clickable game cards")
        self.assertTrue(all(0<=left<right<=1280 and 0<=top<bottom<=720 for target in app.targets for left,top,right,bottom in (target.bounds,)))


if __name__=="__main__":unittest.main()
