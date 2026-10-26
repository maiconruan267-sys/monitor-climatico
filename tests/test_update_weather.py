import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from update_weather import merge_city, validate_daily, FIELDS

class HistoryTests(unittest.TestCase):
    def test_preserves_old_dates_and_replaces_overlap(self):
        old = {'daily': {'time': ['2026-09-01','2026-09-02'], 'min': [1,2]}}
        new = {'cidade': 'Teste', 'daily': {'time': ['2026-09-02','2026-09-03'], 'min': [20,30]}}
        merged = merge_city(old, new)
        self.assertEqual(merged['daily']['time'], ['2026-09-01','2026-09-02','2026-09-03'])
        self.assertEqual(merged['daily']['min'], [1,20,30])
        self.assertEqual(merged['daily']['rain'], [None,None,None])

    def test_rejects_incomplete_window(self):
        with self.assertRaises(ValueError):
            validate_daily({'time': ['2026-10-26']}, '2026-10-26')

    def test_rejects_missing_required_value(self):
        from datetime import date, timedelta
        dates = [(date(2026,10,26)+timedelta(days=i)).isoformat() for i in range(-15,16)]
        daily = {'time': dates, **{v:[1]*31 for v in FIELDS.values()}}
        validate_daily(daily,'2026-10-26')
        daily['temperature_2m_max'][-1] = None
        with self.assertRaises(ValueError):
            validate_daily(daily,'2026-10-26')


class IncrementalTests(unittest.TestCase):
    def fixture(self):
        from datetime import date, timedelta
        from update_weather import FIELDS
        dates = [(date(2026,10,26)+timedelta(days=i)).isoformat() for i in range(-15,16)]
        city = dict(cidade='PASSO FUNDO', uf='RS', latitude=-28.26, longitude=-52.4,
                    daily={'time':dates, **{k:[1]*31 for k in FIELDS}})
        location = dict(Cidade='PASSO FUNDO',UF='RS',Latitude='-28.26',Longitude='-52.4')
        payload = dict(cidades=[city], status='completo',atualizado_em='2026-10-26T09:00:00-03:00')
        return city,location,payload

    def test_only_new_city_is_pending(self):
        from update_weather import select_pending
        city, loc, payload = self.fixture()
        added = dict(Cidade='CURITIBA',UF='PR',Latitude='-25.4',Longitude='-49.2')
        pending, reused = select_pending([loc,added],payload,'2026-10-26')
        self.assertEqual(pending,[added])
        self.assertEqual(reused,[city])

    def test_stale_incomplete_and_changed_coordinates_are_pending(self):
        from update_weather import is_current
        import copy
        city,loc,payload = self.fixture()
        self.assertFalse(is_current(city,loc,'2026-10-27',payload))
        self.assertFalse(is_current(city,dict(loc,Latitude='-28.5'),'2026-10-26',payload))
        for field in ('min','rain'):
            invalid=copy.deepcopy(city)
            invalid['daily'][field][-1]=None
            self.assertFalse(is_current(invalid,loc,'2026-10-26',payload))
        invalid=copy.deepcopy(city)
        invalid['daily']['time'][-1]='2026-11-15'
        self.assertFalse(is_current(invalid,loc,'2026-10-26',payload))

    def test_city_timestamp_overrides_global_timestamp(self):
        from update_weather import is_current
        city,loc,payload = self.fixture()
        city['atualizado_em']='2026-10-25T09:00:00-03:00'
        self.assertFalse(is_current(city,loc,'2026-10-26',payload))
        city['atualizado_em']=None
        self.assertFalse(is_current(city,loc,'2026-10-26',payload))
        city['atualizado_em']='2026-10-26T09:00:00-03:00'
        payload['status']='parcial'
        self.assertTrue(is_current(city,loc,'2026-10-26',payload))

    def test_main_fetches_only_new_city_and_preserves_existing(self):
        import tempfile, json, csv
        from datetime import datetime
        from unittest.mock import patch
        import update_weather as module
        city,loc,payload=self.fixture()
        added=dict(Cidade='CURITIBA',UF='PR',Latitude='-25.4',Longitude='-49.2')
        fresh=dict(city,cidade='CURITIBA',uf='PR',latitude=-25.4,longitude=-49.2)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'_sistema').mkdir()
            target=root/'_sistema/dados_clima.js'
            target.write_text('window.DADOS_CLIMA = '+json.dumps(payload)+';')
            with (root/'_sistema/Locais.csv').open('w') as f:
                writer=csv.DictWriter(f,fieldnames=list(loc),delimiter=';')
                writer.writeheader();writer.writerows([loc,added])
            with patch.object(module,'ROOT',root), patch.object(module,'datetime') as clock, patch.object(module,'fetch_adaptive',return_value=([fresh],[])) as fetch:
                clock.now.return_value=datetime.fromisoformat('2026-10-26T13:00:00-03:00')
                clock.fromisoformat.side_effect=datetime.fromisoformat
                module.main()
                fetch.assert_called_once_with([added],'2026-10-26')
                saved=module.read_payload(target)
                self.assertEqual(saved['total_reaproveitado'],1)
                self.assertEqual(saved['total_retornado'],2)
                self.assertEqual(saved['cidades'][0]['daily'],city['daily'])
                fetch.reset_mock()
                module.main()
                fetch.assert_not_called()
