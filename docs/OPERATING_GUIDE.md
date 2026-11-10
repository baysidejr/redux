# Redux Media Library 0.7.0 operating guide

## Start and choose storage

Open `Redux.exe` from its complete distribution folder. Keep `_internal` beside the executable. For source setup and packaging commands, see the [README](../README.md).

The default is **library** inside the project/app folder, regardless of the working directory. Normal disc installation automatically uses this library's managed game storage and does not ask for an output path. Project-contained library settings are stored relatively, so the complete app folder can move between PCs. Choose **System → Library & storage → Library folder**, or **Media → Library location**, only to change to another local disk, SSD or SD card. An external location remains dependent on that storage. You can also launch with an explicit folder:

```powershell
python -m redux --library D:\ReduxLibrary gui
```

The library contains its SQLite database, managed media, user-selected artwork, game preferences, session records, and save backups. Moving the whole library preserves managed media, ratings, and relative artwork paths. External emulator settings use absolute executable paths; reconfigure them after moving those programs. Linked media stays at the original path and is offline when that storage is absent. Close the app and emulator before manually moving or copying a live library.

No reference application hardware, account, resource pack, or internet check is required to operate the independent library. Specific backends may require local firmware or other runtime components.

## Navigate the desktop

The left navigation opens **Now Playing**, **Collection**, **Playlists**, **Media**, and **System**. Now Playing has a large game banner, system tiles, and recent activity. Collection opens a full-width artwork grid; selecting a game opens its cinematic details screen. Playlists groups your custom lists alongside Favorites, Continue Playing, Wishlist, and Completed views. Media holds import and optical-drive actions. System holds storage, playback, controller, backup, and display settings.

The Collection filter bar offers **A–Z** sorting, **Genre**, **Players**, **Year**, **Rating**, and **System**. Genre and year use saved metadata; Rating uses your own local stars. Players currently reports that player counts have not been assigned. Select **All Systems** below the bar to switch between All Games, Continue Playing, Favorites, Wishlist, and Completed Games. A game's details screen provides **Play**, **Manage**, and **Rate** tabs. Manage opens game information, artwork, playlists, saves, and more options. Rate saves 1–5 stars or clears the rating.

Mouse clicks activate controls. Hovering a game selects it; the mouse wheel moves through library pages. The interface also has directional keyboard and SDL3 gamepad navigation. Known mappings are loaded automatically; the XInput-only reader is retained as a fallback if SDL cannot start:

| Action | Keyboard | Mapped controller |
| --- | --- | --- |
| Move focus | Arrow keys | D-pad or left stick |
| Select a control / open card details | Enter | A |
| Go back / close overlay | Escape; Backspace outside search | B |
| Toggle selected game's favorite | F or X outside search | X |
| Manage selected game while browsing Now Playing, Collection, or details | Select the Manage tab | Y |
| Quick play selected game on those screens | Select Play | Start |
| Open library search | Ctrl+F or click the search orb | Select the search orb |
| Cycle system filter | Use the System filter | LB / RB |
| Open settings | Select System | Select System |
| Go to Now Playing | Home | Select/Back while browsing; Home/Guide also returns home when no game is running |
| Cycle focus | Tab | Directional controls |
| Toggle frontend fullscreen | F11 | System → Display & artwork |
| Open / close running-game menu | Embedded game: Escape or F10; external game: focus Redux and press F10 or select Play again | Home/Guide, or hold Select/Back + Start for one second |
| Quit the running game | Choose Stop in the game menu | Select/Back + Start + LB + RB together |
| Close Redux | Alt+F4 | System → Exit app |

In Search, type directly or use the rounded onscreen keyboard in the lower overlay. Backspace deletes a character; **Results** hides the keyboard and moves to the result cards. Text fields also accept direct typing and paste. Hold Select/Back and Start together for one second to request the same menu as Home/Guide. Guide availability depends on what Windows and the controller expose. The frontend ignores normal navigation presses while an external game is active so it does not also browse the library during gameplay. External emulator gameplay bindings remain separate.

