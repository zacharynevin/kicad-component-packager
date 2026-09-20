import csv
import io
import re
import unittest

from partshelf.files import PartShelfError
from partshelf.headers import generated_header_model, header_spec, assembly_csv


class HeaderLengthTests(unittest.TestCase):
    pads = [{'id': 'J1.1', 'at': [0, 0]}, {'id': 'J1.2', 'at': [2.54, 0]}]

    def boxes(self, **settings):
        text = generated_header_model(self.pads, **settings).decode()
        return [(list(map(lambda v: float(v)*2.54, position.split())),
                 list(map(lambda v: float(v)*2.54, size.split())))
                for position, size in re.findall(r'Transform \{ translation ([\d.\-e ]+).*?Box \{ size ([\d.\-e ]+)', text)]

    def test_regular_male_bottom_has_short_top_tails_and_long_bottom_mating_pins(self):
        housing, pin = self.boxes(style='male', mounting_side='bottom')[:2]
        self.assertAlmostEqual(housing[0][2]+housing[1][2]/2, -1.6, places=5)
        self.assertAlmostEqual(pin[0][2]+pin[1][2]/2, 1.4, places=5)
        self.assertAlmostEqual(pin[0][2]-pin[1][2]/2, -10.14, places=5)
        long_pin = self.boxes(style='male', mounting_side='bottom', profile='stacking')[1]
        self.assertAlmostEqual(long_pin[0][2]+long_pin[1][2]/2, 1.4, places=5)
        self.assertAlmostEqual(long_pin[0][2]-long_pin[1][2]/2, -15.14, places=5)

    def test_female_stacking_extends_tail_without_extending_socket_housing(self):
        short = self.boxes(style='female')
        stacking = self.boxes(style='female', profile='stacking')
        self.assertAlmostEqual(short[0][0][2]-short[0][1][2]/2, -3, places=5)
        self.assertAlmostEqual(stacking[0][0][2]-stacking[0][1][2]/2, -11, places=5)
        self.assertEqual(short[1:6], stacking[1:6])

    def test_custom_dimensions_roundtrip_to_bom(self):
        config, requirements = header_spec(self.pads, 'male', profile='custom', mounting_side='bottom',
                                           height=2, mating_length=5, tail_length=2.4, board_thickness=1.2)
        pin = self.boxes(**config)[1]
        self.assertAlmostEqual(pin[0][2]+pin[1][2]/2, 1.2, places=5)
        rows = list(csv.DictReader(io.StringIO(assembly_csv([{'id':'module', 'assembly':requirements}]).decode('utf-8-sig'))))
        self.assertEqual(rows[0]['Header profile'], 'custom')
        self.assertEqual(rows[0]['Mounting side'], 'bottom')
        self.assertEqual(rows[0]['Exposed mating pin length (mm)'], '5.0')
        self.assertEqual(rows[0]['Solder tail length (mm)'], '2.4')
        self.assertEqual(rows[0]['Module PCB thickness (mm)'], '1.2')

    def test_invalid_dimensions_and_orientation_rejected(self):
        for settings in [{'tail_length':float('nan')}, {'mating_length':-1}, {'board_thickness':0},
                         {'profile':'unknown'}, {'mounting_side':'sideways'}]:
            with self.subTest(settings=settings), self.assertRaises(PartShelfError):
                header_spec(self.pads, 'male', **settings)
