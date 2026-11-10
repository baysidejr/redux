"""Choose media entry files without importing their descriptor tracks twice."""
from pathlib import Path

from .library import ImportCancelled,MEDIA_EXTENSIONS,inspect_bundle


def prepare_media_selection(paths,cancelled=None):
    selected=[];seen=set()
    def check():
        if cancelled is not None and cancelled.is_set():raise ImportCancelled("Media selection cancelled")
    for path in paths:
        check();path=Path(path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.casefold() not in MEDIA_EXTENSIONS:continue
        key=str(path).casefold()
        if key not in seen:selected.append(path);seen.add(key)
    referenced=set()
    for path in selected:
        check()
        if path.suffix.casefold() in {".cue",".ccd",".m3u"}:
            try:
                bundle=inspect_bundle(path)
                referenced.update(str(part).casefold() for part in bundle.files if part!=bundle.entry)
            except (OSError,ValueError):pass  # Import reports malformed entries.
    check()
    return [path for path in selected if str(path).casefold() not in referenced]
