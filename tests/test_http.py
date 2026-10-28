import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
import requests
import update_weather as w

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(Cidade='TESTE', UF='RS', Latitude='-28', Longitude='-52')]
        self.daily = {'time': [(date(2026,10,27)+timedelta(days=i)).isoformat() for i in range(-15,16)],
                      **{field: [1]*31 for field in w.FIELDS.values()}}
    def response(self, status=200):
        r = MagicMock()
        r.__enter__.return_value = r
        r.json.return_value = {'daily': self.daily}
        if status != 200:
            actual = requests.Response()
            actual.status_code = status
            actual.headers['Retry-After'] = '120'
            r.raise_for_status.side_effect = requests.HTTPError(response=actual)
        return r
    def test_reuses_session_for_multiple_batches(self):
        w.close_session()
        with patch.object(w.requests, 'Session') as constructor, patch.object(w, 'wait_for_request'):
            session = constructor.return_value
            session.get.return_value = self.response()
            for _ in range(2):
                result = w.fetch_batch(self.rows, '2026-10-27')
                self.assertEqual(result[0]['daily']['min'], [1]*31)
            constructor.assert_called_once()
            self.assertEqual(session.get.call_count, 2)
            self.assertEqual(session.get.call_args.kwargs['timeout'], (6.1,30))
            w.close_session()
    def test_connect_timeout_retries_and_preserves_rate_gate(self):
        session = MagicMock()
        session.get.side_effect = [requests.ConnectTimeout('SSL'), self.response()]
        with patch.object(w,'get_session',return_value=session), patch.object(w,'close_session') as close, patch.object(w,'wait_for_request') as gate, patch.object(w.time,'sleep'):
            self.assertEqual(len(w.fetch_batch(self.rows,'2026-10-27')),1)
            self.assertEqual(gate.call_count,2)
            close.assert_called_once()
    def test_429_waits_and_never_splits(self):
        session = MagicMock()
        session.get.return_value = self.response(429)
        with patch.object(w,'get_session',return_value=session), patch.object(w,'wait_for_request'), patch.object(w.time,'sleep') as sleep:
            with self.assertRaises(w.RateLimitError):
                w.fetch_adaptive(self.rows,'2026-10-27')
            self.assertEqual(session.get.call_count,3)
            self.assertEqual([x.args[0] for x in sleep.call_args_list],[120,120])

    def test_missing_data_keeps_valid_city_without_retry_or_split(self):
        import copy
        daily = copy.deepcopy(self.daily)
        daily['temperature_2m_min'][-1] = None
        response = self.response()
        response.json.return_value = [{'daily': self.daily}, {'daily': daily}]
        session = MagicMock()
        session.get.return_value = response
        with patch.object(w, 'get_session', return_value=session), patch.object(w, 'wait_for_request'):
            found, errors = w.fetch_adaptive(self.rows * 2, '2026-10-27')
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(len(found), 1)
        self.assertEqual(errors[0]['campos_datas']['temperature_2m_min'], ['2026-11-11'])
        self.assertEqual(errors[0]['tipo'], 'dados_incompletos')

    def test_reports_all_missing_essential_fields_and_dates(self):
        self.daily['temperature_2m_min'][0] = None
        self.daily['precipitation_sum'][-1] = float('nan')
        with self.assertRaises(w.IncompleteDataError) as caught:
            w.validate_daily(self.daily, '2026-10-27')
        self.assertEqual(caught.exception.missing, {
            'temperature_2m_min': ['2026-10-12'], 'precipitation_sum': ['2026-11-11']})
