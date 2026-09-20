"""Editable catalog data carried inside ordinary KiCad PCM ZIP resources."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile

from .components import Catalog, validate_component
from .files import PartShelfError, atomic_write, canonical, contained, digest, sha, valid_id
from .progress import report

MANIFEST = "resources/packager/manifest.json"
OBJECTS = "resources/packager/objects/"
FORMAT = "kicad-component-packager"


def is_package(files):
    # A damaged editable package must fail validation, not silently become a
    # generic library import with new identities and missing revision history.
    return MANIFEST in files


def package_options(catalog):
    path = catalog.root / "package-options.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def export_package(catalog: Catalog, selections=None, name="Component collection") -> bytes:
    from .pcm import export_pcm
    return export_pcm(catalog, selections, {"name": name})


def add_catalog_snapshot(files, catalog, selections, options, extra_files=None):
    """Reuse installed models/documents; store each remaining byte blob once."""
    from .files import inventory
    native = inventory(files)
    locations = {}
    for path, checksum in native.items():
        locations.setdefault(checksum, path)
    objects, components = {}, []
    for part_id, selected_revision in sorted(selections):
        for revision in catalog.revisions(part_id):
            if revision > selected_revision:
                continue
            meta, payload, integrity = catalog.read(part_id, revision)
            for path, checksum in integrity["files"].items():
                if checksum not in locations:
                    locations[checksum] = OBJECTS + checksum
                    files[locations[checksum]] = payload.asset(path) if hasattr(files, 'asset') and hasattr(payload, 'asset') else payload[path]
                objects[checksum] = locations[checksum]
            components.append({"id": part_id, "revision": revision,
                               "digest": integrity["digest"], "files": integrity["files"]})
    manifest = {"format": FORMAT, "format_version": 1, "name": options["name"],
                "components": components, "selected": sorted([list(item) for item in selections]),
                "libraries": catalog.libraries(), "options": options,
                "extra_files": extra_files or {},
                "objects": objects, "pcm_files": native}
    files[MANIFEST] = canonical(manifest)
    return manifest


def inspect_package(files):
    from .storage import Payload, Asset
    try:
        manifest = json.loads(files[MANIFEST])
        if manifest.get("format") != FORMAT or manifest.get("format_version") != 1:
            raise PartShelfError("Unsupported editable PCM ZIP version.")
        pcm = json.loads(files["metadata.json"])
        if pcm.get("type") != "library":
            raise PartShelfError("This ZIP is not a KiCad library package.")
        native = manifest["pcm_files"]
        if not isinstance(native, dict) or "metadata.json" not in native:
            raise PartShelfError("Invalid PCM file index.")
        referenced = {MANIFEST}
        verified = set()
        def check_file(path, expected):
            contained(Path("/tmp/partshelf-path-check"), path)
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise PartShelfError("Invalid package checksum.")
            actual = files.asset(path).checksum if isinstance(files, Payload) and isinstance(files.asset(path), Asset) else sha(files[path])
            if (path, expected) not in verified and actual != expected:
                raise PartShelfError(f"Package contains a damaged asset: {path}")
            verified.add((path, expected)); referenced.add(path)
        for path, expected in native.items():
            if path == MANIFEST or path.startswith(OBJECTS):
                raise PartShelfError("Invalid PCM file index.")
            check_file(path, expected)
        extras = manifest.get('extra_files', {})
        if not isinstance(extras, dict): raise PartShelfError('Invalid native resources index.')
        for relative, path in extras.items():
            contained(Path('/tmp/partshelf-path-check'), relative)
            if not re.fullmatch(r'[a-f0-9]{20}', relative.split('/')[0]) or path != 'resources/packager/native/' + relative or path not in native:
                raise PartShelfError('Invalid native resource path.')
        objects = manifest["objects"]
        if not isinstance(objects, dict):
            raise PartShelfError("Invalid package object index.")
        for checksum, path in objects.items():
            if path not in native and path != OBJECTS + checksum:
                raise PartShelfError("Invalid package object location.")
            check_file(path, checksum)
        entries = manifest.get("components")
        libraries = manifest.get('libraries', [])
        if not isinstance(libraries, list) or len(libraries) > 10000:
            raise PartShelfError('Invalid library tree in package.')
        paths = set()
        for item in libraries:
            if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or len(item[key]) > 200 for key in ('name', 'path', 'parent')) or not item['name'] or not item['path'] or item['path'].casefold() in paths:
                raise PartShelfError('Invalid or duplicate library in package.')
            paths.add(item['path'].casefold())
        by_path = {item['path']: item for item in libraries}
        for item in libraries:
            visited = {item['path']}; parent = item['parent']
            while parent:
                if parent not in by_path or parent in visited:
                    raise PartShelfError('Invalid library hierarchy in package.')
                visited.add(parent); parent = by_path[parent]['parent']
        if not isinstance(entries, list) or (not entries and not libraries):
            raise PartShelfError("Package contains no components or libraries.")
        if not isinstance(manifest.get('options'), dict):
            raise PartShelfError('Invalid saved package properties.')
        result, revisions, used_objects = [], set(), set()
        report("check-package", "Checking saved revisions…", 0, len(entries), "revisions")
        for entry in entries:
            part_id, revision = valid_id(entry["id"]), entry["revision"]
            if type(revision) is not int or revision < 1 or (part_id, revision) in revisions:
                raise PartShelfError("Duplicate component or invalid revision in package.")
            revisions.add((part_id, revision))
            payload = Payload()
            if not isinstance(entry["files"], dict):
                raise PartShelfError("Invalid package file index.")
            for path, expected in entry["files"].items():
                contained(Path("/tmp/partshelf-path-check"), path)
                if path.casefold() in {p.casefold() for p in payload}:
                    raise PartShelfError("Component contains case-conflicting paths.")
                payload[path] = files.asset(objects[expected]) if isinstance(files, Payload) else files[objects[expected]]
                used_objects.add(expected)
            if digest(payload) != entry["digest"]:
                raise PartShelfError("Component content does not match its package checksum.")
            meta = json.loads(payload["component.json"])
            if meta.get("schema") != 1 or meta.get("id") != part_id or meta.get("revision") != revision:
                raise PartShelfError("Component identity does not match the package index.")
            assets = meta["assets"]
            for path in filter(None, [assets["symbol"], assets["footprint"], *assets["models"], *assets["documents"]]):
                if path not in payload:
                    raise PartShelfError(f"A declared component asset is missing: {path}")
            validate_component(meta, payload)
            result.append((meta, payload, {"digest": entry["digest"], "files": entry["files"]}))
            report("check-package", "Checking saved revisions…", len(result), len(entries), "revisions", meta["name"])
        selected = manifest['selected']
        latest = {}
        for part_id, revision in revisions:
            latest[part_id] = max(revision, latest.get(part_id, 0))
        if selected != sorted([list(item) for item in latest.items()]):
            raise PartShelfError('The selected revisions do not match the saved history.')
        if used_objects != set(objects) or set(files) != referenced:
            raise PartShelfError("Package contains unindexed files.")
        return manifest, result
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError, UnicodeError) as exc:
        raise PartShelfError("Invalid or incomplete editable PCM ZIP.") from exc


def import_package(catalog: Catalog, files):
    manifest, items = inspect_package(files)
    with catalog.write_lock():
        return _import_items(catalog, manifest, items, files)


def _import_items(catalog, manifest, items, archive_files=None):
    libraries = {item['path']: item for item in catalog.libraries()}
    for item in manifest.get('libraries', []):
        if any(path.casefold() == item['path'].casefold() and path != item['path'] for path in libraries):
            raise PartShelfError('A library with different capitalization already exists. Rename it before importing.')
        if item['path'] in libraries and libraries[item['path']]['parent'] != item['parent']:
            raise PartShelfError(f"Library {item['path']} already exists under a different parent.")
        libraries[item['path']] = item
    actions = []
    report("check-revisions", "Checking existing revisions…", 0, len(items), "components")
    for index, (meta, payload, integrity) in enumerate(items, 1):
        if meta["revision"] in catalog.revisions(meta["id"]):
            _, _, existing = catalog.read(meta["id"], meta["revision"])
            if existing["digest"] != integrity["digest"]:
                raise PartShelfError(f"{meta['id']} revision {meta['revision']} already exists with different content. Revisions cannot be overwritten.")
        else:
            actions.append((meta, payload, integrity))
        report("check-revisions", "Checking existing revisions…", index, len(items), "components", meta["name"])
    catalog.root.mkdir(parents=True, exist_ok=True)
    settings_path = catalog.root / 'package-options.json'
    previous_settings = settings_path.read_bytes() if settings_path.exists() else None
    previous_libraries = catalog.libraries_path.read_bytes() if catalog.libraries_path.exists() else None
    added = []
    added_extras = []
    settings_written = libraries_written = False
    try:
        with tempfile.TemporaryDirectory(prefix=".package-import-", dir=catalog.root) as temp:
            report("import-package", "Importing saved components…", 0, len(actions), "components")
            for meta, payload, integrity in actions:
                relative = meta["id"] + "/" + str(meta["revision"])
                stage = Path(temp) / relative
                from .storage import write_payload
                write_payload(stage, payload)
                (stage / 'integrity.json').write_bytes(canonical(integrity))
                destination = contained(catalog.root, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise PartShelfError("Catalog changed during import. Please try again.")
                stage.rename(destination)
                added.append(destination)
                report("import-package", "Importing saved components…", len(added), len(actions), "components", meta["name"])
            atomic_write(settings_path, canonical(manifest['options']))
            settings_written = True
            atomic_write(catalog.libraries_path, canonical({'schema': 1, 'libraries': list(libraries.values())}))
            libraries_written = True
            for relative, source in manifest.get('extra_files', {}).items():
                target = contained(catalog.root, '.native-packages/' + relative)
                if target.exists():
                    if sha(target.read_bytes()) != manifest['pcm_files'][source]:
                        raise PartShelfError('Native package resources conflict with an existing collection.')
                    continue
                atomic_write(target, archive_files[source]); added_extras.append(target)
    except Exception:
        for path in added:
            shutil.rmtree(path)
        for path in added_extras: path.unlink()
        for path, previous, changed in ((settings_path, previous_settings, settings_written),
                                        (catalog.libraries_path, previous_libraries, libraries_written)):
            if changed:
                if previous is None:
                    path.unlink()
                else:
                    atomic_write(path, previous)
        raise
    latest = {}
    for meta, _, _ in items:
        if meta['revision'] > latest.get(meta['id'], {}).get('revision', 0):
            latest[meta['id']] = meta
    return {"name": manifest["name"], "imported": len(actions), "unchanged": len(items) - len(actions), "components": list(latest.values()), "options": manifest['options']}
