import base64
import copy
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from partshelf import sexpr as sx, projects, trash
from partshelf.boards import inspect_boards, read_board, prepare_module, transform
from partshelf.components import Catalog, Source, validate_component
from partshelf.files import PartShelfError, load_source
from partshelf.packages import export_package, import_package
from partshelf.web import Application
from test_partshelf import WorkspaceTest

BOARD = b'''<?xml version="1.0"?><!DOCTYPE eagle SYSTEM "eagle.dtd">
<eagle><drawing><board><plain>
<wire x1="0" y1="0" x2="20" y2="0" layer="20"/>
<wire x1="20" y1="0" x2="20" y2="10" layer="20"/>
<wire x1="20" y1="10" x2="0" y2="10" layer="20"/>
<wire x1="0" y1="10" x2="0" y2="0" layer="20"/>
</plain><libraries><library name="test"><packages>
<package name="1X02"><pad name="1" x="1" y="0" drill="1"/><pad name="2" x="3.54" y="0" drill="1"/></package>
<package name="USB_C"><pad name="S" x="0" y="0" drill="1"/><smd name="1" x="1" y="0"/><hole x="1" y="1" drill=".5"/></package>
<package name="MOUNTINGHOLE_2.5"><pad name="P$1" x="0" y="0" drill="2.5"/></package>
</packages></library></libraries><elements>
<element name="J1" library="test" package="1X02" x="2" y="2" rot="R90"/>
<element name="J2" library="test" package="1X02" x="15" y="2" rot="MR90"/>
<element name="USB" library="test" package="USB_C" x="5" y="5"/>
<element name="H1" library="test" package="MOUNTINGHOLE_2.5" x="18" y="8"/>
</elements><signals><signal name="GND"><contactref element="J1" pad="1"/><contactref element="J2" pad="1"/></signal></signals></board></drawing></eagle>'''


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.board = read_board("test.brd", BOARD)
        self.files = {"test.brd": BOARD, "test.sch": b"schematic source", "LICENSE.txt": b"Example license"}
        self.connections = [{"id": pad["id"], "name": pad["net"] or pad["id"]} for group in self.board["groups"] if group["recommended"] for pad in group["pads"]]

    def prepare(self, **settings):
        return prepare_module(self.board, self.files, {"connections":self.connections, **settings}, {"id":"example.board"})

    def test_top_view_rotation_and_mirroring(self):
        import xml.etree.ElementTree as ET
        self.assertEqual(self.board["dimensions"], [20, 10])
        pads = {p["id"]:p for g in self.board["groups"] for p in g["pads"]}
        self.assertEqual(pads["J1.1"]["at"], [-8, 2])
        self.assertEqual(pads["J2.1"]["at"], [5, 2])
        for rot, expected in [("R90", [-2,1]), ("MR90", [2,1]), ("MR0", [-1,2]), ("SMR180", [1,-2])]:
            result = transform(ET.fromstring(f'<element x="0" y="0" rot="{rot}"/>'), [1,2])
            for actual, value in zip(result, expected):self.assertAlmostEqual(actual,value)

    def test_internal_usb_holes_are_excluded(self):
        self.assertEqual([g["reference"] for g in self.board["groups"] if g["recommended"]], ["J1","J2"])
        self.assertEqual(len(self.board["holes"]),1)
        self.assertEqual(self.board["holes"][0]["at"],[8,-3])

    def test_review_generates_selected_passive_pins_and_no_carrier_cutout(self):
        meta, files = self.prepare(connections=[{"id":"J1.2", "name":"RESET"}], mounting="guides")
        validate_component(meta,files)
        fp=sx.loads(files["footprints/Part.kicad_mod"].decode())
        self.assertEqual([sx.value(p[1]) for p in sx.children(fp,"pad")],["J1.2"])
        self.assertEqual(len(sx.children(fp,"fp_circle")),1)
        self.assertNotIn(b'Edge.Cuts',files["footprints/Part.kicad_mod"])
        self.assertNotIn(b'(via',files["footprints/Part.kicad_mod"])
        self.assertEqual(meta["pins"],["J1.2"])
        self.assertEqual(files["sources/board.brd"], BOARD)
        self.assertEqual(files["sources/board.sch"],self.files["test.sch"])
        self.assertTrue(any(data==b'Example license' for data in files.values()))
        pins=[n for n in sx.walk(sx.loads(files["symbol.kicad_sym"].decode())) if sx.tag(n)=="pin"]
        self.assertEqual(sx.value(pins[0][1]),"passive")
        self.assertEqual(sx.field(pins[0],"name"),"RESET")

    def test_mounting_drills_are_unnumbered_npth(self):
        _,files=self.prepare(mounting="drill")
        pads=sx.children(sx.loads(files["footprints/Part.kicad_mod"].decode()),"pad")
        holes=[p for p in pads if sx.value(p[2])=="np_thru_hole"]
        self.assertEqual(len(holes),1)
        self.assertEqual(sx.value(holes[0][1]),"")
        self.assertEqual(sx.field(holes[0],"drill"),"2.5")

    def test_bad_settings_and_overlaps_are_rejected(self):
        for settings in ({"connections":[]},{"connections":[{"id":"USB.missing","name":"No"}]},{"connections":self.connections*2},{"drill":float('nan')},{"diameter":1.1},{"diameter":3},{"mounting":"oops"},{"holes":["not-a-hole"]}):
            with self.subTest(settings=settings),self.assertRaises(PartShelfError):self.prepare(**settings)

    def test_circle_and_curved_outline_extents(self):
        from partshelf.boards import segment
        line=segment([1,0],[-1,0],180)
        self.assertAlmostEqual(line["mid"][1],1)
        self.assertEqual(line["curve"],180)
        import re
        circular=re.sub(br'<plain>.*?</plain>',b'<plain><circle x="0" y="0" radius="10" layer="20"/></plain>',BOARD,flags=re.S)
        board=read_board("circle.brd",circular)
        self.assertEqual(board["dimensions"],[20,20])
        self.assertEqual(len(board["outline"]),2)
        self.assertAlmostEqual(board["outline"][0]["mid"][1],10)

    def test_unsafe_xml_and_incomplete_outlines_fail(self):
        for data in (BOARD.replace(b'<eagle>',b'<!ENTITY boom "x"><eagle>'), BOARD.replace(b'x2="20" y2="0"',b'x2="19" y2="0"',1), BOARD.replace(b'rot="R90"',b'rot="invalid"')):
            with self.assertRaises(PartShelfError):read_board('broken.brd',data)

    def test_application_detects_board_and_prepares_review_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);app=Application(root,root/'catalog')
            result=app.inspect({'files':[{'name':'test.brd','data':base64.b64encode(BOARD).decode()}]})
            self.assertEqual(result['kind'],'board')
            with mock.patch.object(app,'previews',return_value={}):
                prepared=app.post('prepare-board',{'session':result['session'],'board':'test.brd','settings':{'connections':self.connections},'metadata':{'id':'example.board'}})
            self.assertEqual(prepared['metadata']['pins'],['J1.1','J1.2','J2.1','J2.2'])
            result=app.post('publish',{'prepared':prepared['prepared']})
            self.assertEqual(result['revision'],1)


