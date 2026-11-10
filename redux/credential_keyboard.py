"""Pure controller-keyboard layouts and edits; credentials never become labels.

The three pages expose every printable ASCII character. Space is a separate
control so a blank key never becomes an ambiguous focus target. The UI owns
focus, page buttons, physical typing and save/cancel actions.
"""
from __future__ import annotations


PAGES = ("lower", "upper", "symbols")
CONTROL_KEYS = ("SPACE", "BACKSPACE", "CLEAR")
MAX_LENGTH = 4096
_ROWS = {
    "lower": ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"),
    "upper": ("1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"),
    "symbols": ("!@#$%^&*()", "-_=+[]{}\\|", ";:'\",.<>/?", "`~"),
}


def current_page(page):
    """Return a canonical page; absent/stale settings start on lowercase."""
    return page if isinstance(page, str) and page in PAGES else PAGES[0]


def keyboard_rows(page):
    """Return immutable rows, one printable character per controller cell."""
    return _ROWS[current_page(page)]


def next_page(page):
    return PAGES[(PAGES.index(current_page(page)) + 1) % len(PAGES)]


def previous_page(page):
    return PAGES[(PAGES.index(current_page(page)) - 1) % len(PAGES)]


def _value(value):
    if (not isinstance(value, str) or len(value) > MAX_LENGTH
            or any(not character.isprintable() for character in value)):
        raise ValueError("Account text must contain at most 4096 printable characters")
    return value


def edit_value(value, key, max_length=MAX_LENGTH):
    """Apply a button without exposing text in errors or exceeding its limit.

    At the length limit, another printable character leaves the value unchanged.
    A literal printable Unicode character is also accepted for physical typing;
    multi-character paste is handled by the owning editor rather than a key.
    """
    value = _value(value)
    if type(max_length) is not int or not 1 <= max_length <= MAX_LENGTH:
        raise ValueError("Account text limits must be from 1 to 4096 characters")
    if len(value) > max_length:
        raise ValueError("Account text exceeds this field's character limit")
    if key == "BACKSPACE":
        return value[:-1]
    if key == "CLEAR":
        return ""
    if key == "SPACE":
        key = " "
    if not isinstance(key, str) or len(key) != 1 or not key.isprintable():
        raise ValueError("Choose a printable account key or an editing control")
    return value + key if len(value) < max_length else value


def masked_value(value):
    """Return only bullets, preserving position/length without secret labels."""
    return "\u2022" * len(_value(value))
