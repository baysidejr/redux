"""Controller-operated artwork services, account setup and match selection."""
from __future__ import annotations

from pathlib import Path

from .scraper_settings import ScraperSettings
from .scraper_providers import provider_info
from .credential_keyboard import keyboard_rows, next_page, edit_value, masked_value


WHITE = "#f4f4f2"
MUTED = "#a3a3ab"
LABELS = {"cover":"Box cover", "tile":"Library tile", "hero":"Background / fan art",
          "screenshot":"Gameplay screenshot", "logo":"Game logo"}
REGIONS = (("North America", "us"), ("Europe", "eu"), ("Japan", "jp"), ("World", "wor"))
LANGUAGES = (("English", "en"), ("French", "fr"), ("German", "de"), ("Spanish", "es"),
             ("Italian", "it"), ("Portuguese", "pt"), ("Japanese", "ja"))
ACCOUNT_LABELS = {"screenscraper":"Login + API", "thegamesdb":"API key",
                  "hfsdb":"Login", "igdb":"Client ID / secret"}


class ConsoleScraper:
    def _scraper_cancellable(self, work):
        from .library import ImportCancelled
        from .scraper_providers import ScrapeCancelled
        try:return work()
        except (ImportCancelled, ScrapeCancelled):return None

    def scraper_settings(self):
        store = getattr(self, "_scraper_store", None)
        if store is None or store.library_root != Path(self.library.root).resolve():
            store = self._scraper_store = ScraperSettings(self.library.root)
        return store

    def scraper_service(self):
        from .scraping import ScraperService
        return ScraperService(self.library, self.scraper_settings())

    def _scraper_info(self, provider=None):
        provider = provider or self.scraper_settings().load()["provider"]
        return next(info for info in provider_info() if info["id"] == provider)

    def _artwork_settings(self):
        options = self.scraper_settings().load()
        info = self._scraper_info(options["provider"])
        self._setting_row("scraper:source", "Artwork service", info["name"], 218, self.scraper_provider_menu, icon="collections")
        self._setting_row("scraper:account", "Accounts & API keys",
            "Enter usernames, passwords and API credentials for any service", 300,
            self.scraper_accounts_menu, icon="controller")
        self._setting_row("scraper:games", "Find game artwork", "Selected game, current system or entire library", 382, self.scraper_scope_menu, icon="search")
        self._setting_row("scraper:options", "Download options", "Region, language, images and existing artwork", 464, self.scraper_options_menu, icon="settings")
        self._setting_row("scraper:auto", "Artwork after import", "Look up exact matches for newly imported games", 546, self.toggle_automatic_scraping, icon="plus", toggle=options["automatic"])

    def scraper_provider_menu(self):
        selected = self.scraper_settings().load()["provider"]
        self.menu("Artwork service", [(info["name"] + ("  ·  selected" if info["id"] == selected else ""),
                   lambda provider=info["id"]: self.set_scraper_provider(provider)) for info in provider_info()] +
                  [("Back", self.open_scraper_settings)],
                  message="The Batocera services, plus Libretro thumbnails without an account. Downloaded art stays with your library.")

    def open_scraper_settings(self):
        self.settings_section = "artwork"
        if self.screen != "settings": self.navigate("settings")
        self.focus = "scraper:source"; self.schedule_draw()

    def set_scraper_provider(self, provider):
        self.scraper_settings().save({"provider":provider})
        self.status.set(self._scraper_info(provider)["name"] + " selected")
        self.schedule_draw()

    def scraper_accounts_menu(self):
        store = self.scraper_settings()
        selected = self._scraper_info(store.load()["provider"])["name"]
        items = []
        for info in provider_info():
            fields = info["credentials"]
            if fields:
                status = store.status(info["id"])
                required = [field for field in fields if field.get("required")]
                saved = sum(status.get(field["key"]) == "Saved" for field in required)
                summary = "Saved" if saved == len(required) else f"{saved}/{len(required)} saved" if saved else "Not set"
                label = info["name"] + "  ·  " + ACCOUNT_LABELS[info["id"]] + "  ·  " + summary
            else:
                label = info["name"] + "  ·  No account needed"
            items.append((label, lambda provider=info["id"]:self.scraper_account_menu(provider)))
        items.append(("Back", self.open_scraper_settings))
        self.menu("Accounts & API keys", items,
            message="Choose any service to enter its login or API keys. Current artwork service: " + selected + ".")
        self.overlay["back_action"] = self.open_scraper_settings

    def scraper_account_menu(self, provider=None):
        if provider is None:
            self.scraper_accounts_menu(); return
        info = self._scraper_info(provider)
        fields = info["credentials"]
        status = self.scraper_settings().status(info["id"]) if fields else {}
        items = [(field["label"] + "  ·  " + status.get(field["key"], "Not set"),
                  lambda item=field: self.edit_scraper_credential(info["id"], item)) for field in fields]
        selected = self.scraper_settings().load()["provider"] == info["id"]
        if not selected:
            items.append(("Use this artwork service", lambda:self.use_scraper_account_provider(info["id"])))
        items += [("Test service connection", lambda: self.test_scraper_connection(info["id"]))]
        if fields:
            items.append(("Clear this service's credentials", lambda: self.confirm_clear_scraper_credentials(info["id"])))
        items.append(("Back", self.scraper_accounts_menu))
        detail = "This is the current artwork service." if selected else "Account setup keeps your current artwork service. Choose Use this artwork service when ready."
        self.menu(info["name"] + (" account" if fields else ""), items,
            message=info["description"] + "\n" + detail +
                    (" Passwords and API secrets stay protected on this Windows account." if fields else ""))
        self.overlay["back_action"] = self.scraper_accounts_menu

    def use_scraper_account_provider(self, provider):
        self.set_scraper_provider(provider)
        self.scraper_account_menu(provider)

    def edit_scraper_credential(self, provider, field):
        def save(value):
            self.scraper_settings().set_credential(provider, field["key"], value)
            self.status.set("Artwork service credential saved" if value else "Artwork service credential cleared")
        self.text_input(self._scraper_info(provider)["name"], field["label"], save,
            secret=field.get("secret", False), credential=True,
            hint="Enter a replacement value. Save an empty field to clear it. Passwords are case-sensitive.",
            return_callback=lambda: self.scraper_account_menu(provider))

    def confirm_clear_scraper_credentials(self, provider):
        self.menu("Clear saved credentials?", [("Keep credentials", lambda: self.scraper_account_menu(provider)),
            ("Clear credentials", lambda: self.clear_scraper_credentials(provider))],
            message="This clears only this service's saved account details. Downloaded artwork stays in your library.")

    def clear_scraper_credentials(self, provider):
        self.scraper_settings().clear_credentials(provider)
        self.scraper_account_menu(provider)

    def test_scraper_connection(self, provider=None):
        from .scraper_providers import search
        provider = provider or self.scraper_settings().load()["provider"]
        info = self._scraper_info(provider)
        game = dict(self.selected() or {"title":"Sonic the Hedgehog", "system":"genesis"})
        options = self.scraper_settings().load()
        game.update(scrape_region=options["region"], scrape_language=options["language"])
        if game["system"] not in info["systems"]:
            self.notice("Choose a supported game", "This service does not support the selected game's system. " + info["description"])
            return
        store = self.scraper_settings()
        def work():
            return self._scraper_cancellable(lambda:search(provider, game, store.credentials(provider), cancelled=self.cancelled))
        def done(candidates):
            if candidates is None:
                self.status.set("Artwork service check cancelled"); return
            self.menu("Service connection checked", [("Back to account", lambda:self.scraper_account_menu(provider))],
                message=info["name"] + f" responded. {len(candidates)} matching result(s) for the selected title.")
        self.task(work, done, "Checking artwork service…")

    def scraper_options_menu(self):
        options = self.scraper_settings().load()
        self.menu("Artwork download options", [
            ("Region  ·  " + dict((code,label) for label,code in REGIONS).get(options["region"], options["region"]), self.scraper_region_menu),
            ("Language  ·  " + options["language"].upper(), self.scraper_language_menu),
            ("Image types", self.scraper_roles_menu),
            ("Tile style  ·  " + {"mixed":"Screenshot + box", "screenshot":"Screenshot", "cover":"Full box cover"}[options["tile_style"]], self.scraper_tile_style_menu),
            ("Replace existing art  ·  " + ("ON" if options["overwrite"] else "OFF"), self.toggle_scraper_overwrite),
            ("Back", self.open_scraper_settings)],
            message="Default: keep existing artwork and fill missing images. Bulk downloads accept only a unique matching title and system; uncertain matches stay unchanged.")

    def _set_scraper_option(self, name, value, back):
        self.scraper_settings().save({name:value}); back()

    def scraper_region_menu(self):
        self.menu("Preferred region", [(name, lambda code=value:self._set_scraper_option("region", code, self.scraper_options_menu)) for name,value in REGIONS])

    def scraper_language_menu(self):
        self.menu("Metadata language", [(name, lambda code=value:self._set_scraper_option("language", code, self.scraper_options_menu)) for name,value in LANGUAGES])

    def scraper_tile_style_menu(self):
        self.menu("Library tile style", [(name, lambda choice=value:self._set_scraper_option("tile_style", choice, self.scraper_options_menu))
            for name,value in (("Screenshot + full box + title", "mixed"), ("Screenshot + title", "screenshot"), ("Full box + title", "cover"))],
            message="Used when a service has no dedicated landscape tile. Complete box covers keep their proportions.")

    def scraper_roles_menu(self):
        selected = self.scraper_settings().load()["roles"]
        self.menu("Download image types", [(LABELS[role] + "  ·  " + ("ON" if role in selected else "OFF"),
            lambda choice=role:self.toggle_scraper_role(choice)) for role in LABELS] + [("Back", self.scraper_options_menu)],
            message="Unavailable image types are skipped. Library tiles can be composed locally from covers and screenshots.")

    def toggle_scraper_role(self, role):
        selected = self.scraper_settings().load()["roles"]
        if role in selected:
            if len(selected) == 1:
                self.notice("Keep an image type", "Select at least one image type to download."); return
            selected.remove(role)
        else:selected.append(role)
        self.scraper_settings().save({"roles":selected}); self.scraper_roles_menu()

    def toggle_scraper_overwrite(self):
        options = self.scraper_settings().load()
        self.scraper_settings().save({"overwrite":not options["overwrite"]}); self.scraper_options_menu()

    def toggle_automatic_scraping(self):
        options = self.scraper_settings().load()
        self.scraper_settings().save({"automatic":not options["automatic"]}); self.schedule_draw()

    def scraper_scope_menu(self):
        items = []
        if self.selected(): items.append(("Choose artwork for selected game", self.scrape_selected_game))
        if self.rows: items.append(("Fill artwork in this view", lambda:self.scrape_game_ids([game["id"] for game in self.rows])))
        if self.all_games: items.append(("Fill artwork in entire library", lambda:self.scrape_game_ids([game["id"] for game in self.all_games])))
        items.extend([("Accounts & API keys", self.scraper_accounts_menu), ("Back", self.open_scraper_settings)])
        self.menu("Find game artwork", items,
            message="Choose a match for one game, or fill missing art in bulk. B cancels after the current network read.")

    def game_artwork_menu(self):
        game = self.selected()
        if not game: return
        self.menu("Game artwork", [("Find artwork online", self.scrape_selected_game)] +
            [("Choose " + LABELS[role].lower(), lambda kind=role:self.choose_artwork_role(kind)) for role in LABELS] +
            [("Artwork service settings", self.open_scraper_settings), ("Back", lambda:None)],
            message="Covers, landscape tiles and backgrounds are separate. User-selected images stay with the portable library.")

    def choose_artwork_role(self, role):
        game = self.selected()
        if not game: return
        identity = game["id"]
        def selected(path):
            self.task(lambda:self.library.set_artwork(identity, path, role=role),
                      lambda _:self._cover_selected(), "Saving game artwork…")
        self.open_file_browser("Choose " + LABELS[role].lower(), mode="files", on_select=selected,
            extensions={".png", ".jpg", ".jpeg", ".webp", ".gif"}, multiple=False)

    def scrape_selected_game(self, query=None):
        game = self.selected()
        if not game:
            self.notice("Choose a game", "Open a game from Collection first."); return
        identity = game["id"]
        service = self.scraper_service()
        def done(candidates):
            if candidates is None:
                self.status.set("Artwork lookup cancelled"); return
            items = [(candidate.title + (f"  ·  {candidate.year}" if candidate.year else "") +
                       ("  ·  " + candidate.region if candidate.region else ""),
                       lambda match=candidate:self.apply_scrape_match(identity, match)) for candidate in candidates]
            items.extend([("Change search title", lambda:self.edit_scraper_query(identity, query or game["title"])),
                          ("Artwork service settings", self.open_scraper_settings), ("Back", lambda:None)])
            self.menu("Choose artwork match" if candidates else "No artwork match", items,
                message=("Choose the correct title and region. Existing images are kept unless Replace existing art is enabled." if candidates else
                         "No title matched this system. Try another title, region or artwork service."))
        self.task(lambda:self._scraper_cancellable(lambda:service.search(identity, query=query, cancelled=self.cancelled)), done, "Finding game artwork…")

    def edit_scraper_query(self, identity, query):
        def save(value):
            self._pending_scraper_query = (identity, value)
        def returned():
            pending = getattr(self, "_pending_scraper_query", None)
            self._pending_scraper_query = None
            if pending:
                self.selected_id = pending[0]; self.scrape_selected_game(pending[1])
        self.text_input("Find game artwork", "Search title", save, query, credential=True,
                        hint="Use the game's title. This changes the search only, not your library title.", return_callback=returned)

    def apply_scrape_match(self, identity, candidate):
        service = self.scraper_service()
        def done(result):
            if result is None:
                self.status.set("Artwork download cancelled"); return
            self.art.clear(); self.refresh()
            roles = ", ".join(LABELS[role] for role in result["roles"])
            message = "Saved: " + roles if roles else "Existing artwork kept."
            if result["notes"]: message += "\n" + "; ".join(result["notes"])
            self.menu("Game artwork updated", [("View game", lambda:self.open_game(identity)), ("Back", lambda:None)], message=message)
        self.task(lambda:self._scraper_cancellable(lambda:service.apply(identity, candidate, cancelled=self.cancelled)), done, "Downloading game artwork…")

    def scrape_game_ids(self, identities):
        identities = tuple(identities)
        if not identities:
            self.notice("No games", "Add games to your library first."); return
        service = self.scraper_service()
        def progress(index, total, title):
            self.events.put(("status", f"Artwork {index}/{total}  ·  {title}"))
        def done(result):
            self.art.clear(); self.refresh()
            summary = (f"{len(result.updated)} updated · {len(result.skipped)} kept · "
                       f"{len(result.unmatched)} need a match · {len(result.errors)} errors")
            if result.cancelled: summary = "Cancelled · " + summary
            if result.errors: summary += "\n" + result.errors[0][1]
            if result.unmatched: summary += "\nChoose artwork for individual games to resolve uncertain matches."
            self.menu("Artwork downloads", [("Back to artwork settings", self.open_scraper_settings)], message=summary)
        self.task(lambda:service.batch(identities, cancelled=self.cancelled, progress=progress), done, "Preparing artwork downloads…")

    def _credential_keyboard(self):
        prompt = self.text_prompt
        page = prompt.get("keyboard_page", "lower")
        rows = keyboard_rows(page)
        self.panel(0, 302, 1280, 380, "#111216f2", 0)
        for row_index, row in enumerate(rows):
            cell, top = 56, 321 + row_index*49
            left = (1280-len(row)*cell)/2
            for index, character in enumerate(row):
                x = left+index*cell; key = "credential:key:" + str(ord(character))
                focused = self.focus == key
                if focused:self.panel(x+7, top, 42, 40, WHITE, 20)
                self.text(x+28, top+20, character, 22, "#111216" if focused else WHITE, anchor="center")
                self.target(key, (x, top, x+cell, top+43), lambda char=character:self._credential_edit(char), character)
        controls = (("PAGE", {"lower":"ABC / symbols", "upper":"123 / symbols", "symbols":"abc"}[page]),
                    ("SPACE", "Space"), ("BACKSPACE", "Delete"), ("CLEAR", "Clear"), ("SAVE", "Save"))
        for index, (key, label) in enumerate(controls):
            x = 204 + index*176
            callback = self._accept_text if key == "SAVE" else lambda action=key:self._credential_edit(action)
            self.button_canvas("credential:"+key, label, x, 602, 164, 39, callback, key == "SAVE")

    def _credential_edit(self, key):
        if key == "PAGE":
            self.text_prompt["keyboard_page"] = next_page(self.text_prompt.get("keyboard_page", "lower"))
            first = keyboard_rows(self.text_prompt["keyboard_page"])[0][0]
            self.focus = "credential:key:" + str(ord(first))
        else:
            self.text_prompt["value"] = edit_value(self.text_prompt["value"], key)
        self.schedule_draw()

    def _scraper_text_value(self, prompt):
        return masked_value(prompt["value"]) if prompt.get("secret") else prompt["value"]
