"""Real Saturn buffer-install acceptance in a bounded, isolated native child.

Uses the previously verified local capture and user-supplied stock BIOS. The
only new media copies belong to a fresh analysis library/cache. Python optical
entry points raise if called; no installed frontend/library settings change.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
OUTPUT=ROOT/"analysis/buffer_install/native"
TIMEOUT=55


def digest(path):
    with Path(path).open("rb") as source:return hashlib.file_digest(source,"sha256").hexdigest()


def files(directory):
    return {p.relative_to(directory).as_posix():digest(p) for p in sorted(directory.rglob("*")) if p.is_file()}


def child(directory):
    from redux.disc_buffer import _new_lease,_validate_capture
    from redux.disc_identity import DiscIdentity,parse_saturn_header
    from redux.disc_session import DiscEmbeddedGame,prepare_disc_game
    from redux.embedded import EmbeddedGame
    from redux.library import Library
    directory=Path(directory).resolve()
    if not directory.is_relative_to(OUTPUT.resolve()):raise ValueError("Output must stay in analysis/buffer_install/native")
    record={"observed_at_utc":datetime.now(timezone.utc).isoformat(),
            "physical_drive_access":False,"production_library_changed":False,
            "launcher_config_changed":False,"audio_enabled":False,
            "optical_entrypoint_calls":[],"stages":[],
            "limits":["Uses an already verified local image; no physical-drive acquisition is repeated.",
                      "Headless software-video/state acceptance; audible audio, physical pads and GUI flows are separate.",
                      "Optical guards instrument Python service entry points, not an OS-wide kernel trace."]}
    sessions=[];lease=None;started=time.monotonic()
    def persist():
        (directory/"native-acceptance.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
    def blocked(*args,**kwargs):
        record["optical_entrypoint_calls"].append("Unexpected optical service invocation")
        persist()
        raise AssertionError("This acceptance must not access a physical optical drive")
    def capture(session,label,frames):
        frame=session.host.step(frames)
        path=session.host.save_screenshot(directory/f"{label}.png")
        record["stages"].append({"label":label,"frames_advanced":frames,
            "width":frame.width,"height":frame.height,"frame_sequence":session.host.frame_count,
            "rgb_sha256":hashlib.sha256(frame.rgb).hexdigest(),"screenshot":str(path)})
        persist()
        return frame
    try:
        source=ROOT/"analysis/emulator_alignment/saturn-world-series-baseball-2"
        manifest=json.loads((source/"manifest.json").read_text(encoding="utf-8"))
        if digest(source/"disc.bin")!=manifest["sha256"]:raise RuntimeError("The captured BIN differs from its manifest")
        if digest(source/"disc.cue")!=manifest["cue_sha256"]:raise RuntimeError("The captured CUE differs from its manifest")
        bios=ROOT/"bios"
        expected={"mpr-17933.bin":"96e106f740ab448cf89f0dd49dfbac7fe5391cb6bd6e14ad5e3061c13330266f",
                  "sega_101.bin":"dcfef4b99605f872b6c3b6d05c045385cdea3d1b702906a0ed930df7bcb7deac"}
        record["firmware"]={name:digest(bios/name) for name in expected}
        if record["firmware"]!=expected:raise RuntimeError("Supplied Saturn BIOS hashes do not match documented stock firmware")
        core=ROOT/"tools/cores/mednafen_saturn_libretro.dll"
        record.update(core_path=str(core),core_sha256=digest(core),source_image_sha256=manifest["sha256"],
                      source_image_bytes=(source/"disc.bin").stat().st_size)
        with (source/"disc.bin").open("rb") as stream:identity=parse_saturn_header(stream.read(2352)[16:2064])
        if identity is None:raise RuntimeError("The local capture lacks a supported Saturn header")
        identity=DiscIdentity("G:",identity.title,identity.system,identity.game_id,identity.format,
            identity.confidence,identity.notes,identity.details,manifest["sha256"])
        library=Library(directory/"isolated-library")
        lease=_new_lease(library.root/"cache"/"disc-play",identity)
        buffer_directory=lease.directory
        capture_directory=buffer_directory/"capture";capture_directory.mkdir()
        for name in ("disc.cue","disc.bin","manifest.json"):
            shutil.copyfile(source/name,capture_directory/name)
        if digest(capture_directory/"disc.bin")!=manifest["sha256"]:raise RuntimeError("Local buffer copy changed")
        _validate_capture(SimpleNamespace(output_directory=capture_directory,
            cue_path=capture_directory/"disc.cue",bin_path=capture_directory/"disc.bin",
            manifest_path=capture_directory/"manifest.json",verified_output=manifest["verified_output"],
            verified_source_passes=manifest["verified_source_passes"],image_kind=manifest["image_kind"],
            sha256=manifest["sha256"],source_sha256=manifest["sha256"],
            bytes_written=manifest["bytes_written"]),lease)
        guards=("redux.disc_buffer.buffer_disc","redux.disc_buffer.identify_drive",
                "redux.disc_buffer.capture_raw_cd","redux.disc_buffer.mounted_image_source",
                "redux.disc_identity.identify_drive","redux.raw_acquisition.capture_raw_cd",
                "redux.raw_acquisition._get_api","redux.optical._get_api")
        record["blocked_optical_entrypoints"]=list(guards)
        with ExitStack() as stack:
            for name in guards:stack.enter_context(patch(name,side_effect=blocked))
            game=prepare_disc_game(library,lease)
            session=DiscEmbeddedGame(library,game,core,media_lease=lease,firmware=bios,audio=False)
            sessions.append(session)
            record.update(transient_game_id=game["id"],bundle_hash=game["bundle_hash"],
                          buffered_media_path=str(session.host.game_path),source_save_directory=str(session.directory))
            persist()
            session.start();session.pause()
            record["core"]=session.host.core_info
            capture(session,"buffered-boot",1200)
            session.host.set_input(0,1<<3);session.host.step(1);session.host.set_input(0,0)
            capture(session,"buffered-game-intro",600)
            initial_slot=session.save(0)
            original_paths=(session.host.game_path,session.host.save_directory,session.directory)
            installed=session.install_to_library()
            record.update(installed_game_id=installed["id"],installed_bundle_hash=installed["bundle_hash"],
                          installed_storage=installed["storage"],installed_media_path=str(library.path_for(installed)),
                          paths_unchanged_during_install=original_paths==(session.host.game_path,session.host.save_directory,session.directory),
                          lease_open_after_install=not lease.closed,core_paused_after_install=session.host.paused,
                          initial_slot_bytes=initial_slot.size)
            if not record["paths_unchanged_during_install"] or not record["lease_open_after_install"]:
                raise RuntimeError("Installing retargeted the live core or released its media")
            capture(session,"after-install-progress",120)
            post_slot=session.save(1)
            expected_frame=capture(session,"expected-after-post-install-state",32)
            record.update(post_install_slot_bytes=post_slot.size,post_install_slot_sha256=post_slot.sha256)
            session.stop()
            record.update(lease_closed_after_stop=lease.closed,buffer_removed_after_stop=not buffer_directory.exists(),
                          original_saves_retained=session.directory.is_dir(),host_shutdown_error=session.host.error)
            target=library.root/"game_data"/str(installed["id"])/installed["bundle_hash"]
            before=files(session.directory);after=files(target)
            record.update(source_userdata_files=before,managed_userdata_files=after,
                          final_save_snapshot_identical=before==after)
            if not lease.closed or buffer_directory.exists() or before!=after:
                raise RuntimeError("Final handoff or buffer cleanup failed")
            managed=EmbeddedGame(library,installed,core,firmware=bios,audio=False)
            sessions.append(managed)
            managed.start();managed.pause()
            copied_slot=managed.slot(1,verify=True)
            managed.load(1)
            actual_frame=capture(managed,"managed-restored-state",32)
            record.update(managed_slot_verified=copied_slot.exists,
                          managed_state_roundtrip_identical_frame=expected_frame.rgb==actual_frame.rgb,
                          managed_loaded_slot_sha256=copied_slot.sha256,
                          cue_entry_hash_unchanged=session.host._game_hash==managed.host._game_hash,
                          core_hash_unchanged=session.host._core_hash==managed.host._core_hash)
            if expected_frame.rgb!=actual_frame.rgb:raise RuntimeError("Managed relaunch/state restore diverged")
            managed.stop()
            record.update(managed_clean_shutdown=managed.host.error is None,
                          optical_services_unused=not record["optical_entrypoint_calls"])
    except BaseException as error:
        record.update(error=f"{type(error).__name__}: {error}",traceback=traceback.format_exc())
    finally:
        for session in reversed(sessions):
            try:
                if session.finished_seconds is None:session.stop()
            except BaseException as error:record.setdefault("cleanup_errors",[]).append(str(error))
        # A session's failed stop must retain its lease for investigation.
        if lease is not None and not sessions:
            record["unused_lease_cleanup"]=lease.close()
        record["duration_seconds"]=time.monotonic()-started
        persist()
    print(json.dumps(record,indent=2))
    return int(bool(record.get("error") or record.get("cleanup_errors")))


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
    process.update(timeout_seconds=TIMEOUT,duration_seconds=time.monotonic()-started,output_directory=str(directory))
    (directory/"process-acceptance.json").write_text(json.dumps(process,indent=2),encoding="utf-8")
    (OUTPUT/"latest.json").write_text(json.dumps(process,indent=2),encoding="utf-8")
    print(json.dumps(process,indent=2))
    if (directory/"native-acceptance.json").exists():
        record=json.loads((directory/"native-acceptance.json").read_text(encoding="utf-8"))
        print(json.dumps({key:record.get(key) for key in ("error","core","paths_unchanged_during_install",
            "lease_closed_after_stop","buffer_removed_after_stop","final_save_snapshot_identical",
            "managed_state_roundtrip_identical_frame","managed_clean_shutdown","optical_services_unused")},indent=2))
    return int(process["timed_out"] or process["exit_code"]!=0)


if __name__=="__main__":raise SystemExit(main())
