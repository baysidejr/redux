"""The console banner carousel: 52 hanging pennants that scroll right to left and stop on a console.

A port of the owner's clock-driven motion reference (``carousel.js``). Phase is a position
in banners along a virtual strip that repeats ``order``; time is in seconds from any
monotonic clock. Cruising advances the phase at a constant speed. A stop request brakes
with a cubic ease-out whose initial slope matches the cruise speed, so the strip never
jerks or reverses, travels at least ``minimum_stop_travel`` banners, and comes to rest with
the chosen banner exactly centred. The phase is never wrapped during the brake; the
modulo is applied only when a banner is looked up.
"""
from __future__ import annotations

import json
import math
from pathlib import Path


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class BannerCarousel:
    def __init__(self, manifest, atlas=None):
        self.manifest = manifest
        self.atlas = Path(atlas) if atlas else None
        self.order = list(manifest.get("order") or [])
        self.count = len(self.order)
        settings = manifest.get("carousel") or {}
        self.cruise_speed = float(settings.get("cruiseBannersPerSecond", 32))
        self.minimum_stop_travel = int(settings.get("minimumStopTravelBanners", 8))
        frame = manifest.get("frame") or {}
        self.frame_width = int(frame.get("width", 180))
        self.frame_height = int(frame.get("height", 540))
        self.aliases = dict(manifest.get("aliases") or {})
        self.systems = dict(manifest.get("systems") or {})
        if not self.count or not self.cruise_speed > 0:
            raise ValueError("Invalid carousel configuration")
        self.mode = "idle"
        self.origin = 0.0
        self.epoch = 0.0
        self.end = 0.0
        self.duration = 0.0
        self.speed = self.cruise_speed
        self.target = None

    # ------------------------------------------------------------------ identity
    def resolve(self, system_id):
        """The banner id for a system, following aliases; None when there is no banner."""
        seen = set()
        current = str(system_id or "")
        while current in self.aliases:
            if current in seen:
                return None
            seen.add(current)
            current = self.aliases[current]
        return current if current in self.systems and current in self.order else None

    def rect(self, banner_id):
        """The banner's cell on the atlas as a crop box (left, top, right, bottom); the manifest stores x, y, width, height."""
        x, y, width, height = self.systems[banner_id]["rect"]
        return (int(x), int(y), int(x) + int(width), int(y) + int(height))

    # ------------------------------------------------------------------ motion
    def sample(self, now):
        dt = max(0.0, now - self.epoch)
        if self.mode == "cruise":
            return self.origin + dt * self.speed
        if self.mode == "brake":
            u = min(1.0, dt / self.duration) if self.duration > 0 else 1.0
            return self.origin + (self.end - self.origin) * (1 - (1 - u) ** 3)
        return self.origin

    def velocity(self, now):
        if self.mode == "cruise":
            return self.speed
        if self.mode == "brake":
            if self.duration <= 0:
                return 0.0
            u = min(1.0, max(0.0, (now - self.epoch) / self.duration))
            return 3 * (self.end - self.origin) / self.duration * (1 - u) ** 2
        return 0.0

    def start(self, now, speed=None):
        """Cruise from wherever the strip is now, at ``speed`` banners per second (the manifest's by default)."""
        self.origin = self.sample(now)
        self.epoch = now
        self.speed = float(speed) if speed else self.cruise_speed
        self.mode = "cruise"
        self.target = None

    def hold(self, now):
        """Stand still wherever the strip is now."""
        self.origin = self.sample(now)
        self.epoch = now
        self.mode = "idle"
        self.target = None

    def stop_at(self, system_id, now):
        """Brake to a stop with this console's banner centred. Returns the seconds the brake takes."""
        banner = self.resolve(system_id)
        if banner is None:
            raise KeyError(f"No banner for {system_id}")
        if self.mode == "brake" and self.target == banner:
            return max(0.0, self.epoch + self.duration - now)
        current = self.sample(now)
        velocity = self.velocity(now)
        index = self.order.index(banner)
        self.target = banner
        if velocity < 1e-6:
            self.origin = float(index)
            self.mode = "idle"
            self.epoch = now
            return 0.0
        distance = (index - current) % self.count
        while distance < self.minimum_stop_travel:
            distance += self.count
        self.origin = current
        self.end = current + distance
        self.epoch = now
        self.duration = 3 * distance / velocity
        self.mode = "brake"
        return self.duration

    def status(self, now):
        if self.mode == "cruise":
            return "scrolling"
        if self.mode == "brake" and now - self.epoch < self.duration:
            return "stopping"
        return "stopped"

    def centered_id(self, now):
        return self.order[int(round(self.sample(now))) % self.count]

    # ------------------------------------------------------------------ layout
    def placements(self, width, height, now):
        """[(banner id, source rect, (left, top, width, height))] for every banner touching a viewport.

        Banners keep the frame's shape at the viewport's height and are rounded so neighbours
        touch with no gaps; the one at ``phase`` sits centred."""
        if not (width > 0 and height > 0):
            raise ValueError("Invalid viewport")
        tile = height * self.frame_width / self.frame_height
        phase = self.sample(now)
        half = width / (2 * tile)
        result = []
        for n in range(math.floor(phase - half) - 1, math.ceil(phase + half) + 2):
            banner = self.order[n % self.count]
            left = round(width / 2 + (n - phase - 0.5) * tile)
            right = round(width / 2 + (n - phase + 0.5) * tile)
            if right <= 0 or left >= width:
                continue
            result.append((banner, self.rect(banner), (left, 0, right - left, height)))
        return result
