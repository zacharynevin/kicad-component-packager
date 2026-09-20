"""Import native PCM libraries, including unpaired symbols and footprints."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import sexpr as sx
from .components import Catalog, properties, pin_numbers, pad_numbers, rename_symbol, resolve_symbol, set_property
from .files import PartShelfError, atomic_write, canonical, sha, valid_id
from .progress import report
from .storage import Asset, Payload, DiskStore, unpack_library


def validate_entry(meta, files):
    assets = meta['assets']
    symbol_path, footprint_path = assets['symbol'], assets['footprint']
    if symbol_path not in ('', 'symbol.kicad_sym') or footprint_path not in ('', 'footprints/Part.kicad_mod') or not (symbol_path or footprint_path):
        raise PartShelfError('A library entry needs a symbol or footprint.')
    if 'integrity.json' in files:
        raise PartShelfError('Reserved asset filename.')
    for key, prefix in [('models', 'models/'), ('documents', 'documents/')]:
        if not isinstance(assets[key], list) or any(not isinstance(p, str) or not p.startswith(prefix) or p not in files for p in assets[key]):
            raise PartShelfError('Invalid library asset index.')
    pins, pads = [], []
    if symbol_path:
        library = sx.loads(files[symbol_path].decode())
        symbols = sx.children(library, 'symbol')
        if sx.tag(library) != 'kicad_symbol_lib' or len(symbols) != 1 or sx.value(symbols[0][1]) != 'Part' or sx.field(symbols[0], 'extends'):
            raise PartShelfError('Invalid standalone library symbol.')
        pins = pin_numbers(symbols[0]); props = properties(symbols[0])
        expected = f"PS_{meta['id']}:Part" if footprint_path else ''
        if props.get('Footprint', '') != expected:
            raise PartShelfError('Invalid library footprint reference.')
        sheet = props.get('Datasheet', '')
        if sheet and sheet != '~' and not sheet.lower().startswith(('https://', 'http://')) and sheet not in assets['documents']:
            raise PartShelfError('The symbol references a document outside its package.')
        if props.get('Sim.Library'):
            raise PartShelfError('External SPICE libraries must be bundled separately.')
    if footprint_path:
        footprint = sx.loads(files[footprint_path].decode())
        if sx.tag(footprint) not in ('footprint', 'module') or sx.value(footprint[1]) != 'Part':
            raise PartShelfError('Invalid standalone library footprint.')
        pads = pad_numbers(footprint)
        for model in sx.children(footprint, 'model'):
            name = sx.value(model[1])
            if name not in assets['models'] or Path(name).suffix.lower() not in ('.step', '.stp', '.wrl'):
                raise PartShelfError('The footprint references a model outside its package.')
    elif assets['models']:
        raise PartShelfError('A 3D model needs a footprint reference.')
    if pins != meta['pins'] or pads != meta['pads']:
        raise PartShelfError('Library pin/pad metadata does not match its geometry.')
    # Native library symbols may be generic, graphical, power, or have extra
    # mechanical pads. Preserve them and expose mapping status without inventing pins.


def entry_id(identifier, kind, name):
    label = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:38] or kind
    return valid_id('native.' + label + '.' + sha((identifier + ':' + kind + ':' + name).encode())[:20])


def library_tree(symbol_libraries, footprint_libraries, vendors=None):
    entries = {}
    def add(path, name, parent=''):
        entries.setdefault(path, {'path': path, 'name': name, 'parent': parent})
    for library in sorted(symbol_libraries):
        if library.startswith('Core_'):
            add('Core', 'Core'); group, _, rest = library[5:].partition('_')
            parent = 'Core_' + group; add(parent, group, 'Core')
            if group == 'Vendors' and rest:
                manufacturer = (vendors or {}).get(library, '')
                slug = re.sub(r'[^A-Za-z0-9_-]+', '_', manufacturer).strip('_')
                vendor, _, family = rest.partition('_')
                if slug and rest.startswith(slug + '_'):
                    vendor, family = slug, rest[len(slug) + 1:]
                vendor_path = parent + '_' + vendor; add(vendor_path, vendor, parent)
                if manufacturer: entries[vendor_path]['name'] = manufacturer
                if family: add(library, family, vendor_path)
            elif rest: add(library, rest, parent)
        else: add(library, library)
    for library in sorted(footprint_libraries):
        if library.startswith('Core_'):
            add('Core', 'Core'); add('Core_Footprints', 'Footprints', 'Core')
            add('Core_Footprints_' + library[5:], library[5:], 'Core_Footprints')
        else:
            add(library, library)
    return list(entries.values())


def import_native(catalog, path):
    """Stage the complete collection before merging. Never skip an entry silently."""
    catalog.root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.native-import-', dir=catalog.root) as temporary:
        root = Path(temporary)
        source = unpack_library(path, root / 'objects')
        pcm = json.loads(source['metadata.json']); identifier = pcm['identifier']
        if pcm.get('type') != 'library': raise PartShelfError('Select a KiCad library ZIP.')
        package_dir = identifier.replace('.', '_')
        stage = Catalog(root / 'catalog'); store = DiskStore(root / 'normalized')
        notices = {p: source.asset(p) for p in source if Path(p).name.lower().startswith(('license', 'licence', 'copying', 'notice', 'readme'))}
        footprints = {}; used_models = set(); symbols = [p for p in source if p.startswith('symbols/') and p.endswith('.kicad_sym')]
        fp_paths = [p for p in source if p.startswith('footprints/') and p.endswith('.kicad_mod')]
        def metadata(name, props, collection, kind):
            return {'schema': 1, 'kind': 'library_entry', 'id': entry_id(identifier, kind, collection + ':' + name),
                    'native_name': name, 'name': name, 'description': props.get('Description', ''),
                    'manufacturer': props.get('Manufacturer', ''), 'mpn': props.get('MPN', ''),
                    'website': props.get('Website', ''), 'datasheet': props.get('Datasheet', ''),
                    'notes': props.get('Notes', ''), 'category': 'Footprints' if kind == 'footprint' else 'Symbols',
                    'collection': collection, 'properties': props, 'pins': [], 'pads': [],
                    'assets': {'symbol': '', 'footprint': '', 'models': [], 'documents': []},
                    'provenance': {'files': {}, 'conversions': [], 'source': {'kind': 'pcm', 'name': pcm['name'], 'identifier': identifier}}}
        def payload():
            return Payload({'sources/' + sha(p.encode())[:10] + '-' + Path(p).name: v for p, v in notices.items()})
        def put(files, key, data):
            store[key] = data; files[key] = store.asset(key)
        for index, file in enumerate(fp_paths):
            library = Path(file).parent.stem; name = Path(file).stem
            collection = 'Core_Footprints_' + library[5:] if library.startswith('Core_') else library
            node = sx.loads(source[file].decode()); props = properties(node)
            meta = metadata(name, props, collection, 'footprint'); files = payload()
            meta['description'] = sx.field(node, 'descr') or meta['description']
            put(files, 'sources/original.kicad_mod', source[file])
            meta['provenance']['files'][file] = source.asset(file).checksum
            for model in sx.children(node, 'model'):
                reference = sx.value(model[1]); prefix = '${KICAD10_3RD_PARTY}/3dmodels/' + package_dir + '/'
                original = '3dmodels/' + reference[len(prefix):] if reference.startswith(prefix) else reference
                if original not in source: raise PartShelfError('Missing linked model: ' + reference)
                used_models.add(original)
                asset = source.asset(original); key = 'models/' + asset.checksum + Path(original).suffix.lower()
                files[key] = asset; model[1] = sx.q(key)
                if key not in meta['assets']['models']: meta['assets']['models'].append(key)
            node[1] = sx.q('Part'); put(files, 'footprints/Part.kicad_mod', sx.encode(node))
            meta['assets']['footprint'] = 'footprints/Part.kicad_mod'; meta['pads'] = pad_numbers(node)
            footprints[library + ':' + name] = (meta, files)
            stage._publish_unlocked(meta, files)
            report('native-footprints', 'Importing native footprints…', index + 1, len(fp_paths), 'footprints', name)
        count = 0; vendors = {}
        for index, file in enumerate(symbols):
            document = sx.loads(source[file].decode()); header = [n for n in document if sx.tag(n) != 'symbol']
            definitions = {sx.value(n[1]): n for n in sx.children(document, 'symbol')}
            for name, raw in definitions.items():
                node = resolve_symbol(raw, definitions); props = properties(node); library = Path(file).stem
                meta = metadata(name, props, library, 'symbol'); files = payload()
                if props.get('Manufacturer'): vendors[library] = props['Manufacturer']
                reference = props.get('Footprint', '')
                ref = reference[4:] if reference.startswith('PCM_') else reference
                if reference:
                    if ref not in footprints: raise PartShelfError('Missing linked footprint: ' + reference)
                    fp_meta, fp_files = footprints[ref]
                    for key in fp_files:
                        if key.startswith(('models/', 'footprints/')): files[key] = fp_files.asset(key)
                    meta['pads'] = fp_meta['pads']; meta['assets']['footprint'] = fp_meta['assets']['footprint']
                    meta['assets']['models'] = fp_meta['assets']['models']
                put(files, 'sources/original.kicad_sym', sx.encode(copy.deepcopy(header) + [copy.deepcopy(node)]))
                meta['provenance']['files'][file] = source.asset(file).checksum
                rename_symbol(node, 'Part'); set_property(node, 'Footprint', f"PS_{meta['id']}:Part" if reference else '')
                meta['pins'] = pin_numbers(node); meta['assets']['symbol'] = 'symbol.kicad_sym'
                meta['mapping_status'] = 'matched' if reference and meta['pins'] == meta['pads'] and meta['pins'] else 'unassigned' if not reference else 'review'
                sheet = props.get('Datasheet', '')
                if sheet.startswith('www.'):
                    sheet = 'https://' + sheet
                    meta['datasheet'] = sheet; set_property(node, 'Datasheet', sheet)
                if sheet and sheet != '~' and not sheet.lower().startswith(('https://', 'http://')):
                    prefix = '${KICAD10_3RD_PARTY}/resources/' + package_dir + '/'
                    original = 'resources/' + sheet[len(prefix):] if sheet.startswith(prefix) else sheet
                    if original not in source:
                        meta['provenance'].setdefault('warnings', []).append('Source datasheet is unavailable: ' + sheet)
                        meta['datasheet'] = ''; set_property(node, 'Datasheet', '')
                    else:
                        asset = source.asset(original); key = 'documents/' + asset.checksum + Path(original).suffix.lower()
                        files[key] = asset; meta['assets']['documents'].append(key); meta['datasheet'] = key; set_property(node, 'Datasheet', key)
                if props.get('Sim.Library'):
                    meta['provenance'].setdefault('warnings', []).append('External simulation library requires configuration: ' + props['Sim.Library'])
                    set_property(node, 'Sim.Library', '')
                put(files, 'symbol.kicad_sym', sx.encode(copy.deepcopy(header) + [node]))
                stage._publish_unlocked(meta, files); count += 1
            report('native-symbols', 'Importing native symbol libraries…', index + 1, len(symbols), 'libraries', library)
        libraries = library_tree({Path(p).stem for p in symbols}, {Path(p).parent.stem for p in fp_paths}, vendors)
        options = {'name': pcm['name'], 'identifier': identifier, 'version': pcm['versions'][0]['version'],
                   'author': pcm.get('author', {}).get('name', 'Library author'), 'license': pcm.get('license', 'See source notices'), 'library_prefix': 'PCM_'}
        added = []; unchanged = 0
        # Retain provenance reports and unreferenced native models as package
        # resources. They are included in full saves, not filtered exports.
        from .storage import write_payload
        # Content-address the supplementary material too, so importing a newer
        # release never silently replaces or drops earlier source notices.
        extra_key = sha((identifier + ':' + options['version']).encode())[:20]
        extra_source = root / 'extras'
        extra_files = Payload({p: source.asset(p) for p in source
                               if p.startswith('resources/') or (p.startswith('3dmodels/') and p not in used_models)})
        write_payload(extra_source, extra_files)
        if not extra_source.exists(): extra_source.mkdir()
        with catalog.write_lock():
            current = {p['path']: p for p in catalog.libraries()}
            for item in libraries:
                if item['path'] in current and current[item['path']] != item:
                    raise PartShelfError('Library hierarchy conflicts with an existing folder: ' + item['path'])
                current[item['path']] = item
            candidates = [p for p in stage.root.iterdir() if p.is_dir()]
            for part in candidates:
                if (catalog.root / part.name).exists():
                    old = json.loads((catalog.root / part.name / '1/integrity.json').read_text())
                    new = json.loads((part / '1/integrity.json').read_text())
                    if old != new: raise PartShelfError('A native component already exists with different content: ' + part.name)
                    unchanged += 1
            previous = catalog.libraries_path.read_bytes() if catalog.libraries_path.exists() else None
            extra_destination = catalog.root / '.native-packages' / extra_key
            if extra_destination.exists():
                previous_extras = {p.relative_to(extra_destination).as_posix(): sha(p.read_bytes())
                                   for p in extra_destination.rglob('*') if p.is_file()}
                if previous_extras != extra_files.checksums():
                    raise PartShelfError('This package version already has different source resources.')
            options_path = catalog.root / 'package-options.json'
            save_options = not options_path.exists() and not catalog.list()
            extra_added = False
            try:
                for part in candidates:
                    destination = catalog.root / part.name
                    if not destination.exists(): part.rename(destination); added.append(destination)
                if not extra_destination.exists():
                    extra_destination.parent.mkdir(parents=True, exist_ok=True)
                    extra_source.rename(extra_destination); extra_added = True
                atomic_write(catalog.libraries_path, canonical({'schema': 1, 'libraries': list(current.values())}))
                if save_options: atomic_write(options_path, canonical(options))
            except Exception:
                for part in added: shutil.rmtree(part)
                if extra_added: shutil.rmtree(extra_destination)
                if save_options: options_path.unlink(missing_ok=True)
                if previous is not None: atomic_write(catalog.libraries_path, previous)
                else: catalog.libraries_path.unlink(missing_ok=True)
                raise
        return {'name': pcm['name'], 'imported': len(added), 'unchanged': unchanged, 'symbols': count,
                'footprints': len(fp_paths), 'options': options}
