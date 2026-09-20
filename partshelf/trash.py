"""Recoverable catalog deletion; project snapshots remain self-contained."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext
import os
import re
import shutil
import uuid

from .files import PartShelfError, atomic_write, canonical, contained, read_json, valid_id


def entry_path(catalog, entry_id):
    if not isinstance(entry_id, str) or not re.fullmatch(r"[a-f0-9]{32}", entry_id):
        raise PartShelfError("Choose an item from Recently deleted.")
    return contained(catalog.root, ".trash/" + entry_id)


def deleted(catalog):
    root = contained(catalog.root, ".trash")
    if not root.exists():
        return []
    entries = []
    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        path = entry_path(catalog, path.name)
        manifest = read_json(contained(path, "deletion.json"))
        # The manifest is written before moves, so an interrupted batch remains
        # discoverable and restorable. Only list folders that actually moved.
        components = [item for item in manifest["components"] if contained(path, valid_id(item["id"])).is_dir()]
        libraries = manifest.get('libraries', [])
        if components or libraries:
            entries.append({"id": path.name, "deleted_at": manifest["deleted_at"], "components": components, "libraries": libraries})
    return sorted(entries, key=lambda item: item["deleted_at"], reverse=True)


def reserved(catalog, part_id):
    return any(item["id"] == part_id for entry in deleted(catalog) for item in entry["components"])


def delete(catalog, selection, libraries=None, _locked=False):
    if not isinstance(selection, list) or (not selection and not libraries) or len(selection) > 100000:
        raise PartShelfError("Select components to delete.")
    with nullcontext() if _locked else catalog.write_lock():
        components, seen = [], set()
        for item in selection:
            if not isinstance(item, dict):
                raise PartShelfError("Select components to delete.")
            part_id = valid_id(item.get("id", ""))
            revisions = catalog.revisions(part_id)
            revision = item.get("revision")
            if not revisions or part_id in seen or isinstance(revision, bool) or revision != revisions[-1]:
                raise PartShelfError("The selection changed. Refresh the catalog and review the components again.")
            seen.add(part_id)
            name = part_id
            try:
                # Damaged components can be moved to recovery too; no contents
                # are changed, and all revisions travel with their checksums.
                name = read_json(contained(catalog.root, f"{part_id}/{revision}/component.json")).get("name") or part_id
            except PartShelfError:
                pass
            components.append({"id": part_id, "name": name, "revisions": revisions})
        entry_id = uuid.uuid4().hex
        folder = entry_path(catalog, entry_id)
        folder.mkdir(parents=True)
        manifest = {"schema": 1, "id": entry_id, "deleted_at": datetime.now(timezone.utc).isoformat(), "components": components, "libraries": libraries or []}
        atomic_write(folder / "deletion.json", canonical(manifest))
        moved = []
        try:
            for item in components:
                source, target = contained(catalog.root, item["id"]), contained(folder, item["id"])
                os.rename(source, target)
                moved.append((source, target))
        except OSError:
            for source, target in reversed(moved):
                os.rename(target, source)
            raise
        return {"deleted": len(components), "entry": entry_id}


def _load_entry(catalog, entry_id):
    folder = entry_path(catalog, entry_id)
    entry = next((item for item in deleted(catalog) if item["id"] == entry_id), None)
    if not entry:
        raise PartShelfError("This item has already been restored or is no longer in Recently deleted.")
    return folder, entry


def _write_remaining(folder, entry, remaining):
    if remaining:
        manifest = {"schema": 1, "id": entry["id"], "deleted_at": entry["deleted_at"], "components": remaining, "libraries": entry.get('libraries', [])}
        atomic_write(folder / "deletion.json", canonical(manifest))
    else:
        shutil.rmtree(folder)


def restore(catalog, entry_id, component_id=None, _locked=False):
    with nullcontext() if _locked else catalog.write_lock():
        folder, entry = _load_entry(catalog, entry_id)
        selected = entry["components"] if component_id is None else [item for item in entry["components"] if item["id"] == component_id]
        if not selected and (component_id is not None or not entry.get('libraries')):
            raise PartShelfError("Choose a component from this Recently deleted entry.")
        for item in selected:
            if contained(catalog.root, item["id"]).exists():
                raise PartShelfError(f"Cannot restore {item['id']}: that component ID already exists in the catalog.")
        libraries=catalog.libraries()
        for library in entry.get('libraries', []):
            existing=next((e for e in libraries if e['path'].casefold()==library['path'].casefold()),None)
            if existing and existing!=library:
                raise PartShelfError('A library with this name now exists in another location. Rename it before restoring this group.')
            if not existing:libraries.append(library)
        moved = []
        try:
            for item in selected:
                source, target = contained(folder, item["id"]), contained(catalog.root, item["id"])
                os.rename(source, target)
                moved.append((source, target))
            if entry.get('libraries'):
                atomic_write(catalog.libraries_path,canonical({'schema':1,'libraries':libraries}))
        except OSError:
            for source, target in reversed(moved):
                os.rename(target, source)
            raise
        restored_ids = {item["id"] for item in selected}
        _write_remaining(folder, entry, [item for item in entry["components"] if item["id"] not in restored_ids])
        return {"restored": len(moved), "components": [item["id"] for item in selected], "libraries":len(entry.get('libraries', []))}


def purge(catalog, entry_id, component_id=None):
    """Permanently remove one component or an entire trash entry."""
    with catalog.write_lock():
        folder, entry = _load_entry(catalog, entry_id)
        selected = entry["components"] if component_id is None else [item for item in entry["components"] if item["id"] == component_id]
        if not selected and (component_id is not None or not entry.get('libraries')):
            raise PartShelfError("Choose a component from this Recently deleted entry.")
        for item in selected:
            shutil.rmtree(contained(folder, item["id"]))
        removed_ids = {item["id"] for item in selected}
        _write_remaining(folder, entry, [item for item in entry["components"] if item["id"] not in removed_ids])
        return {"purged": len(selected), "components": [item["id"] for item in selected]}
