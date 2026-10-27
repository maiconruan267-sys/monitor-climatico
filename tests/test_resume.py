import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import test_update_weather as fixtures
import update_weather as w

class ResumeTests(unittest.TestCase):
    def test_interrupted_collection_resumes_without_losing_history(self):
        city, loc, payload = fixtures.IncrementalTests().fixture()
        payload['atualizado_em'] = '2026-10-25T09:00:00-03:00'
        added = dict(Cidade='CURITIBA', UF='PR', Latitude='-25.4', Longitude='-49.2')
        fresh = dict(city, cidade='CURITIBA', uf='PR', latitude=-25.4, longitude=-49.2)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'_sistema').mkdir()
            target = root/'_sistema/dados_clima.js'
            original = 'window.DADOS_CLIMA = '+json.dumps(payload)+';'
            target.write_text(original)
            with (root/'_sistema/Locais.csv').open('w') as f:
                writer = csv.DictWriter(f, fieldnames=list(loc), delimiter=';')
                writer.writeheader(); writer.writerows([loc, added])
            with patch.object(w, 'ROOT', root), patch.object(w, 'BATCH_SIZE', 1), patch.object(w, 'datetime') as clock:
                clock.now.return_value = datetime.fromisoformat('2026-10-26T13:00:00-03:00')
                clock.fromisoformat.side_effect = datetime.fromisoformat
                with patch.object(w, 'fetch_adaptive', side_effect=[([city], []), w.RateLimitError('429')]):
                    with self.assertRaises(w.RateLimitError): w.main()
                self.assertEqual(target.read_text(), original)
                self.assertTrue((root/'historico/coleta-pendente.json').exists())
                with patch.object(w, 'fetch_adaptive', return_value=([fresh], [])) as fetch:
                    w.main()
                    fetch.assert_called_once_with([added], '2026-10-26')
                self.assertFalse((root/'historico/coleta-pendente.json').exists())
                self.assertEqual(w.read_payload(target)['total_retornado'], 2)

    def test_timeout_does_not_multiply_requests_by_splitting(self):
        rows = [dict(Cidade=str(i), UF='RS') for i in range(7)]
        with patch.object(w, 'fetch_batch', side_effect=TimeoutError('timeout')) as fetch:
            found, errors = w.fetch_adaptive(rows, '2026-10-26')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(errors), 7)
        self.assertEqual(found, [])
