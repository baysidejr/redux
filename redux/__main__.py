"""Desktop app by default; explicit subcommands provide automation-friendly CLI."""
import argparse
from dataclasses import asdict
import json
import sqlite3
from pathlib import Path


def main(argv=None):
    import sys
    from .windows_activation import parse_activation
    activation=parse_activation(sys.argv[1:] if argv is None else argv)
    # Future shell arguments belong in the activation log, not the CLI parser.
    if activation.home:
        from .app import main as gui
        gui(activation=activation)
        return
    parser=argparse.ArgumentParser(description="Independent local media library")
    parser.add_argument("--library",type=Path,help="Library folder on local, SSD or SD storage")
    sub=parser.add_subparsers(dest="command")
    sub.add_parser("gui")
    listing=sub.add_parser("list")
    listing.add_argument("--query",default="")
    listing.add_argument("--system")
    verify=sub.add_parser("verify")
    verify.add_argument("id",type=int)
    configure=sub.add_parser("configure")
    configure.add_argument("system")
    configure.add_argument("--exe",required=True)
    configure.add_argument("--args-json",default='["{game}"]')
    play=sub.add_parser("play")
    play.add_argument("id",type=int)
    sub.add_parser("drives")
    inspect=sub.add_parser("inspect-disc")
    inspect.add_argument("drive")
    identity=sub.add_parser("identify")
    identity.add_argument("file",type=Path)
    raw_plan=sub.add_parser("plan-cd")
    raw_plan.add_argument("drive")
    capture=sub.add_parser("capture-cd")
    capture.add_argument("drive")
    capture.add_argument("destination",type=Path,help="New output folder; existing paths are never overwritten")
    volume=sub.add_parser("capture-volume")
    volume.add_argument("drive")
    volume.add_argument("destination",type=Path)
    args,unknown=parser.parse_known_args(activation.remaining)
    if unknown and args.command not in (None,"gui"):
        parser.error("unrecognized arguments: "+" ".join(unknown))
    if args.command in (None,"gui"):
        from .app import main as gui
        gui(args.library,activation=activation)
        return
    from .library import Library, discover_media
    from .launch import build_command, launch_game
    from .optical import list_drives,inspect_disc
    try:
        if args.command=="drives":
            print(json.dumps([asdict(d) for d in list_drives()],indent=2))
            return
        if args.command=="inspect-disc":
            print(json.dumps(asdict(inspect_disc(args.drive)),indent=2))
            return
        if args.command=="identify":
            from .media_identity import identify_media
            print(json.dumps(asdict(identify_media(args.file)),indent=2,default=str));return
        if args.command in {"plan-cd","capture-cd"}:
            from .raw_acquisition import plan_raw_capture,capture_raw_cd
            result=plan_raw_capture(args.drive) if args.command=="plan-cd" else capture_raw_cd(args.drive,args.destination)
            print(json.dumps(asdict(result),indent=2,default=str));return
        if args.command=="capture-volume":
            from .acquisition import capture_logical_volume
            print(json.dumps(asdict(capture_logical_volume(args.drive,args.destination)),indent=2,default=str));return
        if not args.library:
            parser.error("--library is required for library commands")
        library=Library(args.library)
        if args.command=="list":
            print(json.dumps(library.games(args.query,args.system),indent=2))
        elif args.command=="verify":
            rows=library.verify(args.id)
            print(json.dumps(rows,indent=2))
            if any(r["status"]!="ok" for r in rows):
                raise SystemExit(2)
        elif args.command=="configure":
            arguments=json.loads(args.args_json)
            build_command(args.exe,arguments,library.db)
            library.configure_launcher(args.system,args.exe,arguments)
            print("Emulator configured")
        elif args.command=="play":
            game=library.game(args.id)
            launcher=library.launcher(game["system"])
            process=launch_game(launcher["executable"],launcher["arguments"],library.path_for(game))
            print(json.dumps(dict(pid=process.pid)))
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,sqlite3.Error) as error:
        parser.exit(1,f"Error: {error}\n")


if __name__=="__main__":
    main()
