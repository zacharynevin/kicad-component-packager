import copy
import json
from pathlib import Path
import zipfile
from unittest import mock

from partshelf import sexpr as sx, trash
from partshelf.components import Catalog, properties, set_property, validate_component
from partshelf.collections import edit_metadata
from partshelf.desktop import DesktopApplication
from partshelf.files import PartShelfError, canonical, load_source
from partshelf.native_library import import_native, library_tree
from partshelf.packages import inspect_package, import_package, package_options
from partshelf.pcm import export_pcm_file
from partshelf.storage import unpack_library
from test_partshelf import WorkspaceTest


class NativeLibraryTests(WorkspaceTest):
    def native_zip(self):
        meta, assets = self.prepare()
        document = sx.loads(assets['symbol.kicad_sym'].decode())
        symbol = sx.children(document, 'symbol')[0]
        set_property(symbol, 'Footprint', '')
        set_property(symbol, 'Datasheet', 'www.example.com/resistor.pdf')
        footprint = sx.loads(assets['footprints/Part.kicad_mod'].decode())
        model = sx.children(footprint, 'model')[0]
        model[1] = sx.q('${KICAD10_3RD_PARTY}/3dmodels/com_example_core/passive.wrl')
        files = {'metadata.json': canonical({'name': 'Core', 'identifier': 'com.example.core', 'type': 'library',
                                            'versions': [{'version': '1.0.0'}]}),
                 'symbols/Core_Generic_Resistors.kicad_sym': sx.encode(document),
                 'footprints/Core_Resistors.pretty/Part.kicad_mod': sx.encode(footprint),
                 '3dmodels/passive.wrl': assets[meta['assets']['models'][0]],
                 '3dmodels/unused.wrl': assets[meta['assets']['models'][0]] + b'\n',
                 'resources/LICENSE.txt': b'Original source attribution'}
        paired = copy.deepcopy(document)
        set_property(sx.children(paired, 'symbol')[0], 'Footprint', 'PCM_Core_Resistors:Part')
        files['symbols/Core_Vendors_Example_Resistors.kicad_sym'] = sx.encode(paired)
        path = self.root / 'Core.zip'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items(): archive.writestr(name, data)
        return path

    def test_full_native_import_preserves_unpaired_entries_and_exports_editable_zip(self):
        result = import_native(self.catalog, self.native_zip())
        self.assertEqual((result['symbols'], result['footprints']), (2, 1))
        parts = self.catalog.list()
        self.assertEqual(package_options(self.catalog)['identifier'], 'com.example.core')
        self.assertEqual(len(parts), 3)
        self.assertEqual(sum(bool(p['assets']['footprint']) for p in parts), 2)
        self.assertEqual(sum(bool(p['assets']['models']) for p in parts), 2)
        unpaired = next(p for p in parts if not p['assets']['footprint'])
        self.assertEqual(unpaired['datasheet'], 'https://www.example.com/resistor.pdf')
        self.assertTrue(any(p['path'] == 'Core_Vendors_Example' and p['parent'] == 'Core_Vendors' for p in self.catalog.libraries()))
        output = self.root / 'saved.zip'
        export_pcm_file(self.catalog, output)
        files = load_source(output); manifest, entries = inspect_package(files)
        self.assertEqual(len(entries), 3)
        self.assertTrue(any('unused.wrl' in name for name in manifest['extra_files']))
        other = Catalog(self.root / 'other'); import_package(other, files)
        self.assertEqual(other.libraries(), self.catalog.libraries())
        for part in parts: self.assertEqual(other.read(part['id'], 1), self.catalog.read(part['id'], 1))
        again = self.root / 'again.zip'; export_pcm_file(other, again)
        self.assertEqual(output.read_bytes(), again.read_bytes())
        selected = self.root / 'selected.zip'
        export_pcm_file(other, selected, [(p['id'], p['revision']) for p in parts if p['assets']['footprint'] and p['assets']['models']])
        chosen, selected_entries = inspect_package(load_source(selected))
        self.assertEqual(len(selected_entries), 2); self.assertEqual(chosen['extra_files'], {})

    def test_native_entries_support_metadata_revision_delete_and_restore(self):
        import_native(self.catalog, self.native_zip())
        for part in self.catalog.list():
            meta, files, _ = self.catalog.read(part['id'], 1)
            changed, payload = edit_metadata(meta, files, {'manufacturer': 'New manufacturer'})
            updated = self.catalog.publish(changed, payload)
            self.assertEqual(updated['revision'], 2)
            validate_component(changed, payload)
            entry = trash.delete(self.catalog, [{'id': part['id'], 'revision': 2}])['entry']
            trash.restore(self.catalog, entry)
            self.assertEqual(self.catalog.revisions(part['id']), [1, 2])

    def test_native_inspection_and_duplicate_import_keep_stable_identities(self):
        path = self.native_zip(); app = DesktopApplication(self.root / 'desktop')
        inspection = app.native_inspect([str(path)])
        self.assertEqual(inspection['kind'], 'native-package')
        self.assertEqual(inspection['footprints'], 1)
        result = app.post('import-package', {'session': inspection['session']})
        self.assertEqual(result['imported'], 3)
        before = app.catalog.list()
        result = app.post('import-package', {'session': inspection['session']})
        self.assertEqual(result['unchanged'], 3)
        self.assertEqual(app.catalog.list(), before)

    def test_streaming_archive_rejects_unsafe_paths_and_limits(self):
        for name in ['../escape', '/absolute', 'A/../../escape']:
            path = self.root / 'unsafe.zip'
            with zipfile.ZipFile(path, 'w') as archive: archive.writestr(name, 'bad')
            with self.assertRaises(PartShelfError): unpack_library(path, self.root / 'unpack')
        with mock.patch('partshelf.storage.LIBRARY_BYTES', 4):
            with self.assertRaisesRegex(PartShelfError, 'limit'):
                unpack_library(self.native_zip(), self.root / 'limited')

    def test_missing_footprint_aborts_before_publication(self):
        path = self.native_zip()
        with zipfile.ZipFile(path, 'a') as archive:
            document = sx.loads(self.source_files[next(p for p in self.source_files if p.endswith('.kicad_sym'))].decode())
            archive.writestr('symbols/Unbundled.kicad_sym', sx.encode(document))
        with self.assertRaisesRegex(PartShelfError, 'Missing linked footprint'):
            import_native(self.catalog, path)
        self.assertEqual(self.catalog.list(), [])

    def test_multiword_vendor_is_one_folder(self):
        name = 'Core_Vendors_Texas_Instruments_Amplifier_Operational'
        tree = library_tree([name], [], {name: 'Texas Instruments'})
        by_path = {entry['path']: entry for entry in tree}
        self.assertEqual(by_path[name]['parent'], 'Core_Vendors_Texas_Instruments')
        self.assertEqual(by_path['Core_Vendors_Texas_Instruments']['name'], 'Texas Instruments')

    def test_cached_catalog_observes_other_writer_and_checks_metadata_index(self):
        import_native(self.catalog, self.native_zip())
        parts = self.catalog.list(); part = parts[0]
        other = Catalog(self.catalog.root)
        meta, files, _ = other.read(part['id'], 1)
        changed, payload = edit_metadata(meta, files, {'manufacturer': 'Updated'})
        other.publish(changed, payload)
        self.assertEqual(next(p for p in self.catalog.list() if p['id'] == part['id'])['revision'], 2)
        path = self.catalog.root / part['id'] / '2' / 'integrity.json'
        index = json.loads(path.read_bytes()); index['files'].pop('component.json')
        path.write_bytes(canonical(index))
        with self.assertRaisesRegex(PartShelfError, 'component metadata'):
            self.catalog.read(part['id'], 2)

    def test_changed_native_archive_requires_new_inspection(self):
        path = self.native_zip(); app = DesktopApplication(self.root / 'desktop')
        inspection = app.native_inspect([str(path)])
        with zipfile.ZipFile(path, 'a') as archive: archive.writestr('resources/new.txt', 'change')
        with self.assertRaisesRegex(PartShelfError, 'ZIP changed'):
            app.post('import-package', {'session': inspection['session']})
        self.assertEqual(app.catalog.list(), [])
