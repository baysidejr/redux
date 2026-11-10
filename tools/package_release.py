"""Package the built frontend, documentation and optional core installer only."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from redux import __version__
from tools.release_guard import distribution_files


def digest(path):
    with path.open("rb") as source:return hashlib.file_digest(source,"sha256").hexdigest()


def main():
    output=ROOT/"dist/Redux"
    if not (output/"Redux.exe").is_file():raise SystemExit("Build the frontend first")
    shutil.copy2(ROOT/"README.md",output/"README.md")
    shutil.copytree(ROOT/"docs",output/"docs",dirs_exist_ok=True)
    installer=output/"tools/cores/install_official.py"
    installer.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/"tools/cores/install_official.py",installer)
    shutil.copytree(ROOT/"tools/windows_xbox",output/"tools/windows_xbox",dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    licenses=output/"licenses";licenses.mkdir(exist_ok=True)
    supplied={"Python.txt":Path(sys.base_prefix)/"LICENSE.txt",
        "Pillow.txt":ROOT/".venv/Lib/site-packages/pillow-13.0.0.dist-info/licenses/LICENSE",
        "PyInstaller.txt":ROOT/".venv/Lib/site-packages/pyinstaller-6.22.3.dist-info/licenses/COPYING.txt"}
    supplied["RobotoCondensed-OFL.txt"]=ROOT/"redux/assets/fonts/OFL.txt"
    supplied["SDL3.txt"]=ROOT/"redux/assets/native/SDL3-LICENSE.txt"
    supplied["SDL-GameControllerDB.txt"]=ROOT/"redux/assets/native/GameControllerDB-LICENSE.txt"
    for name,path in supplied.items():
        if path.is_file():shutil.copy2(path,licenses/name)
    (output/"THIRD_PARTY_NOTICES.txt").write_text(
        "Redux frontend dependencies: Python, Tcl/Tk, Pillow, Roboto Condensed (SIL Open Font License), SDL3 and SDL_GameControllerDB (zlib licenses), and the PyInstaller bootloader.\n"
        "Supplied notices are in licenses/ and the bundled runtime.\n"
        "No reference application runtime/assets/catalogue, emulator executables/core DLLs, retail firmware, or user games are bundled.\n"
        "Optional cores are obtained separately under their upstream terms; the named installer records provenance and licenses.\n",
        encoding="utf-8")
    (output/"RELEASE_VERSION.txt").write_text(__version__+"\n",encoding="utf-8")
    files=distribution_files(output)
    archive=ROOT/f"dist/ReduxMediaLibrary-{__version__}-win-x64.zip"
    temporary=archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for path in files:zipped.write(path,Path("Redux")/path.relative_to(output))
    with zipfile.ZipFile(temporary) as zipped:
        if zipped.testzip():raise RuntimeError("Release ZIP verification failed")
    temporary.replace(archive)
    manifest={"version":__version__,"created_utc":datetime.now(timezone.utc).isoformat(),
        "application_exe":str(output/"Redux.exe"),"application_sha256":digest(output/"Redux.exe"),
        "distribution_zip":str(archive),"zip_sha256":digest(archive),"zip_bytes":archive.stat().st_size,
        "distribution_file_count":len(files),"vendor_payloads_bundled":False,"emulators_bundled":False,"roms_bundled":False,
        "files":[{"path":path.relative_to(output).as_posix(),"bytes":path.stat().st_size,"sha256":digest(path)} for path in files],
        "source_sha256":{path.relative_to(ROOT).as_posix():digest(path) for path in sorted((ROOT/"redux").glob("*.py"))}}
    (ROOT/"analysis/build_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps({key:manifest[key] for key in ("version","application_exe","application_sha256","distribution_zip","zip_bytes","zip_sha256")},indent=2))


if __name__=="__main__":main()
