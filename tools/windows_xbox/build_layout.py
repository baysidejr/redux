"""Stage the optional Xbox-home layout from a development checkout."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from redux.windows_identity import build_layout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--portable-root", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(json.dumps(build_layout(args.source, args.portable_root, args.destination, args.version), indent=2))


if __name__ == "__main__":
    main()
