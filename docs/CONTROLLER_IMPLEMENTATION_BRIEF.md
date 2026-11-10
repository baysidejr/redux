# Windows controller and menu implementation brief

Redux 0.4.0 owns the input and presentation path for its integrated libretro cores. This is the same structural approach described for kazeta-player: a common frontend reads controllers, reserves menu shortcuts, supplies logical input to cores, and renders the game and menu. It does not install a system-wide controller hook or turn arbitrary emulator executables into hosted cores.

## Current implementation

1. `controllers.create_controller()` chooses SDL3, loading the bundled Windows x64 SDL DLL and a pinned public SDL_GameControllerDB. SDL normalizes supported physical gamepads; custom mappings can be created in the app. XInput is a fallback if SDL initialization fails. The connected Atari VCS Modern Controller is recognized automatically through its upstream Windows mapping.
2. `sdl_gamepads.SDLGamepadAPI` pumps and reads devices on the Tk/main thread, with background input enabled, and exposes a cached four-player state. A physical Guide button becomes a reserved input bit. Different OS input APIs are not independently merged into duplicate players.
3. `NavigationMapper` emits a Guide edge, Select+Start after one second, or a higher-priority L1+R1+Select+Start quit edge. `ConsoleApp._route_controller_events` processes these before navigation or core input. A simultaneous A press cannot activate a menu that has just opened.
4. For integrated sessions, `game_menu()` pauses the worker that runs `retro_run()`. The host keeps the actual last RGB frame and draws its own menu. Core input never receives Guide or the reserved chord. Resume has an all-player neutral barrier, including buttons, sticks and triggers, so held menu controls do not leak into gameplay.
5. `libretro_host` implements actual libretro input/video/audio callbacks and state serialization. `EmbeddedGame` associates saved states with the game bundle and exact core hash. The UI presents four slots, dates and explicitly associated thumbnails, confirms load/overwrite and blocks incompatible old-core states. Existing service-level slots beyond the four visible slots are preserved.
6. Display controls apply to game RGB frames before UI composition. Clean, Scanlines, CRT and Composite are original software filters. Display preferences are stored per system under the portable library, including strength, shape, zoom, offsets and brightness. Known controller defaults require no per-emulator controller dialog for integrated cores.
7. Separately launched executables remain a distinct path. Redux can observe menu shortcuts in the background and bring its own menu window forward. Real save/load requires a backend adapter, currently implemented for PCSX-Redux. An external window is not automatically filtered by Redux's software renderer, and Redux cannot suppress input that an unrelated process reads for itself.

## Guidance for further work in Cursor

Continue the host-owned design. Use shared emulation-session operations for pause/resume, logical input, frames/audio, serialize/restore, reset and stop. Add managed cores through public APIs and test actual game acceptance. For an external program, implement and validate an explicit adapter rather than treating hotkey injection or process suspension as a universal state API. Add libretro disk-control support and real media/install job state before exposing those menu actions. Keep controller and display defaults centralized.

The complete reference and remaining gaps are in `CONTROLLER_MENU_STATUS.md`. The recovered original executable's common-hook/input evidence is in `../analysis/controller_menu/original-input-menu-findings.md`; it supports the architecture but does not establish every physical Guide-to-menu call chain.

## Verification boundaries

Native SDL tests used a process-local virtual joystick to verify Guide, the one-second fallback and quit priority without installing a driver or injecting input into another application. A real Atari controller was enumerated and mapped; physical button presses and every USB/Bluetooth/Windows overlay combination have not been accepted. Native NES state round trips and actual UI save/display flows have separate evidence. No claim of complete Kazeta or reference application parity follows from these checks.