The console layout uses a 16:9 canvas and can run fullscreen. Its bundled Roboto Condensed font is registered privately while Redux runs. Normal file/folder selection stays in the console: D-pad navigates, A opens folders or marks a file, B goes to the parent and cancels from Locations, X toggles file selection, Y adds selected files or uses the current folder, and LB/RB pages. Locations and Cancel remain reachable while a listing loads or a drive is unavailable. Advanced manual emulator configuration is separate from normal media browsing.

### Controller setup

Connect a USB controller, or pair a Bluetooth controller in Windows. The packaged SDL3 library and controller database provide automatic known-device mappings. This workspace's Atari VCS Modern Controller was detected with a valid mapping and Guide capability. Physical button presses have not yet been verified by that detection record; [the controller/menu status](CONTROLLER_MENU_STATUS.md) separates native discovery, simulated input tests and gameplay acceptance.

For an unknown or differently mapped pad, open **System → Controllers → Configure a controller**. Select the device, press the requested controls, use **Skip** for optional buttons that do not exist, and choose Save. The wizard waits for release between assignments, rejects duplicate assignments, and retains its progress through a recoverable disconnect. Custom mappings are stored per controller GUID/platform in `%LOCALAPPDATA%\ReduxMediaLibrary\controller-mappings.txt`; the rest of the frontend configuration is `%LOCALAPPDATA%\ReduxMediaLibrary\settings.json`.

If an active controller disconnects during a game, Redux requests the game menu and shows **CONTROLLER DISCONNECTED** with the affected player. After resuming integrated playback, gameplay input remains zero until every controller's buttons, sticks and triggers return to neutral. This prevents held menu controls from entering the game. Home/Guide and the fallback chord are handled before ordinary navigation from the same input frame.

## Import existing media

1. In **System → Library & storage**, choose **Import mode: Managed copy** or **Link files**.
2. In **Media**, choose **Add media files** or **Scan a folder**. These actions are also available under **System → Discs & imports**.
3. In the in-app browser, open a location and navigate with the D-pad/A. For Add Media, mark one or more files with A or X and press Y to add them. For Scan Folder, enter the desired folder and press Y to scan it and its subfolders. For CUE/BIN, prefer the CUE; for multi-disc, the M3U; for CloneCD, the CCD. Selected referenced tracks are removed from duplicate import candidates.
4. Let copying and hashing finish. Cancel stops the operation without publishing an incomplete new entry.
5. Open the game, then **Manage → Game information** to correct unassigned or ambiguous information. Select a field, edit it, and choose **Save** on the Game Information page to commit the draft. **Cancel** discards that draft.

Copy mode preserves a descriptor bundle's original bytes and relative references. It does not flatten raw/mixed-mode media into ISO. Link mode saves space but requires the complete original bundle to remain available. Reimporting an existing linked entry in copy mode promotes that same entry into managed storage after validation.

Supported import extensions include common cartridge ROMs and `.cue`, `.m3u`, `.ccd`, `.iso`, `.chd`, and `.bin`. Import support is separate from decoding and playback support. CHD identification/decoding and platforms without a recognized header require a suitable backend or user-supplied metadata. System assignments are not inferred solely from a generic disc-image extension.

Open **Manage → More options → Verify every media file** to compare all recorded files with their imported SHA-256 hashes. Offline or partial entries need their missing storage/files restored. A changed hash reports changed bytes; it does not diagnose whether the game is authentic or playable.

Favorites, wishlist, completion flags, and ratings are local organization tools. Ratings are stored in `game_data/<game-id>/<bundle-hash>/preferences.json` inside the library. Create custom lists in **Playlists**, then use **Manage → Add to playlist** on a game. Removing a library entry removes its database/history/playlist references while retaining source and managed media files.

## Use a mounted image or PC optical drive

The app observes drives in the background, including discs already inserted at startup. An identified physical disc replaces the Now Playing banner with its title and optional exact-match game artwork. Mounted images take lower priority, so unmounting one does not clear a connected physical disc. Detection does not import, launch, or require firmware. Focus the banner and press A/Enter for **Disc options**, or Y for game information. **Play disc** uses the original mounted image or prepares a verified temporary physical-disc buffer on disk. It does not add a library entry, and its owned temporary files are released only after the core closes; game saves remain in the library. **Install disc to library** automatically captures/imports into managed library storage with no destination picker. While playing from a buffer, **Pause → Install to library** reuses those existing files without a second optical read. B cancels before publication; after commit, save synchronization finishes and the game can resume. A USA Saturn disc needs compatible `mpr-17933.bin` firmware in the chosen firmware directory before Play disc starts buffering. Installation does not require firmware.

