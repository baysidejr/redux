"""Small, original drawing vocabulary for Redux's television-style interface.

Coordinates are logical pixels. Rounded surfaces are rasterised at 3x their
device resolution; icons remain native Canvas paths and scale with the window.
No recovered artwork or third-party icon/font library is required.
"""
from __future__ import annotations

from collections import OrderedDict
import math
from types import MappingProxyType

from PIL import Image, ImageColor, ImageDraw, ImageTk


PALETTE = MappingProxyType({
    "background": "#0b0d10",
    "surface": "#15181e",
    "surface_raised": "#20252d",
    "surface_hover": "#2a303a",
    "border": "#303640",
    "border_subtle": "#242a32",
    "text": "#f4f4f2",
    "muted": "#a4abb5",
    "dim": "#6c7582",
    "accent": "#ec407a",
    "accent_hover": "#fa6299",
    "on_accent": "#ffffff",
    "success": "#9bd6b2",
    "warning": "#e7bb83",
    "scrim": "#080a0d",
})

TYPE = MappingProxyType({
    "family": "Segoe UI",
    "display": 42,
    "title": 30,
    "section": 20,
    "body": 13,
    "label": 11,
    "caption": 9,
})

SPACE = MappingProxyType({"xxs": 4, "xs": 8, "sm": 12, "md": 16,
                          "lg": 24, "xl": 32, "xxl": 40})

BACKGROUND = PALETTE["background"]
SURFACE = PALETTE["surface"]
RAISED = PALETTE["surface_raised"]
WHITE = PALETTE["text"]
SUBTLE = PALETTE["muted"]
ACCENT = PALETTE["accent"]
MUTED = PALETTE["dim"]
BORDER = PALETTE["border"]
ON_ACCENT = PALETTE["on_accent"]


