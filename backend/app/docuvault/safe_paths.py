"""Portable trusted-root path checks.

Adapted from the detached DocuVault R1 export's ``paths.py``.  The application
keeps the same forward-slash, no-link, no-traversal policy while narrowing
artifact access to explicit local roots.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafeProfilePath(ValueError):
    """Raised when a manifest path can escape or alias its trusted root."""


_REPARSE_POINT = 0x400
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(status.st_mode) or bool(
        getattr(status, "st_file_attributes", 0) & _REPARSE_POINT
    )


def portable_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or value != value.strip():
        raise UnsafeProfilePath("path must be a non-empty trimmed string")
    if "\x00" in value or "\\" in value or value.startswith("/"):
        raise UnsafeProfilePath("absolute, UNC, backslash, and NUL paths are forbidden")
    windows = PureWindowsPath(value)
    if windows.drive or windows.is_absolute():
        raise UnsafeProfilePath("drive-qualified paths are forbidden")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise UnsafeProfilePath("empty, dot, and traversal components are forbidden")
    for part in parts:
        if not part.isascii() or any(
            not (character.isalnum() or character in "._-") for character in part
        ):
            raise UnsafeProfilePath("path components must use portable ASCII characters")
        if part.endswith((".", " ")) or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED:
            raise UnsafeProfilePath("non-portable Windows path component")
    return PurePosixPath(value)


def safe_path(
    root: Path,
    relative_path: str,
    *,
    allowed_prefixes: tuple[str, ...] = (),
    must_exist: bool = True,
) -> Path:
    portable = portable_relative_path(relative_path)
    if allowed_prefixes and not any(
        portable.parts[: len(PurePosixPath(prefix).parts)] == PurePosixPath(prefix).parts
        for prefix in allowed_prefixes
    ):
        raise UnsafeProfilePath("path is outside the allowed profile artifact prefixes")
    resolved_root = root.resolve(strict=True)
    lexical = resolved_root.joinpath(*portable.parts)
    resolved = lexical.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise UnsafeProfilePath("path escapes the trusted root") from exc
    cursor = resolved_root
    for part in portable.parts:
        cursor = cursor / part
        if _is_link_or_reparse(cursor):
            raise UnsafeProfilePath("links and reparse points are forbidden")
        if not cursor.exists():
            break
    if must_exist and not lexical.is_file():
        raise UnsafeProfilePath("profile artifact does not exist")
    return lexical
