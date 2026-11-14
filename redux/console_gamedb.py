"""The Database pages: System → Database lists the systems, a system lists its games, a game has its own page."""
from __future__ import annotations

from .gamedb import GameDatabase, LABELS, title_key
from .ui_theme import WHITE
from .console_secondary import MUTED, ACCENT, FAINT, PANEL

PAGE_SIZE = 27  # three columns of nine rows
COLUMNS = 3


class ConsoleGameDatabase:
    @property
    def gamedb(self):
        database = getattr(self, "_game_database", None)
        if database is None:
            database = self._game_database = GameDatabase()
        return database

    # ------------------------------------------------------------------ navigation

    def open_gamedb(self, system):
        if not self.gamedb.available(system):
            self.notice("Not in the database yet", f"{LABELS.get(system, system)} has no entries yet.")
            return
        self.gamedb_system = system
        self.pages["gamedb"] = 0
        self.navigate("gamedb")

    def open_gamedb_game(self, game_id):
        self.gamedb_game_id = game_id
        self.navigate("gamedb_game")

    def _gamedb_owned(self, game):
        """The library game that is this database entry, if the player has it."""
        wanted = title_key(game["title"])
        for row in self.all_games:
            if row.get("system") == game.get("system") and title_key(row.get("title")) == wanted:
                return row
        return None

    def _open_owned(self, row):
        self.selected_id = row["id"]
        self.navigate("details")

    def _gamedb_pager(self, key, total, size, X, R, y=642):
        if total <= size:
            return
        page = self.pages.get(key, 0)
        maximum = (total - 1) // size
        self.text(X, y + 10, f"Page {page + 1} of {maximum + 1}", 12, MUTED)
        if page > 0:
            self.button_canvas(f"{key}:prev", "Previous", R - 324, y, 154, 32, lambda: self._change_page(key, -1))
        if page < maximum:
            self.button_canvas(f"{key}:next", "Next", R - 154, y, 154, 32, lambda: self._change_page(key, 1))

    # ------------------------------------------------------------------ System → Database

    def _database_section(self, X, W, R):
        """One tile per system Redux covers, with what the database holds for it."""
        systems = self.gamedb.systems()
        tile_w, gap = 238, 16
        for index, info in enumerate(systems):
            x = X + (index % 4) * (tile_w + gap)
            y = 156 + (index // 4) * 168
            key = f"gamedb:system:{info['system']}"
            focused = self.focus == key
            self.panel(x, y, tile_w, 152, "#2c232a" if focused else PANEL, 3, WHITE if focused else None)
            if not self.picture(self.art.path(info["system"], "tile"), x + 4, y + 4, tile_w - 8, 86):
                self.rect(x + 4, y + 4, tile_w - 8, 86, "#101013")
                self.icon("disc", x + tile_w // 2 - 16, y + 30, 32, ACCENT if info["available"] else "#46464c")
            self.text(x + 12, y + 98, self.fit_text(info["label"], tile_w - 60, 14), 14, WHITE, "bold")
            if info["available"]:
                self.text(x + 12, y + 120, f"{info['games']} games · {info['boxart']} covers · {info['hero']} banners", 11, MUTED)
                self.icon("chevron", x + tile_w - 26, y + 100, 14, ACCENT if focused else FAINT)
                self.target(key, (x, y, x + tile_w, y + 152), lambda s=info["system"]: self.open_gamedb(s), info["label"])
            else:
                self.text(x + 12, y + 120, "Not built yet", 11, "#6a6a72")
        built = [info for info in systems if info["available"]]
        total = sum(info["games"] for info in built)
        self.panel(X, 500, W, 112, PANEL, 3)
        self.icon("info", X + 21, 524, 25, ACCENT)
        self.text(X + 66, 523, f"{total} games across {len(built)} system{'' if len(built) == 1 else 's'}, ready before you insert a disc", 16, WHITE, "bold")
        self.text(X + 66, 556, "Redux recognises a disc by the serial in its header, then shows the database's box art, banner "
                  "and game information. Everything lives in redux/database, open to modding.", 13, MUTED, width=W - 100)

    # ------------------------------------------------------------------ a system's games

    def _gamedb(self):
        system = getattr(self, "gamedb_system", None) or "sega_cd"
        games = self.gamedb.games(system)
        label = self.gamedb._load(system)["label"]
        self.panel(0, 0, 1280, 720, "#000000c4", 0)
        self.targets = []
        self.callbacks = {}
        X, W = 140, 1000
        R = X + W
        self.display(X, 54, label.upper(), 40, WHITE, "bold")
        self.display(X, 108, f"{len(games)} games in the database" if games else "Nothing in the database for this system yet.", 17, "#c9c9ce")
        self.icon("disc", R - 40, 62, 32, "#8a8a90")
        start, end = self._page_bounds("gamedb", len(games), PAGE_SIZE)
        col_w = (W - (COLUMNS - 1) * 16) // COLUMNS
        rows = PAGE_SIZE // COLUMNS
        for offset, game in enumerate(games[start:end]):
            column, row = divmod(offset, rows)
            x = X + column * (col_w + 16)
            y = 150 + row * 52
            key = f"gamedb:{start + offset}"
            focused = self.focus == key
            self.panel(x, y, col_w, 46, "#2c232a" if focused else PANEL, 2, WHITE if focused else None)
            self.rect(x + 6, y + 4, 30, 38, "#101013")
            if not self.picture(self.gamedb.art(game, "boxart"), x + 6, y + 4, 30, 38, mode="contain"):
                self.icon("disc", x + 13, y + 15, 16, "#46464c")
            self.text(x + 46, y + 8, self.fit_text(game["title"], col_w - 100, 13), 13, WHITE)
            self.text(x + 46, y + 27, self.fit_text(game.get("developer") or game.get("publisher") or "", col_w - 100, 11), 11, MUTED)
            if game.get("year"):
                self.text(x + col_w - 12, y + 16, str(game["year"]), 12, MUTED, anchor="ne")
            self.target(key, (x, y, x + col_w, y + 46), lambda gid=game["id"]: self.open_gamedb_game(gid), game["title"])
        self._gamedb_pager("gamedb", len(games), PAGE_SIZE, X, R)

    # ------------------------------------------------------------------ one game

    def _gamedb_game(self):
        system = getattr(self, "gamedb_system", None) or "sega_cd"
        game = self.gamedb.game(system, getattr(self, "gamedb_game_id", None))
        if not game:
            self._empty("Choose a game from the database")
            return
        art = self.gamedb.art
        hero = art(game, "hero") or art(game, "gameimage") or self.art.path(system, "hero")
        if not self.picture(hero, 0, 0, 1280, 388):
            self.rect(0, 0, 1280, 388, "#18121e")
        self._top_shade(0, 0, 1280, 145)
        self.rect(0, 388, 1280, 54, "#212121")
        self.rect(0, 442, 1280, 278, "#080909")
        self.target("gamedb:back", (24, 25, 78, 80), self.back, "Back")
        if self.focus == "gamedb:back":
            self.rect(24, 25, 54, 55, "", WHITE, 1)
        self.icon("back", 39, 41, 25)
        # Box art on the left, the game's name in the band, facts and the description below.
        self.rect(99, 366, 248, 244, "#171719")
        if not self.picture(art(game, "boxart"), 99, 366, 248, 244, mode="contain"):
            if not self.picture(art(game, "gameimage"), 99, 366, 248, 244, mode="contain"):
                self.picture(self.art.path(system, "tile"), 99, 366, 248, 244)
        self.display(403, 400, self.fit_text(game["title"], 800, 30, "bold"), 30, WHITE, "bold")
        badge = self._badge(system, 404, 462)
        if game.get("year"):
            self.panel(421 + badge, 462, 48, 22, "#0c0c0c", radius=1, outline="#b5b5b9")
            self.display(445 + badge, 473, str(game["year"]), 17, WHITE, anchor="center")
        owned = self._gamedb_owned(game)
        if owned:
            # The ownership mark sits in the band beside the title; Open lines up with the badges.
            self.panel(1010, 400, 230, 30, ACCENT, radius=2)
            self.display(1125, 415, "IN MY COLLECTION", 14, WHITE, "bold", anchor="center")
            self.button_canvas("gamedb:open", "Open", 1010, 455, 230, 36, lambda row=owned: self._open_owned(row))
        facts = [f"{label}: {value}" for label, value in self.gamedb.facts(game) if label != "Released"]
        self.text(404, 496, self.fit_text("   ·   ".join(facts) if facts else "No further information in the database.", 790, 13), 13, "#a8a8ae")
        description = (game.get("description") or "").replace("\r\n", "\n").strip()
        if description:
            limit = 520
            shown = description if len(description) <= limit else description[:limit].rsplit(" ", 1)[0] + "…"
            self.text(404, 526, shown, 14, "#c4c4c8", width=800)
        else:
            self.text(404, 526, "No description in the database for this game.", 14, "#8e8e94")
        releases = self.gamedb.releases_text(game)
        if releases:
            self.text(404, 662, self.fit_text("Releases: " + releases, 800, 11), 11, MUTED)
        self.text(99, 622, self.fit_text("Database entry " + game["id"], 248, 10), 10, "#56565c")
        if self.focus not in self.callbacks:
            self.focus = "gamedb:back"
