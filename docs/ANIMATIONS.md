# Stage animations

The stage is the banner area of Home. While a disc installs or a game starts, it shows
that console's pixel art with a small 8-bit loading animation: eight console lights
chasing along the bottom, a pixel disc sliding into the slot, and the progress bar when
there is a percentage to show. Those are drawn with rectangles and ship at no cost.

An owner-made frame animation replaces the drawn one whenever a folder with frames exists.

## Where frames live

```
redux/assets/animations/
  loading/                 used for any console that has no animation of its own
    frame_000.png
    frame_001.png
    ...
    animation.json         optional
  loading-sega_cd/         one folder per console, named loading-<system id>
    frame_000.png
    ...
```

System ids are Redux's: `nes`, `snes`, `n64`, `genesis`, `32x`, `sega_cd`, `saturn`, `sms`,
`game_gear`, `psx`, `pce`, `pce_cd`, `supergrafx`, `neo_geo`, `neo_geo_cd`, `3do`,
`atari2600`, `atari7800`, `jaguar`, `jaguar_cd`, `gb`, `gbc`, `gba`, `pc`.

## Frame format

| Property | Value |
|---|---|
| File names | `frame_000.png`, `frame_001.png`, … in order, no gaps |
| Size | **320 × 100** pixels, drawn as pixel art; Redux scales it to the stage |
| Format | PNG, RGB or RGBA; a transparent background shows the console's art behind it |
| Count | 24 to 72 frames is plenty |
| Palette | the tile palette: dark violet night `#0a0816`, lavender frame `#b6aad6`, spark `#9650e6`, cream `#eee4cc`, orange `#ec8424`, white `#faf6ec` |

`animation.json` beside the frames, both keys optional:

```json
{"fps": 12, "loop_from": 18}
```

`fps` is frames per second (1 to 60, default 12). `loop_from` is the frame the loop
restarts at once the last frame has played, so an intro (tray opens, disc goes in) plays
once and the rest (disc spinning, lights chasing) repeats until the game appears.

## Making frames with a script

Each frame is a PNG, so any tool works. The quickest way is a short Python script with
Pillow, the same way the Atari 800 welcome box was drawn pixel by pixel: a 320 × 100
canvas, a few helper functions for rectangles and 5 × 7 letters, and a loop that writes
one frame per step of the animation. A prompt that gets a code assistant there:

> Write a Python script using Pillow that renders a 48-frame pixel-art animation of a
> Sega CD console loading a disc, 320 × 100 pixels per frame, 12 fps, saved as
> frame_000.png … frame_047.png. Dark violet background #0a0816. Frames 0–17: the tray
> slides out, a disc drops in, the tray slides back. Frames 18–47: the power light blinks
> and a small "LOADING" label pulses; this part loops. Draw everything with filled
> rectangles at integer coordinates; no anti-aliasing. Also write animation.json with
> {"fps": 12, "loop_from": 18}.

Drop the folder into `redux/assets/animations/loading-sega_cd/`, rebuild, and the stage
uses it. Nothing else needs to change.
