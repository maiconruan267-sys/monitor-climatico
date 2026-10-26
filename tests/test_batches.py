import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update_weather as weather

class BatchTests(unittest.TestCase):
    def test_divides_failure_and_preserves_successes(self):
        rows = [dict(Cidade=str(i), UF='RS') for i in range(7)]
        def fetch(batch, today):
            if len(batch) > 2 or any(r['Cidade']=='3' for r in batch):
                raise ValueError('Falha simulada')
            return batch
        with patch.object(weather, 'fetch_batch', side_effect=fetch):
            found, errors = weather.fetch_adaptive(rows,'2026-10-26')
        self.assertEqual([r['Cidade'] for r in found], ['0','1','2','4','5','6'])
        self.assertEqual([r['cidade'] for r in errors], ['3'])

    def test_rate_limit_does_not_split(self):
        with patch.object(weather, 'fetch_batch', side_effect=weather.RateLimitError('429')) as fetch:
            with self.assertRaises(weather.RateLimitError):
                weather.fetch_adaptive([{}]*7, '2026-10-26')
        self.assertEqual(fetch.call_count, 1)

    def test_all_cities_are_covered_in_46_batches(self):
        rows = list(range(322))
        batches = [rows[i:i+weather.BATCH_SIZE] for i in range(0,len(rows),weather.BATCH_SIZE)]
        self.assertEqual(len(batches),46)
        self.assertEqual([r for batch in batches for r in batch],rows)
