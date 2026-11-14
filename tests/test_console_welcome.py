"""The welcome banner and box art lead Now Playing and Playlists before any game is chosen."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from redux.console_ui import ConsoleApp
from redux.console_layout import ReferenceLayout
from redux.console_secondary import SecondaryScreens
from redux.ui_assets import Artwork
from redux.ui_model import ScreenHistory


class WelcomeTests(unittest.TestCase):
    def app(self,games=(),focus="welcome:card"):
        app=object.__new__(ConsoleApp)
        app.screen="home";app.overlay=None;app.focus=focus;app.targets=[];app.callbacks={}
        app.all_games=list(games);app.rows=list(games);app.selected_id=games[0]["id"] if games else None
        app.selected=lambda:next((g for g in app.all_games if g["id"]==app.selected_id),None)
        app.inserted_disc=lambda:None
        app.art=SimpleNamespace(welcome=lambda kind="hero":Path("welcome-"+kind),path=lambda system,kind="hero":Path(f"{system}-{kind}"))
        app.pictures=[];app.texts=[]
        app.picture=lambda path,*a,**k:(app.pictures.append(path),True)[1]
        app.display=lambda x,y,value,*a,**k:app.texts.append(str(value))
        app.text=app.display
        app.rect=app.icon=app.panel=app._identity=app._controller_hint=app._top_shade=lambda *a,**k:None
        app._badge=lambda system,x,y:(app.texts.append("badge:"+system),40)[1]
        app.fit_text=lambda value,*a,**k:str(value)
        app.button_canvas=lambda key,label,*a,**k:app.target(key,(0,0,1,1),a[-1] if a else None,label)
        app.target=lambda key,bounds,callback,label="":(app.targets.append(key),app.callbacks.__setitem__(key,callback))
        app._game_hero=lambda game:Path("hero-of-"+str(game["id"]))
        app._game_artwork=lambda game,role:None;app._game_screenshot=lambda game:None
        app.add_files=lambda:app.texts.append("add_files");app.play=lambda:app.texts.append("play")
        app.open_game=lambda identity:app.texts.append(f"open:{identity}");app.scan_folder=lambda:None
        app.library=SimpleNamespace(games=lambda view=None:[],collections=lambda:[])
        app.pages={"collections":0};app.history=ScreenHistory()
        return app

    def test_empty_home_shows_the_banner_and_the_welcome_card_first(self):
        app=self.app()
        ReferenceLayout._home(app)
        self.assertEqual(app.pictures[0],Path("welcome-hero"))
        self.assertIn("WELCOME TO REDUX",app.texts);self.assertIn("badge:welcome",app.texts)
        self.assertEqual(app.targets[:4],["hero:play","hero:details","welcome:card","home:storage"])
        app.navigate=lambda screen,**k:app.texts.append("navigate:"+screen);app._set_settings=lambda section:app.texts.append("section:"+section)
        app.callbacks["welcome:card"]();app.callbacks["hero:play"]()
        self.assertEqual(app.texts[-4:],["navigate:settings","section:storage","navigate:settings","section:storage"])
        self.assertNotIn("add_files",app.texts)

    def test_welcome_card_leads_the_games_row_and_fronts_the_banner_while_focused(self):
        games=[{"id":1,"title":"First","system":"psx","last_played":None},{"id":2,"title":"Second","system":"saturn","last_played":None}]
        app=self.app(games)
        ReferenceLayout._home(app)
        self.assertEqual(app.pictures[0],Path("welcome-hero"))
        self.assertIn("welcome-box",[str(p) for p in app.pictures])
        row=[key for key in app.targets if key=="welcome:card" or key.startswith("game:")]
        self.assertEqual(row,["welcome:card","game:1","game:2"])
        app.focus="game:1";app.pictures.clear();app.texts.clear()
        ReferenceLayout._home(app)
        self.assertEqual(app.pictures[0],Path("hero-of-1"))
        self.assertIn("FIRST",app.texts);self.assertNotIn("WELCOME TO REDUX",app.texts)
        app.callbacks["hero:play"]()
        self.assertEqual(app.texts[-1],"play")

    def test_navigation_lands_on_the_welcome_entries(self):
        app=self.app();app.refresh=lambda:None;app.schedule_draw=lambda:None
        app.view="all";app.collection_id=None;app.system_filter=None;app.search_text="";app.facet_filters={}
        app.facet_tab="system";app.sort="title";app.details_tab="play";app.keyboard_visible=True;app.embedded=None
        app.navigate("home");self.assertEqual(app.focus,"welcome:card")
        app.navigate("collections");self.assertEqual(app.focus,"collection:welcome")

    def test_playlists_lead_with_the_welcome_tile(self):
        app=self.app();app.screen="collections";app.focus="collection:welcome"
        app._secondary_heading=lambda *a,**k:None;app.create_collection_dialog=lambda:None
        app._open_collection_view=lambda view:app.texts.append("view:"+view)
        app._open_collection_view_all=lambda:app.texts.append("view:all")
        app._page_bounds=lambda key,total,size:(0,0);app._secondary_pager=lambda *a,**k:None
        SecondaryScreens._collections(app)
        self.assertEqual(app.targets[1:2],["collection:welcome"])
        self.assertFalse(any(key.startswith("collection:") and key not in {"collection:new","collection:welcome"} for key in app.targets),"the built-in views no longer live on Playlists")
        self.assertEqual([str(p) for p in app.pictures[:2]],["welcome-box","welcome-hero"])
        app.callbacks["collection:welcome"]()
        self.assertEqual(app.texts[-1],"view:all")

    def test_welcome_art_ships_with_the_frontend(self):
        for kind in ("hero","box"):
            path=Artwork.bundled("welcome","banner.jpg" if kind=="hero" else "box.jpg")
            self.assertIsNotNone(path,kind);self.assertTrue(path.is_file())
            self.assertLess(path.stat().st_size,600_000)


if __name__=="__main__":unittest.main()
