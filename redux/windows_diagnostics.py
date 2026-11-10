"""Opt-in local launch evidence for Windows launcher integration troubleshooting."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys


def capture_requested_launch(app):
    """Write one bounded snapshot only when the app-folder marker exists.

    No game/firmware bytes or complete environment are captured. The marker and
    output stay beside the portable EXE, outside the release and user library.
    """
    if not getattr(sys,"frozen",False):return
    folder=Path(sys.executable).resolve().parent
    request=folder/".redux-diagnose-next-launch"
    if not request.is_file():return
    from .app import config_path
    from .ui_assets import project_root
    from .windows_gaming import current_package_full_name
    settings=config_path()
    try:
        raw=settings.read_bytes()
        settings_hash=hashlib.sha256(raw).hexdigest()
        configured=json.loads(raw).get("library_root")
    except (OSError,ValueError,AttributeError):settings_hash=configured=None
    selected={name:os.environ.get(name) for name in ("LOCALAPPDATA","APPDATA","USERPROFILE","__COMPAT_LAYER","PYTHONHOME","PYTHONPATH")}
    selected.update({name:value for name,value in os.environ.items() if name.startswith("_PYI_")})
    result={"pid":os.getpid(),"executable":sys.executable,"working_directory":os.getcwd(),
            "package":current_package_full_name(),"settings_path":str(settings),"settings_sha256":settings_hash,
            "configured_library":configured,"preference_library":app.preferences.get("library_root"),
            "project_root":str(project_root()),"library_root":str(app.library.root),"game_count":len(app.all_games),
            "modules":{"app":sys.modules["redux.app"].__file__,
                       "console":sys.modules["redux.console_ui"].__file__},"environment":selected}
    output=folder/"redux-launch-diagnostic.json"
    try:
        output.write_text(json.dumps(result,indent=2),encoding="utf-8")
        request.unlink()
    except OSError:pass
