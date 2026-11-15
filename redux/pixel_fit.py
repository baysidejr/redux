"""Fitting pixel art into a space: keep the aspect ratio, prefer whole-number enlargements.

Sprites are authored at a logical size (a loader cell is 240x135, a banner 90x270) and
exported at an exact multiple. Drawn at a whole-number multiple of the logical size, every
art pixel becomes a solid square of screen pixels; any other factor makes some art pixels
a screen pixel wider than their neighbours, which reads as wobble. So a sprite is
contained in its space at the largest whole factor that fits, and only drops to a
fractional factor when even 1x does not fit.
"""
from __future__ import annotations

import math


def fit_factor(avail_w, avail_h, logical_w, logical_h):
    """The largest factor that contains the sprite: a whole number when at least 1x fits."""
    if avail_w <= 0 or avail_h <= 0 or logical_w <= 0 or logical_h <= 0:
        return 0
    factor = min(avail_w / logical_w, avail_h / logical_h)
    return math.floor(factor) if factor >= 1 else factor


def contain(x, y, avail_w, avail_h, logical_w, logical_h):
    """(left, top, width, height, factor) of the sprite contained and centred in the space, in whole pixels."""
    factor = fit_factor(avail_w, avail_h, logical_w, logical_h)
    width, height = round(logical_w * factor), round(logical_h * factor)
    left = x + (avail_w - width) // 2
    top = y + (avail_h - height) // 2
    return int(left), int(top), int(width), int(height), factor


def fit_height(avail_h, logical_h):
    """A factor for a strip of sprites filling a band's height: whole when at least 1x fits."""
    if avail_h <= 0 or logical_h <= 0:
        return 0
    factor = avail_h / logical_h
    return math.floor(factor) if factor >= 1 else factor
