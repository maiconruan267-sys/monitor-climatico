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