Disc identity is read from a small header/TOC probe, not a full-disc hash. Optional USA Saturn artwork comes from exact title/region matches in libretro-thumbnails and is cached with source URLs and hashes under `cache/disc_artwork` in your library. Without internet or a matching image, the identified title still appears. Temporary drive spin-up/read errors retain the known disc; removal and replacement update it without navigating away from your current page or game.

Open **Media → Optical drives**, or **System → Discs & imports → Optical & mounted drives**.

- **Inspect / import mounted source** opens a drive chooser. Select a drive to open the console inspection page, which shows its volume, readable data size, available track layout, and inspection notes. **Import source** appears when a supported virtual driver exposes the original image. WinCDEmu source discovery is implemented; arbitrary virtual drivers may require selecting the original file manually. **Refresh**, **Export data**, and **Capture CD tracks** are available from the page.
- **Acquire a data volume** assesses the selected drive first. A discoverable original mounted image is imported directly. An acceptable ISO9660 data-volume plan can export its cooked logical extent, verify output and repeated source reads, and then import the result.
- **Capture supported physical CD tracks** inspects the source and offers **Install to library**. Capture and managed installation use automatic private storage; there is no output-folder picker. Advanced CLI capture still accepts an explicit destination.
- **Add existing image files** imports an original descriptor/ROM directly from storage.

An inconsistent virtual TOC can coexist with a readable ISO9660 volume. The volume fallback reports that the track layout is unavailable. It does not invent missing tracks or silently adjust sector counts. If source discovery is unavailable, choose the original CUE/ISO manually.

Cooked data-volume acquisition is limited to readable 2048-byte ISO9660 logical data. Known audio/mixed-mode or unsupported track/session layouts are rejected as plain ISO. The separate main-channel CD route accepts qualifying pressed single-session CDs through bounded SCSI READ CD, preserves 2352-byte data/audio sectors and retains TOC INDEX 01. A data-to-audio INDEX 00 can be inferred only with narrow typed-read and Q corroboration. CD-R/RW, uncertain sessions, unsupported modes and unverified transitions are refused.

Neither route claims a full-disc, subchannel/protection-preserving or archival-exact image. Main-channel capture does not calibrate audio offsets/jitter or preserve hidden lead-in audio and CD-TEXT. Short reads, changed data, cancellation and output collisions stop publication. The real Saturn World Series Baseball 2 disc completed two matching source passes and output verification on the attached MATSHITA USB drive. See [exact supported scope and evidence](raw-acquisition.md).

A mounted ISO/CUE can exercise virtual-drive inspection and existing-image import. It cannot validate a physical drive's raw reads, optical errors or audio extraction. Successful capture on one drive/disc does not establish all-device compatibility or Saturn playback; firmware and emulator acceptance remain separate.

## Configure and play a game

Open **System → Emulators & firmware**. **Detect local backends** checks the project's known `tools/emulators` folders; it does not search personal directories or download programs. Existing per-system choices are retained. **System playback** lets you select a backend for each system; its advanced option accepts an executable and an explicit argument array. `{game}` is substituted as one argument; `{game_dir}` is the entry file's directory. Arguments are passed without a command shell.

The backend chooser offers compatible detected programs and **Advanced executable and arguments**. It also offers named integrated cores when the corresponding DLL is present. Choosing an external backend disables integrated playback for that system; choosing an integrated option enables it. The software core profiles select integrated playback automatically when available; NES and PSX have recorded game acceptance. N64 and MAME remain explicit compatibility choices. See [core/configuration alignment](CORE_CONFIG_ALIGNMENT.md) for the selected families, translated options and limits.

