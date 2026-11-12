"""Redux Controller: the phone-in-a-GameSir companion and its Windows receiver.

The receiver (ReduxReceiver.exe, a separate elevated tray application) exposes one
32-button generic HID gamepad that carries the GameSir's physical controls and the
phone's twelve Jaguar keypad keys. This module gives Redux three things:

* the SDL gamepad mapping for that device, so Redux menus and integrated cores see a
  normal gamepad instead of an unknown joystick;
* a client for the receiver's loopback control port (plain HTTP on 127.0.0.1 only),
  used to show pairing state and to hand the phone a per-game overlay profile;
* the title-to-profile rules for Atari Jaguar games, matching the profile ids the
  phone application ships.

Nothing here talks to the network beyond 127.0.0.1, and every call is bounded by a
short timeout so a missing receiver never stalls the console.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

CONTROL_PORT = 19444
VENDOR, PRODUCT = 0x1209, 0x5244
DEVICE_NAME = "Redux Jaguar Controller"
RECEIVER_EXECUTABLE = "ReduxReceiver.exe"

# Verified against the receiver's live device with the bundled SDL3: DirectInput orders the
# axes X, Y, Z, Rx, Ry, Rz, and the receiver's descriptor puts the right stick on Z/Rz and the
# triggers on Rx/Ry. Phone keypad keys are buttons 17-28 (b16-b27) and stay unmapped here.
MAPPING = ("03000000091200004452000000000000," + DEVICE_NAME + ","
           "a:b0,b:b1,x:b2,y:b3,leftshoulder:b4,rightshoulder:b5,back:b6,start:b7,"
           "leftstick:b8,rightstick:b9,guide:b10,leftx:a0,lefty:a1,rightx:a2,righty:a5,"
           "lefttrigger:a3,righttrigger:a4,dpup:h0.1,dpright:h0.2,dpdown:h0.4,dpleft:h0.8,"
           "platform:Windows,")

DEFAULT_PROFILE = "jaguar"
JAGUAR_SYSTEMS = frozenset({"jaguar", "jaguar_cd"})
# First match wins. Ids are shared with the phone's profiles.json.
PROFILES = (
    ("jaguar-avp", ("alien vs", "aliens vs", "alien versus", "avp")),
    ("jaguar-baldies", ("baldies",)),
    ("jaguar-battlemorph", ("battlemorph",)),
    ("jaguar-blue-lightning", ("blue lightning",)),
    ("jaguar-cannon-fodder", ("cannon fodder",)),
    ("jaguar-cybermorph", ("cybermorph",)),
    ("jaguar-doom", ("doom",)),
    ("jaguar-dragon", ("bruce lee",)),
    ("jaguar-hover-strike-unconquered", ("unconquered",)),
    ("jaguar-hover-strike", ("hover strike", "hoverstrike")),
    ("jaguar-iron-soldier", ("iron soldier",)),
    ("jaguar-jagmind", ("jagmind", "bomb squad")),
    ("jaguar-trevor-mcfur", ("trevor", "crescent galaxy")),
    ("jaguar-wolfenstein", ("wolfenstein",)),
)


def profile_for_title(title, system="jaguar"):
    """The phone overlay profile for a Jaguar game, or None for other systems."""
    if system not in JAGUAR_SYSTEMS:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(title or "").casefold())).strip()
    for profile, patterns in PROFILES:
        if any(pattern in text for pattern in patterns):
            return profile
    return DEFAULT_PROFILE


class ReceiverClient:
    """Loopback control-port client. `state()` returns None when no receiver answers."""

    def __init__(self, port=CONTROL_PORT, timeout=0.5):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Control port must be 1-65535")
        self.port = port
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}))

    def _call(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                          headers={"Content-Type": "application/json", "Accept": "application/json"},
                          method="POST" if data is not None else "GET")
        with self._opener.open(request, timeout=self.timeout) as response:
            body = response.read(65536)
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("The receiver answered with something other than a JSON object")
        return value

    def state(self):
        try:
            return self._call("/control/state")
        except (OSError, ValueError, HTTPError, URLError):
            return None

    def set_profile(self, profile):
        """Hand the phone a profile id (None clears the hand-off). Raises OSError when unreachable."""
        if profile is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(profile)):
            raise ValueError("Profile ids use letters, digits, - and _")
        return self._call("/control/profile", {"profile": profile})

    def disconnect(self):
        return self._call("/control/disconnect", {})


def receiver_executable(root, preferences=None):
    """The installed receiver, from a chosen path or the tools folder; None when absent."""
    chosen = str(((preferences or {}).get("redux_controller") or {}).get("receiver") or "")
    candidates = [Path(chosen)] if chosen else []
    candidates.append(Path(root) / "tools" / "ReduxReceiver" / RECEIVER_EXECUTABLE)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "ReduxController" / RECEIVER_EXECUTABLE)
    for candidate in candidates:
        if candidate.name.casefold() == RECEIVER_EXECUTABLE.casefold() and candidate.is_file():
            return candidate
    return None


def start_receiver(path, *, setup=False):
    """Launch the receiver through the shell, which raises its own elevation prompt.

    With `setup`, the receiver also registers its Start with Windows logon task during that
    one elevated launch; its first run installs the HIDMaestro driver and firewall rules anyway.
    """
    path = Path(path)
    if path.name.casefold() != RECEIVER_EXECUTABLE.casefold() or not path.is_file():
        raise OSError(f"{RECEIVER_EXECUTABLE} was not found at {path}")
    arguments = "--start-with-windows" if setup else ""
    os.startfile(str(path), arguments=arguments, cwd=str(path.parent))  # noqa: S606 - the user's own installed receiver


def describe(state, executable=None):
    """One short status paragraph for the Phone controller menu."""
    if state is None:
        if executable:
            return (f"The receiver is not running. It is installed at {executable}. Choose Set up the phone controller the "
                    "first time: one elevated launch installs its driver, allows it through the firewall and registers "
                    "Start with Windows. It then runs as a tray icon and makes the phone and GameSir one Windows controller.")
        return ("The receiver is not running and ReduxReceiver.exe is missing from tools/ReduxReceiver. Reinstall Redux, "
                "or extract the Redux Controller receiver ZIP there, or locate the executable below.")
    lines = [f"Receiver {state.get('version', '?')} running"]
    lines.append("Windows controller ready (32 buttons)" if state.get("outputReady") else f"Windows controller unavailable: {state.get('error') or 'unknown error'}")
    if state.get("phonePaired"):
        lines.append(f"{state.get('phoneName') or 'Phone'} connected · {state.get('packets', 0)} frames")
    else:
        paired = int(state.get("pairedPhones") or 0)
        lines.append(f"Phone not connected · {paired} paired phone(s) reconnect on their own" if paired else "No phone paired yet")
    addresses = ", ".join(str(a) for a in (state.get("addresses") or [])[:2]) or "no network"
    lines.append(f"PIN {state.get('pin', '------')} · PC {addresses}")
    if state.get("profile"):
        lines.append(f"Phone overlay: {state['profile']}")
    return "\n".join(lines)
