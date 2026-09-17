"""Real HTTP transport against a synthetic ledger; no external services."""
import importlib.util
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import requests
from ssa.forecast_api import accept

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('signed_cases', ROOT / 'tests/test_signed_forecasts.py')
cases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cases)
spec = importlib.util.spec_from_file_location('signed_handler', ROOT / 'api/forecasts.py')
http = importlib.util.module_from_spec(spec)
spec.loader.exec_module(http)


class HTTPTest(cases.SignedTests):
    def test_real_http_accept_retry_and_tamper(self):
        def route(raw, headers):
            return accept(raw, headers, store=self.store, clock=lambda: self.now)
        with patch.object(http, 'accept', route):
            server = ThreadingHTTPServer(('127.0.0.1', 0), http.handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = 'http://127.0.0.1:' + str(server.server_port) + '/api/v1/forecasts'
                raw, headers = self.request()
                headers['Content-Type'] = 'application/json'
                first = requests.post(url, data=raw, headers=headers, timeout=5)
                self.assertEqual(first.status_code, 200)
                again = requests.post(url, data=raw, headers=headers, timeout=5)
                self.assertEqual(first.json(), again.json())
                bad = requests.post(url, data=raw.replace(b'42', b'43'), headers=headers, timeout=5)
                self.assertEqual(bad.status_code, 401)
                self.assertNotIn('topline', bad.text)
                malformed = requests.post(url, data=b'{', headers=headers, timeout=5)
                self.assertEqual(malformed.status_code, 400)
                self.assertEqual(len(self.store.records), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
