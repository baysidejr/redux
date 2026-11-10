"""Bounded native libretro metadata/options probes, without loading content.

Each DLL is initialized in a separate process. No ROM, BIOS, drive, emulator
window, user save or permanent frontend configuration is opened by this probe.
Initialization and declarations are not evidence of game acceptance or video.
"""
from __future__ import annotations

import argparse
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from redux.core_profiles import PROFILES, default_options
from redux.libretro_host import EmbeddedSession, _SystemInfo


CORES = (
    "mednafen_saturn", "mednafen_supafaust", "mednafen_pce",
    "mupen64plus_next", "mame", "picodrive",
    "neocd", "opera",
)


def _save(path, result):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _decode(value):
    return (value or b"").decode("utf-8", "replace")


def _profiles(filename):
    return {key: default_options(key) for key, profile in PROFILES.items()
            if profile.filename == filename}


def _session_details(result, session):
    declared = {key: list(values) for key, values in session.declared_options.items()}
    selected = {_decode(key): _decode(value) for key, value in session._variables.items()}
    result["declared_options"] = declared
    result["selected_values"] = selected
    validations = {}
    for system, requested in result["requested_profiles"].items():
        values = {}
        for key, value in requested.items():
            values[key] = {"requested": value, "key_declared": key in declared,
                           "value_declared": value in declared.get(key, ()),
                           "selected_value": selected.get(key),
                           "selected_matches_request": selected.get(key) == value}
        validations[system] = {
            "requested_count": len(requested), "options": values,
            "all_requested_values_declared": all(item["value_declared"] for item in values.values()),
            "all_requested_values_selected": all(item["selected_matches_request"] for item in values.values()),
            "game_acceptance_tested": False,
        }
    result["profile_validation"] = validations
    result["environment_callback_error"] = session._callback_error
    result["core_log_format_strings"] = list(session.messages)
    result["logger_formats_arguments"] = False
    result["log_note"] = "Host captures bounded C format strings without interpreting variadic arguments."
    if result["core"] == "mupen64plus_next":
        result["software_plugin_declarations"] = {
            "angrylion_rdp_declared_by_installed_binary": "angrylion" in declared.get("mupen64plus-rdp-plugin", ()),
            "cxd4_rsp_declared_by_installed_binary": "cxd4" in declared.get("mupen64plus-rsp-plugin", ()),
            "basis": "Runtime core option values; no content was loaded and no rendering plugin was executed.",
        }


