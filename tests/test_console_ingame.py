"""Model actions and save metadata survive presentation without native UI."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from redux.console_ingame import ConsoleIngame
from redux.console_overlays import OverlayScreens
from redux.state_presentation import SlotCard,format_saved_at


class PanelHarness(ConsoleIngame,OverlayScreens):
    def __init__(self,overlay):
        self.overlay=overlay;self.focus="overlay:0";self.targets=[];self.drawn=[];self.pictures=[]
        self.embedded=object();self.current_game={"title":"Real session game"}
    def display(self,x,y,value,*args,**kwargs):self.drawn.append(("text",x,y,value))
    text=display
    def panel(self,x,y,w,h,*args,**kwargs):self.drawn.append(("panel",x,y,w,h))
    rect=panel
    def picture(self,path,*args,**kwargs):self.pictures.append(path);return True
    def fit_text(self,value,*args,**kwargs):return str(value)
    def target(self,key,bounds,callback,label):self.targets.append(SimpleNamespace(key=key,bounds=bounds,callback=callback,label=label))
    def button_canvas(self,key,label,x,y,w,h,callback,*args,**kwargs):self.target(key,(x,y,x+w,y+h),callback,label)
    def _controller_hint(self,*args,**kwargs):pass
    def schedule_draw(self):pass
    def _overlay_game(self,in_game=False):return self.current_game


class IngamePanelTests(unittest.TestCase):
    def assert_targets_accessible(self,app):
        seen=set()
        for target in app.targets:
            self.assertNotIn(target.key,seen);seen.add(target.key)
            x1,y1,x2,y2=target.bounds
            self.assertTrue(0<=x1<x2<=1280 and 0<=y1<y2<=720,(target.key,target.bounds))
        for index,first in enumerate(app.targets):
            for second in app.targets[index+1:]:
                a,b,c,d=first.bounds;e,f,g,h=second.bounds
                self.assertFalse(a<g and c>e and b<h and d>f,(first.key,second.key))

    def test_display_panel_preserves_real_game_preview_and_reaches_all_model_actions(self):
        selected=[]
        overlay={"title":"Display","message":"Real active-game settings","previous_focus":"gameplay:menu",
                 "items":[(f"Actual display option {number}",lambda n=number:selected.append(n)) for number in range(10)]}
        app=PanelHarness(overlay);app.focus="overlay:7";app._display_panel(overlay)
        self.assert_targets_accessible(app)
        self.assertEqual({target.key for target in app.targets},{f"overlay:{n}" for n in range(10)})
        self.assertTrue(all(row[2]>=292 for row in app.drawn if row[0]=="panel"),"Display panel must leave the genuine paused-frame area visible")
        self.assertEqual(app.pictures,[],"Do not replace paused gameplay with unrelated artwork")
        next(target for target in app.targets if target.key=="overlay:7").callback()
        self.assertEqual(selected,[7]);self.assertIsNone(app.overlay)
        self.assertEqual(app.focus,"gameplay:menu")

    def test_controls_panel_displays_only_controller_model_labels_and_invokes_matching_assignment(self):
        assigned=[]
        labels=["PLAYER 1 · Atari VCS Wireless Modern Controller","PLAYER 2 · No controller assigned",
                "Assign connected USB controller to player 2","Back to game menu"]
        overlay={"title":"Controllers","items":[(label,lambda n=n:assigned.append(n)) for n,label in enumerate(labels)]}
        app=PanelHarness(overlay);app._controls_panel(overlay)
        self.assert_targets_accessible(app)
        self.assertEqual([target.label for target in app.targets],labels)
        next(target for target in app.targets if target.key=="overlay:2").callback()
        self.assertEqual(assigned,[2])
        self.assertFalse(any("XInput" in str(row) for row in app.drawn))

    def test_save_cards_use_their_own_thumbnails_dates_and_callbacks(self):
        saved="2026-11-08T21:00:00+00:00";selected=[]
        slot_cards=(SlotCard(0,True,saved,Path("C:/fixture/slot0.png")),SlotCard(1,False),
                    SlotCard(2,True,saved,Path("C:/fixture/slot2.png"),True,False,"Different core"),
                    SlotCard(3,True,None,None,False,False,"Damaged state"))
        overlay={"title":"Load state","operation":"load","cards":slot_cards,"previous_focus":"gameplay:menu",
                 "items":[(f"Real slot {n}",lambda n=n:selected.append(n)) for n in range(4)]+[("Back to game menu",lambda:selected.append("back"))]}
        app=PanelHarness(overlay);app.focus="overlay:2";app._state_slots_panel(overlay)
        self.assert_targets_accessible(app)
        self.assertEqual(app.pictures,[Path("C:/fixture/slot0.png"),Path("C:/fixture/slot2.png")])
        values=[row[3] for row in app.drawn if row[0]=="text"]
        self.assertIn("OLD VERSION",values);self.assertIn("UNAVAILABLE",values)
        self.assertIn("Empty slot",values);self.assertIn(format_saved_at(saved),values)
        self.assertEqual([target.key for target in app.targets],[f"overlay:{n}" for n in range(5)])
        next(target for target in app.targets if target.key=="overlay:2").callback()
        self.assertEqual(selected,[2],"A card must use the model's callback even when incompatible; the service owns the warning")

    def test_absent_slot_model_does_not_fabricate_four_saved_games(self):
        selected=[];overlay={"items":[("Back",lambda:selected.append("back"))],"cards":(),"operation":"save"}
        app=PanelHarness(overlay);app._state_slots_panel(overlay)
        self.assertEqual(len(app.targets),1);self.assertEqual(app.targets[0].label,"Back")
        self.assertEqual(app.pictures,[])
        self.assertFalse(any(str(row[3]).startswith("SLOT ") for row in app.drawn if row[0]=="text"))

    def test_long_main_menu_scrolls_to_focused_real_action_without_dropping_callback(self):
        selected=[];overlay={"title":"Game paused","previous_focus":"gameplay:menu",
             "items":[(f"Real session action {n}",lambda n=n:selected.append(n)) for n in range(12)]}
        app=PanelHarness(overlay);app.focus="overlay:11";app._game_menu_panel(overlay)
        self.assert_targets_accessible(app)
        self.assertEqual(len(app.targets),8)
        self.assertIn("overlay:11",[target.key for target in app.targets])
        self.assertIn("Real session game",[row[3] for row in app.drawn if row[0]=="text"])
        next(target for target in app.targets if target.key=="overlay:11").callback()
        self.assertEqual(selected,[11]);self.assertEqual(app.focus,"gameplay:menu")


if __name__=="__main__":unittest.main()
