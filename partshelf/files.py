"""Bounded local file I/O and portable bundle handling."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile

from .progress import report


class PartShelfError(ValueError):
    """An actionable input or integrity error, safe to display to the user."""


MAX_BYTES = 128 * 1024 * 1024
MAX_FILES = 10000


def canonical(data) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def valid_id(part_id: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", part_id) or ".." in part_id or part_id.endswith("."):
        raise PartShelfError("Component ID must use lowercase letters, numbers, dots, underscores or hyphens (up to 80 characters).")
    if part_id.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise PartShelfError("Component ID is reserved on Windows.")
    return part_id


def relative_name(name: str) -> str:
    if not isinstance(name, str) or "\\" in name or "\x00" in name or ":" in name:
        raise PartShelfError("Unsafe or non-portable file path")
    p = PurePosixPath(name)
    if p.is_absolute() or any(item in {"..", ".", ""} for item in name.split("/")):
        raise PartShelfError("Files must have safe relative paths")
    return p.as_posix()


def contained(root: Path, name: str) -> Path:
    name = relative_name(name)
    root = root.resolve()
    target = root / name
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise PartShelfError(f"Refusing to follow a symlink: {parent.name}")
    if not target.resolve().is_relative_to(root):
        raise PartShelfError("File path escapes its folder")
    return target


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise PartShelfError(f"Refusing to replace a symlink: {path}")
    fd, temporary = tempfile.mkstemp(prefix=".partshelf-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PartShelfError(f"Cannot read {path.name}: {exc}") from exc


def inventory(files: dict[str, bytes]) -> dict[str, str]:
    if hasattr(files, 'checksums'):
        return files.checksums()
    return {name: sha(data) for name, data in sorted(files.items())}


def digest(files: dict[str, bytes]) -> str:
    return sha(canonical(inventory(files)))


def load_source(source: Path) -> dict[str, bytes]:
    if source.is_symlink():
        raise PartShelfError("Select a real folder or ZIP file, rather than a symlink.")
    files = {}
    names_seen = set()
    total = 0
    if source.is_file() and zipfile.is_zipfile(source):
        report("unpack", "Opening archive…")
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES:
                raise PartShelfError("This archive contains too many files (limit: 10,000).")
            file_count = sum(not item.is_dir() for item in entries)
            report("unpack", "Unpacking library…", 0, file_count, "files")
            for item in entries:
                name = relative_name(item.filename.rstrip("/"))
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise PartShelfError("ZIP files containing symlinks are not supported.")
                if item.is_dir():
                    continue
                total += item.file_size
                if total > MAX_BYTES:
                    raise PartShelfError("Bundle exceeds the 128 MB uncompressed limit.")
                if name.casefold() in names_seen:
                    raise PartShelfError("Bundle contains duplicate or case-conflicting filenames.")
                names_seen.add(name.casefold())
                files[name] = archive.read(item)
                report("unpack", "Unpacking library…", len(files), file_count, "files", name)
    elif source.is_dir():
        report("scan", "Finding library files…", current=source.name)
        paths = []
        for parent, dirs, names in os.walk(source, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__MACOSX")
            for directory in dirs:
                if (Path(parent) / directory).is_symlink():
                    raise PartShelfError("Source folders containing symlinks are not supported.")
            for name in sorted(names):
                if name.startswith("."):
                    continue
                path = Path(parent) / name
                if path.is_symlink() or not path.is_file():
                    raise PartShelfError(f"Source contains a non-regular file: {name}")
                total += path.stat().st_size
                if total > MAX_BYTES or len(paths) >= MAX_FILES:
                    raise PartShelfError("Bundle exceeds the 128 MB / 10,000 file limit.")
                relative = relative_name(path.relative_to(source).as_posix())
                if relative.casefold() in names_seen:
                    raise PartShelfError("Bundle contains case-conflicting filenames.")
                names_seen.add(relative.casefold())
                paths.append((relative, path))
        report("read", "Reading library files…", 0, len(paths), "files")
        for relative, path in paths:
            files[relative] = path.read_bytes()
            report("read", "Reading library files…", len(files), len(paths), "files", relative)
    elif source.is_file() and source.suffix.lower() in {".brd", ".sch", ".lbr", ".xml", ".schlib", ".pcblib", ".intlib", ".kicad_sym", ".kicad_mod", ".lib"}:
        size = source.stat().st_size
        if size > MAX_BYTES:
            raise PartShelfError("Library exceeds the 128 MB limit.")
        report("read", "Reading library file…", 0, size, "bytes", source.name)
        files[source.name] = source.read_bytes()
        report("read", "Reading library file…", len(files[source.name]), size, "bytes", source.name)
    else:
        raise PartShelfError("Select a folder or ZIP containing modern KiCad libraries.")
    return files