The local setup includes separately obtained Mesen and PCSX-Redux. Their provenance is outside the frontend package. PCSX-Redux's profile uses the bundled open-source OpenBIOS and the actual `PCSXReduxCore.exe` process. Other profiles, including DuckStation, Mednafen, and Mupen64Plus, are configuration aids rather than bundled, fully verified platform implementations. Their supported formats, plugins, firmware, and compatibility remain backend-specific.

Firmware defaults to the project's **bios** folder. The app creates the default folder when writable and checks its files again on the next Play action. Adding a BIOS there requires no manual folder selection. **Firmware directory** shows the active path and lets you choose an alternative; a custom selection is remembered and takes precedence. Readiness checks confirm expected local files, while the emulator validates their contents/region. The app never obtains retail BIOS automatically. OpenBIOS allows the tested PSX paths to boot without a retail PS1 BIOS; compatibility varies by game.

Select a game and use its **Play** tab, or press **Start** while browsing that game. A missing bundle, unavailable backend, or known firmware requirement appears as a visible error. The frontend starts one external game at a time. It records launches, last played time, and played duration; acknowledged pause time is excluded when the desktop session ends normally. The CLI `play` command launches directly and does not provide the desktop menu/session lifecycle.

For the local PCSX-Redux keyboard defaults, Return is Start; X is Cross; D is Circle; Z is Square; S is Triangle; Backspace is Select; arrows are the D-pad. Those settings belong to that portable backend configuration and may change if you reconfigure it.

### Integrated playback

With optional core DLLs present, playback stays inside Redux and shares its menu. NES uses Mesen; PSX uses software Beetle PSX; SNES uses Supafaust; Saturn uses Beetle Saturn; PC Engine/CD/SuperGrafx uses full Beetle PCE. Sega profiles use qualified Genesis Plus GX or PicoDrive replacements. Game Boy/Color uses Gambatte. Presence and initialization do not establish compatibility with every game. Diablo has boot/menu/state acceptance through integrated PSX; Saturn still needs compatible firmware and game acceptance.

The host accepts software video, digital joypads, both analog sticks and trigger buttons. Requested profile options are validated against the installed core's declarations before use. GPU-context cores, lightgun/mouse controls, rumble and an integrated disc-swap interface remain unimplemented. External backends remain available for requirements outside that subset.

Keyboard gameplay uses arrows for the D-pad, **X for A**, **Z for B**, **Enter for Start**, and **Backspace or right Shift for Select**. Mapped pads use D-pad/left stick, A/B, Start and Select/Back. Escape, F10, the visible **Pause / menu** button, Home/Guide, or Select/Back + Start held for one second opens the menu. Gameplay inputs are cleared while the menu is open or keyboard focus is lost.

The integrated menu provides **Resume**, **Save State**, **Load State**, **Display**, **Controls**, **Screenshot**, **Reset**, and **Stop**. Save/load presents four cards numbered 1–4, with saved dates and explicitly associated thumbnails when available. Saving to an occupied card asks before replacement; loading asks before replacing current in-memory progress and warns that in-game saves may also rewind. An incompatible core-build state is marked **OLD VERSION** and cannot bypass the backend's compatibility checks. Empty or damaged slots report their actual status. Older state files in other slot numbers are retained on disk even though the default card view shows four.

New integrated saves attempt to capture a thumbnail from the actual current game frame. A failed or unavailable preview does not invalidate an otherwise valid state; the card shows that the preview is unavailable. It never substitutes an unrelated screenshot. Reset asks for confirmation. Orderly stop writes supported battery memory and finalizes recorded play duration; a save state is still needed to return to an exact running moment.

### Display and controls during integrated playback

**Display** opens a bottom panel while the actual paused game frame remains visible above it. Use Left/Right on the focused row to adjust **Clean**, **Scanlines**, **CRT**, or **Composite**, scanline strength, shape, zoom, horizontal/vertical offset and brightness. **Reset to defaults** affects the current system. Clean ignores the scanline-strength control. Default filter selection is Composite for Saturn, Sega CD and the reserved 32X CD system identifier; other systems default to Scanlines at 40 percent. A default is not a claim that a backend for that system is installed.

