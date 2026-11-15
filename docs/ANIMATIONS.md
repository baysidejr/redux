# Stage animations

The stage is the banner area of Home. Idle, it shows a still stretch of the **console
banner carousel**: 52 hanging pennants, a different stretch each time Redux starts.
While a disc installs or a game starts, the whole screen becomes the loading screen: the
carousel scrolls across the top quarter and brakes to a stop with the loading console's
pennant centred under the gold marker, and that console's loader animation plays below
with the game's name and the progress. The pennants only move during a load.

## The banner carousel

`redux/assets/banners/console_banners.png` is an atlas of 52 banners, 8 columns by 7 rows
of 180 × 540 cells (logical art 90 × 270 at an exact 2× scale). `banners.json` gives each
banner's rectangle (`x, y, width, height`), aliases (`playstation` → `psx`, `tg16` → `pce`,
…) and the scroll `order`. `redux/banner_carousel.py` is the motion: still on Home at a
random stretch, cruise at the manifest's 32 banners a second on the loading screen, and on
`stop_on_system(system)` a cubic ease-out that keeps the current speed as its initial
slope, travels at least 8 banners, never reverses, and comes to rest with the target
centred. The stop fires 0.6 s into a load and is independent of the load finishing; a
console without a banner (PC) keeps scrolling. Cells are scaled nearest-neighbour and
clipped at the stage edges.

## The sheets that ship

`redux/assets/animations/` holds one sheet per console and the manifest that describes them.

| Property | Value |
|---|---|
| Sheet | `<system id>.png`, 3840 × 2160, PNG with a transparent background |
| Cells | 8 columns of 480 × 270, read left to right, top to bottom |
| Frames | 60 per console: **intro** 0–23 (the tray opens or the slot takes the game), **loop** 24–47 (the loading bar), **outro** 48–59 (the bar completes) |
| Rate | 12 frames per second |
| Manifest | `animations.json`: `fps`, `frame` size, and per system `sheet`, `columns`, `intro`, `loop`, `outro`, `mechanism` |

The intro plays once, the loop runs for as long as the install or the core load takes,
and the outro plays when an install hands over to the game. Cells are scaled with
nearest-neighbour sampling so the pixels stay crisp at any screen size.

System ids: `nes`, `snes`, `n64`, `genesis`, `32x`, `sega_cd`, `saturn`, `sms`, `game_gear`,
`psx`, `pce`, `pce_cd`, `supergrafx`, `neo_geo`, `neo_geo_cd`, `3do`, `atari2600`,
`atari7800`, `jaguar`, `jaguar_cd`, `gb`, `gbc`. Game Boy Advance and PC have no sheet
and use the drawn animation below.

## Adding or replacing a console

1. Draw the sheet at the same geometry: 8 columns, 480 × 270 cells, transparent background.
2. Save it as `redux/assets/animations/<system id>.png`.
3. Add or edit the system's entry in `animations.json`:

```json
"gba": {"sheet": "gba.png", "columns": 8, "intro": [0, 23], "loop": [24, 47], "outro": [48, 59], "mechanism": "rear cartridge"}
```

Frame ranges are inclusive cell indexes. A console may have a shorter intro or no outro;
the loop is the only range that must exist. Rebuild, and the stage uses it.

## Other ways in

- **Frame folders.** `redux/assets/animations/loading-<system id>/frame_000.png` … (any
  size, 16:9 fills the stage best) with an optional `animation.json` holding `fps` and
  `loop_from`. Used when a console has no sheet.
- **The drawn animation.** Eight console lights chasing, a pixel disc sliding into a slot,
  and the install percentage, drawn with rectangles. Used when nothing else exists.

## Generating a sheet with a script

Every frame is pixel art on a transparent 480 × 270 cell, so a short Pillow script can
produce a sheet. A prompt that gets a code assistant there:

> Write a Python script using Pillow that renders a 60-frame pixel-art sprite sheet of a
> Game Boy Advance loading a cartridge: 3840 × 2160 PNG, 8 columns of 480 × 270 cells,
> transparent background, everything drawn with filled rectangles at integer coordinates
> and no anti-aliasing. Frames 0–23: the cartridge slides into the rear slot and the power
> light comes on. Frames 24–47: a 20-segment loading bar at x 34–446, y 236–262 fills and
> empties (this part loops). Frames 48–59: the bar turns green and holds. Use the palette
> dark violet #0a0816, lavender #b6aad6, spark #9650e6, cream #eee4cc, orange #ec8424,
> white #faf6ec. Save as gba.png.
