import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from register_city import validate

class RegistrationTests(unittest.TestCase):
    def test_accepts_decimal_comma(self):
        r=validate(dict(cidade='São Teste',uf='RS',latitude='-28,2',longitude='-52,4'),[])
        self.assertEqual(r['Latitude'],'-28.20000000')
    def test_rejects_duplicate_without_accents(self):
        with self.assertRaises(ValueError):
            validate(dict(cidade='São Teste',uf='RS',latitude=-28,longitude=-52),[dict(Cidade='SAO TESTE',UF='RS')])
    def test_rejects_invalid_coordinates(self):
        for lat,lon in [('NaN',-52),(-52,-28),(0,0)]:
            with self.assertRaises(ValueError):
                validate(dict(cidade='Teste',uf='RS',latitude=lat,longitude=lon),[])
    def test_rejects_invalid_state(self):
        with self.assertRaises(ValueError):
            validate(dict(cidade='Teste',uf='SP',latitude=-28,longitude=-52),[])
