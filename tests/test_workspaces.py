"""Project tabs must never redirect a reviewed operation into another project."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from partshelf import projects
from partshelf.components import Source
from partshelf.desktop import DesktopApplication
from partshelf.files import PartShelfError, load_source
from partshelf.web import Application


class OpenProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'data'
        self.app = DesktopApplication(self.data)
        self.a, self.b = self.root / 'one' / 'board', self.root / 'two' / 'board'

    def open_both(self):
        for path in (self.a, self.b):
            self.app.post('create-project', {'path': str(path)})

    def test_open_activate_close_restart_and_alias(self):
        self.open_both()
        self.app.post('select-project', {'path': str(self.a / 'board.kicad_pro')})
        saved = DesktopApplication(self.data)
        self.assertEqual(saved.open_projects, [self.a, self.b])
        self.assertEqual(saved.project, self.a)
        before = {str(p): projects.project_digest(p) for p in (self.a, self.b)}
        saved.post('close-project', {'path': str(self.a)})
        self.assertEqual(saved.project, self.b)
        saved.post('close-project', {'path': str(self.b)})
        restarted = DesktopApplication(self.data)
        self.assertIsNone(restarted.project)
        self.assertEqual(restarted.open_projects, [])
        self.assertEqual(before, {str(p): projects.project_digest(p) for p in (self.a, self.b)})
        restarted.post('select-project', {'path': str(self.a)})
        self.assertEqual(restarted.open_projects, [self.a])

    def test_legacy_settings_migrate_without_opening_all_recent_projects(self):
        projects.initialize(self.a)
        self.app.settings_path.write_text(json.dumps({'project': str(self.a), 'recent_projects': [str(self.a), str(self.b)]}))
        migrated = DesktopApplication(self.data)
        self.assertEqual(migrated.open_projects, [self.a])
        self.assertEqual(migrated.project, self.a)

    def test_reviewed_install_is_bound_to_project_even_with_identical_empty_digests(self):
        self.open_both()
        source = Source.read(load_source(Path(__file__).resolve().parents[1] / 'examples/vendor'))
        choice = source.inspect()['symbols'][0]
        meta, files = source.prepare(choice['key'], choice['footprint'], {'id': 'test.part'})
        self.app.catalog.publish(meta, files)
        self.app.post('activate-project', {'path': str(self.a)})
        plan = self.app.post('plan', {'id': 'test.part', 'revision': 1, 'project_path': str(self.a)})
        self.assertEqual(projects.project_digest(self.a), projects.project_digest(self.b))
        self.app.post('activate-project', {'path': str(self.b)})
        with self.assertRaisesRegex(PartShelfError, 'active project changed'):
            self.app.post('install', plan)
        with self.assertRaisesRegex(PartShelfError, 'explicitly'):
            self.app.post('restore', {})
        with self.assertRaisesRegex(PartShelfError, 'active project changed'):
            self.app.request('export', {'source': 'project-archive?project_path=' + str(self.a), 'destination': str(self.root / 'wrong.zip')})
        self.assertFalse((self.root / 'wrong.zip').exists())
        self.assertEqual(projects.status(self.b)['components'], [])
        self.app.post('activate-project', {'path': str(self.a)})
        self.app.post('install', plan)
        self.assertEqual(len(projects.status(self.a)['components']), 1)
        self.assertEqual(projects.status(self.b)['components'], [])

    def test_missing_project_stays_closeable_and_cannot_be_recreated_by_restore(self):
        self.open_both()
        moved = self.b.with_name('moved')
        self.b.rename(moved)
        restarted = DesktopApplication(self.data)
        state = restarted.state()
        self.assertFalse(state['open_projects'][1]['available'])
        self.assertFalse(state['project']['ok'])
        with self.assertRaisesRegex(PartShelfError, 'unavailable'):
            restarted.post('restore', {'project_path': str(self.b)})
        self.assertFalse(self.b.exists())
        restarted.post('activate-project', {'path': str(self.a)})
        self.assertTrue(restarted.state()['project']['ok'])
        restarted.post('close-project', {'path': str(self.b)})
        self.assertTrue(moved.is_dir())

    def test_damaged_project_does_not_block_other_tabs(self):
        self.open_both()
        (self.b / projects.LOCKFILE).write_text('{broken')
        self.assertFalse(self.app.state()['project']['ok'])
        self.app.post('activate-project', {'path': str(self.a)})
        self.assertTrue(self.app.state()['project']['ok'])

    def test_browser_workspace_is_persistent_and_cannot_escape_workspace_root(self):
        app = Application(self.root, self.data / 'browser')
        app.post('create-project', {'path': 'one/board'})
        app.post('create-project', {'path': 'two/board'})
        saved = Application(self.root, self.data / 'browser')
        self.assertEqual(saved.open_projects, [self.a, self.b])
        with self.assertRaises(PartShelfError):
            saved.post('select-project', {'path': str(self.root.parent)})
        self.assertEqual(saved.open_projects, [self.a, self.b])
