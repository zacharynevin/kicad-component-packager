from unittest import mock

from partshelf import moves, trash, projects
from partshelf.components import properties, validate_component
from partshelf import sexpr as sx
from partshelf.files import PartShelfError
from partshelf.web import Application
from test_partshelf import WorkspaceTest


class ComponentMoveTests(WorkspaceTest):
    def setup_move(self, duplicate=False):
        self.catalog.create_library('Vox')
        self.catalog.create_library('Adafruit', 'Vox')
        self.catalog.create_library('Diodes', 'Vox_Adafruit')
        incoming = self.publish(collection='Imported', category='Unsorted', description='Incoming description')
        target = self.publish(part_id='destination.part', collection='Vox_Adafruit_Diodes',
                              name='Resistor_10k' if duplicate else 'Capacitor_100n')
        return incoming, target, [{'id': incoming['id'], 'revision': 1}]

    def test_selected_move_merges_into_existing_category_and_keeps_history(self):
        incoming, target, selection = self.setup_move()
        original = self.catalog.read(incoming['id'], 1)
        existing = self.catalog.read(target['id'], 1)
        app = Application(self.root, self.catalog.root)
        review = app.post('preview-component-move', {'components': selection, 'destination': 'Vox_Adafruit_Diodes'})
        self.assertEqual(review['conflicts'], [])
        result = app.post('move-components', {'components': selection, 'destination': review['destination'], 'token': review['token']})
        self.assertEqual(result['moved'], 1)
        self.assertEqual(self.catalog.read(incoming['id'], 1), original)
        self.assertEqual(self.catalog.read(target['id'], 1), existing)
        new, files, _ = self.catalog.read(incoming['id'], 2)
        self.assertEqual(new['collection'], 'Vox_Adafruit_Diodes')
        self.assertEqual(new['category'], 'Components')
        for path in new['assets']['models']:
            self.assertEqual(files[path], original[1][path])
        self.assertIn('Imported', [l['path'] for l in self.catalog.libraries()])
        same = [{'id': incoming['id'], 'revision': 2}]
        review = moves.preview(self.catalog, same, new['collection'])
        self.assertEqual(moves.move(self.catalog, same, new['collection'], review['token'])['moved'], 0)

    def test_duplicate_revision_retains_destination_id_and_installed_snapshot(self):
        incoming, target, selection = self.setup_move(True)
        original = self.catalog.read(target['id'], 1)
        projects.initialize(self.project)
        projects.install(self.project, self.catalog, target['id'], 1)
        review = moves.preview(self.catalog, selection, 'Vox_Adafruit_Diodes')
        self.assertEqual(len(review['conflicts']), 1)
        with self.assertRaises(PartShelfError):
            moves.move(self.catalog, selection, review['destination'], review['token'])
        result = moves.move(self.catalog, selection, review['destination'], review['token'],
                            [{'id': incoming['id'], 'target': target['id'], 'action': 'revision'}])
        self.assertEqual(self.catalog.read(target['id'], 1), original)
        meta, files, _ = self.catalog.read(target['id'], 2)
        validate_component(meta, files)
        self.assertEqual(meta['description'], 'Incoming description')
        self.assertEqual(properties(sx.children(sx.loads(files['symbol.kicad_sym'].decode()), 'symbol')[0])['PartShelf ID'], target['id'])
        self.assertTrue(projects.verify(self.project)['ok'])
        self.assertEqual(self.catalog.revisions(incoming['id']), [])
        trash.restore(self.catalog, result['recovery_entry'])
        self.assertEqual(self.catalog.read(incoming['id'], 1)[0]['collection'], 'Imported')

    def test_overwrite_replaces_entry_and_keeps_old_entry_restorable(self):
        incoming, target, selection = self.setup_move(True)
        original = self.catalog.read(target['id'], 1)
        review = moves.preview(self.catalog, selection, 'Vox_Adafruit_Diodes')
        result = moves.move(self.catalog, selection, review['destination'], review['token'],
                            [{'id': incoming['id'], 'target': target['id'], 'action': 'overwrite'}])
        self.assertEqual(self.catalog.revisions(incoming['id']), [1, 2])
        self.assertEqual(self.catalog.revisions(target['id']), [])
        trash.restore(self.catalog, result['recovery_entry'])
        self.assertEqual(self.catalog.read(target['id'], 1), original)

    def test_keep_both_and_stale_destination_review(self):
        incoming, target, selection = self.setup_move(True)
        review = moves.preview(self.catalog, selection, 'Vox_Adafruit_Diodes')
        self.publish(part_id='destination.part', collection='Vox_Adafruit_Diodes')
        with self.assertRaisesRegex(PartShelfError, 'changed'):
            moves.move(self.catalog, selection, review['destination'], review['token'],
                       [{'id': incoming['id'], 'target': target['id'], 'action': 'revision'}])
        review = moves.preview(self.catalog, selection, review['destination'])
        moves.move(self.catalog, selection, review['destination'], review['token'], [{'id': incoming['id'], 'action': 'keep-both'}])
        self.assertEqual(len(self.catalog.list()), 2)
        self.assertEqual(trash.deleted(self.catalog), [])

    def test_failed_merge_restores_source_and_destination(self):
        incoming, target, selection = self.setup_move(True)
        before = self.catalog.list()
        review = moves.preview(self.catalog, selection, 'Vox_Adafruit_Diodes')
        with mock.patch('partshelf.moves.atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                moves.move(self.catalog, selection, review['destination'], review['token'],
                           [{'id': incoming['id'], 'target': target['id'], 'action': 'revision'}])
        self.assertEqual(self.catalog.list(), before)
        self.assertEqual(trash.deleted(self.catalog), [])

    def test_same_mpn_from_different_manufacturers_is_not_auto_matched(self):
        self.assertIsNone(moves.match_reason({'name': 'A', 'manufacturer': 'A', 'mpn': '123'},
                                            {'name': 'B', 'manufacturer': 'B', 'mpn': '123'}))
        self.assertTrue(moves.match_reason({'name': 'A', 'manufacturer': 'Vendor', 'mpn': '123'},
                                          {'name': 'B', 'manufacturer': 'vendor', 'mpn': '123'}))
