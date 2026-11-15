"""Landscape library tiles composed from a game's own cover and screenshot (game cards, PC games)."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont, ImageOps

def _font(size):
    try:
        from .ui_fonts import font_directory
        paths = sorted(font_directory().glob("*.ttf"), key=lambda path:("italic" in path.name.casefold(), path.name))
        if paths:
            return ImageFont.truetype(str(paths[0]), size)
    except (OSError, ValueError):
        pass
    return ImageFont.load_default(size=size)

def compose_tile(destination, title, sources, *, style="mixed"):
    """Original landscape card composition: keep the complete cover visible."""
    canvas = Image.new("RGBA", (640, 448), (19, 20, 24, 255))
    screenshot = sources.get("screenshot") or sources.get("hero")
    cover = sources.get("cover")
    if screenshot and style != "cover":
        with Image.open(screenshot) as source:
            # Screenshots are contained too; avoid cutting off gameplay UI.
            fitted = ImageOps.contain(source.convert("RGBA"), (640, 366), Image.Resampling.LANCZOS)
            canvas.alpha_composite(fitted, ((640-fitted.width)//2, (366-fitted.height)//2))
    if cover and (style != "screenshot" or not screenshot):
        with Image.open(cover) as source:
            fitted = ImageOps.contain(source.convert("RGBA"), (228 if screenshot and style == "mixed" else 260, 350), Image.Resampling.LANCZOS)
            left = 18 if screenshot and style == "mixed" else (640-fitted.width)//2
            canvas.alpha_composite(fitted, (left, (366-fitted.height)//2))
    if sources.get("logo") and style != "cover":
        with Image.open(sources["logo"]) as source:
            logo = ImageOps.contain(source.convert("RGBA"), (310, 100), Image.Resampling.LANCZOS)
            canvas.alpha_composite(logo, (624-logo.width, 18))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 366, 640, 448), fill=(7, 8, 11, 255))
    font = _font(30)
    text = str(title).strip()[:500]
    lines = []
    while text and len(lines) < 2:
        count = len(text)
        while count > 1 and draw.textlength(text[:count], font=font) > 606:
            count -= 1
        if count < len(text) and " " in text[:count]:
            count = text.rfind(" ", 0, count) or count
        lines.append(text[:count].rstrip()); text = text[count:].lstrip()
    if text and lines:
        lines[-1] = lines[-1].rstrip(" .") + "…"
    for index, line in enumerate(lines):
        draw.text((17, 371 + index*34), line, font=font, fill=(245, 245, 244))
    canvas.save(destination, format="PNG")
