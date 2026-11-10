"""Launch a separately installed emulator with an explicit argument vector.

No emulator, ROM, firmware, metadata corpus, or vendor resource is bundled.
Templates are lists of arguments, not command-line strings or shell commands.
"""
from __future__ import annotations

import os
from pathlib import Path
import string
import subprocess
from typing import Sequence

_FORMATTER = string.Formatter()
_FIELDS = frozenset({"game", "game_dir"})


def _file(path: str | os.PathLike[str], label: str) -> Path:
    if not isinstance(path, (str, os.PathLike)):
        raise TypeError(f"{label} must be a filesystem path")
    if not os.fspath(path) or "\0" in os.fspath(path):
        raise ValueError(f"{label} is empty or contains a NUL character")
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file: {resolved}")
    return resolved


def build_command(
    executable: str | os.PathLike[str],
    args_template: Sequence[str],
    game_path: str | os.PathLike[str],
) -> list[str]:
    """Validate files and substitute {game}/{game_dir} into separate arguments.

    Include ``{game}`` at least once. Literal braces can be written as ``{{`` and
    ``}}``. Attribute access, conversions, format specifications, and additional
    fields are intentionally unsupported. Shell metacharacters stay literal.
    """
    program = _file(executable, "Emulator executable")
    game = _file(game_path, "Game image")
    if os.name == "nt" and program.suffix.casefold() not in {".exe", ".com"}:
        raise ValueError("Choose a Windows executable (.exe or .com), not a shell script")
    if os.name != "nt" and not os.access(program, os.X_OK):
        raise ValueError(f"Emulator executable is not executable: {program}")
    if isinstance(args_template, (str, bytes)) or not isinstance(args_template, Sequence):
        raise TypeError("Argument template must be a list of separate strings")
    values = {"game": str(game), "game_dir": str(game.parent)}
    argv = [str(program)]
    has_game = False
    for argument in args_template:
        if not isinstance(argument, str):
            raise TypeError("Every emulator argument must be a string")
        if "\0" in argument:
            raise ValueError("Emulator arguments cannot contain NUL characters")
        try:
            fields = list(_FORMATTER.parse(argument))
        except ValueError as error:
            raise ValueError(f"Malformed argument template: {argument!r}") from error
        for _, name, specification, conversion in fields:
            if name is None:
                continue
            if name not in _FIELDS or specification or conversion:
                raise ValueError("Only plain {game} and {game_dir} placeholders are supported")
            has_game |= name == "game"
        argv.append(argument.format_map(values))
    if not has_game:
        raise ValueError("Argument template must include {game}")
    return argv


def launch_game(
    executable: str | os.PathLike[str],
    args_template: Sequence[str],
    game_path: str | os.PathLike[str],
    cwd: str | os.PathLike[str] | None = None,
) -> subprocess.Popen:
    """Start the configured emulator with shell=False and return its process.

    The default working directory is the emulator's folder. Supply another
    existing directory for emulators whose configuration requires it. This does
    not determine whether an emulator supports the selected image or system.
    """
    argv = build_command(executable, args_template, game_path)
    working_directory = Path(argv[0]).parent if cwd is None else Path(cwd).expanduser().resolve(strict=True)
    if not working_directory.is_dir():
        raise ValueError(f"Working directory must be a directory: {working_directory}")
    return subprocess.Popen(argv, cwd=str(working_directory), shell=False)