class DeletionTests(WorkspaceTest):
    def test_all_revisions_restore_byte_for_byte_and_projects_survive(self):
        first=self.publish();self.publish(name='Resistor_10k', description='Revision two')
        projects.initialize(self.project)
        projects.install(self.project,self.catalog,first['id'],1)
        before={rev:self.catalog.read(first['id'],rev) for rev in (1,2)}
        archive=export_package(self.catalog)
        result=trash.delete(self.catalog,[{'id':first['id'],'revision':2}])
        self.assertEqual(self.catalog.list(),[])
        self.assertEqual(trash.deleted(self.catalog)[0]['components'][0]['revisions'],[1,2])
        self.assertTrue(projects.verify(self.project)['ok'])
        projects.restore(self.project,self.catalog)
        self.assertTrue(projects.verify(self.project)['ok'])
        replacement = self.publish()
        self.assertEqual(replacement['revision'],1)
        with self.assertRaisesRegex(PartShelfError,'already exists'):
            trash.restore(self.catalog,result['entry'])
        imported = import_package(self.catalog,self.unpack(archive))
        self.assertGreaterEqual(imported['imported'], 1)
        trash.purge(self.catalog,result['entry'],first['id'])
        self.assertEqual(trash.deleted(self.catalog),[])
        self.assertEqual(self.catalog.revisions(first['id']),[1,2])

    def test_stale_bulk_selection_changes_nothing(self):
        first=self.publish();second=self.publish(name='Capacitor_100n',part_id='test.capacitor')
        with self.assertRaisesRegex(PartShelfError,'selection changed'):
            trash.delete(self.catalog,[{'id':first['id'],'revision':1},{'id':second['id'],'revision':2}])
        self.assertEqual(len(self.catalog.list()),2)
        self.assertEqual(trash.deleted(self.catalog),[])

    def test_move_failure_rolls_back_whole_batch(self):
        first=self.publish();second=self.publish(name='Capacitor_100n',part_id='test.capacitor')
        original=trash.os.rename
        calls=0
        def rename(source,target):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('Simulated full disk')
            original(source,target)
        with mock.patch.object(trash.os,'rename',side_effect=rename),self.assertRaises(OSError):
            trash.delete(self.catalog,[{'id':first['id'],'revision':1},{'id':second['id'],'revision':1}])
        self.assertEqual(len(self.catalog.list()),2)
        self.assertEqual(trash.deleted(self.catalog),[])

    def test_restore_conflict_cannot_overwrite_an_existing_component(self):
        part=self.publish();result=trash.delete(self.catalog,[{'id':part['id'],'revision':1}])
        (self.catalog.root/part['id']).mkdir()
        with self.assertRaisesRegex(PartShelfError,'already exists'):
            trash.restore(self.catalog,result['entry'])
        self.assertEqual(len(trash.deleted(self.catalog)),1)
        self.assertEqual(list((self.catalog.root/part['id']).iterdir()),[])

    def test_delete_and_restore_actions_share_desktop_service_contract(self):
        part=self.publish();app=Application(self.root,self.catalog.root)
        deleted=app.post('delete-components',{'components':[{'id':part['id'],'revision':1}]})
        self.assertEqual(app.state()['catalog'],[])
        result=app.post('restore-components',{'entry':deleted['entry']})
        self.assertEqual(result['restored'],1)
        self.assertEqual(len(app.state()['catalog']),1)
