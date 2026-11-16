"""Fail closed if a distribution contains local data or unexpected payloads."""
from pathlib import Path

ROOT_FILES = frozenset({'Redux.exe', 'README.md', 'RELEASE_VERSION.txt', 'THIRD_PARTY_NOTICES.txt'})
FORBIDDEN_PARTS = frozenset({'library', 'bios', 'analysis', 'windows-home', 'emulators', '.git', '.venv'})
FORBIDDEN_SUFFIXES = frozenset({'.dpapi', '.sqlite3', '.iso', '.bin', '.cue', '.chd', '.rom', '.nes',
                                '.j64','.jag','.bigpimg','.cdi','.cof','.abs',
                                '.sfc', '.smc', '.mdf', '.nrg', '.pfx', '.p12', '.pem', '.key'})
# Public certificates only: Mozilla's root list (certifi) backs up the Windows certificate store.
PUBLIC_CERTIFICATES = frozenset({'_internal/certifi/cacert.pem'})


def _public_certificates_only(path):
    text = path.read_bytes()
    return b'PRIVATE KEY' not in text and b'-----BEGIN CERTIFICATE-----' in text


def distribution_files(root):
    root = Path(root).resolve(strict=True)
    files = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('Release must not contain symbolic links or junctions')
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        public = relative.as_posix() in PUBLIC_CERTIFICATES and _public_certificates_only(path)
        if any(p.casefold() in FORBIDDEN_PARTS for p in parts) or (path.suffix.lower() in FORBIDDEN_SUFFIXES and not public):
            raise ValueError('Release contains local data or a forbidden payload: ' + relative.as_posix())
        allowed = (len(parts) == 1 and path.name in ROOT_FILES or
                   parts[0] in {'_internal', 'docs', 'licenses'} or
                   parts[:2] == ('tools', 'windows_xbox') or
                   parts[:2] == ('tools', 'ReduxReceiver') or
                   relative.as_posix() == 'tools/cores/install_official.py')
        if not allowed or path.name.startswith('.env'):
            raise ValueError('Release contains an unexpected file: ' + relative.as_posix())
        if not path.resolve().is_relative_to(root):
            raise ValueError('Release file resolves outside the distribution')
        files.append(path)
    return files
