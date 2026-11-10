"""Small original presentation filters for software RGB emulator frames.

These operate only on a supplied game frame. Menu/UI pixels and source game
data are never touched. They require no emulator shader, graphics driver hook,
or vendor assets. Cached scanline surfaces are private and never returned.
"""
from __future__ import annotations

from functools import lru_cache
from types import MappingProxyType

from PIL import Image, ImageChops


DISPLAY_FILTERS = MappingProxyType({
    "clean": "Clean",
    "scanlines": "Scanlines",
    "crt": "CRT",
    "composite": "Composite",
})
FILTERS = MappingProxyType({
    "sharp": "Original / sharp",
    "smooth": "Smooth",
    "scanlines": "Scanlines",
    "clean": "Clean",
    "crt": "CRT",
    "composite": "Composite",
})
DEFAULT_FILTER = "sharp"
_MAX_PIXELS = 16_777_216
_MAX_AXIS = 8192


def filter_ids():
    """Legacy per-game menu identifiers; system presets use DISPLAY_FILTERS."""
    return ("sharp", "smooth", "scanlines")


def display_filter_ids():
    return tuple(DISPLAY_FILTERS)


def filter_name(filter_id):
    try:
        return FILTERS[filter_id]
    except (KeyError, TypeError) as error:
        raise ValueError("Unknown game-video filter") from error


def _dimensions(width, height):
    for value in (width, height):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_AXIS:
            raise ValueError("Frame dimensions must be positive integers up to 8192")
    if width * height > _MAX_PIXELS:
        raise ValueError("Frame dimensions exceed the pixel limit")
    return width, height


def fit_size(source_width, source_height, bounds_width, bounds_height):
    """Fit an image's pixel aspect into bounds without cropping or distortion.

    For consoles with a separate display aspect ratio, pass dimensions that
    express that ratio instead (for example 4, 3). The rendering caller owns
    console-specific geometry; filters do not impose an aspect ratio.
    """
    _dimensions(source_width, source_height)
    _dimensions(bounds_width, bounds_height)
    if bounds_width * source_height <= bounds_height * source_width:
        return bounds_width, max(1, bounds_width * source_height // source_width)
    return max(1, bounds_height * source_width // source_height), bounds_height


@lru_cache(maxsize=2)
def _scanline_multiplier(width, height, source_height, strength):
    # At >=2x height, put a narrow dark band at the bottom of each source
    # scanline. At native/downscaled height use alternating display rows.
    # Integer arithmetic avoids accumulated drift at fractional scales.
    rows = bytearray(height * 3)
    for y in range(height):
        if height >= source_height * 2:
            phase = ((2 * y + 1) * source_height) % (height * 2)
            dark = phase * 10 >= height * 14
        else:
            dark = y % 2 == 1
        value = 255 * (100 - strength) // 100 if dark else 255
        rows[y * 3:y * 3 + 3] = bytes((value, value, value))
    return Image.frombytes("RGB", (1, height), bytes(rows)).resize(
        (width, height), Image.Resampling.NEAREST)


@lru_cache(maxsize=1)
def _crt_multiplier(width, height, source_height, strength):
    # An original mild RGB aperture grille. Each three-column group has equal
    # average attenuation, so it does not bias the full image toward a primary.
    row = bytearray(width * 3)
    for x in range(width):
        values = [225, 225, 225]
        values[x % 3] = 255
        row[x * 3:x * 3 + 3] = bytes(values)
    grille = Image.frombytes("RGB", (width, 1), bytes(row)).resize(
        (width, height), Image.Resampling.NEAREST)
    if strength:
        grille = ImageChops.multiply(grille, _scanline_multiplier(width, height, source_height, strength))
    return grille


@lru_cache(maxsize=32)
def _brightness_lut(brightness):
    return tuple(min(255, value * brightness // 100) for value in range(256)) * 3


def _percentage(value, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def render_frame(rgb, width, height, output_width, output_height, filter_id=DEFAULT_FILTER,
                 *, scanline_strength=40, brightness=100):
    """Return an independent RGB image with exactly the requested dimensions.

    Input is packed RGB888 of exactly width*height*3 bytes. "sharp" preserves
    source colors and uses nearest-neighbor scaling; "clean" is its modern
    system-preset alias. "smooth" retains legacy bilinear scaling. "scanlines"
    applies neutral bands, "crt" adds mild blur and an RGB aperture grille,
    and "composite" softens horizontal color transitions without vertical blur.
    These are original presentation approximations, not analog-signal emulation.
    Filtering never alters the caller's bytes or a previously returned image.
    """
    _dimensions(width, height)
    _dimensions(output_width, output_height)
    filter_name(filter_id)
    _percentage(scanline_strength, 0, 100, "Scanline strength")
    _percentage(brightness, 25, 200, "Brightness")
    if not isinstance(rgb, (bytes, bytearray, memoryview)):
        raise TypeError("Frame pixels must be packed RGB bytes")
    if isinstance(rgb, memoryview):
        if not rgb.c_contiguous or rgb.itemsize != 1:
            raise ValueError("Frame memory must be contiguous bytes")
        rgb = rgb.tobytes()
    if len(rgb) != width * height * 3:
        raise ValueError("Frame byte count does not match RGB dimensions")
    image = Image.frombytes("RGB", (width, height), bytes(rgb))
    if filter_id == "composite":
        if width > 1:
            softened = image.resize((max(1, width // 2), height), Image.Resampling.BILINEAR).resize(
                (width, height), Image.Resampling.BILINEAR)
            image = Image.blend(image, softened, 0.65)
        # Horizontal interpolation preserves the source's vertical color rows.
        if output_width != width:
            image = image.resize((output_width, height), Image.Resampling.BILINEAR)
        if output_height != height:
            image = image.resize((output_width, output_height), Image.Resampling.NEAREST)
    else:
        if filter_id == "crt":
            # Blur before scaling keeps the CPU work at the source resolution.
            from PIL import ImageFilter
            image = image.filter(ImageFilter.GaussianBlur(0.35))
        # CRT is already softened at source resolution. Nearest scaling avoids
        # repeating expensive full-output interpolation on every video frame.
        resample = Image.Resampling.BILINEAR if filter_id == "smooth" else Image.Resampling.NEAREST
        if image.size != (output_width, output_height):
            image = image.resize((output_width, output_height), resample)
    if filter_id == "scanlines" and scanline_strength:
        image = ImageChops.multiply(image, _scanline_multiplier(output_width, output_height, height, scanline_strength))
    elif filter_id == "crt":
        image = ImageChops.multiply(image, _crt_multiplier(output_width, output_height, height, scanline_strength))
    if brightness != 100:
        image = image.point(_brightness_lut(brightness))
    return image
