"""The stage: the banner area of Home, where a system is shown and a game is seen loading.

Kazeta-style: Home is a shelf of console tiles. Put a disc in and Redux installs it (the
first time) and starts it; the stage shows that system's pixel art with a small 8-bit
loading animation while it happens, and "A to play" stays as a backup. No game box art,
no facts: the database is a place to browse, not something the collection borrows from.

The owner's sprite sheets ship in ``redux/assets/animations``: one 3840x2160 PNG per
console, 8 columns of 480x270 cells, described by ``animations.json`` (frame size, fps,
intro / loop / outro ranges). The intro plays once (the tray opens, the game goes in),
the loop runs while the game installs or starts, and the outro plays when the load is
done. A frame folder (``<name>/frame_000.png`` ...) is also accepted, and a drawn
animation of lights and a sliding disc covers any console without art. See
docs/ANIMATIONS.md.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .library import SYSTEMS
from .ui_theme import WHITE, MUTED, ACCENT

ZOOM_SECONDS = 0.55
AUTOPLAY_DELAY = 0.4      # seconds after the insert event before Play fires on its own
AUTOPLAY_STAGE = 0.9      # seconds of stage animation before the install or load starts
LIGHTS = 8
HOME_ORDER = ("nes", "snes", "n64", "genesis", "32x", "sega_cd", "saturn", "sms", "game_gear", "psx", "pce", "pce_cd", "supergrafx",
              "neo_geo", "neo_geo_cd", "3do", "atari2600", "atari7800", "jaguar", "jaguar_cd", "gb", "gbc", "gba", "pc")
_PERCENT = re.compile(r"(\d{1,3})%")


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _lerp(a, b, t):
    return a + (b - a) * t


class FrameAnimation:
    """A folder of PNG frames: frame_000.png, frame_001.png, ... and an optional animation.json."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.frames = sorted(self.folder.glob("frame_*.png"))
        self.fps = 12
        self.loop_from = 0
        try:
            settings = json.loads((self.folder / "animation.json").read_text(encoding="utf-8"))
            self.fps = max(1, min(60, int(settings.get("fps", 12))))
            self.loop_from = max(0, min(len(self.frames) - 1, int(settings.get("loop_from", 0))))
        except (OSError, ValueError, TypeError):
            pass

    def __bool__(self):
        return bool(self.frames)

    def frame_at(self, seconds):
        if not self.frames:
            return None
        index = int(seconds * self.fps)
        if index >= len(self.frames):
            span = max(1, len(self.frames) - self.loop_from)
            index = self.loop_from + (index - self.loop_from) % span
        return self.frames[index]


class SpriteSheetAnimation:
    """One console's cells on a sheet: intro once, loop while loading, outro when done."""

    def __init__(self, sheet, entry, settings):
        self.sheet = Path(sheet)
        frame = settings.get("frame") or {}
        self.width = int(frame.get("width", 480))
        self.height = int(frame.get("height", 270))
        self.fps = max(1, min(60, int(entry.get("fps", settings.get("fps", 12)))))
        self.columns = max(1, int(entry.get("columns", 8)))
        self.intro = tuple(entry.get("intro") or (0, -1))
        self.loop = tuple(entry.get("loop") or self.intro)
        self.outro = tuple(entry.get("outro") or ())
        self.mechanism = str(entry.get("mechanism", ""))

    def __bool__(self):
        return self.sheet.is_file()

    def index_at(self, seconds, *, finishing_since=None):
        """The cell to show ``seconds`` into the load; the outro once ``finishing_since`` is given."""
        step = int(max(0.0, seconds) * self.fps)
        intro_length = self.intro[1] - self.intro[0] + 1
        if finishing_since is not None and self.outro:
            offset = int(max(0.0, finishing_since) * self.fps)
            return min(self.outro[0] + offset, self.outro[1])
        if step < intro_length:
            return self.intro[0] + step
        span = max(1, self.loop[1] - self.loop[0] + 1)
        return self.loop[0] + (step - intro_length) % span

    def box(self, index):
        row, column = divmod(max(0, int(index)), self.columns)
        left, top = column * self.width, row * self.height
        return (left, top, left + self.width, top + self.height)

    @property
    def outro_seconds(self):
        return ((self.outro[1] - self.outro[0] + 1) / self.fps) if self.outro else 0.0


