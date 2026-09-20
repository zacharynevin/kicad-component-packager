"""Project-local snapshots, explicit revision updates, and KiCad library tables."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import uuid
import zipfile

from . import sexpr as sx
from .components import Catalog, properties, set_property
from .files import PartShelfError, atomic_write, canonical, contained, digest, inventory, read_json, sha, valid_id

MANIFEST = "partshelf.json"
LOCKFILE = "partshelf.lock.json"


def root_path(path: Path) -> Path:
    path = path.expanduser()
    return path.parent.resolve() if path.suffix == ".kicad_pro" else path.resolve()


@contextmanager
def project_lock(root):
    path = contained(root, ".partshelf/operation.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PartShelfError("Another component operation is using this project. If a previous process crashed, remove .partshelf/operation.lock after ensuring it has stopped.") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def load_state(root):
    manifest_file, lock_file = contained(root, MANIFEST), contained(root, LOCKFILE)
    if not manifest_file.exists() and not lock_file.exists():
        return {"schema": 1, "components": {}}, {"schema": 1, "components": {}, "packages": {}, "generated": {}}
    if not manifest_file.exists() or not lock_file.exists():
        raise PartShelfError("The project needs both partshelf.json and partshelf.lock.json. Restore the missing file from your project backup.")
    manifest, lock = read_json(manifest_file), read_json(lock_file)
    if not isinstance(manifest, dict) or not isinstance(lock, dict) or manifest.get("schema") != 1 or lock.get("schema") != 1:
        raise PartShelfError("Unsupported project manifest or lockfile schema.")
    if manifest.get("components") != lock.get("components"):
        raise PartShelfError("The manifest and lockfile disagree. Use KiCad Component Packager to change component revisions, or restore both files from the same backup.")
    for part_id, selected in lock["components"].items():
        valid_id(part_id)
        key = f"{part_id}@{selected['revision']}"
        if key not in lock["packages"] or lock["packages"][key]["digest"] != selected["digest"]:
            raise PartShelfError("The lockfile has an inconsistent component reference.")
    return manifest, lock


def project_digest(root):
    names = [MANIFEST, LOCKFILE, "sym-lib-table", "fp-lib-table"]
    return sha(canonical({name: sha(contained(root, name).read_bytes()) if contained(root, name).is_file() else None for name in names}))


def initialize(path: Path, create_design=False):
    root = root_path(path)
    root.mkdir(parents=True, exist_ok=True)
    with project_lock(root):
        manifest, lock = load_state(root)
        writes = {}
        if not (root / MANIFEST).exists():
            writes.update({MANIFEST: canonical(manifest), LOCKFILE: canonical(lock)})
        if create_design and not list(root.glob("*.kicad_pro")):
            name = root.name
            if any((root / (name + ext)).exists() for ext in (".kicad_sch", ".kicad_pcb")):
                raise PartShelfError("A schematic or board already exists here. Create the KiCad project in KiCad, then select its folder.")
            writes[name + ".kicad_pro"] = canonical({"meta": {"filename": name + ".kicad_pro", "version": 1}, "text_variables": {}})
            writes[name + ".kicad_sch"] = f'(kicad_sch (version 20250114) (generator "partshelf") (uuid "{uuid.uuid4()}") (paper "A4") (lib_symbols) (embedded_fonts no))\n'.encode()
            writes[name + ".kicad_pcb"] = b'(kicad_pcb (version 20241229) (generator "partshelf") (general (thickness 1.6)) (paper "A4") (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user)) (setup (pad_to_mask_clearance 0)))\n'
        if writes:
            transaction(root, writes)
    return status(root)


def table_entry(part_id, symbol):
    name = "PS_" + part_id
    uri = "${KIPRJMOD}/.partshelf/kicad/" + part_id + (".kicad_sym" if symbol else ".pretty")
    # This description is part of the version-1 registration format checked by
    # verify(); retain it so existing projects also work in earlier releases.
    return [sx.atom("lib"), [sx.atom("name"), sx.q(name)], [sx.atom("type"), sx.q("KiCad")], [sx.atom("uri"), sx.q(uri)], [sx.atom("options"), sx.q("")], [sx.atom("descr"), sx.q("Managed by PartShelf: " + part_id)]]


def read_table(root, name):
    path = contained(root, name)
    if not path.exists():
        return [sx.atom("sym_lib_table" if name == "sym-lib-table" else "fp_lib_table"), [sx.atom("version"), sx.atom(7)]]
    try:
        tree = sx.loads(path.read_text("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise PartShelfError(f"Cannot read {name}: {exc}") from exc
    expected = "sym_lib_table" if name == "sym-lib-table" else "fp_lib_table"
    if sx.tag(tree) != expected:
        raise PartShelfError(f"Unexpected contents in {name}")
    return tree


def update_table(root, name, old_ids, new_ids):
    tree = read_table(root, name)
    previous_names = {"PS_" + part_id for part_id in old_ids}
    new_names = {"PS_" + part_id for part_id in new_ids}
    for row in sx.children(tree, "lib"):
        nickname = sx.field(row, "name")
        if nickname.casefold() in {n.casefold() for n in new_names} and nickname not in previous_names:
            raise PartShelfError(f"Library name {nickname} is already used in {name}; the existing library will not be replaced.")
    tree[:] = [row for row in tree if not (sx.tag(row) == "lib" and sx.field(row, "name") in previous_names)]
    tree.extend(table_entry(part_id, name == "sym-lib-table") for part_id in sorted(new_ids))
    return sx.encode(tree)


def snapshot(root, record):
    files = {}
    for name, expected in record["files"].items():
        path = contained(root, record["path"] + "/" + name)
        if not path.is_file():
            raise PartShelfError(f"Missing project component asset: {record['path']}/{name}")
        data = path.read_bytes()
        if sha(data) != expected:
            raise PartShelfError(f"Project component asset was modified: {record['path']}/{name}")
        files[name] = data
    if digest(files) != record["digest"]:
        raise PartShelfError("Component snapshot checksum does not match the lockfile.")
    return files


def projection(part_id, revision, metadata, files):
    prefix = f"${{KIPRJMOD}}/.partshelf/components/{part_id}/{revision}/"
    symbol = sx.loads(files[metadata["assets"]["symbol"]].decode())
    for definition in sx.children(symbol, "symbol"):
        set_property(definition, "Footprint", f"PS_{part_id}:Part")
        set_property(definition, "PartShelf Revision", str(revision))
        sheet = properties(definition).get("Datasheet", "")
        if sheet in metadata["assets"]["documents"]:
            set_property(definition, "Datasheet", prefix + sheet)
    footprint = sx.loads(files[metadata["assets"]["footprint"]].decode())
    for model in sx.children(footprint, "model"):
        model_name = sx.value(model[1])
        if model_name not in metadata["assets"]["models"]:
            raise PartShelfError("Component contains an untracked 3D model reference.")
        model[1] = sx.q(prefix + model_name)
    return {f".partshelf/kicad/{part_id}.kicad_sym": sx.encode(symbol), f".partshelf/kicad/{part_id}.pretty/Part.kicad_mod": sx.encode(footprint)}


def verify(path: Path):
    root = root_path(path)
    manifest, lock = load_state(root)
    errors, components = [], []
    for key, record in lock["packages"].items():
        try:
            snapshot(root, record)
        except PartShelfError as exc:
            errors.append(str(exc))
    for name, expected in lock.get("generated", {}).items():
        path = contained(root, name)
        if not path.exists():
            errors.append(f"Missing generated library: {name}")
        elif sha(path.read_bytes()) != expected:
            errors.append(f"Generated library was modified: {name}")
    for part_id, selected in lock["components"].items():
        record = lock["packages"][f"{part_id}@{selected['revision']}"]
        try:
            meta = json.loads(snapshot(root, record)["component.json"])
            components.append({**meta, "digest": record["digest"]})
        except (PartShelfError, ValueError, KeyError):
            components.append({"id": part_id, **selected, "name": part_id, "error": "Component snapshot is incomplete or modified."})
        for name in ("sym-lib-table", "fp-lib-table"):
            try:
                entries = [row for row in sx.children(read_table(root, name), "lib") if sx.field(row, "name") == "PS_" + part_id]
                if len(entries) != 1 or entries[0] != table_entry(part_id, name == "sym-lib-table"):
                    errors.append(f"Missing or modified registration for {part_id} in {name}")
            except PartShelfError as exc:
                errors.append(str(exc))
    unmanaged = []
    for name in ("sym-lib-table", "fp-lib-table"):
        for row in sx.children(read_table(root, name), "lib"):
            if sx.field(row, "name") not in {"PS_" + p for p in lock["components"]}:
                unmanaged.append({"table": name, "name": sx.field(row, "name"), "uri": sx.field(row, "uri")})
    return {"ok": not errors, "errors": sorted(set(errors)), "components": components, "unmanaged_libraries": unmanaged, "archived_revisions": len(lock["packages"]) - len(lock["components"])}


def status(path):
    root = root_path(path)
    return {"path": str(root), "name": root.name, "initialized": (root / MANIFEST).exists(), "state_digest": project_digest(root), **verify(root)}


def plan(path, catalog: Catalog, part_id, revision):
    root = root_path(path)
    _, lock = load_state(root)
    meta, files, integrity = catalog.read(part_id, revision)
    if meta.get('kind') == 'library_entry':
        raise PartShelfError('Export native library entries as a PCM ZIP and install them with KiCad PCM.')
    previous = lock["components"].get(part_id)
    changes = []
    if previous:
        record = lock["packages"][f"{part_id}@{previous['revision']}"]
        old_files = snapshot(root, record)
        old = json.loads(old_files["component.json"])
        for key in ("name", "description", "manufacturer", "mpn", "category", "pins", "pads", "datasheet", "website", "notes"):
            if old.get(key) != meta.get(key):
                changes.append({"field": key, "before": old.get(key), "after": meta.get(key)})
        for key, label in (("symbol.kicad_sym", "Symbol"), ("footprints/Part.kicad_mod", "Footprint")):
            if old_files.get(key) != files.get(key):
                changes.append({"field": label, "before": "Previous asset", "after": "Asset changed"})
        for folder, label in (("models/", "3D models"), ("documents/", "Documents")):
            old_assets = {k: sha(v) for k, v in old_files.items() if k.startswith(folder)}
            new_assets = {k: sha(v) for k, v in files.items() if k.startswith(folder)}
            if old_assets != new_assets:
                changes.append({"field": label, "before": len(old_assets), "after": len(new_assets)})
    return {"id": part_id, "name": meta["name"], "revision": revision, "previous_revision": previous["revision"] if previous else None, "action": "unchanged" if previous and previous["digest"] == integrity["digest"] else ("update" if previous else "add"), "changes": changes, "state_digest": project_digest(root), "package_digest": integrity["digest"]}


def install(path, catalog, part_id, revision, expected_state=None, expected_package=None):
    root = root_path(path)
    root.mkdir(parents=True, exist_ok=True)
    with project_lock(root):
        if expected_state and project_digest(root) != expected_state:
            raise PartShelfError("The project changed after the preview. Review the update again.")
        health = verify(root)
        if not health["ok"]:
            raise PartShelfError("Repair this project's managed libraries first: " + "; ".join(health["errors"][:3]))
        manifest, lock = load_state(root)
        meta, files, integrity = catalog.read(part_id, revision)
        if expected_package and integrity["digest"] != expected_package:
            raise PartShelfError("The component changed after the preview.")
        key = f"{part_id}@{revision}"
        if key in lock["packages"] and lock["packages"][key]["digest"] != integrity["digest"]:
            raise PartShelfError("This component revision already has different content in the project.")
        new_manifest, new_lock = copy.deepcopy(manifest), copy.deepcopy(lock)
        chosen = {"revision": revision, "digest": integrity["digest"]}
        new_manifest["components"][part_id] = chosen
        new_lock["components"][part_id] = chosen
        package_path = f".partshelf/components/{part_id}/{revision}"
        new_lock["packages"][key] = {"path": package_path, **integrity}
        writes = {package_path + "/" + name: data for name, data in files.items()}
        generated = projection(part_id, revision, meta, files)
        new_lock["generated"].update(inventory(generated))
        writes.update(generated)
        for name in ("sym-lib-table", "fp-lib-table"):
            writes[name] = update_table(root, name, lock["components"], new_lock["components"])
        writes.update({MANIFEST: canonical(new_manifest), LOCKFILE: canonical(new_lock)})
        # Refuse to overwrite unowned files, even under the managed directory.
        known = set(lock["generated"])
        for record in lock["packages"].values():
            known.update(record["path"] + "/" + name for name in record["files"])
        for name in writes:
            if name.startswith(".partshelf/") and contained(root, name).exists() and name not in known:
                raise PartShelfError(f"An unmanaged file occupies {name}; it will not be overwritten.")
        backup = transaction(root, writes)
    return {"project": status(root), "backup": backup}


def restore(path, catalog=None, repair_modified=False):
    root = root_path(path)
    with project_lock(root):
        manifest, lock = load_state(root)
        if not (root / LOCKFILE).exists():
            raise PartShelfError("This project has no lockfile to restore.")
        writes, resolved = {}, {}
        for key, record in lock["packages"].items():
            try:
                resolved[key] = snapshot(root, record)
            except PartShelfError:
                if not catalog:
                    raise
                part_id, revision_text = key.rsplit("@", 1)
                meta, files, integrity = catalog.read(part_id, int(revision_text))
                if integrity["digest"] != record["digest"]:
                    raise PartShelfError("The catalog revision differs from the locked snapshot.")
                for name, data in files.items():
                    dest = contained(root, record["path"] + "/" + name)
                    if dest.exists() and sha(dest.read_bytes()) != record["files"][name] and not repair_modified:
                        raise PartShelfError("A snapshot was edited. Use --repair-modified to replace it with the locked catalog copy; a backup is retained.")
                    writes[record["path"] + "/" + name] = data
                resolved[key] = files
        for part_id, selected in lock["components"].items():
            files = resolved[f"{part_id}@{selected['revision']}"]
            meta = json.loads(files["component.json"])
            generated = projection(part_id, selected["revision"], meta, files)
            for name, data in generated.items():
                dest = contained(root, name)
                if dest.exists() and sha(dest.read_bytes()) != sha(data) and not repair_modified:
                    raise PartShelfError("A generated library was edited. Import those edits as a new revision, or use --repair-modified to replace it (with a backup).")
            writes.update(generated)
        for name in ("sym-lib-table", "fp-lib-table"):
            writes[name] = update_table(root, name, lock["components"], lock["components"])
        backup = transaction(root, writes)
    return {"project": status(root), "backup": backup}


def transaction(root, writes):
    """Atomic replacement per file, with a recoverable backup and error rollback."""
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    history = contained(root, ".partshelf/history/" + token)
    old, changed = {}, []
    for name, data in writes.items():
        path = contained(root, name)
        before = path.read_bytes() if path.exists() else None
        if before != data:
            old[name] = before
    if not old:
        return None
    history.mkdir(parents=True)
    record = {"state": "prepared", "files": {name: {"before": sha(data) if data is not None else None, "after": sha(writes[name])} for name, data in old.items()}}
    for name, data in old.items():
        if data is not None:
            backup = contained(history, "before/" + name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(data)
    (history / "transaction.json").write_bytes(canonical(record))
    try:
        for name in old:
            atomic_write(contained(root, name), writes[name])
            changed.append(name)
    except Exception:
        for name in reversed(changed):
            path = contained(root, name)
            if old[name] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, old[name])
        record["state"] = "rolled_back"
        (history / "transaction.json").write_bytes(canonical(record))
        raise
    record["state"] = "committed"
    (history / "transaction.json").write_bytes(canonical(record))
    return str(history.relative_to(root))


def export_project(path):
    root = root_path(path)
    health = verify(root)
    if not health["ok"]:
        raise PartShelfError("Repair the managed components before exporting the project.")
    output = io.BytesIO()
    total = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for directory, folders, names in os.walk(root, followlinks=False):
            folders[:] = [name for name in folders if name not in {".git", ".history", "__pycache__"} and not (Path(directory).name == ".partshelf" and name == "history")]
            for name in names:
                item = Path(directory) / name
                if item.is_symlink() or name in {".DS_Store", "operation.lock"}:
                    continue
                total += item.stat().st_size
                if total > 256 * 1024 * 1024:
                    raise PartShelfError("Project exceeds the 256 MB archive limit. Copy its folder directly instead.")
                archive.write(item, root.name + "/" + item.relative_to(root).as_posix())
    return output.getvalue()
