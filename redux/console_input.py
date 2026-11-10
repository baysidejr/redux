"""Route normalized controller input into the embedded core's frame state.

This is the console caller boundary: SDL/XInput-normalized Y is positive up,
whereas libretro analog Y is positive down. Menus and the shared resume barrier
send neutral axes as well as neutral buttons to every player.
"""
from __future__ import annotations

import time


_KEYBOARD = {"z": 0, "a": 1, "shift_r": 2, "backspace": 2, "return": 3,
             "up": 4, "down": 5, "left": 6, "right": 7, "x": 8, "s": 9, "q": 10, "w": 11}
_AXES = ("thumb_lx", "thumb_ly", "thumb_rx", "thumb_ry")
_NEUTRAL = (0, 0, 0, 0)


def _libretro_axes(state):
    if state is None:
        return _NEUTRAL
    values = tuple(getattr(state, axis, 0) for axis in _AXES)
    return tuple(max(-32768, min(32767, -value if index % 2 else value))
                 for index, value in enumerate(values))


def _set_axes(host, player, axes):
    # Older injected host adapters only expose the digital-input API. Native
    # EmbeddedSession always implements set_analog; keep those adapters usable.
    setter = getattr(host, "set_analog", None)
    if setter is not None:
        setter(player, *axes)


def embedded_input(app):
    """Update four players, preserving menu/chord and release isolation."""
    if not app.embedded:
        return
    host = app.embedded.host
    keyboard = 0
    if not app.overlay:
        for key, bit in _KEYBOARD.items():
            if key in app.held_keys:
                keyboard |= 1 << bit
    states = [app.controller.api.state(index) if app.controller and app.controller.api else None
              for index in range(4)]
    now = time.monotonic()
    if getattr(app, "input_release_gate", False):
        held = any(state and (state.buttons or
            any(abs(getattr(state, axis, 0)) >= 11000 for axis in _AXES) or
            getattr(state, "left_trigger", 0) >= 32 or getattr(state, "right_trigger", 0) >= 32)
            for state in states)
        if held or app.held_keys:
            for index, state in enumerate(states):
                app.gameplay_input.update(index, state, now)
                host.set_input(index, 0)
                _set_axes(host, index, _NEUTRAL)
            return
        app.input_release_gate = False
        app.gameplay_input.reset()
    for index, state in enumerate(states):
        mask = keyboard if index == 0 else 0
        mapped = app.gameplay_input.update(index, state, now)
        system = getattr(app, "embedded_game", {}).get("system")
        if getattr(app, "preferences", {}).get("system_controls", {}).get(system, {}).get("swap_ab", False):
            a, b = bool(mapped & (1 << 8)), bool(mapped & 1)
            mapped = (mapped & ~((1 << 8) | 1)) | int(a) | (int(b) << 8)
        buttons = state.buttons if state else 0
        reserved = bool(buttons & 0x0400 or buttons & 0x30 == 0x30)
        axes = _NEUTRAL
        if not app.overlay and not reserved:
            mask |= mapped
            axes = _libretro_axes(state)
        else:
            mask = 0
        host.set_input(index, mask)
        _set_axes(host, index, axes)
