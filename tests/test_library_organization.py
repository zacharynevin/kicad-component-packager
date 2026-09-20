from unittest import mock
from partshelf import libraries, trash
from partshelf.files import PartShelfError
from partshelf.packages import export_package, import_package
from partshelf.components import Catalog
from test_partshelf import WorkspaceTest


class LibraryOrganizationTests(WorkspaceTest):
    def hierarchy(self):
        self.catalog.create_library('Core')
        self.catalog.create_library('Adafruit')
        self.catalog.create_library('Modules','Adafruit')
        return self.publish(collection='Adafruit_Modules')

    def test_move_rename_tree_keeps_history_and_roundtrips(self):
        part=self.hierarchy();original=self.catalog.read(part['id'],1)
        result=libraries.edit(self.catalog,'Adafruit','Adafruit','Core')
        self.assertEqual(result['path'],'Core_Adafruit');self.assertEqual(result['updated'],1)
        self.assertEqual(self.catalog.read(part['id'],1),original)
        self.assertEqual(self.catalog.read(part['id'],2)[0]['collection'],'Core_Adafruit_Modules')
        result=libraries.edit(self.catalog,'Core_Adafruit','Vendor','')
        self.assertEqual(result['path'],'Vendor')
        self.assertEqual(self.catalog.read(part['id'],3)[0]['collection'],'Vendor_Modules')
        other=Catalog(self.root/'other')
        import_package(other,self.unpack(export_package(self.catalog)))
        self.assertEqual(other.libraries(),self.catalog.libraries())
        self.assertEqual(libraries.edit(self.catalog,'Vendor','Vendor','')['updated'],0)

    def test_cycles_and_collisions_leave_catalog_unchanged(self):
        part=self.hierarchy();before=self.catalog.libraries()
        for path,name,parent in [('Adafruit','Adafruit','Adafruit_Modules'),('Adafruit','Core','')]:
            with self.assertRaises(PartShelfError):libraries.edit(self.catalog,path,name,parent)
        self.assertEqual(self.catalog.libraries(),before);self.assertEqual(self.catalog.revisions(part['id']),[1])

    def test_failed_move_rolls_back_only_new_revisions(self):
        part=self.hierarchy();before=self.catalog.read(part['id'],1)
        with mock.patch('partshelf.libraries.atomic_write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):libraries.edit(self.catalog,'Adafruit','Adafruit','Core')
        self.assertEqual(self.catalog.revisions(part['id']),[1]);self.assertEqual(self.catalog.read(part['id'],1),before)

    def test_delete_and_individual_restore_carries_library_hierarchy(self):
        a=self.hierarchy();b=self.publish(part_id='another.part',collection='Adafruit')
        result=libraries.delete(self.catalog,'Adafruit')
        self.assertEqual(result['deleted'],2);self.assertEqual(self.catalog.libraries(),[{'path':'Core','name':'Core','parent':''}])
        self.assertEqual(len(trash.deleted(self.catalog)[0]['libraries']),2)
        trash.restore(self.catalog,result['entry'],a['id'])
        self.assertIn({'path':'Adafruit_Modules','name':'Modules','parent':'Adafruit'},self.catalog.libraries())
        self.assertEqual(self.catalog.read(a['id'],1)[0]['collection'],'Adafruit_Modules')
        self.assertEqual(len(trash.deleted(self.catalog)[0]['components']),1)
        trash.restore(self.catalog,result['entry'])
        self.assertEqual(self.catalog.revisions(b['id']),[1]);self.assertEqual(trash.deleted(self.catalog),[])

    def test_empty_library_can_be_deleted_restored_or_purged(self):
        self.catalog.create_library('Empty')
        entry=libraries.delete(self.catalog,'Empty')['entry']
        self.assertEqual(len(trash.deleted(self.catalog)),1)
        trash.restore(self.catalog,entry);self.assertEqual(self.catalog.libraries()[0]['path'],'Empty')
        entry=libraries.delete(self.catalog,'Empty')['entry']
        trash.purge(self.catalog,entry);self.assertEqual(trash.deleted(self.catalog),[])