class ConsoleStage:
    # ------------------------------------------------------------------ state
    def start_stage(self, system, title="", *, kind="load", origin=None):
        """Begin a stage animation: the system's picture grows from ``origin`` (a tile rect) into the stage.

        A load that is already showing for the same console carries on (an install handing
        over to the core keeps its outro) instead of starting its intro again."""
        current = getattr(self, "stage_animation", None)
        if current and kind == "load" and current.get("kind") == "load" and current.get("system") == (system or "unknown") and origin is None:
            if title:
                current["title"] = title
            return
        self.stage_animation = dict(kind=kind, system=system or "unknown", title=title or "", started=time.monotonic(), origin=origin)
        draw = getattr(self, "schedule_draw", None)
        if draw:
            draw()

    def show_stage(self, system, title=""):
        """Go Home and start the loading animation for a game that is about to run; harmless on a bare app."""
        try:
            if getattr(self, "screen", None) not in (None, "home"):
                self.navigate("home", remember=False)
            self.start_stage(system, title)
        except (AttributeError, TypeError):
            self.stage_animation = None

    def finish_stage(self):
        """The load is done: play the outro (the bar completes) until the screen changes."""
        animation = getattr(self, "stage_animation", None)
        if animation and animation.get("finishing_since") is None:
            animation["finishing_since"] = time.monotonic()
            draw = getattr(self, "schedule_draw", None)
            if draw:
                draw()

    def clear_stage(self):
        self.stage_animation = None

    def _stage_tick(self):
        """Keep the stage moving while something is loading or a disc just went in."""
        animation = getattr(self, "stage_animation", None)
        pending = getattr(self, "disc_autoplay", None)
        now = time.monotonic()
        if pending and now - pending["at"] >= AUTOPLAY_DELAY and self._stage_idle():
            disc = self.inserted_disc()
            if not disc or (disc.drive, disc.fingerprint) != (pending["drive"], pending["fingerprint"]):
                self.disc_autoplay = None
            else:
                self.disc_autoplay = None
                if self.screen != "home":
                    self.navigate("home", remember=False)
                # The disc's console grows out of the shelf into the stage, then the disc loads.
                self.home_system = disc.system
                self.focus = "hero:play"
                self.start_stage(disc.system, disc.title, origin=self._shelf_tile_rect(disc.system))
                self._later(int(AUTOPLAY_STAGE * 1000), self.play_disc)
        if animation and now - animation["started"] > 20 and not self.busy:
            self.stage_animation = None
        if (animation or self.busy) and self.screen == "home" and now - getattr(self, "_stage_frame_at", 0) >= 0.1:
            self._stage_frame_at = now
            self.schedule_draw()

    def _shelf_tile_rect(self, system):
        """Where a console's tile sits on the shelf page that shows it, for the zoom into the stage."""
        systems = self.home_systems()
        if system not in systems:
            return None
        index = systems.index(system) % 8
        return (360 + (index % 4) * 226.5, 348 + (index // 4) * 176, 213, 149)

    def _stage_idle(self):
        playing = bool(self.embedded) or bool(self.playback and self.playback.active())
        return not self.busy and not playing and not self.overlay and getattr(self, "text_prompt", None) is None

    def _later(self, milliseconds, callback):
        root = getattr(self, "root", None)
        if root is not None and hasattr(root, "after"):
            root.after(milliseconds, callback)
        else:
            callback()

    def note_disc_insert(self, event):
        """A disc that goes in while Redux runs plays on its own; one already in at start-up waits for A."""
        if getattr(event, "startup", False) or not getattr(event, "identity", None):
            return
        identity = event.identity
        if not getattr(identity, "system", None):
            return
        self.disc_autoplay = dict(drive=event.drive, fingerprint=getattr(event, "fingerprint", None) or identity.fingerprint, at=time.monotonic())

    # ------------------------------------------------------------------ drawing
    def sheet_animation(self, system):
        """The owner's sprite-sheet animation for a console, from assets/animations/animations.json, or None."""
        cache = self.__dict__.setdefault("_sheet_animations", {})
        if system in cache:
            return cache[system]
        animation = None
        manifest = self.art.bundled("animations", "animations.json")
        if manifest:
            try:
                settings = json.loads(Path(manifest).read_text(encoding="utf-8"))
                entry = (settings.get("systems") or {}).get(system)
                if isinstance(entry, dict) and entry.get("sheet"):
                    sheet = self.art.bundled("animations", str(entry["sheet"]))
                    if sheet:
                        animation = SpriteSheetAnimation(sheet, entry, settings)
            except (OSError, ValueError, TypeError):
                animation = None
        cache[system] = animation or None
        return cache[system]

    def frame_animation(self, *names):
        cache = self.__dict__.setdefault("_frame_animations", {})
        for name in names:
            if name not in cache:
                folder = self.art.bundled("animations", name, "animation.json") or self.art.bundled("animations", name, "frame_000.png")
                cache[name] = FrameAnimation(folder.parent) if folder else None
            if cache[name]:
                return cache[name]
        return None

    def _stage(self, x, y, w, h, disc, focused_system, counts):
        animation = getattr(self, "stage_animation", None)
        system = disc.system if disc and disc.system else (animation or {}).get("system") or focused_system
        picture = self.art.path(system, "hero") if system else None
        loading = bool(animation) or self.busy
        if animation and animation.get("origin") and time.monotonic() - animation["started"] < ZOOM_SECONDS:
            # The tile grows out of the shelf into the stage; the shelf fades behind it.
            t = _ease((time.monotonic() - animation["started"]) / ZOOM_SECONDS)
            ox, oy, ow, oh = animation["origin"]
            self.panel(x, 0, w, 720, "#080909%02x" % int(200 * t), 0)
            rx, ry, rw, rh = _lerp(ox, x, t), _lerp(oy, y, t), _lerp(ow, w, t), _lerp(oh, h, t)
            if not self.picture(picture, rx, ry, rw, rh):
                self.rect(rx, ry, rw, rh, "#17121f")
            return
        if not self.picture(picture, x, y, w, h, shade=loading or bool(disc)):
            self.rect(x, y, w, h, "#17121f")
        label = SYSTEMS.get(system, "Redux") if system else "Redux"
        if loading:
            self._stage_loading(x, y, w, h, system, (animation or {}).get("title") or (disc.title if disc else ""))
        elif disc:
            self._top_shade(x, y, w, h - 29)
            self.display(x + 36, 58, "DISC INSERTED" if not disc.is_virtual else "MOUNTED IMAGE", 14, "#c7c1d1", "bold")
            self.display(x + 36, 92, disc.title.upper(), 38, WHITE, "bold", width=w - 70)
            self.display(x + 36, 197, label + "  ·  " + disc.drive, 18, "#dedbe3")
            self.display(x + 36, 227, "Installed  ·  A to play" if self.installed_disc_game(disc) else "Not installed  ·  A to install and play", 16, "#b4b0bb")
        else:
            count = counts.get(system, 0)
            self.display(x + 36, h - 92, label.upper(), 30, WHITE, "bold", width=w - 70)
            self.display(x + 36, h - 50, f"{count} game{'' if count == 1 else 's'}  ·  A to open" if count else "No games yet  ·  " + self._how_games_arrive(system), 15, "#b4b0bb", width=w - 70)
        strip_y = h - 29
        self.rect(x, strip_y, w, 29, "#080808")
        badge_width = self._badge(system or "welcome", x + 12, strip_y + 4)
        strip_title = (animation or {}).get("title") if loading else None
        self.display(x + max(65, 24 + badge_width), strip_y + 7, self.fit_text((strip_title or (disc.title if disc else label)).upper(), 540, 15), 15, WHITE)
        if loading:
            cancel = getattr(self, "cancelled", None)
            if self.busy and cancel is not None and getattr(self, "task_cancellable", True):
                self.button_canvas("task:cancel", "Cancel", 1150, strip_y + 2, 100, 25, cancel.set)
            else:
                self.display(1240, strip_y + 7, "LOADING", 13, "#9a9aa2", "bold", anchor="ne")
        else:
            self._controller_hint("A", "Play" if disc else "Open", 1080, strip_y + 6, "#35a4c6")
            if disc:
                self._controller_hint("Y", "Info", 1180, strip_y + 6, "#d6bb34")
        self.target("hero:play", (x + 2, 2, 1278, h - 2), self.play_disc if disc else (lambda s=system: self._open_system(s)), "Play disc" if disc else label)
        if disc:
            self.target("hero:details", (1174, strip_y, 1279, h), self.inserted_disc_information, "Disc information")
        if self.focus in {"hero:play", "hero:details"}:
            self.rect(x + 1, 1, w - 3, h - 2, "", WHITE, 2)

    def _how_games_arrive(self, system):
        from .game_cards import CD_SYSTEMS, system_folder_name
        if system == "pc":
            return "install GOG games on a GOG cartridge"
        if system in CD_SYSTEMS:
            return "insert a disc"
        return f"put games in the {system_folder_name(system)} folder of a console cartridge"

    def _stage_loading(self, x, y, w, h, system, title):
        animation = getattr(self, "stage_animation", None) or {}
        elapsed = time.monotonic() - animation.get("started", time.monotonic())
        status = self.status.get() if hasattr(self.status, "get") else ""
        percent = _PERCENT.search(status or "")
        sheet = self.sheet_animation(system)
        if sheet:
            # The owner's animation on the right, the words on the left, the console's art behind.
            self._top_shade(x, y, w, h - 29)
            sprite_h = h - 29 - 8
            sprite_w = sprite_h * sheet.width / sheet.height
            sprite_x = x + w - sprite_w - 10
            finishing = animation.get("finishing_since")
            index = sheet.index_at(elapsed, finishing_since=None if finishing is None else time.monotonic() - finishing)
            self.sprite(sheet.sheet, sheet.box(index), sprite_x, y + 4, sprite_w, sprite_h)
            text_w = sprite_x - x - 56
            self.display(x + 36, 58, "NOW LOADING", 14, "#c7c1d1", "bold")
            self.display(x + 36, 92, self.fit_text((title or SYSTEMS.get(system, "")).upper(), text_w, 30, "bold"), 30, WHITE, "bold", width=text_w)
            self.display(x + 36, h - 92, self.fit_text(status or "Starting…", text_w, 15), 15, "#dedbe3")
            if percent:
                value = max(0, min(100, int(percent.group(1))))
                self.rect(x + 36, h - 60, text_w, 6, "#2a2531")
                self.rect(x + 36, h - 60, text_w * value / 100, 6, ACCENT)
            return
        frames = self.frame_animation(f"loading-{system}", "loading")
        if frames:
            frame = frames.frame_at(elapsed)
            if frame and self.picture(frame, x, y, w, h - 29, mode="cover"):
                return
        self._top_shade(x, y, w, h - 29)
        self.display(x + 36, 58, "NOW LOADING", 14, "#c7c1d1", "bold")
        self.display(x + 36, 92, (title or SYSTEMS.get(system, "")).upper(), 38, WHITE, "bold", width=w - 70)
        self.display(x + 36, 197, self.fit_text(status or "Starting…", w - 90, 16), 16, "#dedbe3")
        # Eight console lights chase along the bottom; a pixel disc slides into the slot.
        lit = int(elapsed * 9) % LIGHTS
        for index in range(LIGHTS):
            on = index == lit or index == (lit - 1) % LIGHTS
            self.rect(x + 36 + index * 22, h - 60, 14, 8, ACCENT if on else "#3a2a44")
        slot_x = x + w - 180
        self.rect(slot_x, h - 66, 120, 20, "#101013")
        self.rect(slot_x + 4, h - 62, 112, 12, "#1d1a24")
        travel = (elapsed * 1.4) % 1.0
        dx = slot_x + 130 - travel * 130
        for row, cols in enumerate(((2, 3, 4), (1, 2, 3, 4, 5), (0, 1, 2, 4, 5, 6), (1, 2, 3, 4, 5), (2, 3, 4))):
            for column in cols:
                self.rect(dx + column * 5, h - 78 + row * 5, 5, 5, "#e8e4f0" if (row, column) != (2, 3) else "#2b2535")
        if percent:
            value = max(0, min(100, int(percent.group(1))))
            self.rect(x + 36, h - 44, w - 72, 6, "#2a2531")
            self.rect(x + 36, h - 44, (w - 72) * value / 100, 6, ACCENT)
            self.display(x + w - 36, h - 62, f"{value}%", 15, WHITE, "bold", anchor="ne")
