"""Import, validate and revision complete KiCad components."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import posixpath
import re
import tempfile
import os

from . import sexpr as sx
from .conversion import normalize
from .files import PartShelfError, atomic_write, canonical, contained, digest, inventory, read_json, sha, valid_id
from .progress import report


def properties(node):
    return {sx.value(p[1]): sx.value(p[2]) for p in sx.children(node, "property") if len(p) >= 3}


def set_property(node, name, value):
    item = next((p for p in sx.children(node, "property") if sx.value(p[1]) == name), None)
    if item is None:
        item = [sx.atom("property"), sx.q(name), sx.q(value),
                [sx.atom("at"), sx.atom(0), sx.atom(0), sx.atom(0)],
                [sx.atom("effects"), [sx.atom("font"), [sx.atom("size"), sx.atom(1.27), sx.atom(1.27)]]],
                [sx.atom("hide"), sx.atom("yes")]]
        node.append(item)
    else:
        item[2] = sx.q(value)


def rename_symbol(node, name):
    old = sx.value(node[1])
    node[1] = sx.q(name)
    for unit in sx.children(node, "symbol"):
        unit_name = sx.value(unit[1])
        if not unit_name.startswith(old + "_"):
            raise PartShelfError(f"Unrecognized symbol unit name: {unit_name}")
        unit[1] = sx.q(name + unit_name[len(old):])


def resolve_symbol(node, symbols, seen=None):
    seen = set() if seen is None else set(seen)
    name = sx.value(node[1])
    if name in seen:
        raise PartShelfError("Cyclic symbol inheritance")
    seen.add(name)
    base_name = sx.field(node, "extends")
    if not base_name:
        return copy.deepcopy(node)
    if base_name not in symbols:
        raise PartShelfError(f"Missing parent symbol: {base_name}")
    if sx.children(node, "symbol"):
        raise PartShelfError("A derived symbol with its own drawing units needs flattening in KiCad first.")
    result = resolve_symbol(symbols[base_name], symbols, seen)
    rename_symbol(result, name)
    for item in node[2:]:
        key = sx.tag(item)
        if key == "extends":
            continue
        def matches(other):
            return sx.tag(other) == key and (key != "property" or sx.value(other[1]) == sx.value(item[1]))
        result[2:] = [other for other in result[2:] if not matches(other)]
        result.append(copy.deepcopy(item))
    return result


def pin_numbers(symbol):
    return sorted({sx.field(node, "number") for node in sx.walk(symbol) if sx.tag(node) == "pin" and sx.field(node, "number")})


def pad_numbers(footprint):
    return sorted({sx.value(node[1]) for node in sx.children(footprint, "pad") if len(node) > 2 and sx.value(node[1]) and sx.value(node[2]) != "np_thru_hole"})


def resolve_asset(files, reference, source, *, allow_missing=False):
    reference = reference.replace("\\", "/")
    candidates = [posixpath.normpath(posixpath.join(posixpath.dirname(source), reference)), posixpath.normpath(reference)]
    for candidate in candidates:
        if candidate in files:
            return candidate
    base = PurePosixPath(reference).name
    matches = [name for name in files if PurePosixPath(name).name == base]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Common with converted Altium libraries: prefer assets from that conversion.
        prefix = "/".join(source.split("/")[:2]) + "/" if source.startswith("converted/") else None
        grouped = [n for n in matches if prefix and n.startswith(prefix)]
        if len(grouped) == 1:
            return grouped[0]
        raise PartShelfError(f"More than one file matches {base}; make the reference unambiguous.")
    if allow_missing:
        return None
    raise PartShelfError(f"Missing linked asset: {reference}. Include it in the import folder or ZIP.")


@dataclass
class Source:
    files: dict
    reports: list
    origins: dict
    symbols: dict
    footprints: dict
    libraries: dict

    @classmethod
    def read(cls, files):
        files, reports, origins = normalize(files)
        symbols, footprints, libraries = {}, {}, {}
        pcm = False
        if "metadata.json" in files:
            try:
                pcm = json.loads(files["metadata.json"]).get("type") == "library"
            except (ValueError, AttributeError):
                pass
        assets = [(name, data) for name, data in sorted(files.items())
                  if name.lower().endswith((".kicad_sym", ".kicad_mod"))
                  and (not pcm or name.startswith(("symbols/", "footprints/")))]
        for index, (name, data) in enumerate(assets):
            report("parse", "Parsing library files…", index, len(assets), "files", name)
            if name.lower().endswith(".kicad_sym"):
                try:
                    root = sx.loads(data.decode("utf-8-sig"))
                except (ValueError, UnicodeError) as exc:
                    raise PartShelfError(f"Cannot read symbol library {name}: {exc}") from exc
                if sx.tag(root) != "kicad_symbol_lib":
                    raise PartShelfError(f"{name} is not a KiCad symbol library.")
                definitions = {sx.value(s[1]): s for s in sx.children(root, "symbol")}
                libraries[name] = root
                for symbol_name, definition in definitions.items():
                    key = name + "#" + symbol_name
                    symbols[key] = {"file": name, "name": symbol_name, "node": definition, "definitions": definitions}
            elif name.lower().endswith(".kicad_mod"):
                try:
                    root = sx.loads(data.decode("utf-8-sig"))
                except (ValueError, UnicodeError) as exc:
                    raise PartShelfError(f"Cannot read footprint {name}: {exc}") from exc
                if sx.tag(root) not in {"footprint", "module"}:
                    raise PartShelfError(f"{name} is not a KiCad footprint.")
                footprints[name] = root
        report("parse", "Parsing library files…", len(assets), len(assets), "files")
        if not symbols:
            if any(name.lower().endswith((".brd", ".sch")) and b"<eagle" in data[:4096] for name, data in files.items()):
                raise PartShelfError("This source contains an Eagle board design. Open its .brd file or repository in the application's Import dialog to choose external connections and create a module. A .sch file alone does not contain the board's pad positions; include its matching .brd file.")
            raise PartShelfError("No symbols found. Include a .kicad_sym, Eagle .lbr, or Altium .SchLib/.IntLib. For separate Altium libraries, select the symbol and footprint files together.")
        return cls(files, reports, origins, symbols, footprints, libraries)

    def inspect(self):
        result = []
        footprint_pads = {}
        report("check-footprints", "Checking footprint pads…", 0, len(self.footprints), "footprints")
        for key, node in self.footprints.items():
            footprint_pads[key] = pad_numbers(node)
            report("check-footprints", "Checking footprint pads…", len(footprint_pads), len(self.footprints), "footprints", sx.value(node[1]))
        report("check-components", "Checking component mappings…", 0, len(self.symbols), "components")
        for key, info in self.symbols.items():
            try:
                symbol = resolve_symbol(info["node"], info["definitions"])
                props = properties(symbol)
                reference = props.get("Footprint", "")
                candidates = [p for p, node in self.footprints.items() if sx.value(node[1]) == reference.split(":")[-1] or Path(p).stem == reference.split(":")[-1]] if reference else []
                same_group = [p for p in candidates if p.startswith(posixpath.dirname(info["file"]) + "/")]
                if len(same_group) == 1:
                    candidates = same_group
                suggested = candidates[0] if len(candidates) == 1 else (next(iter(self.footprints)) if len(self.footprints) == 1 and not reference else "")
                pins = pin_numbers(symbol)
                pads = footprint_pads.get(suggested, [])
                missing, extra = sorted(set(pins) - set(pads)), sorted(set(pads) - set(pins))
                mapping = {"ok": bool(suggested and pins and not missing and not extra), "missing": missing, "extra": extra}
                result.append({"key": key, "name": info["name"], "description": props.get("Description", ""), "footprint": suggested, "original_footprint": reference, "pins": pins, "properties": props, "mapping": mapping})
            except PartShelfError as exc:
                result.append({"key": key, "name": info["name"], "error": str(exc), "footprint": ""})
            report("check-components", "Checking component mappings…", len(result), len(self.symbols), "components", info["name"])
        return {"symbols": result, "footprints": [{"key": key, "name": sx.value(node[1]), "pads": footprint_pads[key]} for key, node in self.footprints.items()], "reports": self.reports, "documents": sorted(name for name in self.files if name.lower().endswith(".pdf"))}

    def prepare(self, symbol_key: str, footprint_key: str, metadata: dict):
        if symbol_key not in self.symbols or footprint_key not in self.footprints:
            raise PartShelfError("Choose a symbol and its footprint from the imported files.")
        part_id = valid_id(str(metadata.get("id", "")))
        info = self.symbols[symbol_key]
        symbol = resolve_symbol(info["node"], info["definitions"])
        footprint = copy.deepcopy(self.footprints[footprint_key])
        original_symbol = copy.deepcopy(symbol)
        original_footprint = copy.deepcopy(footprint)
        props = properties(symbol)
        for name in ("Sim.Library", "Sim.Params"):
            if name == "Sim.Library" and props.get(name, ""):
                raise PartShelfError("External SPICE libraries are not packaged in this version. Remove that model link in a copy before importing.")
        pins, pads = pin_numbers(symbol), pad_numbers(footprint)
        missing = sorted(set(pins) - set(pads))
        extra = sorted(set(pads) - set(pins))
        if missing or extra:
            raise PartShelfError("Pin/pad mapping does not match. " + (f"Pins without pads: {', '.join(missing)}. " if missing else "") + (f"Pads without pins: {', '.join(extra)}." if extra else ""))
        if not pins:
            raise PartShelfError("This component has no numbered electrical pins. This version manages components with matching pins and pads.")
        files, models, documents = {}, [], []
        import_warnings = []
        unresolved_datasheet = None
        for model in sx.children(footprint, "model"):
            source_path = resolve_asset(self.files, sx.value(model[1]), footprint_key)
            data = self.files[source_path]
            ext = Path(source_path).suffix.lower()
            if ext not in {".step", ".stp", ".wrl"}:
                raise PartShelfError(f"Unsupported 3D model type: {ext}")
            name = "models/" + sha(data)[:20] + ext
            files[name] = data
            model[1] = sx.q(name)
            if name not in models:
                models.append(name)
        datasheet = str(metadata.get("datasheet", props.get("Datasheet", ""))).strip()
        if datasheet and datasheet != "~" and not datasheet.lower().startswith(("https://", "http://")):
            original = resolve_asset(self.files, datasheet, info["file"], allow_missing=True)
            # Some vendor exports put the symbol name or part number in this
            # field. It is metadata, not a missing file. Still resolve real
            # extensionless files first and keep genuine document links strict.
            identifiers = {str(value).strip().casefold() for value in
                           (info["name"], props.get("Value", ""), props.get("MPN", ""), metadata.get("mpn", "")) if value}
            placeholder = (datasheet.casefold() in identifiers
                           and not any(mark in datasheet for mark in ("/", "\\"))
                           and Path(datasheet).suffix.lower() not in {".pdf", ".txt", ".html", ".htm", ".doc", ".docx"})
            if original is None and placeholder:
                unresolved_datasheet = datasheet
                import_warnings.append(f'Datasheet not provided: the source field contains the part name or number "{datasheet}". Add a datasheet URL or PDF in Properties when available.')
                datasheet = ""
            else:
                if original is None:
                    resolve_asset(self.files, datasheet, info["file"])
                data = self.files[original]
                name = "documents/" + sha(data)[:20] + Path(original).suffix.lower()
                files[name] = data
                documents.append(name)
                datasheet = name
        name = str(metadata.get("name") or info["name"]).strip()
        description = str(metadata.get("description", props.get("Description", "")))
        manufacturer = str(metadata.get("manufacturer", props.get("Manufacturer", "")))
        mpn = str(metadata.get("mpn", props.get("MPN", "")))
        website = str(metadata.get("website", props.get("Website", ""))).strip()
        if website:
            from urllib.parse import urlsplit
            parsed = urlsplit(website)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                raise PartShelfError("Website must be an HTTP or HTTPS link without embedded credentials.")
        rename_symbol(symbol, "Part")
        set_property(symbol, "Footprint", f"PS_{part_id}:Part")
        set_property(symbol, "Value", name)
        set_property(symbol, "Datasheet", datasheet)
        set_property(symbol, "Description", description)
        set_property(symbol, "PartShelf ID", part_id)
        if website or "website" in metadata:
            set_property(symbol, "Website", website)
        if manufacturer:
            set_property(symbol, "Manufacturer", manufacturer)
        if mpn:
            set_property(symbol, "MPN", mpn)
        footprint[1] = sx.q("Part")
        # Select the header before copying: vendor libraries can contain hundreds
        # of large symbols that are unrelated to this component.
        header = [n for n in self.libraries[info["file"]] if sx.tag(n) != "symbol"]
        library = copy.deepcopy(header) + [symbol]
        files["symbol.kicad_sym"] = sx.encode(library)
        files["footprints/Part.kicad_mod"] = sx.encode(footprint)
        source_paths = {info["file"], footprint_key}
        source_paths |= {self.origins[p] for p in source_paths if p in self.origins}
        source_hashes = {p: sha(self.files[p]) for p in sorted(source_paths)}
        # Keep the selected original geometry, not a copy of a 1,000-part library
        # inside every component. Full source-library hashes remain in provenance.
        source_library = copy.deepcopy(header) + [original_symbol]
        files["sources/original.kicad_sym"] = sx.encode(source_library)
        files["sources/original.kicad_mod"] = sx.encode(original_footprint)
        for p, data in self.files.items():
            if Path(p).name.lower().startswith(("license", "licence", "copying", "readme", "notice")):
                files["sources/" + sha(p.encode())[:10] + "-" + Path(p).name] = data
        meta = {"schema": 1, "id": part_id, "name": name, "description": description, "manufacturer": manufacturer, "mpn": mpn, "website": website, "category": str(metadata.get("category") or props.get("Category") or "Components"), "datasheet": datasheet, "pins": pins, "pads": pads, "assets": {"symbol": "symbol.kicad_sym", "footprint": "footprints/Part.kicad_mod", "models": models, "documents": documents}, "provenance": {"files": source_hashes, "conversions": self.reports}, "notes": str(metadata.get("notes", props.get("Notes", "")))}
        if import_warnings:
            meta["provenance"].update(warnings=import_warnings, original_datasheet=unresolved_datasheet)
        if metadata.get("collection"):
            collection = metadata["collection"]
            if not isinstance(collection, str) or len(collection) > 200:
                raise PartShelfError("Enter a library folder name up to 200 characters.")
            meta["collection"] = collection.strip()
        return meta, files


def validate_component(meta, files):
    """Validate packaged geometry and its dependency closure, including third-party packages."""
    try:
        part_id = valid_id(meta["id"])
        if meta.get("schema") != 1 or not isinstance(meta.get("name"), str) or not meta["name"].strip():
            raise PartShelfError("A component needs schema 1 and a non-empty name.")
        assets = meta["assets"]
        if meta.get('kind') == 'library_entry':
            from .native_library import validate_entry
            return validate_entry(meta, files)
        if assets["symbol"] != "symbol.kicad_sym" or assets["footprint"] != "footprints/Part.kicad_mod":
            raise PartShelfError("Unsupported component asset layout.")
        for key, prefix in (("models", "models/"), ("documents", "documents/")):
            if not isinstance(assets[key], list) or any(not isinstance(p, str) or not p.startswith(prefix) for p in assets[key]):
                raise PartShelfError(f"Invalid component {key} index.")
        paths = [assets["symbol"], assets["footprint"], *assets["models"], *assets["documents"]]
        if "integrity.json" in files or any(path not in files for path in paths):
            raise PartShelfError("A required component asset is missing, or a reserved filename is used.")
        library = sx.loads(files[assets["symbol"]].decode("utf-8"))
        definitions = sx.children(library, "symbol")
        footprint = sx.loads(files[assets["footprint"]].decode("utf-8"))
        if sx.tag(library) != "kicad_symbol_lib" or len(definitions) != 1 or sx.value(definitions[0][1]) != "Part" or sx.field(definitions[0], "extends"):
            raise PartShelfError("A component must contain exactly one flattened symbol named Part.")
        if sx.tag(footprint) not in {"footprint", "module"} or sx.value(footprint[1]) != "Part":
            raise PartShelfError("A component must contain a footprint named Part.")
        pins, pads = pin_numbers(definitions[0]), pad_numbers(footprint)
        if not pins or pins != pads or pins != meta["pins"] or pads != meta["pads"]:
            raise PartShelfError("Packaged symbol pins, footprint pads and metadata do not match.")
        props = properties(definitions[0])
        if props.get("Footprint") != f"PS_{part_id}:Part" or props.get("Sim.Library"):
            raise PartShelfError("The symbol contains an unsupported external library reference.")
        sheet = props.get("Datasheet", "")
        if sheet and sheet != "~" and not sheet.lower().startswith(("http://", "https://")) and sheet not in assets["documents"]:
            raise PartShelfError("The symbol references a document outside its package.")
        for model in sx.children(footprint, "model"):
            path = sx.value(model[1])
            if path not in assets["models"] or Path(path).suffix.lower() not in {".step", ".stp", ".wrl"}:
                raise PartShelfError("The footprint references a model outside its package.")
    except (KeyError, IndexError, TypeError, AttributeError, UnicodeError, sx.FormatError) as exc:
        raise PartShelfError("Invalid component metadata or KiCad geometry.") from exc


class Catalog:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._list_cache = None
        self._list_stamp = None
        self._verified_assets = set()
        self._validated_entries = set()

    @property
    def libraries_path(self):
        return contained(self.root, "libraries.json")

    def libraries(self):
        data = read_json(self.libraries_path) if self.libraries_path.exists() else {}
        entries = data.get("libraries", []) if isinstance(data, dict) else None
        if not isinstance(entries, list) or any(not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in ("path", "name", "parent")) for item in entries):
            raise PartShelfError("The library folder index is invalid.")
        from .collections import collection_name
        known = {item['path'] for item in entries}
        for part in self.list():
            name = collection_name(part)
            if name not in known:
                entries.append({"name": name, "parent": "", "path": name})
                known.add(name)
        return entries

    def create_library(self, name, parent=""):
        name = str(name or "").strip()
        parent = str(parent or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,79}", name):
            raise PartShelfError("Library names may contain letters, numbers, spaces, underscores, and hyphens.")
        with self.write_lock():
            entries = self.libraries()
            if parent and not any(item["path"] == parent for item in entries):
                raise PartShelfError("Choose an existing parent library.")
            path = f"{parent}_{name}" if parent else name
            if len(path) > 200:
                raise PartShelfError("The full library name must be 200 characters or fewer.")
            if any(item["path"].casefold() == path.casefold() for item in entries):
                raise PartShelfError("That library already exists.")
            item = {"name": name, "parent": parent, "path": path}
            entries.append(item)
            atomic_write(self.libraries_path, canonical({"schema": 1, "libraries": entries}))
            return item

    def revisions(self, part_id):
        root = contained(self.root, valid_id(part_id))
        return sorted(int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdigit()) if root.exists() else []

    @contextmanager
    def write_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = contained(self.root, ".write.lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise PartShelfError("Another import is writing the catalog. Try again shortly. After a crash, remove the catalog's .write.lock only once the previous process has stopped.") from exc
        try:
            os.close(fd)
            yield
        finally:
            self._list_cache = None
            lock.unlink(missing_ok=True)

    def publish(self, metadata, files):
        with self.write_lock():
            return self._publish_unlocked(metadata, files)

    def _publish_unlocked(self, metadata, files):
        """Publish while the caller holds write_lock (including library transactions)."""
        validate_component(metadata, files)
        part_id = valid_id(metadata["id"])
        parent = contained(self.root, part_id)
        parent.mkdir(parents=True, exist_ok=True)
        revision = max(self.revisions(part_id), default=0) + 1
        metadata = {**metadata, "revision": revision}
        from .storage import Payload, write_payload
        files = files.copy() if isinstance(files, Payload) else dict(files)
        files['component.json'] = canonical(metadata)
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=parent) as temp:
            stage = Path(temp) / str(revision)
            stage.mkdir()
            write_payload(stage, files)
            (stage / "integrity.json").write_bytes(canonical({"digest": digest(files), "files": inventory(files)}))
            os.rename(stage, contained(parent, str(revision)))
        return {**metadata, "digest": digest(files)}

    def read(self, part_id, revision):
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise PartShelfError("Revision must be a positive integer.")
        root = contained(self.root, valid_id(part_id) + "/" + str(revision))
        integrity = read_json(root / "integrity.json")
        metadata = read_json(root / 'component.json')
        if metadata.get('kind') == 'library_entry':
            from .storage import Payload, Asset
            if 'component.json' not in integrity.get('files', {}):
                raise PartShelfError('The catalog checksum index does not include component metadata.')
            files = Payload()
            for name, checksum in integrity.get('files', {}).items():
                path = contained(root, name)
                if not path.is_file():
                    raise PartShelfError('Catalog revision is missing ' + name)
                info = path.stat()
                key = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size, checksum)
                if key not in self._verified_assets:
                    if sha(path.read_bytes()) != checksum:
                        raise PartShelfError('Catalog revision was modified: ' + name)
                    self._verified_assets.add(key)
                files[name] = Asset(path, checksum, info.st_size)
            if digest(files) != integrity.get('digest') or metadata.get('id') != part_id or metadata.get('revision') != revision:
                raise PartShelfError('Catalog revision integrity check failed.')
            if integrity['digest'] not in self._validated_entries:
                validate_component(metadata, files)
                self._validated_entries.add(integrity['digest'])
            return metadata, files, integrity
        files = {}
        for name, expected in integrity.get("files", {}).items():
            path = contained(root, name)
            if not path.is_file():
                raise PartShelfError(f"Catalog revision is missing {name}.")
            data = path.read_bytes()
            if sha(data) != expected:
                raise PartShelfError(f"Catalog revision was modified: {name}. Import changes as a new revision.")
            files[name] = data
        if digest(files) != integrity.get("digest"):
            raise PartShelfError("Catalog revision integrity check failed.")
        if "component.json" not in files:
            raise PartShelfError("The catalog checksum index does not include component metadata.")
        try:
            metadata = json.loads(files["component.json"])
        except (ValueError, UnicodeError) as exc:
            raise PartShelfError("Invalid catalog component metadata.") from exc
        if metadata.get("id") != part_id or metadata.get("revision") != revision:
            raise PartShelfError("Catalog component identity does not match its folder.")
        validate_component(metadata, files)
        return metadata, files, integrity

    def list(self):
        # Native catalogs can have tens of thousands of entries and gigabytes
        # of models. Geometry is verified on access, not read for every redraw.
        stamp = self.root.stat().st_mtime_ns if self.root.exists() else None
        if self._list_cache is not None and self._list_stamp == stamp:
            return self._list_cache
        result = []
        if not self.root.exists():
            return result
        for path in sorted(self.root.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            revisions = self.revisions(path.name)
            if revisions:
                try:
                    folder = path / str(revisions[-1])
                    meta = read_json(folder / 'component.json')
                    if meta.get('kind') == 'library_entry':
                        integrity = read_json(folder / 'integrity.json')
                        if sha((folder / 'component.json').read_bytes()) != integrity['files']['component.json']:
                            raise PartShelfError('Catalog metadata was modified.')
                        if meta.get('id') != path.name or meta.get('revision') != revisions[-1]:
                            raise PartShelfError('Catalog component identity does not match its folder.')
                    else:
                        meta, _, integrity = self.read(path.name, revisions[-1])
                    result.append({**meta, "digest": integrity["digest"], "revisions": revisions})
                except (PartShelfError, OSError, KeyError, TypeError, ValueError) as exc:
                    result.append({"id": path.name, "name": path.name, "error": str(exc), "revisions": revisions})
        if any(p.get('kind') == 'library_entry' for p in result):
            self._list_cache = result
            self._list_stamp = stamp
        return result
