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
  manifest.json                which art zips belong to this build (asset, bytes, sha256), written by the pack step
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

* The database covers **North American releases**. A game with a USA, Canada
  or World pressing is in, with all of its regional pressings listed so a
  European or Japanese disc of the same game is still recognised. An overseas
  game gets in only by hand: `{"game id": {"keep": true}}` in `overrides.json`.
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

## Art that is downloaded

Only Sega CD's art ships inside the installer. The other systems' `art/`
folders are packed into one zip each (`gamedb-<system>-art.zip`) and attached
to the GitHub release tagged `database`; `manifest.json` records each zip's
size and SHA-256. System → Database offers the download per system; Redux
checks the hash, unpacks into `%LOCALAPPDATA%\ReduxMediaLibrary\database\<system>\art`
and reads art from there as well as from the bundle. The catalogs and facts
work without any download. In git, `art/` is ignored for every system but Sega CD.

## Where it comes from

Catalog: Redump disc lists as mirrored by libretro-database. Box art and game
images: libretro-thumbnails. Descriptions and facts: TheGamesDB. Hero banners:
SteamGridDB first, TheGamesDB fan art where it has none. The build script lives
outside the repository history (`tools/gamedb/`, gitignored) because it needs a
developer's TheGamesDB and SteamGridDB keys; the output here is what ships.

## Neo Geo cartridge names

`neo_geo/names.json` is not a catalog: it maps FinalBurn Neo set names (`mslug`, `kof98`) to a title and publisher so a zipped Neo Geo AES/MVS game on a game card gets a readable name. Source: libretro-database `dat/SNK - Neo Geo.dat`, English title only, set numbers and board codes stripped. The Database page does not list this system; it has no art or descriptions of its own.
