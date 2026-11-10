"""Bounded headless acceptance of the installed software Beetle PSX core.

Uses only the already-managed Diablo bundle and an empty analysis firmware
directory. Native execution is isolated in an owned child process so a crash or
hang cannot take down the frontend or change its selected launcher.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "analysis/emulator_alignment/psx"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def child():
    from redux.libretro_host import EmbeddedSession
    from redux.library import Library
    OUTPUT.mkdir(parents=True, exist_ok=True)
    firmware = OUTPUT / "empty-firmware"
    firmware.mkdir(exist_ok=True)
    if any(firmware.iterdir()):
        raise RuntimeError("The acceptance firmware directory must remain empty")
    library = Library(ROOT / "library")
    game = library.game(2)
    if game["system"] != "psx" or not game["title"].startswith("Diablo"):
        raise RuntimeError("This acceptance script targets only the existing Diablo game 2")
    source = library.path_for(game)
    core = ROOT / "tools/cores/mednafen_psx_libretro.dll"
    record = {"observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "core_path": str(core), "core_sha256": digest(core),
              "game_id": 2, "title": game["title"], "bundle_hash": game["bundle_hash"],
              "game_path": str(source), "firmware_directory": str(firmware),
              "firmware_files": [], "audio_enabled": False,
              "physical_drive_access": False, "launcher_config_changed": False,
              "source_or_firmware_copied": False, "stages": []}
    from redux.core_profiles import default_options
    session = EmbeddedSession(core, source, firmware, OUTPUT / "isolated-saves", audio=False,core_options=default_options("psx"))
    started = time.monotonic()
    try:
        session.start(timeout=15)
        session.pause()
        record.update(core=session.core_info, fps=session.fps, sample_rate=session.sample_rate,
                      initialized=True, active_variables={k.decode(): v.decode() for k, v in session._variables.items()})
        for frames in (1, 60, 180, 600, 600):
            frame = session.step(frames)
            screenshot = session.save_screenshot(OUTPUT / f"native-frame-{session.frame_count}.png")
            record["stages"].append({"advanced_frames": frames,
                "frame_sequence": session.frame_count, "width": frame.width, "height": frame.height,
                "rgb_sha256": hashlib.sha256(frame.rgb).hexdigest(),
                "screenshot": str(screenshot), "unique_rgb_bytes": len(set(frame.rgb))})
        state = session.save_state(OUTPUT / "native-roundtrip.state")
        before = session.step(32)
        session.load_state(state)
        after = session.step(32)
        record.update(state_bytes=state.stat().st_size,
                      state_roundtrip_identical_frame=before.rgb == after.rgb,
                      frame_sequence_after_roundtrip=session.frame_count,
                      restored_rgb_sha256=hashlib.sha256(after.rgb).hexdigest())
        session.save_screenshot(OUTPUT / "native-restored-frame.png")
    except BaseException as error:
        record["error"] = f"{type(error).__name__}: {error}"
        record["traceback"] = traceback.format_exc()
    finally:
        try:
            session.stop(timeout=5)
            record["clean_shutdown"] = session.error is None
        except BaseException as error:
            record["shutdown_error"] = f"{type(error).__name__}: {error}"
        record.update(duration_seconds=time.monotonic()-started,
                      host_error=session.error, messages=session.messages,
                      firmware_files_after=[p.name for p in firmware.iterdir()])
        (OUTPUT / "native-acceptance.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    return int(bool(record.get("error") or record.get("shutdown_error") or record.get("host_error")))


def main():
    if "--child" in sys.argv:
        return child()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    start=time.monotonic()
    try:
        result=subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child"],
                              cwd=ROOT, capture_output=True, timeout=45)
        (OUTPUT / "native-stdout.log").write_bytes(result.stdout)
        (OUTPUT / "native-stderr.log").write_bytes(result.stderr)
        process={"exit_code": result.returncode, "timed_out": False,
                 "duration_seconds": time.monotonic()-start, "timeout_seconds": 45}
    except subprocess.TimeoutExpired as error:
        (OUTPUT / "native-stdout.log").write_bytes(error.stdout or b"")
        (OUTPUT / "native-stderr.log").write_bytes(error.stderr or b"")
        process={"exit_code": None, "timed_out": True,
                 "duration_seconds": time.monotonic()-start, "timeout_seconds": 45}
    (OUTPUT / "process-acceptance.json").write_text(json.dumps(process, indent=2), encoding="utf-8")
    print(json.dumps(process, indent=2))
    if (OUTPUT / "native-acceptance.json").is_file():
        print((OUTPUT / "native-acceptance.json").read_text(encoding="utf-8"))
    return int(process["timed_out"] or process["exit_code"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
