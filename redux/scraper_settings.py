"""Portable scraper choices and local Windows-protected account credentials.

Only display/download choices live with the library. Account names, passwords
and API credentials are encrypted using current-user DPAPI in ordinary local
application data. Moving a library therefore never moves usable credentials.
The protector argument exists for isolated tests, not a plaintext fallback.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import threading
import uuid

from .windows_paths import redux_user_data


PROVIDERS = ("screenscraper", "thegamesdb", "hfsdb", "igdb", "arcadedb", "libretro")
ROLES = ("cover", "tile", "hero", "screenshot", "logo")
PROVIDER_FIELDS = {
    "screenscraper": ("username", "password", "developer_id", "developer_password"),
    "thegamesdb": ("api_key",),
    "hfsdb": ("username", "password"),
    "igdb": ("client_id", "client_secret"),
    "arcadedb": (),
    "libretro": (),
}
DEFAULTS = {"provider": "screenscraper", "region": "us", "language": "en",
            "overwrite": False, "roles": list(ROLES), "automatic": False,
            "tile_style": "mixed"}
_MAX_CONFIG_BYTES = 16 * 1024
_MAX_PLAIN_BYTES = 64 * 1024
_MAX_SECRET_BYTES = 128 * 1024
_MAGIC = b"ReduxScraperCredentials\x00\x01"
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _path_lock(path):
    key = os.path.normcase(str(path))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _check_chain(path):
    for candidate in (*reversed(path.parents), path):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("Scraper settings cannot use symlinks or junctions")


def _root(value):
    value = Path(value).expanduser().absolute()
    _check_chain(value)
    if value.exists() and not value.is_dir():
        raise ValueError("Scraper settings need a directory")
    return value.resolve()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Scraper settings contain duplicate fields")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("Scraper settings contain a non-finite JSON value")


def _json(data, message):
    try:
        result = json.loads(data, object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError(message) from None
    if not isinstance(result, dict):
        raise ValueError(message)
    return result


def _provider(value):
    if not isinstance(value, str) or value not in PROVIDERS:
        raise ValueError("Choose a supported artwork service")
    return value


def _credential_field(provider, field):
    _provider(provider)
    if not isinstance(field, str) or field not in PROVIDER_FIELDS[provider]:
        raise ValueError("This artwork service does not use that credential field")
    return field


def _credential_value(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError("Account values must be text of at most 4096 characters without control characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Account values contain unsupported text") from None
    return value


def _options(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError("Scraper options contain an unsupported setting")
    output = dict(DEFAULTS, roles=list(ROLES))
    output.update(values)
    _provider(output["provider"])
    if output["tile_style"] not in ("mixed", "screenshot", "cover"):
        raise ValueError("Choose a supported library tile style")
    for name in ("region", "language"):
        value = output[name]
        if not isinstance(value, str) or not re.fullmatch(r"[a-z]{2,8}", value):
            raise ValueError("Choose a supported artwork language and region code")
    for name in ("overwrite", "automatic"):
        if type(output[name]) is not bool:
            raise ValueError("Artwork switches must be ON or OFF")
    roles = output["roles"]
    if (not isinstance(roles, list) or not roles or len(roles) > len(ROLES)
            or any(not isinstance(role, str) or role not in ROLES for role in roles)
            or len(set(roles)) != len(roles)):
        raise ValueError("Choose at least one supported artwork type without duplicates")
    output["roles"] = list(roles)
    return output


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsDataProtector:
    """Current-user DPAPI with no machine-wide scope or interactive prompts.

    ABI: Windows SDK dpapi.h, CryptProtectData/CryptUnprotectData. Buffers
    allocated by Windows are cleared and freed with LocalFree. Python's returned
    immutable bytes are temporary application data, not a secure-memory promise.
    """
    def _transform(self, data, protect):
        if os.name != "nt":
            raise OSError("Saved scraper accounts require Windows protected storage")
        if not isinstance(data, bytes) or not 1 <= len(data) <= _MAX_SECRET_BYTES:
            raise ValueError("Protected account data has an invalid size")
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(_DataBlob),
                             wintypes.LPCWSTR if protect else ctypes.c_void_p,
                             ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                             wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        function.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        buffer = ctypes.create_string_buffer(data)
        incoming = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = _DataBlob()
        entropy_buffer = ctypes.create_string_buffer(b"ReduxMediaLibrary/scraper-credentials/v1")
        entropy = _DataBlob(len(entropy_buffer) - 1,
                            ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        try:
            description = "Redux artwork accounts" if protect else None
            # CRYPTPROTECT_UI_FORBIDDEN. Do not use CRYPTPROTECT_LOCAL_MACHINE.
            if not function(ctypes.byref(incoming), description, ctypes.byref(entropy),
                            None, None, 1, ctypes.byref(outgoing)):
                raise OSError("Windows could not protect the artwork account" if protect else
                              "Saved artwork accounts could not be opened for this Windows user")
            if not outgoing.pbData or not 1 <= outgoing.cbData <= _MAX_SECRET_BYTES:
                raise OSError("Windows returned invalid protected account data")
            return ctypes.string_at(outgoing.pbData, outgoing.cbData)
        finally:
            ctypes.memset(buffer, 0, len(buffer))
            if outgoing.pbData:
                if outgoing.cbData <= _MAX_SECRET_BYTES:
                    ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
                kernel.LocalFree(outgoing.pbData)

    def protect(self, data):
        return self._transform(data, True)

    def unprotect(self, data):
        return self._transform(data, False)


class ScraperSettings:
    """Selections move with the library; account credentials stay with the user.

    Reads are lazy. Invalid existing files are reported, not silently replaced.
    ``status`` returns presence labels only; call ``credentials`` only for a
    provider request or a deliberately masked account editor.
    """
    def __init__(self, library_root, user_data_root=None, *, protector=None):
        self.library_root = _root(library_root)
        self.user_data_root = _root(user_data_root if user_data_root is not None else redux_user_data())
        if self.user_data_root.is_relative_to(self.library_root):
            raise ValueError("Artwork account storage must be outside the portable library")
        self.path = self.library_root / "settings" / "scraper.json"
        self.config_path = self.path
        self.credentials_path = self.user_data_root / "scraper-credentials.dpapi"
        self.protector = protector if protector is not None else WindowsDataProtector()
        self._settings_lock = _path_lock(self.path)
        self._secrets_lock = _path_lock(self.credentials_path)

    def _guard(self, path):
        _check_chain(path)
        root = self.library_root if path == self.path or path.parent == self.path.parent else self.user_data_root
        if not path.resolve().is_relative_to(root):
            raise ValueError("Scraper storage resolves outside its data folder")
        if path.exists() and not path.is_file():
            raise ValueError("Scraper settings are not a regular file")

    def _read(self, path, limit):
        self._guard(path)
        try:
            with path.open("rb") as source:
                result = source.read(limit + 1)
        except FileNotFoundError:
            return None
        self._guard(path)
        if len(result) > limit:
            raise ValueError("Saved scraper settings exceed the size limit")
        return result

    def _write(self, path, data, original, limit):
        if len(data) > limit:
            raise ValueError("Updated scraper settings exceed the size limit")
        self._guard(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / (".scraper-" + uuid.uuid4().hex + ".tmp")
        self._guard(temporary)
        try:
            with temporary.open("xb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            self._guard(temporary)
            if self._read(path, limit) != original:
                raise RuntimeError("Artwork settings changed during this update; try again")
            os.replace(temporary, path)
            self._guard(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _decode_options(raw):
        if raw is None:
            return _options({})
        document = _json(raw, "Saved artwork settings are invalid")
        if (set(document) != {"schema", "options"} or type(document["schema"]) is not int
                or document["schema"] != 1):
            raise ValueError("Saved artwork settings use an unsupported format")
        return _options(document["options"])

    def load(self):
        with self._settings_lock:
            return self._decode_options(self._read(self.path, _MAX_CONFIG_BYTES))

    def save(self, options):
        # Validate the supplied keys before even reading/creating a directory.
        if not isinstance(options, dict) or set(options) - set(DEFAULTS):
            raise ValueError("Scraper options contain an unsupported setting")
        with self._settings_lock:
            original = self._read(self.path, _MAX_CONFIG_BYTES)
            values = self._decode_options(original)
            values.update(options)
            values = _options(values)
            data = (json.dumps({"schema": 1, "options": values}, indent=2) + "\n").encode("utf-8")
            self._write(self.path, data, original, _MAX_CONFIG_BYTES)
            return values

    def _decode_credentials(self, raw):
        if raw is None:
            return {}
        if not raw.startswith(_MAGIC) or len(raw) <= len(_MAGIC):
            raise ValueError("Saved artwork accounts use an unsupported format")
        try:
            plain = self.protector.unprotect(raw[len(_MAGIC):])
        except Exception:
            raise OSError("Saved artwork accounts could not be opened for this Windows user; clear and enter them again") from None
        if not isinstance(plain, bytes) or len(plain) > _MAX_PLAIN_BYTES:
            raise ValueError("Saved artwork accounts are invalid")
        document = _json(plain, "Saved artwork accounts are invalid")
        if (set(document) != {"schema", "accounts"} or type(document["schema"]) is not int
                or document["schema"] != 1 or not isinstance(document["accounts"], dict)):
            raise ValueError("Saved artwork accounts use an unsupported format")
        accounts = document["accounts"]
        for provider, values in accounts.items():
            _provider(provider)
            if not isinstance(values, dict):
                raise ValueError("Saved artwork accounts are invalid")
            for field, value in values.items():
                _credential_field(provider, field)
                _credential_value(value)
        return accounts

    def credentials(self, provider):
        _provider(provider)
        with self._secrets_lock:
            accounts = self._decode_credentials(self._read(self.credentials_path, _MAX_SECRET_BYTES))
            return dict(accounts.get(provider, {}))

    def status(self, provider):
        values = self.credentials(provider)
        return {field: "Saved" if values.get(field) else "Not set" for field in PROVIDER_FIELDS[provider]}

    def has_credentials(self, provider):
        return bool(self.credentials(provider))

    def _save_accounts(self, accounts, original):
        plain = json.dumps({"schema": 1, "accounts": accounts}, ensure_ascii=False).encode("utf-8")
        if len(plain) > _MAX_PLAIN_BYTES:
            raise ValueError("Artwork accounts exceed the size limit")
        try:
            encrypted = self.protector.protect(plain)
        except Exception:
            raise OSError("Windows protected account storage is unavailable; no account was saved") from None
        if not isinstance(encrypted, bytes) or not encrypted:
            raise OSError("Windows protected account storage did not return account data")
        self._write(self.credentials_path, _MAGIC + encrypted, original, _MAX_SECRET_BYTES)

    def set_credential(self, provider, field, value):
        _credential_field(provider, field)
        _credential_value(value)
        with self._secrets_lock:
            original = self._read(self.credentials_path, _MAX_SECRET_BYTES)
            accounts = self._decode_credentials(original)
            values = dict(accounts.get(provider, {}))
            if value:
                values[field] = value
            else:
                values.pop(field, None)
            if values:
                accounts[provider] = values
            else:
                accounts.pop(provider, None)
            if original is not None or accounts:
                self._save_accounts(accounts, original)
        return self.status(provider)

    def clear_credentials(self, provider):
        _provider(provider)
        with self._secrets_lock:
            original = self._read(self.credentials_path, _MAX_SECRET_BYTES)
            accounts = self._decode_credentials(original)
            if provider in accounts:
                accounts.pop(provider)
                self._save_accounts(accounts, original)
        return self.status(provider)

    def clear_all_credentials(self):
        """Explicit recovery when another Windows user's blob cannot be opened."""
        with self._secrets_lock:
            original = self._read(self.credentials_path, _MAX_SECRET_BYTES)
            if original is not None:
                self._save_accounts({}, original)
