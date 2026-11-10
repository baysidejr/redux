"""Build a per-user Redux installer from an already packaged portable release."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from redux import __version__
from tools.release_guard import distribution_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, help='Path to a trusted Inno Setup ISCC.exe')
    args = parser.parse_args()
    compiler = args.compiler or shutil.which('ISCC.exe')
    if not compiler or not Path(compiler).is_file():
        parser.error('Install Inno Setup, then supply --compiler or put ISCC.exe on PATH')
    source = ROOT / 'dist/Redux'
    if not (source / 'Redux.exe').is_file():
        parser.error('Build Redux.spec and run tools/package_release.py first')
    if (source / 'RELEASE_VERSION.txt').read_text().strip() != __version__:
        parser.error('Packaged build version does not match the source version')
    distribution_files(source)
    subprocess.run([str(Path(compiler).resolve()), '/DAppVersion=' + __version__,
                    str(ROOT / 'tools/installer/Redux.iss')], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
