"""Bounded headless Saturn acceptance using user-provided, verified firmware.

Runs the already-captured World Series Baseball 2 BIN/CUE in an owned native
child. No physical-drive access, frontend configuration or library mutation.
Each invocation creates its own firmware copies, screenshots and save state.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
OUTPUT=ROOT/"analysis/firmware_setup/saturn"
TIMEOUT=55
FIRMWARE={
    "mpr-17933.bin":("3240872c70984b6cbfda1586cab68dbe",
                     "96e106f740ab448cf89f0dd49dfbac7fe5391cb6bd6e14ad5e3061c13330266f"),
    "sega_101.bin":("85ec9ca47d8f6807718151cbcca8b964",
                   "dcfef4b99605f872b6c3b6d05c045385cdea3d1b702906a0ed930df7bcb7deac"),
}


def digest(path):
    with Path(path).open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()


def child(directory):
    from redux.core_profiles import default_options
    from redux.libretro_host import EmbeddedSession
    directory=Path(directory).resolve()
    if not directory.is_relative_to(OUTPUT.resolve()):raise ValueError("Output must stay in the Saturn analysis directory")
    directory.mkdir(parents=True,exist_ok=True)
    record={"observed_at_utc":datetime.now(timezone.utc).isoformat(),
            "physical_drive_access":False,"launcher_config_changed":False,
            "library_changed":False,"audio_enabled":False,"software_video":True,
            "firmware_source":"User-supplied bios directory",
            "firmware_reference":"https://docs.libretro.com/library/beetle_saturn/",
            "firmware":[],"stages":[],"input_events":[]}
    session=None
    started=time.monotonic()
    def persist():
        (directory/"native-acceptance.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
    try:
        firmware=directory/"verified-user-firmware"
        firmware.mkdir(exist_ok=True)
        for name,(expected_md5,expected_sha) in FIRMWARE.items():
            source=ROOT/"bios"/name
            data=source.read_bytes()
            md5=hashlib.md5(data,usedforsecurity=False).hexdigest();sha=hashlib.sha256(data).hexdigest()
            item={"filename":name,"source":str(source),"bytes":len(data),"md5":md5,"sha256":sha,
                  "matches_documented_firmware":len(data)==524288 and md5==expected_md5 and sha==expected_sha}
            record["firmware"].append(item)
            if not item["matches_documented_firmware"]:raise RuntimeError("User firmware does not match documented BIOS: "+name)
            target=firmware/name
            if target.exists():raise FileExistsError("Acceptance firmware target already exists")
            shutil.copyfile(source,target)
            if digest(target)!=expected_sha:raise RuntimeError("Firmware copy hash changed")
        source=ROOT/"analysis/emulator_alignment/saturn-world-series-baseball-2/disc.cue"
        image=source.with_name("disc.bin")
        manifest=json.loads(source.with_name("manifest.json").read_text(encoding="utf-8"))
        image_hash=digest(image)
        if image_hash!=manifest["sha256"]:raise RuntimeError("Captured image differs from its verified manifest")
        core=ROOT/"tools/cores/mednafen_saturn_libretro.dll"
        options=default_options("saturn")
        record.update(core_path=str(core),core_sha256=digest(core),game_path=str(source),
                      game_cue_sha256=digest(source),image_sha256=image_hash,
                      image_bytes=image.stat().st_size,capture_manifest_sha256=digest(source.with_name("manifest.json")),
                      capture_verified_output=manifest.get("verified_output"),
                      capture_source_passes=manifest.get("verified_source_passes"),
                      requested_core_options=options,firmware_directory=str(firmware))
        persist()
        session=EmbeddedSession(core,source,firmware,directory/"isolated-saves",audio=False,core_options=options)
        session.start(timeout=15)
        session.pause()
        record.update(initialized=True,core=session.core_info,fps=session.fps,sample_rate=session.sample_rate,
                      active_variables={k.decode():v.decode() for k,v in session._variables.items()})
        def stage(frames,label):
            frame=session.step(frames)
            screenshot=session.save_screenshot(directory/f"{label}-frame-{session.frame_count}.png")
            record["stages"].append({"label":label,"advanced_frames":frames,"frame_sequence":session.frame_count,
                "width":frame.width,"height":frame.height,"rgb_sha256":hashlib.sha256(frame.rgb).hexdigest(),
                "screenshot":str(screenshot),"unique_rgb_bytes":len(set(frame.rgb))})
            persist()
            return frame
        for number,frames in enumerate((1,60,180,360,600)):
            stage(frames,f"boot-{number}")
        # A synthetic libretro Start tap checks host/core delivery without a
        # physical controller or changes to any game/controller configuration.
        session.set_input(0,1<<3)
        stage(1,"start-pressed")
        session.set_input(0,0)
        record["input_events"].append({"port":0,"button":"libretro Start","held_frames":1})
        stage(600,"after-start")
        try:
            state=session.save_state(directory/"native-roundtrip.state")
            before=session.step(32)
            session.load_state(state)
            after=session.step(32)
            record.update(state_bytes=state.stat().st_size,state_sha256=digest(state),
                          state_roundtrip_identical_frame=before.rgb==after.rgb,
                          restored_rgb_sha256=hashlib.sha256(after.rgb).hexdigest())
            session.save_screenshot(directory/"native-restored-frame.png")
        except BaseException as error:
            record["state_error"]=f"{type(error).__name__}: {error}"
        persist()
    except BaseException as error:
        record["error"]=f"{type(error).__name__}: {error}"
        record["traceback"]=traceback.format_exc()
    finally:
        if session is not None:
            try:
                session.stop(timeout=5)
                record["clean_shutdown"]=session.error is None
            except BaseException as error:record["shutdown_error"]=f"{type(error).__name__}: {error}"
            record.update(host_error=session.error,messages=session.messages)
        record["duration_seconds"]=time.monotonic()-started
        persist()
    print(json.dumps(record,indent=2))
    return int(any(record.get(key) for key in ("error","state_error","shutdown_error","host_error")))


def main():
    if len(sys.argv)==3 and sys.argv[1]=="--child":return child(sys.argv[2])
    directory=OUTPUT/(datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-")+uuid.uuid4().hex[:6])
    directory.mkdir(parents=True)
    started=time.monotonic()
    try:
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),"--child",str(directory)],
                              cwd=ROOT,capture_output=True,timeout=TIMEOUT)
        (directory/"native-stdout.log").write_bytes(result.stdout)
        (directory/"native-stderr.log").write_bytes(result.stderr)
        process={"exit_code":result.returncode,"timed_out":False}
    except subprocess.TimeoutExpired as error:
        (directory/"native-stdout.log").write_bytes(error.stdout or b"")
        (directory/"native-stderr.log").write_bytes(error.stderr or b"")
        process={"exit_code":None,"timed_out":True}
    process.update(timeout_seconds=TIMEOUT,duration_seconds=time.monotonic()-started,
                   output_directory=str(directory),physical_drive_access=False)
    (directory/"process-acceptance.json").write_text(json.dumps(process,indent=2),encoding="utf-8")
    (OUTPUT/"latest.json").write_text(json.dumps(process,indent=2),encoding="utf-8")
    print(json.dumps(process,indent=2))
    record_path=directory/"native-acceptance.json"
    if record_path.exists():
        record=json.loads(record_path.read_text(encoding="utf-8"))
        print(json.dumps({key:record.get(key) for key in ("initialized","core","error","state_error",
            "host_error","state_roundtrip_identical_frame","clean_shutdown")},indent=2))
        print("Recorded stages:",len(record["stages"]))
    return int(process["timed_out"] or process["exit_code"]!=0)


if __name__=="__main__":raise SystemExit(main())
