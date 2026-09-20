import json
from unittest import mock
from urllib.parse import urlencode

from partshelf import sexpr as sx
from partshelf.boards import prepare_module, read_board
from partshelf.components import Catalog, Source, properties
from partshelf.desktop import DesktopApplication
from partshelf.files import PartShelfError, atomic_write, canonical, load_source, sha
from partshelf.models import edit_models
from partshelf.packages import MANIFEST, OBJECTS, import_package, inspect_package, is_package, package_options
from partshelf.pcm import export_pcm
from test_boards_and_deletion import BOARD
from test_partshelf import WorkspaceTest


class PcmRoundtripTests(WorkspaceTest):
    options = {'name': 'Vox collection', 'identifier': 'com.vox.components',
               'author': 'Vox', 'version': '2.3.4', 'license': 'CC0-1.0', 'library_prefix': 'PCM_'}

    def collection(self):
        self.catalog.create_library('Vox')
        self.catalog.create_library('Adafruit', 'Vox')
        self.catalog.create_library('Empty', 'Vox_Adafruit')
        part = self.publish(collection='Vox_Adafruit', manufacturer='Example', mpn='R-10K')
        meta, files, _ = self.catalog.read(part['id'], 1)
        meta, files = edit_models(meta, files, {'models': [{'index': 0, 'offset': [1.2, -3.4, 5.6], 'rotate': [0, 0, 90]}]})
        meta['notes'] = 'Revised placement'
        self.catalog.publish(meta, files)
        board = read_board('module.brd', BOARD)
        pads = [{'id': p['id'], 'name': p['id']} for g in board['groups'] if g['recommended'] for p in g['pads']]
        meta, files = prepare_module(board, {'module.brd': BOARD}, {'connections': pads},
                                     {'id': 'test.module', 'collection': 'Vox_Adafruit'})
        meta, files = edit_models(meta, files, {'headers': {'style': 'male', 'profile': 'stacking', 'mounting_side': 'bottom'}})
        self.catalog.publish(meta, files)

    def test_same_pcm_zip_restores_full_history_tree_properties_models_and_options(self):
        self.collection()
        data = export_pcm(self.catalog, options=self.options)
        files = self.unpack(data)
        manifest, items = inspect_package(files)
        self.assertEqual(len(items), 3)
        self.assertEqual(json.loads(files['metadata.json'])['versions'][0]['install_size'], sum(map(len, files.values())))
        other = Catalog(self.root / 'other')
        self.assertEqual(import_package(other, files)['imported'], 3)
        self.assertEqual(other.libraries(), self.catalog.libraries())
        self.assertEqual(package_options(other), self.options)
        for part in self.catalog.list():
            self.assertEqual(other.revisions(part['id']), part['revisions'])
            for revision in part['revisions']:
                self.assertEqual(other.read(part['id'], revision), self.catalog.read(part['id'], revision))
        self.assertEqual(export_pcm(other), data)
        # The native model is also the editable snapshot's model, stored once.
        meta, payload, _ = self.catalog.read('test.resistor', 2)
        checksum = sha(payload[meta['assets']['models'][0]])
        self.assertTrue(manifest['objects'][checksum].startswith('3dmodels/'))
        self.assertNotIn(OBJECTS + checksum, files)
        symbol = next(s for s in sx.children(sx.loads(files['symbols/Vox_Adafruit.kicad_sym'].decode()), 'symbol') if sx.value(s[1]) == 'test.resistor')
        self.assertEqual(properties(symbol)['PartShelf Revision'], '2')

    def test_selected_older_revision_excludes_other_components_and_future_history(self):
        self.collection()
        files = self.unpack(export_pcm(self.catalog, [('test.resistor', 1)]))
        manifest, items = inspect_package(files)
        self.assertEqual([(m['id'], m['revision']) for m, _, _ in items], [('test.resistor', 1)])
        library = sx.loads(files['symbols/Vox_Adafruit.kicad_sym'].decode())
        self.assertEqual(len(sx.children(library, 'symbol')), 1)
        self.assertEqual(manifest['libraries'], self.catalog.libraries())

    def test_native_or_saved_asset_damage_and_incomplete_manifest_do_not_modify_catalog(self):
        self.collection()
        files = self.unpack(export_pcm(self.catalog))
        for path in [next(p for p in files if p.startswith('3dmodels/')),
                     next(p for p in files if p.startswith(OBJECTS)), MANIFEST]:
            with self.subTest(path=path):
                damaged = {**files, path: files[path] + b'corrupt'}
                other = Catalog(self.root / 'unmodified')
                self.assertTrue(is_package(damaged))
                with self.assertRaises(PartShelfError):
                    import_package(other, damaged)
                self.assertFalse(other.root.exists())

    def test_failed_library_write_rolls_back_revisions_and_saved_options(self):
        self.collection()
        files = self.unpack(export_pcm(self.catalog, options=self.options))
        other = Catalog(self.root / 'other')
        other.create_library('Existing')
        settings = other.root / 'package-options.json'
        atomic_write(settings, canonical({'name': 'Keep me'}))
        previous = other.libraries_path.read_bytes()
        def fail_library(path, data):
            if path == other.libraries_path:
                raise OSError('disk full')
            atomic_write(path, data)
        with mock.patch('partshelf.packages.atomic_write', side_effect=fail_library):
            with self.assertRaisesRegex(OSError, 'disk full'):
                import_package(other, files)
        self.assertEqual(other.list(), [])
        self.assertEqual(other.libraries_path.read_bytes(), previous)
        self.assertEqual(package_options(other), {'name': 'Keep me'})

    def test_desktop_save_reopen_and_edit_continues_revision_history(self):
        self.collection()
        first = DesktopApplication(self.root / 'desktop')
        first.catalog = self.catalog
        destination = self.root / 'Vox.zip'
        first.request('export', {'source': 'pcm-package?' + urlencode({'options': json.dumps(self.options)}), 'destination': str(destination)})
        self.assertEqual(first.state()['package_options'], self.options)
        second = DesktopApplication(self.root / 'second')
        inspection = second.native_inspect([str(destination)])
        self.assertEqual(inspection['kind'], 'package')
        self.assertEqual(len(inspection['components']), 2)
        self.assertEqual(inspection['revision_count'], 3)
        second.post('import-package', {'session': inspection['session']})
        self.assertEqual(second.state()['package_options'], self.options)
        second.post('edit-properties', {'components': [{'id': 'test.resistor', 'revision': 2}], 'fields': {'mpn': 'R-10K-NEW'}})
        self.assertEqual(second.catalog.revisions('test.resistor'), [1, 2, 3])
        self.assertEqual(second.catalog.read('test.resistor', 1), self.catalog.read('test.resistor', 1))

    def test_empty_library_zip_is_valid_and_legacy_format_is_not_imported(self):
        self.catalog.create_library('Empty')
        files = self.unpack(export_pcm(self.catalog))
        self.assertEqual(sx.tag(sx.loads(files['symbols/Empty.kicad_sym'].decode())), 'kicad_symbol_lib')
        other = Catalog(self.root / 'other')
        import_package(other, files)
        self.assertEqual(other.libraries(), self.catalog.libraries())
        legacy = {'package.json': canonical({'format': 'kcpkg', 'format_version': 1, 'components': []})}
        self.assertFalse(is_package(legacy))
        with self.assertRaises(PartShelfError):
            Source.read(legacy)

    def test_export_limits_prevent_creating_zip_that_cannot_be_reopened(self):
        self.publish()
        with mock.patch('partshelf.files.MAX_BYTES', 100):
            with self.assertRaisesRegex(PartShelfError, 'Export smaller library groups'):
                export_pcm(self.catalog)
