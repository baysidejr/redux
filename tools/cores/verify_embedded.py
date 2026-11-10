"""Run only the original generated Redux NES fixture, without opening a window."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from redux.libretro_host import EmbeddedSession


def main():
    output = ROOT / "analysis" / "embedded_test"
    output.mkdir(parents=True, exist_ok=True)
    session = EmbeddedSession(ROOT / "tools/cores/mesen_libretro.dll",
                              ROOT / "analysis/playback_fixture/Redux Self Test.nes",
                              output / "system", output / "saves", audio=False)
    try:
        session.start()
        session.pause()
        frame = session.step(90)
        state = session.save_state(output / "native-roundtrip.state")
        first = session.step(32)
        session.load_state(state)
        second = session.step(32)
        equal = first.rgb == second.rgb
        if not equal:
            raise RuntimeError("Real native core state restoration did not reproduce the same frame.")
        screenshot = session.save_screenshot(output / "native-frame.png")
        result = {"core": session.core_info, "fps": session.fps, "sample_rate": session.sample_rate,
                  "frame_width": frame.width, "frame_height": frame.height,
                  "rgb_bytes": len(frame.rgb), "frame_sequence": session.frame_count,
                  "state_roundtrip_identical_frame": equal,
                  "frame_changes_after_32_frames": frame.rgb != first.rgb,
                  "restored_frame_sha256": hashlib.sha256(second.rgb).hexdigest(),
                  "screenshot": str(screenshot), "state_bytes": state.stat().st_size,
                  "audio_tested": False, "hardware_or_bios_tested": False,
                  "test_content": "Original generated Redux Self Test NES ROM only"}
    finally:
        session.stop()
    result["clean_shutdown"] = session.error is None
    result["error"] = session.error
    (output / "native_verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