These choices are saved by system, rather than by individual game, in `<library>\settings\display.json`. They apply to software frames rendered by the integrated host and update the paused preview. This does not apply a filter to arbitrary external emulator windows. Supported ranges are scanlines 0–100 percent, zoom 50–150 percent, horizontal/vertical offsets -200 to +200, and brightness 25–200 percent. Shape choices are Auto, 4:3, Pixel and Stretch.

**Controls** shows actual assigned controller names/player ports. Select a pad or hold Right on its row for two seconds to move it to the next of four host player slots. Player support inside a game still depends on the core and game. **Swap A and B** is remembered per system in `settings.json` under `system_controls`. Player assignments are managed in the running controller session. This screen is not a universal configuration writer for external emulators.

Integrated files live in `game_data/<game-id>/<bundle-hash>/` inside the library: `states/<core-name>`, `screenshots`, and `battery/<core-name>`. State headers bind the exact core-build and entry-file hashes, and slot manifests verify saved data; replacing a core DLL can make an old state incompatible. Battery memory support depends on the game/core. Audio uses the Windows PCM output interface; the original visual self-test ROM contains no audio, so a successful silent run does not establish audible game output.

### Running-game menu and backend-specific features

For external games, press **Home/Guide** on a supported mapped controller, or hold **Select/Back + Start** for one second, to bring the frontend's game menu forward. With a keyboard, return focus to Redux and press F10 or select Play again while the game is running. A supported pause freezes gameplay; if pause is unavailable the menu reports the running backend. Resume returns execution to the game. Stop requests a graceful backend close first. If it remains open, inspect any emulator confirmation before choosing the explicit force-stop fallback, which can lose unsaved progress.

Save states and screenshots require a working backend-specific interface. The PCSX-Redux Lua bridge offers actual emulator state save/load, image capture, and emulation pause after a real readiness acknowledgment. Its host must remain responsive; an OS-frozen process cannot service Lua commands. External emulators without such an interface keep their own save-state and screenshot controls. A directory backup does not create a running emulator's in-memory state.

PCSX-Redux bridge states belong under `game_data/<game-id>/states/pcsx_redux/<bundle-hash>` and screenshots under `game_data/<game-id>/<bundle-hash>/screenshots` inside the selected library. State formats are tied to their emulator/version; do not treat them as interchangeable with another backend. The UI only exposes available bridge actions after the backend responds.

The full reference appliance flow is still incomplete. Buffered in-game installation is implemented; Change Disc with tray/loading progress, remove/keep-on-console undo transitions, service-only tools, FPS/log collection and square-wave notification sounds remain pending. Existing status messages do not constitute the requested bottom notification system. See the [feature-by-feature status](CONTROLLER_MENU_STATUS.md).

## Save-directory backups and restore

Open a game's **Manage → Saves & backups**, or **System → Saves & backups → Game saves & backups**. Choose **Back up save directory** and select the emulator's save-only directory. The archive includes matching contents of the selected directory, so avoid selecting an entire program installation. In the external running-game menu, the **Backup** action uses backend-specific filename filters; PCSX-Redux selects `.mcd` and `.sstate*` files beside its portable executable. Integrated battery/state folders can be backed up through their explicit library directories.

Backups are kept in `save_backups/game-<game-id>` within the library, with a manifest and per-file hashes. They preserve files already written to disk. Stop or otherwise flush the emulator before backing up progress that may still exist only in memory.

Select a backup to restore it to an explicitly chosen directory. Stop the emulator first. The dialog offers **Restore into empty paths**, **Replace matching files and preserve an undo backup**, and **Cancel**. The first choice refuses matching files that already exist. The second verifies an automatic backup of the existing matching files before replacing them, allowing a later restore to undo that replacement. Unrelated files are retained. Invalid manifests, unsafe paths, or checksum mismatches prevent restoration.

The backup service stages restoration and rolls back touched files if publication fails. This handles detected operation failures; it does not replace an independent backup of your whole library against disk failure or sudden removal.

## Artwork and metadata

**Manage → Artwork** copies a selected image into the library so it can move with managed media. **Manage → Game information** edits the title, system, year, publisher, developer, genre, region, and description. **System → Display & artwork → Local artwork folder** selects an optional local reference tree.

