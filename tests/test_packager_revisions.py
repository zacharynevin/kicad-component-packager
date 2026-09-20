import base64
import csv
import io
import json
import re
import unittest
from unittest import mock

from partshelf import sexpr as sx, trash
from partshelf.components import Catalog, properties, validate_component
from partshelf.files import PartShelfError
from partshelf.models import edit_models, model_scene
from partshelf.packages import export_package, import_package
from partshelf.pcm import export_pcm
from partshelf.web import Application
from test_partshelf import WorkspaceTest
from test_boards_and_deletion import BOARD
from partshelf.boards import read_board, prepare_module
from partshelf.headers import configure_headers


class RevisionFeaturesTests(WorkspaceTest):
    def test_edit_individual_properties_creates_only_changed_revision(self):
        part=self.publish()
        original=self.catalog.read(part['id'],1)
        app=Application(self.root,self.catalog.root)
        fields={'manufacturer':'Hirose','mpn':'EXAMPLE-123','website':'https://www.hirose.com/','datasheet':'https://www.hirose.com/example.pdf','description':'Updated description','name':'New displayed name'}
        result=app.post('edit-properties',{'components':[{'id':part['id'],'revision':1}],'fields':fields})
        self.assertEqual(result['updated'],1)
        new,files,_=self.catalog.read(part['id'],2)
        props=properties(sx.children(sx.loads(files['symbol.kicad_sym'].decode()),'symbol')[0])
        for key in fields:self.assertEqual(new[key],fields[key])
        self.assertEqual(props['MPN'],fields['mpn']);self.assertEqual(props['Datasheet'],fields['datasheet'])
        self.assertEqual(self.catalog.read(part['id'],1),original)
        result=app.post('edit-properties',{'components':[{'id':part['id'],'revision':2}],'fields':fields})
        self.assertEqual(result['unchanged'],1);self.assertEqual(self.catalog.revisions(part['id']),[1,2])
        with self.assertRaises(PartShelfError):app.post('edit-properties',{'components':[{'id':part['id'],'revision':1}],'fields':{'mpn':'stale'}})

    def test_empty_hierarchy_roundtrip_and_imported_parent(self):
        self.catalog.create_library('Adafruit')
        self.catalog.create_library('Core','Adafruit')
        other=Catalog(self.root/'other')
        import_package(other,self.unpack(export_package(self.catalog)))
        self.assertEqual(other.libraries(),self.catalog.libraries())
        self.publish(collection='Vendor')
        self.assertEqual(self.catalog.create_library('Modules','Vendor')['path'],'Vendor_Modules')
        with self.assertRaises(PartShelfError):self.catalog.create_library('vendor')

    def test_single_library_pcm_preserves_name_and_model_paths(self):
        part=self.publish(collection='Adafruit_Core')
        files=self.unpack(export_pcm(self.catalog))
        self.assertIn('symbols/Adafruit_Core.kicad_sym',files)
        sym=sx.children(sx.loads(files['symbols/Adafruit_Core.kicad_sym'].decode()),'symbol')[0]
        self.assertEqual(properties(sym)['Footprint'],'PCM_Adafruit_Core:'+part['id'])
        fp=sx.loads(files['footprints/Adafruit_Core.pretty/'+part['id']+'.kicad_mod'].decode())
        path=sx.value(sx.children(fp,'model')[0][1])
        self.assertTrue(path.startswith('${KICAD10_3RD_PARTY}/3dmodels/local_kicad-component-packager_collection/Adafruit_Core.3dshapes/'))
        self.publish(part_id='another.part',collection='Adafruit Core')
        with self.assertRaisesRegex(PartShelfError,'same KiCad export name'):export_pcm(self.catalog)

    def test_scene_uses_local_geometry_without_calling_kicad(self):
        part=self.publish();meta,files,_=self.catalog.read(part['id'],1)
        with mock.patch('partshelf.models.run_cli',side_effect=AssertionError('No subprocess')):
            scene=Application(self.root,self.catalog.root).post('model-scene',{'id':part['id'],'revision':1})
        self.assertEqual(len(scene['pads']),2);self.assertEqual(scene['models'][0]['scale'],[1,1,1])
        self.assertEqual(base64.b64decode(scene['assets'][0]['data']),files[meta['assets']['models'][0]])

    def test_individual_restore_and_purge_preserve_group_remainder(self):
        a=self.publish();b=self.publish(name='Capacitor_100n',part_id='test.cap')
        entry=trash.delete(self.catalog,[{'id':a['id'],'revision':1},{'id':b['id'],'revision':1}])['entry']
        trash.restore(self.catalog,entry,a['id'])
        self.assertEqual([c['id'] for c in trash.deleted(self.catalog)[0]['components']],[b['id']])
        self.publish(name='Capacitor_100n',part_id='test.cap')
        before=self.catalog.read(b['id'],1)
        trash.purge(self.catalog,entry,b['id'])
        self.assertEqual(self.catalog.read(b['id'],1),before);self.assertEqual(trash.deleted(self.catalog),[])

    def test_header_geometry_and_supplemental_bom_survive_export(self):
        board=read_board('test.brd',BOARD)
        pads=[{'id':p['id'],'name':p['id']} for g in board['groups'] if g['recommended'] for p in g['pads']]
        meta,files=prepare_module(board,{'test.brd':BOARD},{'connections':pads,'header':'male'},{'id':'test.module'})
        validate_component(meta,files)
        self.assertEqual([r['positions'] for r in meta['assembly']],[2,2])
        self.assertEqual([r['pitch_mm'] for r in meta['assembly']],[2.54,2.54])
        data=files[meta['generated_header_models'][0]].decode()
        position=re.search(r'translation ([\d.\-e ]+) children',data)[1].split()
        recipe=json.loads(files['sources/module-recipe.json'])
        self.assertAlmostEqual(float(position[0])*2.54,recipe['connections'][0]['at'][0],places=5)
        self.assertAlmostEqual(float(position[1])*2.54,-recipe['connections'][0]['at'][1],places=5)
        self.assertAlmostEqual(float(position[2])*2.54,1.27,places=5)
        meta,files=edit_models(meta,files,{'attachment':{'name':'board.step','data':base64.b64encode(b'ISO-10303-21;\nEND-ISO-10303-21;').decode()}})
        boardpath=meta['assets']['models'][-1]
        meta,files=configure_headers(meta,files,{'style':'female','height':8.5})
        self.assertIn(boardpath,meta['assets']['models']);self.assertEqual(len(meta['assets']['models']),2)
        self.catalog.publish(meta,files)
        archive=self.unpack(export_pcm(self.catalog))
        rows=list(csv.DictReader(io.StringIO(archive['resources/assembly-bom.csv'].decode('utf-8-sig'))))
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['Sourcing'],'generic');self.assertEqual(rows[0]['MPN'],'')
        self.assertIn('substitution',archive['resources/README.txt'].decode())
        meta,files=configure_headers(meta,files,{'style':'none'})
        self.assertEqual(meta['assets']['models'],[boardpath]);self.assertEqual(meta['assembly'],[])
