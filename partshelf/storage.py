"""Bounded, disk-backed assets for full native library collections."""
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
import hashlib
import shutil
import stat
import zipfile

from .files import PartShelfError, relative_name, sha
from .progress import report

LIBRARY_BYTES = 8 * 1024**3
LIBRARY_FILES = 300000
LIBRARY_MEMBER_BYTES = 512 * 1024**2


@dataclass(frozen=True)
class Asset:
    path: Path
    checksum: str
    size: int


class Payload(MutableMapping):
    def __init__(self, entries=None):
        self.entries = dict(entries or {})

    def __getitem__(self, key):
        value = self.entries[key]
        return value.path.read_bytes() if isinstance(value, Asset) else value

    def __setitem__(self, key, value): self.entries[key] = value
    def __delitem__(self, key): del self.entries[key]
    def __iter__(self): return iter(self.entries)
    def __len__(self): return len(self.entries)
    def checksums(self):
        return {p: v.checksum if isinstance(v, Asset) else sha(v) for p, v in sorted(self.entries.items())}
    def size(self): return sum(v.size if isinstance(v, Asset) else len(v) for v in self.entries.values())
    def copy(self): return Payload(self.entries)
    def asset(self, key): return self.entries[key]


class DiskStore(Payload):
    def __init__(self, root):
        super().__init__(); self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    def __setitem__(self, key, data):
        if isinstance(data, Asset):
            self.entries[key] = data
            return
        checksum = sha(data)
        target = self.root / checksum
        if not target.exists(): target.write_bytes(data)
        self.entries[key] = Asset(target, checksum, len(data))


def unpack_library(path, root):
    """Check paths/limits and stream every ZIP member into shared immutable files."""
    output = DiskStore(root)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > LIBRARY_FILES or sum(e.file_size for e in entries) > LIBRARY_BYTES:
            raise PartShelfError('Library exceeds the 8 GiB / 300,000 file limit.')
        if any(e.file_size > LIBRARY_MEMBER_BYTES for e in entries):
            raise PartShelfError('An individual library asset exceeds the 512 MiB limit.')
        names = set(); total = sum(e.file_size for e in entries); completed = 0
        for entry in entries:
            name = relative_name(entry.filename.rstrip('/'))
            if stat.S_ISLNK(entry.external_attr >> 16) or name.casefold() in names:
                raise PartShelfError('ZIP contains a symlink or duplicate/case-conflicting path.')
            names.add(name.casefold())
            if entry.is_dir(): continue
            scratch = output.root / '.extracting'
            checksum = hashlib.sha256(); size = 0
            with archive.open(entry) as source, scratch.open('wb') as target:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > entry.file_size: raise PartShelfError('Invalid ZIP member size.')
                    checksum.update(chunk); target.write(chunk)
            value = checksum.hexdigest(); target = output.root / value
            if target.exists(): scratch.unlink()
            else: scratch.rename(target)
            output.entries[name] = Asset(target, value, size)
            completed += size
            report('unpack-library', 'Reading library assets…', completed, total, 'bytes', name)
    return output


def write_payload(root, payload):
    """Hard-link shared immutable assets, falling back to a copy across volumes."""
    from .files import contained
    import os
    for name in payload:
        target = contained(root, name); target.parent.mkdir(parents=True, exist_ok=True)
        value = payload.asset(name) if isinstance(payload, Payload) else payload[name]
        if isinstance(value, Asset):
            try: os.link(value.path, target)
            except OSError: shutil.copyfile(value.path, target)
        else: target.write_bytes(value)