Open **System → Artwork & scraping → Accounts & API keys**, then select a provider to enter its username, password or API fields. This chooser is independent of the active artwork service, so every account remains accessible while Libretro is selected. Each field opens the controller keyboard; optional physical typing and paste are supported. Passwords and API secrets are masked and protected on the current Windows account. **Use this artwork service** makes the configured provider active. See [artwork service setup](SCRAPER_PROVIDERS.md) for the exact access each provider requires.

Cards prefer a landscape tile, then a complete fitted cover or screenshot. Redux can compose a mixed tile from a full box cover and gameplay screenshot. Game details use a separate cover and background, preserving portrait proportions. An available captured game frame or designed system graphic remains a fallback when artwork is missing. The Diablo and World Series Baseball 2 entries in this workspace now have Libretro artwork and generated mixed tiles.

This development workspace can read its already decoded local reference catalogue/artwork. The standard frontend build specification excludes that material. Missing reference data does not block startup, imports, editing, or playback setup. Metadata, artwork, emulator programs, cores, firmware, and game images have separate provenance; the presence of one does not establish the others' compatibility.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Executable cannot find runtime files | Keep the complete distribution and `_internal` directory together. |
| Source launch reports a missing `PIL` module | Run `python -m pip install -e .` in the Python environment used to launch it. |
| Game is offline or partial | Reconnect linked storage, restore referenced tracks, then refresh and verify. |
| Disc system is unassigned | Edit the game's system; a generic image extension is insufficient evidence. |
| No emulator assigned | Configure a local executable for that game system or detect the known tool folders. |
| Firmware readiness fails | Select the correct backend-specific local folder and verify the emulator's own requirements. |
| Image imports but fails to run | Check that backend's image format and game compatibility. Library import does not emulate a game. |
| Virtual TOC is unsupported | Inspect the readable volume or import the original descriptor; do not convert mixed-mode media to plain ISO. |
| Game menu opens but save-state controls are unavailable | The backend has no ready common-control interface; use its own controls or save-file backups. |
| Restore reports existing files | Restore to an empty directory or use an explicit, reviewable overwrite workflow. |
| Stop does not close the game | Check the emulator's confirmation; the fallback force stop is explicit. |
| Controller is absent | Check its Windows USB/Bluetooth connection and keep the complete SDL3 distribution present. Use System → Controllers → Configure a controller for an unknown mapping. External gameplay input remains separate. |
| Home/Guide does not open Redux | Use Select/Back + Start held for one second. The device/Windows input path must expose Guide; Windows or another input application can also consume it. |

For automation help, run `python -m redux --help` or append `--help` to `import`, `configure`, or another subcommand. `identify <file>` inspects media without importing it. `plan-cd <drive>` only plans raw main-channel acquisition; `capture-cd <drive> <new-folder>` explicitly starts it. `capture-volume <drive> <new-iso>` is the distinct cooked-data exporter. No optical capture is automatically invoked. `verify` exits with status 2 if any recorded file is not `ok`; ordinary command errors exit with status 1.

## Validation still required

Physical USB/internal-drive acceptance, unusual mixed-mode layouts, audio offsets/jitter, real SSD/SD hot-removal behavior, and a custom hardware-board interface require supplied hardware and representative media. Full-disc subchannel/protection/pregap imaging remains outside the implemented main-channel scope. Full commercial-game compatibility and an accepted backend for every listed platform remain outside this release's verified scope. See [project status](PROJECT_STATUS.md) for the current evidence and remaining work.

## Emulator setup

A fresh installation offers Download recommended emulators on first launch. Set up later keeps PC games available. System → Emulators & firmware → Emulator setup installs the recommended set or individual cores and offers explicit repair/update. Guide/A/B navigation uses the normal controller menus; progress and cancellation use the standard task screen. Missing integrated cores on library or inserted-disc Play offer Download and continue. Existing external-emulator choices remain unchanged unless integrated playback is explicitly selected. Updates are blocked while a game is running; no automatic background core updates occur. Core changes may make older save states incompatible. User games, battery saves and firmware are retained. Required retail BIOS must be supplied by the user.
