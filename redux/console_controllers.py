"""In-app SDL gamepad mapping, including simple USB NES-style controllers.

Capture uses raw device edges while normal menu navigation is suspended by the
caller. No keyboard input is injected and no driver or emulator is configured.
Mappings are validated by SDL before they are stored in the user's config.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import uuid

from .app import config_path

WHITE = "#f4f4f2"
MUTED = "#a3a3ab"
ACCENT = "#ec407a"
_GUID = re.compile(r"[0-9a-fA-F]{32}\Z")
_MAX_MAPPING_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class BindingStep:
    key: str
    label: str
    required: bool = False


STEPS = (
    BindingStep("dpup", "D-pad Up", True),
    BindingStep("dpdown", "D-pad Down", True),
    BindingStep("dpleft", "D-pad Left", True),
    BindingStep("dpright", "D-pad Right", True),
    BindingStep("a", "Confirm / A", True),
    BindingStep("b", "Back / B", True),
    BindingStep("x", "X"),
    BindingStep("y", "Y"),
    BindingStep("leftshoulder", "Left shoulder"),
    BindingStep("rightshoulder", "Right shoulder"),
    BindingStep("back", "Select / View", True),
    BindingStep("start", "Start / Menu", True),
    BindingStep("guide", "Home / Guide"),
)


def _raw_values(raw):
    if not isinstance(raw, dict):
        raise ValueError("The controller did not return raw input")
    buttons, axes, hats = (tuple(raw.get(name, ())) for name in ("buttons", "axes", "hats"))
    if len(buttons) > 256 or len(axes) > 64 or len(hats) > 16:
        raise ValueError("The raw controller has too many controls")
    if any(not isinstance(value, bool) for value in buttons):
        raise ValueError("Controller button data is invalid")
    if any(isinstance(value, bool) or not isinstance(value, int) or not -32768 <= value <= 32767 for value in axes):
        raise ValueError("Controller axis data is invalid")
    if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 15 for value in hats):
        raise ValueError("Controller D-pad data is invalid")
    return buttons, axes, hats


class ControllerSetupSession:
    """Pure edge/release state machine, separate from SDL and Tk."""
    def __init__(self, device, raw):
        guid = str(device.get("guid", ""))
        if not _GUID.fullmatch(guid):
            raise ValueError("The controller has no valid SDL device identifier")
        self.device = dict(device)
        self.device["guid"] = guid.lower()
        self.index = 0
        self.bindings = {}
        self.skipped = set()
        self.baseline = _raw_values(raw)
        self.previous = self.baseline
        self.armed = False
        self.message = "Release all buttons and let sticks rest to begin."

    @property
    def done(self):
        return self.index >= len(STEPS)

    @property
    def step(self):
        return None if self.done else STEPS[self.index]

    def _resting(self, values):
        buttons, axes, hats = values
        baseline_axes = self.baseline[1]
        return (not any(buttons) and not any(hats) and len(axes) == len(baseline_axes)
                and all(abs(value - initial) <= 8000 for value, initial in zip(axes, baseline_axes)))

    def _tokens(self, values):
        buttons, axes, hats = values
        result = {f"b{index}" for index, pressed in enumerate(buttons) if pressed}
        for index, hat in enumerate(hats):
            for bit in (1, 2, 4, 8):
                if hat & bit:
                    result.add(f"h{index}.{bit}")
        for index, (value, initial) in enumerate(zip(axes, self.baseline[1])):
            # An idle trigger may rest at -32768. Only deliberate movement
            # away from its baseline can become an axis binding.
            if abs(value - initial) >= 16000 and abs(value) >= 18000:
                result.add(("+" if value > 0 else "-") + f"a{index}")
        return result

    def observe(self, raw):
        """Capture at most one fresh control; return whether visible state changed."""
        if self.done:
            return False
        before = (self.index, self.armed, self.message, tuple(self.bindings.items()))
        values = _raw_values(raw)
        if tuple(map(len, values)) != tuple(map(len, self.baseline)):
            self.message = "The controller layout changed. Start setup again."
            self.armed = False
            return before != (self.index, self.armed, self.message, tuple(self.bindings.items()))
        if not self.armed:
            if self._resting(values):
                self.armed = True
                self.message = "Press one control for " + self.step.label + "."
            self.previous = values
            return before != (self.index, self.armed, self.message, tuple(self.bindings.items()))
        fresh = self._tokens(values) - self._tokens(self.previous)
        self.previous = values
        if len(fresh) > 1:
            self.message = "More than one control moved. Release them and try one at a time."
            self.armed = False
        elif fresh:
            token = next(iter(fresh))
            if token in self.bindings.values():
                self.message = "That control is already assigned. Release it and choose another."
                self.armed = False
            else:
                self.bindings[self.step.key] = token
                self.index += 1
                self.armed = False
                self.message = "Review your controls, then save." if self.done else "Release the control before the next assignment."
        return before != (self.index, self.armed, self.message, tuple(self.bindings.items()))

    def skip(self):
        if self.done:
            return False
        if self.step.required:
            self.message = "This control is needed for navigation or the Select + Start shortcut."
            return False
        self.skipped.add(self.step.key)
        self.index += 1
        self.armed = False
        self.message = "Review your controls, then save." if self.done else "Release all controls for the next assignment."
        return True

    def previous_step(self):
        if self.index == 0:
            return False
        self.index -= 1
        self.bindings.pop(self.step.key, None)
        self.skipped.discard(self.step.key)
        self.armed = False
        self.message = "Release all controls, then assign " + self.step.label + "."
        return True

    def mapping(self, platform=None):
        if not self.done or any(step.required and step.key not in self.bindings for step in STEPS):
            raise ValueError("Assign all required controls before saving")
        name = re.sub(r"[\x00-\x1f\x7f,]", " ", str(self.device.get("name", "")))
        name = re.sub(r"\s+", " ", name).strip()[:120] or "Redux Controller"
        platform = platform or ("Windows" if os.name == "nt" else "Linux")
        if platform not in {"Windows", "Linux", "Mac OS X"}:
            raise ValueError("Unsupported SDL mapping platform")
        pairs = [f"{step.key}:{self.bindings[step.key]}" for step in STEPS if step.key in self.bindings]
        return f"{self.device['guid']},{name}," + ",".join(pairs) + f",platform:{platform},"


def save_controller_mapping(path, mapping):
    """Atomically replace this GUID/platform while preserving other mappings."""
    if not isinstance(mapping, str) or "\n" in mapping or "\r" in mapping or len(mapping) > 4096:
        raise ValueError("Invalid controller mapping line")
    fields = mapping.split(",")
    if len(fields) < 4 or not _GUID.fullmatch(fields[0]):
        raise ValueError("Invalid controller mapping identifier")
    platform = next((field for field in fields if field.startswith("platform:")), "")
    if platform not in {"platform:Windows", "platform:Linux", "platform:Mac OS X"}:
        raise ValueError("Controller mapping must specify its platform")
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Controller mappings cannot use a symlink")
    try:
        with path.open("rb") as source:
            previous = source.read(_MAX_MAPPING_BYTES + 1)
    except FileNotFoundError:
        previous = b""
    if len(previous) > _MAX_MAPPING_BYTES:
        raise ValueError("Controller mappings exceed the size limit")
    text = previous.decode("utf-8")
    kept = []
    guid = fields[0].lower()
    for line in text.splitlines(keepends=True):
        values = line.strip().split(",")
        existing_platform = next((value for value in values if value.startswith("platform:")), "")
        if values[0].lower() == guid and existing_platform in {"", platform}:
            continue
        kept.append(line)
    result = "".join(kept)
    if result and not result.endswith(("\n", "\r")):
        result += "\n"
    result += mapping + "\n"
    encoded = result.encode("utf-8")
    if len(encoded) > _MAX_MAPPING_BYTES:
        raise ValueError("Controller mappings exceed the size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ConsoleControllers:
    def configure_controller(self):
        if self.busy:
            return
        if self.embedded or self.playback and self.playback.active():
            self.notice("A game is running", "Return to the library before configuring a controller.")
            return
        api = getattr(self.controller, "api", None)
        if not callable(getattr(api, "raw_state", None)):
            self.notice("Controller setup unavailable", "Broad controller support is unavailable. Reopen Redux with its complete distribution folder.")
            return
        try:
            devices = [device for device in self.controller.devices() if _GUID.fullmatch(str(device.get("guid", "")))]
        except (OSError, ValueError, RuntimeError) as error:
            self.notice("Could not read controllers", str(error))
            return
        if not devices:
            self.notice("Connect a controller", "Connect a USB gamepad or pair a Bluetooth controller, then open setup again.")
            return
        self.menu("Configure a controller", [
            ((device.get("name") or "USB controller") + (" · mapped" if device.get("mapped") else " · setup needed"),
             lambda selected=dict(device): self._begin_controller_setup(selected))
            for device in devices] + [("Cancel", lambda: None)],
            message="Assign controls here once. Extra buttons can be skipped on a simple NES-style pad.")

    def _begin_controller_setup(self, device):
        try:
            raw = self.controller.api.raw_state(device["instance_id"])
            if raw is None:
                raise ValueError("The controller disconnected. Reconnect it and choose it again.")
            session = ControllerSetupSession(device, raw)
        except (KeyError, OSError, ValueError, RuntimeError) as error:
            self.notice("Could not start controller setup", str(error))
            return
        self.controller_setup_session = session
        self.navigate("controller_setup")
        self.focus = "controller:capture"
        self.schedule_draw()

    def _poll_controller_setup(self):
        session = getattr(self, "controller_setup_session", None)
        if not session or self.screen != "controller_setup" or self.overlay or self.busy:
            return
        try:
            raw = self.controller.api.raw_state(session.device["instance_id"])
            if raw is None:
                matches = [device for device in self.controller.devices()
                           if device.get("guid") == session.device["guid"]
                           and device.get("name") == session.device.get("name")]
                if len(matches) == 1:
                    raw = self.controller.api.raw_state(matches[0]["instance_id"])
                    if raw is not None:
                        session.device = dict(matches[0])
                        session.baseline = _raw_values(raw)
                        session.previous = session.baseline
                        session.armed = False
                if raw is None:
                    message = "Controller disconnected. Reconnect this controller to continue."
                    if session.message != message:
                        session.message = message
                        session.armed = False
                        self.schedule_draw()
                    return
            if session.observe(raw):
                if session.done:
                    self.focus = "controller:save"
                self.schedule_draw()
        except (OSError, ValueError, RuntimeError) as error:
            message = "Could not read this controller: " + str(error)
            if session.message != message:
                session.message = message
                session.armed = False
                self.schedule_draw()

    def _skip_controller_control(self):
        session = getattr(self, "controller_setup_session", None)
        if session:
            session.skip()
            self.focus = "controller:save" if session.done else "controller:capture"
            self.schedule_draw()

    def _previous_controller_control(self):
        session = getattr(self, "controller_setup_session", None)
        if session:
            session.previous_step()
            self.focus = "controller:capture"
            self.schedule_draw()

    def _restart_controller_setup(self):
        session = getattr(self, "controller_setup_session", None)
        if not session:
            return
        try:
            raw = self.controller.api.raw_state(session.device["instance_id"])
            if raw is None:
                raise ValueError("Reconnect the controller before restarting setup.")
            self.controller_setup_session = ControllerSetupSession(session.device, raw)
            self.focus = "controller:capture"
            self.schedule_draw()
        except (OSError, ValueError, RuntimeError) as error:
            self.notice("Could not restart controller setup", str(error))

    def _save_controller_setup(self):
        session = getattr(self, "controller_setup_session", None)
        if not session:
            return
        try:
            mapping = session.mapping()
            self.controller.api.add_mapping(mapping)
            save_controller_mapping(config_path().parent / "controller-mappings.txt", mapping)
            refresh = getattr(self.controller.api, "refresh", None)
            if refresh:
                refresh()
        except (OSError, UnicodeError, ValueError, RuntimeError) as error:
            self.notice("Could not save controller setup", str(error))
            return
        self.controller_setup_session = None
        self.back()
        self.status.set("Controller mapping saved • Home or Select + Start opens the game menu")

    def _controller_setup(self):
        session = getattr(self, "controller_setup_session", None)
        self.display(80,45,"Controller Setup",38,WHITE)
        if not session:
            self.display(80,120,"Choose a controller from System → Controllers.",22,MUTED)
            self.button_canvas("controller:cancel","Back",1030,621,170,40,self.back)
            return
        self.display(80,104,self.fit_text(session.device.get("name") or "USB controller",1120,22),22,MUTED)
        self.rect(80,160,1120,1,"#343439")
        self.target("controller:capture",(80,180,1200,337),lambda:None,"Press the requested controller control")
        if session.done:
            self.display(80,186,"Ready to save",34,WHITE)
            self.display(80,239,"Your mapping will apply to this controller model.",21,MUTED)
        else:
            self.display(80,184,f"{session.index+1} / {len(STEPS)}",17,ACCENT)
            self.display(80,219,"Press " + session.step.label,36,WHITE)
            extra="Required" if session.step.required else "Optional · choose Skip if your controller does not have this button"
            self.display(80,271,extra,18,MUTED,width=1120)
        self.display(80,318,session.message,18,WHITE,width=1120)
        for index, step in enumerate(STEPS):
            column, row = divmod(index, 7)
            x,y=80+column*580,381+row*29
            value=session.bindings.get(step.key)
            if value:
                summary="Assigned"
                color="#c7d8ca"
            elif step.key in session.skipped:
                summary="Not present"
                color="#85858e"
            else:
                summary="Waiting"
                color=MUTED
            self.display(x,y,step.label,17,WHITE if session.step==step else MUTED)
            self.display(x+500,y,summary,16,color,anchor="ne")
        if session.index:
            self.button_canvas("controller:previous","Previous",80,621,170,40,self._previous_controller_control)
        self.button_canvas("controller:restart","Start over",270,621,170,40,self._restart_controller_setup)
        if session.done:
            self.button_canvas("controller:save","Save mapping",840,621,170,40,self._save_controller_setup,True)
        elif not session.step.required:
            self.button_canvas("controller:skip","Skip this button",840,621,170,40,self._skip_controller_control)
        self.button_canvas("controller:cancel","Cancel",1030,621,170,40,self.back)
