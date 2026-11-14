# Redux game database

Preloaded art and facts for the CD systems Redux plays. Redux never scrapes:
when a disc or disc image is recognised, everything on the game screen comes
from here. The database is plain files on purpose, so it can be modded.

## Layout

```
redux/database/
  <system>/
    games.json                 the catalog: one entry per game
    overrides.json             hand-made choices for the build:
                               {"game id": {"thegamesdb": 5011, "steamgriddb": false}}
                               (a TheGamesDB id to pair with; "steamgriddb": false
                               refuses its banner, an id picks one entry)
    art/<game id>/boxart.jpg    front cover, portrait, up to 600x840
    art/<game id>/gameimage.png one in-game picture, native resolution
    art/<game id>/hero.jpg      wide banner shown behind the focused game, up to 1280x720
```

`<system>` is Redux's system id: `sega_cd`, `psx`, `saturn`, `pce_cd`,
`neo_geo_cd`, `3do`, `jaguar_cd`.

## games.json

```json
{
 "system": "sega_cd", "label": "Sega CD", "version": 1, "generated": "2026-11-14",
 "catalog": "Redump via libretro-database (2026.10.07)",
 "games": [
  {
   "id": "sonic-cd",
   "title": "Sonic CD",
   "system": "sega_cd",
   "releases": [
    {"name": "Sonic CD (USA) (RE125)", "region": "USA", "serials": []},
    {"name": "Sonic CD (Europe)", "region": "Europe", "serials": ["4407"]}
   ],
   "year": 1993, "developer": "Sega", "publisher": "Sega",
   "genres": ["Platform"], "players": 1, "rating": "E",
   "description": "...",
   "art": {"boxart": "art/sonic-cd/boxart.jpg", "gameimage": "art/sonic-cd/gameimage.png", "hero": "art/sonic-cd/hero.jpg"},
   "sources": {"boxart": "libretro-thumbnails:Sonic CD (USA)", "information": "thegamesdb:1234"}
  }
 ]
}
```

* A **game** groups every pressing of one title and edition: regions,
  revisions, re-releases and the discs of a multi-disc set. Editions that are
  different discs keep their tag in the title, for example
  `Corpse Killer (Sega CD 32X)`.
* A **release** carries the Redump name, its region and the serials printed in
  the disc header (`T-93175`, `4407`, `G-6025`). Redux matches an inserted disc
  by serial first and by header title second; a few releases have no serial
  on record and rely on the title.
* The facts (`year`, `developer`, `publisher`, `genres`, `players`, `rating`,
  `description`) are optional. Missing ones are simply not shown.
* `art` lists only files that exist. Drop your own `boxart.jpg`,
  `gameimage.png` or `hero.jpg` into a game's folder and the next rebuild keeps
  it (the source is recorded as `local`).

## Where it comes from

Catalog: Redump disc lists as mirrored by libretro-database. Box art and game
images: libretro-thumbnails. Descriptions and facts: TheGamesDB. Hero banners:
SteamGridDB first, TheGamesDB fan art where it has none. The build script lives
outside the repository history (`tools/gamedb/`, gitignored) because it needs a
developer's TheGamesDB and SteamGridDB keys; the output here is what ships.
