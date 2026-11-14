"""The System screen opens on a horizontal menu; sections open from it and B returns to it."""
from types import SimpleNamespace
import unittest

from redux.console_ui import ConsoleApp
from redux.console_secondary import SecondaryScreens
from redux.ui_model import ScreenHistory
from redux.ui_theme import Painter


class Canvas:
    def __init__(self):self.items=[]
    def _add(self,kind,args,options):
        self.items.append((kind,args,options));return len(self.items)
    def create_image(self,*args,**options):return self._add("image",args,options)
    def create_line(self,*args,**options):return self._add("line",args,options)
    def create_oval(self,*args,**options):return self._add("oval",args,options)
    def create_polygon(self,*args,**options):return self._add("polygon",args,options)
    def create_text(self,*args,**options):return self._add("text",args,options)


class SystemMenuTests(unittest.TestCase):
    def bare(self):
        app=object.__new__(ConsoleApp)
        app.root=SimpleNamespace(grab_current=lambda:None)
        app.busy=False;app.embedded=None;app.playback=SimpleNamespace(active=lambda:[])
        app.screen="home";app.overlay=None;app.focus="hero:play";app.targets=[];app.callbacks={}
        app.history=ScreenHistory();app.view="all";app.collection_id=None;app.system_filter=None
        app.search_text="";app.facet_filters={};app.facet_tab="system";app.sort="title";app.details_tab="play"
        app.selected_id=1;app.keyboard_visible=True;app.pages={};app.rows=[];app.all_games=[]
        app.settings_section="general";app.settings_menu=True
        app.schedule_draw=lambda:None;app.refresh=lambda:None
        return app

    def test_system_opens_on_the_menu_with_home_focused(self):
        app=self.bare()
        app.navigate("settings")
        self.assertEqual((app.screen,app.settings_menu,app.focus),("settings",True,"system:home"))

    def test_choosing_a_section_opens_its_page_and_back_returns_to_the_menu_not_the_previous_screen(self):
        app=self.bare()
        app.navigate("settings")
        app._set_settings("input")
        self.assertEqual((app.settings_menu,app.settings_section,app.focus),(False,"input","setting:input"))
        app.back()
        self.assertEqual((app.screen,app.settings_menu,app.focus),("settings",True,"system:input"))
        app.back()
        self.assertEqual(app.screen,"home")

    def test_menu_rows_are_the_only_targets_and_name_the_focused_item(self):
        app=self.bare();app.screen="settings";app.focus="system:input"
        drawn=[];texts=[]
        app.panel=lambda *a,**k:drawn.append(("panel",a))
        app.rect=lambda *a,**k:drawn.append(("rect",a))
        app.icon=lambda *a,**k:drawn.append(("icon",a))
        app.display=lambda x,y,value,*a,**k:texts.append(value)
        app.target=lambda key,bounds,callback,label="":(app.targets.append(key),app.callbacks.__setitem__(key,callback))
        app.targets=["nav:search"];app.callbacks={"nav:search":lambda:None}
        app.navigate=lambda *a,**k:drawn.append(("navigate",a))
        SecondaryScreens._settings(app)
        self.assertEqual(app.targets,[f"system:{item[0]}" for item in SecondaryScreens.SYSTEM_ITEMS])
        self.assertNotIn("nav:search",app.callbacks)
        self.assertEqual(texts[:2],["CONTROLLERS","Made to feel at home in your hands."])
        self.assertIn("Home",texts);self.assertIn("Exit",texts)
        self.assertEqual(drawn[0],("panel",(0,0,1280,720,"#000000b4",0)))
        self.assertEqual(sum(1 for kind,args in drawn if kind=="rect" and args[3]==3),1)
        app.callbacks["system:home"]()
        self.assertEqual(drawn[-1],("navigate",("home",)))

    def test_menu_falls_back_to_home_when_focus_is_stale(self):
        app=self.bare();app.screen="settings";app.focus="setting:general";texts=[]
        app.panel=app.rect=app.icon=lambda *a,**k:None
        app.display=lambda x,y,value,*a,**k:texts.append(value)
        app.target=lambda key,bounds,callback,label="":None
        SecondaryScreens._settings(app)
        self.assertEqual((app.focus,texts[0]),("system:home","REDUX"))

    def test_new_icons_draw(self):
        canvas=Canvas();painter=Painter(canvas)
        self.assertTrue(painter.icon("power",0,0,24))
        self.assertTrue(painter.icon("info",0,0,24))
        self.assertTrue(any(kind=="line" for kind,_,_ in canvas.items))


if __name__=="__main__":unittest.main()
