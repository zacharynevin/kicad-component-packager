import unittest
from tools.core_library.build import classify, annotate_symbol, fingerprint, HIROSE_FP
from partshelf import sexpr as sx
from partshelf.components import properties


class CoreLibraryRulesTests(unittest.TestCase):
    def test_generic_network_is_not_vendor_locked_by_example_datasheet(self):
        target,vendor,evidence,website,kind=classify('Device','R_Network04',{'Datasheet':'http://www.vishay.com/docs/31509/csc.pdf'})
        self.assertEqual(kind,'Resistors');self.assertEqual(vendor,'');self.assertEqual(target,'Core_Generic_Resistors')

    def test_common_passives_have_no_invented_value_rating_or_mpn(self):
        for name,kind in [('R','Resistors'),('C','Capacitors'),('L','Inductors')]:
            node=sx.loads(f'(symbol "{name}" (in_bom yes) (symbol "{name}_0_1" (rectangle (start 0 0) (end 1 1))))')
            before=fingerprint(node)
            annotate_symbol(node,'Device:'+name,'','generic','',kind,'Unassigned','Requires footprint selection')
            self.assertEqual(before,fingerprint(node));p=properties(node)
            self.assertNotIn('MPN',p);self.assertNotIn('Manufacturer',p)
            self.assertEqual(p['Tolerance'],'');self.assertEqual(p['Package'],'')
            self.assertTrue(p['Substitutions'].startswith('Allowed only'))

    def test_vendor_inference_is_labelled_and_order_code_not_assumed(self):
        result=classify('Amplifier_Operational','EXAMPLE123',{'Datasheet':'https://www.ti.com/lit/ds/example.pdf'})
        self.assertEqual(result[1],'Texas Instruments');self.assertIn('Inferred',result[2])
        node=sx.loads('(symbol "EXAMPLE123")')
        annotate_symbol(node,'Amplifier_Operational:EXAMPLE123',*result[1:],'Assigned','Included')
        self.assertEqual(properties(node)['MPN'],'');self.assertEqual(properties(node)['MPN Candidate'],'EXAMPLE123')

    def test_distributor_host_does_not_identify_manufacturer(self):
        self.assertEqual(classify('Analog','Example',{'Datasheet':'https://www.mouser.com/datasheet/1/test.pdf'})[1],'')
