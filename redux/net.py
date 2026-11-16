"""HTTPS for Redux's downloads (cores, BigPEmu, database art, update checks).

Python's own TLS on Windows trusts only the roots already sitting in the Windows
certificate store. A fresh Windows install ships with few of them and fetches the
rest on demand, but only when a program verifies through Windows itself, so on a
new PC Redux failed with CERTIFICATE_VERIFY_FAILED until a browser had happened to
pull the right root in. truststore verifies the server through the Windows
certificate APIs, which fetch a missing root the way a browser does.
"""
from __future__ import annotations

import ssl
import threading
from urllib.request import HTTPSHandler, Request, build_opener

_lock = threading.Lock()
_context = None


def ssl_context():
    """One shared client context: Windows verification when truststore is present."""
    global _context
    with _lock:
        if _context is None:
            try:
                import truststore
                _context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except Exception:
                _context = ssl.create_default_context()
        return _context


def opener(*handlers):
    """urllib opener that verifies HTTPS through ssl_context(), plus any redirect handlers."""
    return build_opener(HTTPSHandler(context=ssl_context()), *handlers)


def urlopen(url, *, headers=None, timeout=30):
    request = url if isinstance(url, Request) else Request(url, headers=headers or {})
    return opener().open(request, timeout=timeout)


def friendly_error(message):
    """Say what to check when a download fails on certificates rather than echo OpenSSL."""
    text = str(message)
    if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text:
        return ("Windows could not verify the download server's certificate. Check that this PC's "
                "date and time are correct and that it is connected to the internet, then try again.\n\n" + text)
    return text
