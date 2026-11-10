"""Stage an immutable loose Xbox-home package without registering it.

The normal portable distribution remains the source of the user's library, BIOS,
cores and settings. Never register dist itself: it is replaced by every build.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
from string import Template
import tempfile

from PIL import Image


PACKAGE_NAME = "Redux.MediaLibrary"
APP_ID = "App"
TEMPLATES = Path(__file__).resolve().parents[1] / "tools/windows_xbox"


def _template_directory(source, templates=None):
    candidate = Path(templates) if templates else Path(source) / "tools/windows_xbox"
    if not (candidate / "AppxManifest.xml.template").is_file():
        candidate = TEMPLATES
    if not (candidate / "AppxManifest.xml.template").is_file():
        raise ValueError("Xbox integration templates are missing from this application build")
    return candidate


def _payload_digest(source, templates):
    """Templates and native runtime changes also need a fresh immutable layout."""
    source, templates = Path(source), Path(templates)
    records = [("Redux.exe", source / "Redux.exe")]
    records.extend(("_internal/" + path.relative_to(source / "_internal").as_posix(), path)
                   for path in (source / "_internal").rglob("*") if path.is_file())
    records.extend(("tools/windows_xbox/" + path.relative_to(templates).as_posix(), path)
                   for path in templates.rglob("*") if path.is_file()
                   and "__pycache__" not in path.parts and path.suffix != ".pyc")
    digest = hashlib.sha256()
    for relative, path in sorted(records):
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def package_version(value):
    """Convert our release version to the four-component Windows identity form."""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", value):
        raise ValueError("Package version must have three or four numeric components")
    parts = [int(part) for part in value.split(".")]
    if len(parts) == 3:
        parts.append(0)
    if any(part > 65535 for part in parts):
        raise ValueError("Windows package version components cannot exceed 65535")
    return ".".join(map(str, parts))


def build_layout(source, portable_root, destination, version, *, icon=None, templates=None):
    source = Path(source).resolve(strict=True)
    portable_root = Path(portable_root).resolve(strict=True)
    destination = Path(destination).resolve()
    version = package_version(version)
    templates = _template_directory(source, templates)
    if not (source / "Redux.exe").is_file() or not (source / "_internal").is_dir():
        raise ValueError("Source must be a complete Redux portable build")
    if not portable_root.is_dir():
        raise ValueError("Portable root must be an existing application folder")
    runtime = source / "_internal"
    if destination == source or destination == runtime or runtime in destination.parents:
        raise ValueError("The identity layout must not replace the portable application or runtime")
    if destination.exists():
        raise FileExistsError("Identity layout already exists; stage a new version instead of replacing registered files")
    icon = Path(icon) if icon else source / "_internal/assets/redux.ico"
    if not icon.is_file():
        raise ValueError("Redux application icon is missing")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".redux-home-staging-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "package"
        staged.mkdir()
        # Only the executable/runtime belong to this identity. User files, retail
        # firmware, cores, installers and reference/vendor assets stay outside it.
        shutil.copy2(source / "Redux.exe", staged / "Redux.exe")
        shutil.copytree(source / "_internal", staged / "_internal")
        (staged / "AppxManifest.xml").write_text(Template(
            (templates / "AppxManifest.xml.template").read_text(encoding="utf-8")
        ).substitute(version=version), encoding="utf-8")
        shutil.copy2(templates / "CustomCapability.SCCD", staged / "CustomCapability.SCCD")
        shutil.copytree(templates / "Public", staged / "Public")
        shutil.copytree(templates, staged / "tools/windows_xbox", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        assets = staged / "Assets"
        assets.mkdir()
        with Image.open(icon) as opened:
            logo = opened.convert("RGBA")
            for size, name in ((150, "Square150x150Logo.png"), (44, "Square44x44Logo.png"), (50, "StoreLogo.png")):
                logo.resize((size, size), Image.Resampling.LANCZOS).save(assets / name)
        (staged / "redux-launch.json").write_text(json.dumps(
            {"portable_root": str(portable_root)}, indent=2), encoding="utf-8")
        payload_sha256 = _payload_digest(staged, staged / "tools/windows_xbox")
        if payload_sha256 != _payload_digest(source, templates):
            raise ValueError("Application files changed while staging identity; retry after the build completes")
        metadata = {"package_name": PACKAGE_NAME, "app_id": APP_ID, "version": version,
                    "portable_root": str(portable_root), "source": str(source),
                    "executable_sha256": hashlib.sha256((staged / "Redux.exe").read_bytes()).hexdigest(),
                    "payload_sha256": payload_sha256,
                    "registration": "loose-development", "developer_mode_required_at_registration": True}
        (staged / "redux-package.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        staged.rename(destination)
    return dict(metadata, layout=str(destination), manifest=str(destination / "AppxManifest.xml"))


def prepare_identity_layout(application_dir, portable_root, version):
    """Stage/reuse an immutable layout; registration is a separate action."""
    source = Path(application_dir).resolve(strict=True)
    root = Path(portable_root).resolve(strict=True)
    fingerprint = hashlib.sha256((source / "Redux.exe").read_bytes()).hexdigest()
    payload_sha256 = _payload_digest(source, _template_directory(source))
    identity_hash = hashlib.sha256((payload_sha256 + "\0" + str(root)).encode("utf-8")).hexdigest()
    name = package_version(version) + "-" + identity_hash[:12]
    destination = root / "windows-home" / name
    metadata_path = destination / "redux-package.json"
    if destination.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Existing identity layout is incomplete; preserve it and stage a new release") from exc
        if (metadata.get("executable_sha256") != fingerprint or metadata.get("portable_root") != str(root)
                or metadata.get("payload_sha256") != payload_sha256):
            raise ValueError("Existing identity layout does not match this portable application")
        if not (destination / "AppxManifest.xml").is_file():
            raise ValueError("Existing identity layout manifest is missing")
        result = dict(metadata, layout=str(destination), manifest=str(destination / "AppxManifest.xml"))
    else:
        result = build_layout(source, root, destination, version)
    scripts = destination / "tools/windows_xbox"
    result.update(register_script=str(scripts / "Register-HomeApp.ps1"),
                  unregister_script=str(scripts / "Unregister-HomeApp.ps1"),
                  enable_picker_script=str(scripts / "Enable-HomePicker.ps1"),
                  restore_device_form_script=str(scripts / "Restore-DeviceForm.ps1"),
                  device_form_wrapper=str(scripts / "Invoke-DeviceForm.ps1"),
                  xbox_app_wrapper=str(scripts / "Invoke-XboxApp.ps1"))
    return result
