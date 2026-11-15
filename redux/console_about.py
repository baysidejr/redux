"""About a console: the page a console tile on Home opens.

No game list and nothing to pick: games arrive on their own (a disc plays itself, a
cartridge's games are read from the card). What a console tile offers instead is the
console itself, from Wikipedia: what it was, who made it, when it came to North America,
how many it sold, what it ran on. Text is Wikipedia's (CC BY-SA 4.0) and says so.
"""
from __future__ import annotations

import json

from .gamedb import database_root
from .library import SYSTEMS
from .ui_theme import WHITE, MUTED, ACCENT

FACT_LABELS = (("manufacturer", "Made by"), ("developer", "Developed by"), ("release_na", "North America"),
               ("release_first", "First released"), ("lifespan", "Years"), ("generation", "Generation"), ("units_sold", "Units sold"),
               ("media", "Media"), ("cpu", "Processor"), ("cpu_speed", "Clock"), ("price", "Launch price"), ("top_game", "Best seller"))
SUMMARY_LIMIT = 900


def console_facts(system=None):
    """The consoles file as a dict, or one console's entry (or {} when it is missing)."""
    try:
        document = json.loads((database_root() / "consoles.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = {}
    if system is None:
        return document
    entry = (document.get("systems") or {}).get(system) or {}
    return dict(entry, license=document.get("license", ""), source=document.get("source", "")) if entry else {}


class ConsoleAbout:
    def open_console(self, system):
        """A on a console tile: the console's page."""
        if system not in SYSTEMS:
            return
        self.console_system = system
        self.home_system = system
        self.navigate("console")

    def _console(self):
        system = getattr(self, "console_system", None) or self.home_focused_system()
        entry = console_facts(system)
        label = SYSTEMS.get(system, system)
        count = sum(1 for game in self.all_games if game.get("system") == system)
        self.panel(0, 0, 1280, 720, "#000000c4", 0)
        self.targets = []
        self.callbacks = {}
        self.target("console:back", (24, 25, 78, 80), self.back, "Back")
        if self.focus == "console:back":
            self.rect(24, 25, 54, 55, "", WHITE, 1)
        self.icon("back", 39, 41, 25)
        # The console's own tile on the left; the words on the right.
        self.rect(99, 60, 300, 210, "#101013")
        if not self.picture(self.art.path(system, "tile"), 99, 60, 300, 210, mode="contain"):
            self.icon("systems", 229, 145, 40, ACCENT)
        self.display(99, 290, f"{count} game{'' if count == 1 else 's'} on your cartridges" if count else "No games yet", 14, MUTED, width=300)
        self.display(99, 318, self._how_games_arrive(system).capitalize() + ".", 13, "#6a6a72", width=300)
        self.display(440, 54, label.upper(), 40, WHITE, "bold", width=800)
        if entry.get("description"):
            self.display(440, 108, entry["description"], 17, "#c9c9ce", width=800)
        y = 150
        facts = [(name, entry.get("facts", {}).get(key)) for key, name in FACT_LABELS if entry.get("facts", {}).get(key)]
        for index, (name, value) in enumerate(facts[:8]):
            column, row = divmod(index, 4)
            fx, fy = 440 + column * 400, y + row * 30
            self.text(fx, fy, name, 12, MUTED, "bold")
            self.text(fx + 128, fy, self.fit_text(str(value), 260, 13), 13, WHITE)
        y += 30 * min(4, max(1, len(facts))) + 20 if facts else 0
        summary = (entry.get("summary") or "").strip()
        if summary:
            if len(summary) > SUMMARY_LIMIT:
                summary = summary[:SUMMARY_LIMIT].rsplit(" ", 1)[0] + "…"
            self.text(440, y, summary, 14, "#c4c4c8", width=800)
        else:
            self.text(440, y, "Nothing on record for this console yet.", 14, "#8e8e94")
        if entry:
            self.text(440, 662, f"From {entry.get('source') or 'Wikipedia'}  ·  {entry.get('license') or 'CC BY-SA 4.0'}  ·  {entry.get('url', '')}", 11, "#56565c", width=800)
        if self.focus not in self.callbacks:
            self.focus = "console:back"
