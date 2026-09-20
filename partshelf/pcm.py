"""Export ordinary KiCad 10 library packages for Install from File in PCM."""
from __future__ import annotations

import copy
import io
from pathlib import Path
import re
import zipfile
import tempfile
import os
from .storage import DiskStore, Payload, Asset, LIBRARY_BYTES, LIBRARY_FILES, LIBRARY_MEMBER_BYTES
from .progress import report

from . import sexpr as sx
from .components import rename_symbol, set_property
from .files import PartShelfError, canonical, sha
from .collections import collection_name
from .headers import assembly_csv
from .packages import add_catalog_snapshot, package_options, MANIFEST


def export_pcm(catalog, selections=None, options=None):
    with catalog.write_lock():
        return _export_pcm(catalog, selections, options)


def export_pcm_file(catalog, destination, selections=None, options=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink(): raise PartShelfError('Choose a regular ZIP destination.')
    with tempfile.TemporaryDirectory(prefix='.pcm-export-', dir=destination.parent) as temp, catalog.write_lock():
        output = Path(temp) / 'library.zip'
        settings = _export_pcm(catalog, selections, options, DiskStore(Path(temp) / 'objects'), output)
        os.replace(output, destination)
        return settings


def _export_pcm(catalog, selections=None, options=None, store=None, destination=None):
    options = options or {}
    if not isinstance(options, dict):
        raise PartShelfError("Choose package export options.")
    options = {**package_options(catalog), **options}
    name = str(options.get("name", "Component collection")).strip()
    identifier = str(options.get("identifier", "local.kicad-component-packager.collection")).strip()
    version = str(options.get("version", "1.0.0")).strip()
    author = str(options.get("author", "Local collection")).strip()
    license_name = str(options.get("license", "See bundled source notices")).strip()
    prefix = str(options.get("library_prefix", "PCM_"))
    if not name or len(name) > 200 or not author or len(author) > 500 or not license_name:
        raise PartShelfError("Enter a package name, author, and license or source-notice description.")
    if not re.fullmatch(r"[a-zA-Z][-a-zA-Z0-9.]{0,98}[a-zA-Z0-9]", identifier):
        raise PartShelfError("Use a package identifier such as local.my-library.components (2–100 letters, numbers, dots, and hyphens).")
    if not re.fullmatch(r"\d{1,4}(\.\d{1,4}(\.\d{1,6})?)?", version):
        raise PartShelfError("Use a numeric package version such as 1.0.0.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{0,30}", prefix):
        raise PartShelfError("The library prefix may contain letters, numbers, underscores, and hyphens.")
    whole_catalog = selections is None
    selections = selections if selections is not None else [(p["id"], p["revision"]) for p in catalog.list() if "error" not in p]
    if (not selections and not catalog.libraries()) or len({part_id for part_id, _ in selections}) != len(selections):
        raise PartShelfError("Choose components with one revision per ID.")
    package_dir = identifier.replace(".", "_")
    libraries, library_names, index, symbol_names = {}, {}, [], {}
    files = store if store is not None else {}
    total_size = lambda: files.size() if isinstance(files, Payload) else sum(map(len, files.values()))
    default_library_name = "KCP_" + re.sub(r"[^A-Za-z0-9_-]", "_", identifier)[-65:]
    selected_meta = {}
    for part_id, revision in selections:
        selected_meta[part_id] = catalog.read(part_id, revision)[0]
        report('check-export', 'Checking selected components…', len(selected_meta), len(selections), 'components', selected_meta[part_id]['name'])
    # Preserve the chosen library names even when exporting just one library.
    # Check names before emitting files so sanitizing cannot merge two libraries.
    for meta in selected_meta.values():
        collection = collection_name(meta)
        library_name = re.sub(r"[^A-Za-z0-9_-]", "_", collection)[:100] or default_library_name
        if any(other != collection and name.casefold() == library_name.casefold() for other, name in library_names.items()):
            raise PartShelfError("Two libraries have the same KiCad export name. Rename one before exporting.")
        library_names[collection] = library_name
    third_party = "${KICAD10_3RD_PARTY}"
    for part_id, revision in sorted(selections):
        meta, assets, integrity = catalog.read(part_id, revision)
        collection = collection_name(meta)
        library_name = library_names[collection]
        nickname = prefix + library_name
        native = meta.get('kind') == 'library_entry'
        symbol_name = meta.get('native_name', part_id) if native else part_id
        symbol = None
        if meta['assets']['symbol']:
            source_library = sx.loads(assets['symbol.kicad_sym'].decode())
            if collection not in libraries:
                header = [entry for entry in source_library if sx.tag(entry) != 'symbol']
                if isinstance(files, DiskStore):
                    folder = files.root / '.libraries'; folder.mkdir(exist_ok=True)
                    library_file = folder / sha(collection.encode())
                    library_file.write_bytes(sx.dumps(header)[:-1].rstrip().encode())
                    libraries[collection] = library_file
                else:
                    libraries[collection] = copy.deepcopy(header)
                symbol_names[collection] = set()
            symbol = copy.deepcopy(sx.children(source_library, 'symbol')[0])
            rename_symbol(symbol, symbol_name)
            set_property(symbol, 'Footprint', nickname + ':' + part_id if meta['assets']['footprint'] else '')
            set_property(symbol, 'PartShelf Revision', str(revision))
            if meta.get('assembly'):
                set_property(symbol, 'Assembly BOM', f'{third_party}/resources/{package_dir}/assembly-bom.csv')
        if meta['assets']['footprint']:
            footprint = sx.loads(assets['footprints/Part.kicad_mod'].decode())
            footprint_name = meta.get('native_name', part_id) if native and symbol is None else part_id
            footprint[1] = sx.q(footprint_name)
            for model in sx.children(footprint, 'model'):
                original = sx.value(model[1])
                value = assets.asset(original) if isinstance(assets, Payload) else assets[original]
                checksum = value.checksum if isinstance(value, Asset) else sha(value)
                model_name = checksum + Path(original).suffix.lower()
                model_library = 'Shared' if native else library_name
                target = f'3dmodels/{model_library}.3dshapes/{model_name}'
                if target not in files: files[target] = value if isinstance(files, Payload) else assets[original]
                model[1] = sx.q(f'{third_party}/3dmodels/{package_dir}/{model_library}.3dshapes/{model_name}')
            target = f'footprints/{library_name}.pretty/{footprint_name}.kicad_mod'
            if target in files:
                raise PartShelfError('Duplicate exported footprint name in ' + library_name + ': ' + footprint_name)
            files[target] = sx.encode(footprint)
        for document in meta['assets']['documents']:
            value = assets.asset(document) if isinstance(assets, Payload) else assets[document]
            checksum = value.checksum if isinstance(value, Asset) else sha(value)
            target = 'documents/' + checksum + Path(document).suffix.lower()
            files['resources/' + target] = value if isinstance(files, Payload) else assets[document]
            if symbol is not None and meta.get('datasheet') == document:
                set_property(symbol, 'Datasheet', f'{third_party}/resources/{package_dir}/{target}')
        for source in assets:
            if source.startswith('sources/'):
                value = assets.asset(source) if isinstance(assets, Payload) else assets[source]
                checksum = value.checksum if isinstance(value, Asset) else sha(value)
                source_group = checksum if native else part_id
                target = f'resources/sources/{source_group}/{source[8:]}'
                if target not in files:
                    files[target] = value if isinstance(files, Payload) else assets[source]
        if symbol is not None:
            if symbol_name in symbol_names[collection]:
                raise PartShelfError('Duplicate exported symbol name in ' + library_name + ': ' + symbol_name)
            symbol_names[collection].add(symbol_name)
            if isinstance(libraries[collection], Path):
                with libraries[collection].open('ab') as output:
                    output.write(('\n  ' + sx.dumps(symbol, 1)).encode())
            else:
                libraries[collection].append(symbol)
        report('export-library', 'Preparing library ZIP…', len(index) + 1, len(selections), 'components', meta['name'])
        index.append({"id": part_id, "revision": revision, "digest": integrity["digest"], "metadata": meta})
    for collection, library in libraries.items():
        if isinstance(library, Path):
            import hashlib
            with library.open('ab') as output: output.write(b'\n)\n')
            checksum = hashlib.sha256()
            with library.open('rb') as source:
                while chunk := source.read(1024 * 1024): checksum.update(chunk)
            data = Asset(library, checksum.hexdigest(), library.stat().st_size)
        else:
            data = sx.encode(library)
        files[f"symbols/{library_names[collection]}.kicad_sym"] = data
    if not libraries:
        # PCM needs a content directory even for a collection of empty folders.
        files['symbols/Empty.kicad_sym'] = b'(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor))\n'
    files["resources/component-index.json"] = canonical({"components": index, "libraries": catalog.libraries()})
    files["resources/assembly-bom.csv"] = assembly_csv([item["metadata"] for item in index])
    files["resources/README.txt"] = (
        f"{name}\n\nInstall this ZIP with KiCad 10: Plugin and Content Manager > Install from File.\n"
        f"Libraries: {', '.join(prefix + value for value in library_names.values())}\nEnable automatic library registration in KiCad preferences. "
        f"The configured library prefix must be {prefix!r}, or set the matching nickname manually.\n\n"
        "Symbols, footprints, models, documents and source notices are included. "
        "Open this same ZIP in KiCad Component Packager to restore its library tree, component properties, source assets and revision history.\n"
        "assembly-bom.csv lists additional headers per module, with quantity per module (not a placed-board BOM). "
        "Multiply by the number of modules used in the design. Generic headers may be sourced by their required dimensions with substitutions allowed; an exact part number is optional.\n"
    ).encode()
    extra_files = {}
    extra_root = catalog.root / '.native-packages'
    if whole_catalog and extra_root.exists():
        for path in sorted(extra_root.rglob('*')):
            if path.is_symlink(): raise PartShelfError('Native package resources contain a symlink.')
            if not path.is_file(): continue
            relative = path.relative_to(extra_root).as_posix()
            target = 'resources/packager/native/' + relative
            data = path.read_bytes()
            files[target] = Asset(path, sha(data), len(data)) if isinstance(files, Payload) else data
            extra_files[relative] = target
    files["metadata.json"] = canonical({
        "$schema": "https://go.kicad.org/pcm/schemas/v2", "name": name,
        "description": f"{len(selections)} packaged components for KiCad",
        "description_full": f"{name}. Symbols, footprints, linked models, documents, and source notices exported with KiCad Component Packager.",
        "identifier": identifier, "type": "library", "author": {"name": author, "contact": {}},
        "license": license_name, "resources": {},
        "versions": [{"version": version, "status": "testing", "kicad_version": "10.0", "install_size": total_size()}]
    })
    manifest = add_catalog_snapshot(files, catalog, selections, {
        'name': name, 'identifier': identifier, 'version': version, 'author': author,
        'license': license_name, 'library_prefix': prefix}, extra_files)
    # The size field changes metadata length; settle it while keeping the
    # editable snapshot's checksum for that metadata synchronized.
    import json
    metadata = json.loads(files['metadata.json'])
    for _ in range(5):
        total = total_size()
        metadata['versions'][0]['install_size'] = total
        files['metadata.json'] = canonical(metadata)
        manifest['pcm_files']['metadata.json'] = sha(files['metadata.json'])
        files[MANIFEST] = canonical(manifest)
        if total == total_size():
            break
    from .files import MAX_BYTES, MAX_FILES
    if len(files) > (LIBRARY_FILES if destination else MAX_FILES) or total_size() > (LIBRARY_BYTES if destination else MAX_BYTES):
        raise PartShelfError('This collection exceeds the ZIP import limit. Export smaller library groups so each saved ZIP can be reopened.')
    output = destination if destination else io.BytesIO()
    if isinstance(files, Payload) and any((v.size if isinstance(v, Asset) else len(v)) > LIBRARY_MEMBER_BYTES for v in files.entries.values()):
        raise PartShelfError('An individual library asset exceeds the ZIP import limit. Export smaller groups.')
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for number, path in enumerate(sorted(files), 1):
            data = files[path]
            entry = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
            report('write-zip', 'Writing library ZIP…', number, len(files), 'files', path)
    return manifest['options'] if destination else output.getvalue()
