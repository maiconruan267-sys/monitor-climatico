import copy
import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import requests
import test_update_weather as fixtures
import update_weather as w


class RecoveryTests(unittest.TestCase):
    def run_scenario(self, persistent=False, expired=False):
        city, loc, payload = fixtures.IncrementalTests().fixture()
        payload['atualizado_em'] = '2026-10-25T09:00:00-03:00'
        rows = [dict(loc, Cidade=str(i)) for i in range(21)]
        calls = []
        def fetch(batch, today):
            calls.append([r['Cidade'] for r in batch])
            if len(calls) == 1 or (persistent and len(calls) == 4):
                return [], [dict(cidade=r['Cidade'], uf=r['UF'], tipo='falha_transitoria', erro='timeout') for r in batch]
            return [dict(copy.deepcopy(city), cidade=r['Cidade']) for r in batch], []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'_sistema').mkdir()
            target = root/'_sistema/dados_clima.js'
            original = 'window.DADOS_CLIMA = '+json.dumps(payload)+';'
            target.write_text(original)
            with (root/'_sistema/Locais.csv').open('w') as f:
                writer = csv.DictWriter(f, fieldnames=list(loc), delimiter=';')
                writer.writeheader(); writer.writerows(rows)
            with patch.object(w, 'ROOT', root), patch.object(w, 'datetime') as clock, patch.object(w, 'fetch_adaptive', side_effect=fetch), patch.object(w.time, 'monotonic') as timer:
                clock.now.return_value = datetime.fromisoformat('2026-10-26T07:00:00-03:00')
                clock.fromisoformat.side_effect = datetime.fromisoformat
                timer.side_effect = lambda: w.COLLECTION_BUDGET + 1 if expired and len(calls) >= 3 else 0
                if persistent or expired:
                    with self.assertRaisesRegex(RuntimeError, 'Tempo de coleta|Menos de 95%'):
                        w.main()
                    self.assertEqual(target.read_text(), original)
                    saved = json.loads((root/'historico/coleta-pendente.json').read_text())
                    self.assertEqual(len(saved['cidades']), 14)
                    self.assertEqual(len(saved['erros']), 7)
                else:
                    w.main()
                    saved = w.read_payload(target)
                    self.assertEqual(saved['total_atualizado'], 21)
                    self.assertEqual(saved['percentual'], 100)
                    self.assertEqual(saved['erros'], [])
                    self.assertEqual(saved['status'], 'completo')
                    self.assertFalse((root/'historico/coleta-pendente.json').exists())
            self.assertEqual(len(calls), 3 if expired else 4)
            if not expired:
                self.assertEqual(calls[0], calls[-1])
                self.assertEqual(len(set(sum(calls[1:3], []))), 14)

    def test_recovers_only_failed_cities_after_other_batches(self):
        self.run_scenario()

    def test_persistent_failure_is_bounded_and_keeps_checkpoint(self):
        self.run_scenario(persistent=True)

    def test_recovery_obeys_original_time_budget(self):
        self.run_scenario(expired=True)

    def test_classifies_only_transient_errors_for_recovery(self):
        rows = [dict(Cidade='TESTE', UF='RS')]
        cases = [(requests.ReadTimeout('timeout'), 'falha_transitoria')]
        for status, kind in [(503, 'falha_transitoria'), (408, 'falha_transitoria'), (400, 'falha_http'), (403, 'falha_http')]:
            response = requests.Response()
            response.status_code = status
            cases.append((requests.HTTPError(response=response), kind))
        for error, kind in cases:
            with self.subTest(error=error, kind=kind), patch.object(w, 'fetch_batch', side_effect=error) as fetch:
                found, errors = w.fetch_adaptive(rows, '2026-10-26')
                self.assertEqual(found, [])
                self.assertEqual(errors[0]['tipo'], kind)
                fetch.assert_called_once()
