"""Playlists are ordered lists of installed games; the order is the owner's, not the sort's."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from redux.console_ui import ConsoleApp
from redux.library import Library


class PlaylistOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.library=Library(root/"library")
        self.games=[]
        for name in ("Zebra","Apple","Mango"):
            media=root/f"{name}.j64";media.write_bytes(name.encode())
            self.games.append(self.library.import_game(media,system="jaguar",linked=True)[0]["id"])
        self.playlist=self.library.create_collection("Weekend")["id"]
        for game in self.games:self.library.add_to_collection(self.playlist,game)

    def ordered(self):return [g["id"] for g in self.library.games(view="collection",collection_id=self.playlist)]

    def test_games_keep_the_order_they_were_added_in_whatever_the_sort(self):
        self.assertEqual(self.ordered(),self.games)
        self.assertEqual([g["id"] for g in self.library.games(collection_id=self.playlist,sort="recent")],self.games)
        self.assertEqual([g["title"] for g in self.library.games()],["Apple","Mango","Zebra"])

    def test_move_up_and_down_renumbers_and_clamps_at_the_ends(self):
        zebra,apple,mango=self.games
        self.assertEqual(self.library.move_in_collection(self.playlist,mango,-1),[zebra,mango,apple])
        self.assertEqual(self.ordered(),[zebra,mango,apple])
        self.library.move_in_collection(self.playlist,zebra,-5)
        self.assertEqual(self.ordered(),[zebra,mango,apple])
        self.library.move_in_collection(self.playlist,zebra,9)
        self.assertEqual(self.ordered(),[mango,apple,zebra])
        with self.assertRaises(KeyError):self.library.move_in_collection(self.playlist,999,1)

    def test_readding_and_removing_keep_a_clean_order(self):
        zebra,apple,mango=self.games
        self.library.add_to_collection(self.playlist,apple)
        self.assertEqual(self.ordered(),self.games)
        self.library.remove_from_collection(self.playlist,zebra)
        self.library.add_to_collection(self.playlist,zebra)
        self.assertEqual(self.ordered(),[apple,mango,zebra])

    def test_older_libraries_gain_the_order_column(self):
        path=self.library.root/"library.sqlite3"
        import contextlib,sqlite3
        with contextlib.closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE legacy(collection_id INTEGER NOT NULL,game_id INTEGER NOT NULL,PRIMARY KEY(collection_id,game_id))")
            db.execute("INSERT INTO legacy SELECT collection_id,game_id FROM collection_games ORDER BY position")
            db.execute("DROP TABLE collection_games");db.execute("ALTER TABLE legacy RENAME TO collection_games")
        reopened=Library(self.library.root)
        self.assertEqual([g["id"] for g in reopened.games(collection_id=self.playlist)],self.games)
        reopened.move_in_collection(self.playlist,self.games[-1],-2)
        self.assertEqual([g["id"] for g in reopened.games(collection_id=self.playlist)],[self.games[-1],*self.games[:-1]])


class PlaylistMenuTests(unittest.TestCase):
    def app(self):
        app=object.__new__(ConsoleApp)
        app.collection_id=7;app.focus="game:3";app.overlay=None;app.calls=[]
        app.library=SimpleNamespace(collections=lambda:[{"id":7,"name":"Weekend"}],
            move_in_collection=lambda c,g,d:app.calls.append(("move",c,g,d)),
            remove_from_collection=lambda c,g:app.calls.append(("remove",c,g)))
        app.selected=lambda:{"id":3,"title":"Mango"}
        app.refresh=lambda:app.calls.append("refresh");app.schedule_draw=lambda:None
        app.menu=lambda title,items,message="":setattr(app,"overlay",{"title":title,"items":items,"message":message})
        return app

    def test_edit_playlist_offers_order_and_removal_for_the_focused_game(self):
        app=self.app();app.manage_collection()
        labels=[label for label,_ in app.overlay["items"]]
        self.assertEqual(labels,["Move game up","Move game down","Remove game from playlist","Rename playlist","Delete playlist","Cancel"])
        dict(app.overlay["items"])["Move game up"]()
        self.assertEqual(app.calls,[("move",7,3,-1),"refresh"]);self.assertEqual(app.focus,"game:3")
        dict(app.overlay["items"])["Remove game from playlist"]()
        self.assertEqual(app.calls[-2:],[("remove",7,3),"refresh"])

    def test_edit_playlist_without_a_focused_game_only_manages_the_list(self):
        app=self.app();app.focus="collection:manage";app.manage_collection()
        self.assertEqual([label for label,_ in app.overlay["items"]],["Rename playlist","Delete playlist","Cancel"])


if __name__=="__main__":unittest.main()