def _rounded_surface(width, height, radius, fill, outline, stroke):
    """Produce an RGBA surface; fractional corner edges retain alpha coverage."""
    width, height = max(1, int(width)), max(1, int(height))
    sample = 3
    image = Image.new("RGBA", (width * sample, height * sample), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    border = min(max(0, float(stroke)), min(width, height) / 2) if outline else 0
    inset = max(0, border * sample / 2)
    box = (inset, inset, width * sample - 1 - inset, height * sample - 1 - inset)
    radius = min(max(0, radius), width / 2, height / 2) * sample
    draw.rounded_rectangle(box, radius=radius,
        fill=ImageColor.getcolor(fill, "RGBA") if fill else None,
        outline=ImageColor.getcolor(outline, "RGBA") if outline else None,
        width=max(1, round(border * sample)))
    return image.resize((width, height), Image.Resampling.LANCZOS)


class Painter:
    """Logical-coordinate surfaces and paths over one Tk Canvas.

    Keep the Painter alive while its images are displayed. A retained Painter
    should call ``begin_frame`` after deleting old Canvas items; that releases
    the previous frame's references while preserving the bounded surface cache.
    """

    def __init__(self, canvas, scale=1, offset=(0, 0), *, _photo_factory=None):
        self.canvas = canvas
        self.scale = float(scale)
        self.offset = tuple(offset)
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._visible = []
        self._photo_factory = _photo_factory or (lambda image: ImageTk.PhotoImage(image, master=canvas))
        self.transform(scale=scale, offset=offset)

    def transform(self, *, scale=None, offset=None):
        if scale is not None:
            if not math.isfinite(scale) or scale <= 0:
                raise ValueError("Painter scale must be finite and positive")
            self.scale = float(scale)
        if offset is not None:
            if len(offset) != 2 or not all(math.isfinite(value) for value in offset):
                raise ValueError("Painter offset must contain two finite coordinates")
            self.offset = tuple(offset)
        return self

    def begin_frame(self, *, scale=None, offset=None):
        self._visible.clear()
        return self.transform(scale=scale, offset=offset)

    def clear_cache(self):
        self._cache.clear()
        self._cache_bytes = 0

    def rounded_rect(self, x, y, w, h, fill, radius=16, outline=None, stroke=1):
        if w <= 0 or h <= 0:
            return None
        scale = self.scale
        width, height = max(1, round(w * scale)), max(1, round(h * scale))
        key = (width, height, round(radius * scale, 2), fill, outline, round(stroke * scale, 2))
        if key not in self._cache:
            surface = _rounded_surface(width, height, key[2], fill, outline, key[5])
            photo = self._photo_factory(surface)
            size = width * height * 4
            self._cache[key] = (photo, size)
            self._cache_bytes += size
            while len(self._cache) > 96 or (self._cache_bytes > 32 * 1024 * 1024 and len(self._cache) > 1):
                _, (_, released) = self._cache.popitem(last=False)
                self._cache_bytes -= released
        self._cache.move_to_end(key)
        photo = self._cache[key][0]
        self._visible.append(photo)
        return self.canvas.create_image(self.offset[0] + x * scale, self.offset[1] + y * scale,
                                        image=photo, anchor="nw")

    def icon(self, name, x, y, size, color=WHITE, stroke=2, filled=False):
        """Draw a standard 24-unit icon; return its Canvas item IDs."""
        if name not in _ICONS:
            raise ValueError(f"Unknown UI icon: {name}")
        if not math.isfinite(size) or size <= 0:
            raise ValueError("Icon size must be finite and positive")
        unit = size * self.scale / 24
        left, top = self.offset[0] + x * self.scale, self.offset[1] + y * self.scale
        width = max(1, stroke * self.scale)
        items = []

        def points(values):
            return tuple(coordinate for px, py in values for coordinate in (left + px * unit, top + py * unit))

        def line(*values):
            items.append(self.canvas.create_line(*points(values), fill=color, width=width,
                         capstyle="round", joinstyle="round"))

        def oval(x1, y1, x2, y2, solid=False):
            items.append(self.canvas.create_oval(left + x1 * unit, top + y1 * unit,
                left + x2 * unit, top + y2 * unit, fill=color if solid else "", outline=color,
                width=width))

        def polygon(values, solid=filled):
            items.append(self.canvas.create_polygon(*points(values), fill=color if solid else "",
                         outline=color, width=width, joinstyle="round"))

        if name == "search":
            oval(3, 3, 16, 16); line((15, 15), (21, 21))
        elif name == "home":
            line((3, 10), (12, 3), (21, 10)); line((5, 9), (5, 21), (10, 21), (10, 15), (14, 15), (14, 21), (19, 21), (19, 9))
        elif name in {"library", "collections"}:
            if name == "library":
                line((4, 4), (4, 20)); line((9, 4), (9, 20)); line((14, 5), (20, 19))
                line((2, 21), (22, 21))
            else:
                line((3, 7), (3, 20), (21, 20), (21, 7), (3, 7)); line((5, 3), (11, 3), (13, 7))
                line((8, 12), (16, 12)); line((12, 10), (12, 16))
        elif name == "systems":
            line((3, 4), (21, 4), (21, 17), (3, 17), (3, 4)); line((12, 17), (12, 21)); line((7, 21), (17, 21))
        elif name == "play":
            polygon(((7, 4), (20, 12), (7, 20)))
        elif name == "plus":
            line((12, 4), (12, 20)); line((4, 12), (20, 12))
        elif name == "back":
            line((10, 5), (3, 12), (10, 19)); line((3, 12), (21, 12))
        elif name in {"chevron", "chevron_left", "chevron_down", "chevron_up"}:
            values = {"chevron": ((9, 5), (16, 12), (9, 19)), "chevron_left": ((15, 5), (8, 12), (15, 19)),
                "chevron_down": ((5, 9), (12, 16), (19, 9)), "chevron_up": ((5, 15), (12, 8), (19, 15))}[name]
            line(*values)
        elif name == "star":
            polygon(tuple((12 + (10 if n % 2 == 0 else 4.5) * math.cos(-math.pi / 2 + n * math.pi / 5),
                           12 + (10 if n % 2 == 0 else 4.5) * math.sin(-math.pi / 2 + n * math.pi / 5)) for n in range(10)))
        elif name == "check":
            line((4, 12), (9, 17), (20, 6))
        elif name == "controller":
            line((6, 7), (18, 7), (20, 9), (22, 17), (20, 20), (17, 17), (7, 17), (4, 20), (2, 17), (4, 9), (6, 7))
            line((6, 10), (6, 14)); line((4, 12), (8, 12)); oval(15, 10, 16, 11, True); oval(18, 13, 19, 14, True)
        elif name == "disc":
            oval(2, 2, 22, 22); oval(9, 9, 15, 15); line((14, 4), (18, 8)); line((6, 16), (10, 20))
        elif name == "storage":
            line((5, 3), (19, 3), (22, 19), (20, 21), (4, 21), (2, 19), (5, 3)); line((3, 16), (21, 16)); oval(17, 18, 18, 19, True)
        elif name == "volume":
            line((3, 9), (7, 9), (12, 4), (12, 20), (7, 15), (3, 15), (3, 9))
            line((16, 8), (18, 10), (18, 14), (16, 16)); line((19, 4), (22, 8), (23, 12), (22, 16), (19, 20))
        elif name == "info":
            oval(3, 3, 21, 21); line((12, 11), (12, 17)); oval(11.1, 7.1, 12.9, 8.9, True)
        elif name == "power":
            arc = tuple((12 + math.cos(math.radians(a)) * 8.5, 12 + math.sin(math.radians(a)) * 8.5) for a in range(-55, 236, 10))
            line(*arc); line((12, 3), (12, 12))
        elif name == "settings":
            gear = []
            for tooth in range(8):
                for offset, radius in ((-.32, 8), (-.22, 10), (.22, 10), (.32, 8)):
                    angle = tooth * math.pi / 4 + offset
                    gear.append((12 + math.cos(angle) * radius, 12 + math.sin(angle) * radius))
            polygon(tuple(gear), False); oval(8.5, 8.5, 15.5, 15.5)
        return tuple(items)


_ICONS = frozenset({"search", "settings", "home", "library", "collections", "systems",
    "play", "plus", "back", "chevron", "chevron_left", "chevron_down", "chevron_up",
    "star", "check", "controller", "disc", "storage", "volume", "info", "power"})
