# Console banner carousel

Owner-made 8-bit console pennants, supplied 2026-11-15: `console_banners.png` is an atlas of
52 banners (8 columns x 7 rows of 180x540 cells, logical art 90x270 at an exact 2x scale,
opaque dark violet background); `banners.json` gives each banner's rectangle, the aliases and
the scroll order. The stage scrolls them right to left as the hero and brakes onto the
console whose game is loading (`redux/banner_carousel.py`, a port of the owner's motion
reference). Original artwork made for Redux. See docs/ANIMATIONS.md.