def _child(core, runtime, output):
    # Suppress Windows native crash/error dialogs; the parent records exit codes.
    if os.name == "nt":
        kernel = C.WinDLL("kernel32", use_last_error=True)
        kernel.SetErrorMode.argtypes = [C.c_uint]
        kernel.SetErrorMode.restype = C.c_uint
        kernel.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    filename = core + "_libretro.dll"
    path = ROOT / "tools" / "cores" / filename
    requested_profiles = _profiles(filename)
    requested = {}
    for values in requested_profiles.values():
        for key, value in values.items():
            if key in requested and requested[key] != value:
                raise ValueError("One DLL has conflicting profile values: " + key)
            requested[key] = value
    runtime = Path(runtime).resolve(strict=True)
    system = runtime / "system"
    saves = runtime / "saves"
    system.mkdir()
    saves.mkdir()
    session = EmbeddedSession(path, runtime / "NO_CONTENT_IS_LOADED", system, saves,
                              audio=False, core_options=requested)
    result = {"core": core, "dll_path": str(path), "pointer_bits": C.sizeof(C.c_void_p) * 8,
              "requested_profiles": requested_profiles, "stage": "loading_dll",
              "initialized": False, "deinitialized": False, "content_loaded": False,
              "retro_load_game_called": False, "retro_run_called": False,
              "firmware_tested": False, "physical_hardware_tested": False}
    _save(output, result)
    initialized = False
    try:
        session._core = C.CDLL(str(path))
        result["stage"] = "binding_typed_public_abi"
        _save(output, result)
        session._bind()
        result["api_version"] = int(session._core.retro_api_version())
        info = _SystemInfo()
        session._core.retro_get_system_info(C.byref(info))
        result["system_info"] = {"name": _decode(info.library_name),
            "version": _decode(info.library_version), "valid_extensions": _decode(info.valid_extensions),
            "need_fullpath": bool(info.need_fullpath), "block_extract": bool(info.block_extract)}
        for name, callback in zip(("environment", "video_refresh", "audio_sample", "audio_sample_batch",
                                   "input_poll", "input_state"), session._callbacks):
            result["stage"] = "registering_" + name
            _session_details(result, session)
            _save(output, result)
            getattr(session._core, "retro_set_" + name)(callback)
        result["stage"] = "retro_init"
        _session_details(result, session)
        _save(output, result)
        session._core.retro_init()
        initialized = True
        result["initialized"] = True
        result["stage"] = "initialized_without_content"
        _session_details(result, session)
        _save(output, result)
    except Exception as exc:
        result["python_error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        if initialized:
            result["stage"] = "retro_deinit"
            _session_details(result, session)
            _save(output, result)
            try:
                session._core.retro_deinit()
                result["deinitialized"] = True
                result["stage"] = "completed_without_content"
            except Exception as exc:
                result["python_error"] = type(exc).__name__ + ": " + str(exc)
        _session_details(result, session)
        _save(output, result)
    # Keep session, native callbacks and all strings alive until native deinit.
    # Do not manually FreeLibrary; process exit owns the DLL's lifetime.
    return 1 if result.get("python_error") else 0


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def _tail(path, limit=8192):
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - limit))
        return stream.read(limit).decode("utf-8", "replace"), size > limit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=CORES)
    parser.add_argument("--runtime")
    parser.add_argument("--result")
    parser.add_argument("--timeout", type=float, default=40)
    parser.add_argument("--output", type=Path, default=ROOT / "analysis/emulator_alignment/native-core-probes.json")
    arguments = parser.parse_args()
    if arguments.child:
        if not arguments.runtime or not arguments.result:
            parser.error("Child probes require --runtime and --result")
        return _child(arguments.child, arguments.runtime, arguments.result)
    if not 1 <= arguments.timeout <= 45:
        parser.error("Per-core timeout must be between 1 and 45 seconds")
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    results = {"probed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Separate native Windows x64 child process per core; metadata/options and init/deinit only.",
        "limitations": ["No ROM, BIOS, disc, game acceptance, frames, audio, input, save states or plugin execution tested.",
                        "Declaring an option validates that this installed binary offers its value; it does not prove a game boots.",
                        "Empty requested option sets do not validate compatibility for that system."],
        "per_core_timeout_seconds": arguments.timeout, "cores": []}
    _save(output, results)
    for core in CORES:
        path = ROOT / "tools/cores" / (core + "_libretro.dll")
        with tempfile.TemporaryDirectory(prefix="redux-core-probe-") as directory:
            runtime = Path(directory)
            child_result = runtime / "result.json"
            log = runtime / "native-output.txt"
            started = time.monotonic()
            code = None
            timed_out = False
            with log.open("wb") as stream:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", core,
                    "--runtime", str(runtime), "--result", str(child_result)], cwd=runtime,
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    code = process.wait(timeout=arguments.timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.kill()
                    code = process.wait(timeout=10)
            result = json.loads(child_result.read_text(encoding="utf-8")) if child_result.is_file() else {"core": core}
            result["child_exit_code"] = code
            result["child_exit_code_hex"] = f"0x{code & 0xffffffff:08X}" if code is not None else None
            result["timed_out"] = timed_out
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            result["dll_bytes"] = path.stat().st_size if path.is_file() else None
            result["dll_sha256"] = _sha256(path) if path.is_file() else None
            result["native_output_tail"], result["native_output_truncated"] = _tail(log)
            result["clean_metadata_probe"] = bool(code == 0 and not timed_out and
                result.get("initialized") and result.get("deinitialized") and not result.get("environment_callback_error"))
            results["cores"].append(result)
            _save(output, results)
            print(json.dumps({"core": core, "clean_metadata_probe": result["clean_metadata_probe"],
                "stage": result.get("stage"), "child_exit_code": code,
                "declared_option_count": len(result.get("declared_options", {})),
                "profile_validation": result.get("profile_validation"),
                "software_plugin_declarations": result.get("software_plugin_declarations")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
