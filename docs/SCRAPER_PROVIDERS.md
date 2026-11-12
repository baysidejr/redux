# Artwork services

Redux uses original service adapters for the scraper choices exposed by
[Batocera EmulationStation](https://github.com/batocera-linux/batocera-emulationstation/blob/master/es-app/src/scrapers/Scraper.cpp).
It does not run EmulationStation or reuse another application's developer keys.
Images are selected by role: front cover, landscape tile/mix, hero/fan art,
screenshot and logo. An ordinary screenshot is never labeled a dedicated tile.

## Living-room controls

Open **System → Artwork & scraping → Accounts & API keys**. This always lists
every service, even when the current artwork source is the no-account Libretro
fallback. Choose **ScreenScraper** for its username, password and developer API
fields, **TheGamesDB** for its API key, **HFSDB** for its API login, or **IGDB** for
its client ID and secret. ArcadeDB and Libretro are labeled **No account needed**.
The list shows only whether values are saved, never the account values themselves.

Choosing an account does not change the active artwork service. After entering
its details, choose **Use this artwork service** to switch to it. **Back** or
B returns from the service's account page to the all-services account list.
The same account list is available from **Find game artwork**.

The controller keyboard includes lowercase, uppercase, digits and punctuation;
password and secret fields are masked. Keyboard typing and paste remain optional.
Save returns to the same service's account page; B cancels without saving.
Credentials are encrypted with current-user Windows DPAPI outside the portable
library. Copying a library copies its art and preferences, not usable account
credentials. Enter those again on a different Windows account or PC.

**Find game artwork** can search for the selected game, fill the current view or
fill the whole library. A single-game search shows matches for you to choose;
**Change search title** changes only that lookup, not your library title. Bulk
scraping applies only a matching system/title and compatible known region
and year. An exact regional release wins over worldwide or regionless entries;
multiple entries at the best region still require confirmation. TheGamesDB's
country/region identifiers distinguish regional releases, including US and
Brazilian NTSC entries. Known trailing disc/revision, region and language dump
tags are ignored for artwork lookup only; editions, subtitles and library/media
identity are retained. A title's region tag is used before the preferred-region
setting when no region metadata is present. Ambiguous matches remain unchanged
and the completion menu offers **Review unmatched games** for controller-based
match selection.
B cancels after the current bounded read, retaining earlier completed games.

Since 0.10.10 every account service and Libretro cover Atari Jaguar, Jaguar CD and
3DO. A service with no entries for a game's system, such as ArcadeDB for console
games, is reported as *not covered* with a **Change artwork service** choice
instead of a failed lookup, for a single game and in bulk; those games are never
sent to the service. Bulk errors show the service's own display message. For
lookup only, a trailing release year, ROM-collection bracket codes such as
`[a1]`, Jaguar file-format labels such as `(jag)` and the publisher label that
GoodTools/TOSEC names place directly after the year are ignored alongside the
disc/revision/region/language tags; release-status tags such as `(Beta)`,
`(Proto)`, `(Demo)` and `(PD)` and any edition text are kept, so a prototype or
public-domain demo never receives the retail release's artwork. Libretro accepts
a `(World)` or multi-region release for any preferred region, collapses `(Rev A)`
and `(Rev B)` files into one choice, and reads the three thumbnail folders one at
a time when a system's index (PlayStation) exceeds GitHub's recursive listing.

**Download options** controls preferred region/language, image types, generated
tile style and replacement of existing artwork. Replacement is OFF by default.
Missing files in Redux's own managed artwork folder can be repaired; external
artwork references stay protected until you explicitly allow replacement.
**Artwork after import** optionally looks up newly imported media files; it is
OFF by default. This includes files found through Scan a folder and imported by
the normal media-file workflow. Physical-disc detection and disc installation do
not automatically start this scraper; use the selected-game or library action
for those entries. No service account or network connection is needed to keep using
the local library or already downloaded artwork.

From a game's **Manage → Artwork** menu, choose **Find artwork online** or select
local files separately for its cover, tile, background, screenshot and logo.
All selected/downloaded images are copied into content-addressed files beneath
`<library>/artwork`; references are relative. Source attribution and image hashes
are retained without request credentials.

Cards prefer a dedicated landscape tile. When requested and unavailable, Redux
can build an original landscape tile using a complete fitted box cover, screenshot,
logo and title. With no tile, the full cover is fitted instead of center-cropped.
Game-details covers are fitted too; cinematic backgrounds use a separate hero,
screenshot or system fallback rather than stretching a portrait cover.

| Provider | Required setup | Scope |
|---|---|---|
| ScreenScraper | Account username/password **and** the independent application's developer API ID/password | Covers, screenshots, mixed tiles, fan art and logos |
| TheGamesDB | Your own API key | Covers, screenshots, fan art and logos where supplied |
| HFSDB | Your own HFS API-enabled username/password | Covers, screenshots, artwork and logos where supplied |
| IGDB | Your Twitch application's client ID and client secret | Covers, screenshots and artwork |
| ArcadeDB | No account | Arcade ROM-set identifiers, such as `mslug`; not console games |
| Libretro thumbnails | No account | Exact system/title/region box art and screenshots from the public community repositories |

The ArcadeDB adapter is implemented and live-checked independently. The current
library system list does not expose Arcade or Neo Geo AES/MVS entries, so its
console entries receive an unsupported-system notice when using ArcadeDB. This
provider integration does not add or claim arcade gameplay support; Neo Geo CD
is a separate console system.

Batocera's ScreenScraper, TheGamesDB and HFSDB adapters depend on developer
credentials provided by that project's build. Open-source frontend code does
not provide API authorization for an unrelated application. Redux therefore
requires credentials belonging to the user or this application deployment.
An ordinary HFS website account is not established to have API access.

An identified game's region takes precedence; the selected scraper region is
used when the game has no known region. ScreenScraper chooses matching-region
names/media and the selected synopsis/genre language when supplied. With an
explicit region, artwork falls back only to worldwide or regionless images;
foreign-region-only artwork is left missing rather than mislabeled. The
candidate's region describes this requested projection, not verification of a
particular physical release. TheGamesDB supplies regional identifiers for its
individual release records; Redux uses those for automatic matching. HFSDB chooses
localized title/description fields and a matching regional release date when
available. Both fall back to English/French. TheGamesDB, IGDB and ArcadeDB do not
currently provide translated descriptions through these adapters. Libretro uses
the region to select the matching cover/screenshot; it does not translate text.

ScreenScraper's API documents separate developer authentication and optional
user authentication. Redux asks for the account too because the service may
restrict guest scraping. IGDB uses the documented Twitch client-credentials
grant; tokens are held for the lookup and not added to portable game metadata.

Provider requests have HTTPS, timeout, response-size and result-count limits.
API redirects cannot forward credentials to another origin. Authentication and
quota failures are shown separately; raw responses, request URLs, account
passwords and API tokens are not included in errors. Download URL provenance
must use `sanitize_url`, which removes query values and user information.
ScreenScraper's documented game-not-found status is an empty search result;
service closure, blocked-client, concurrency and daily quota statuses remain
distinct failures. Its HTTP 401 can mean temporary service load/account access,
so it is not reported as a proven incorrect password.

No ROM bytes are uploaded by these adapters. Lookup sends only the selected
game title, system/region filter or arcade set identifier and the credentials
needed by that provider. Libretro queries a bounded public filename index,
checks that it is complete, and caches it in memory for 30 minutes. This avoids
inventing artwork filenames or treating an unavailable image as a match.

## Acceptance

The fixture suite exercises all six adapters, role mapping, wrong-platform
filtering, special-character credentials, cancellation, API response limits,
redirect isolation, and sanitized authentication/quota errors. Live account
services remain dependent on actual configured credentials. ScreenScraper,
TheGamesDB, HFSDB and IGDB were initially tested with fixtures. The full 0.9.0 suite passed
808 tests with no failures, errors or skips; its versioned record is
`analysis/test_results_0.9.0.json`.

On 2026-11-09, a real no-account Libretro lookup matched the existing Saturn
World Series Baseball 2 title to World Series Baseball II (USA), with box art
and a screenshot. A real ArcadeDB lookup matched `mslug` and returned cover,
screenshot and logo roles. Evidence:
`analysis/scraper_reference/live-provider-acceptance.json`.

On 2026-11-10, the 0.10.9 TheGamesDB bulk regression was live-tested with the
configured account in an isolated copy of the installed library. Five games
received cover, screenshot and mixed tile artwork, one kept existing artwork,
and two non-exact titles remained available for manual review. Reopening the
database and decoding the saved files verified persistence. ScreenScraper,
HFSDB and IGDB still require authenticated live acceptance. Evidence:
`analysis/artwork-batch-0.10.9/result.json`.

On 2026-11-12, 0.10.10 was live-tested on isolated copies of the installed
154-game library (139 Atari Jaguar entries). A no-account Libretro pass over the
whole library updated 41 games, kept 6 with complete artwork, reported the 6
unassigned-system entries as not covered and left 100 homebrew, demo and
unlisted titles for review, with no lookup errors after the PlayStation
folder-by-folder index fallback. With the configured TheGamesDB key, a
single-game search for Alien vs Predator returned one Jaguar match and applied
cover, hero, screenshot, logo and a composed tile; an eight-game Jaguar batch
updated six, kept one and left Doom - Evil Unleashed for review. Files were
decoded after reopening each copy. Evidence: `analysis/artwork-batch-0.10.10/`.

## Primary references

- [Batocera scraper selection and accounts](https://wiki.batocera.org/scrape_from)
- [ScreenScraper API2](https://www.screenscraper.fr/webapi2.php)
- [TheGamesDB API documentation](https://api.thegamesdb.net/)
- [HFSDB's integration in Batocera](https://github.com/batocera-linux/batocera-emulationstation/blob/master/es-app/src/scrapers/HfsDBScraper.cpp)
- [IGDB API and authentication](https://api-docs.igdb.com/)
- [Arcade Database scraper API and attribution](https://adb.arcadeitalia.net/service_scraper.php)
- [Libretro thumbnails](https://github.com/libretro-thumbnails/libretro-thumbnails)

Arcade Database requests that consumers credit the source and use one connection
at a time. Its images and game data remain subject to their respective authors'
rights; the service attribution is retained with scraped results.
