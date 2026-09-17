"""Signed intake. Never log request bodies, headers or raw exceptions."""
import json
from http.server import BaseHTTPRequestHandler
from ssa.forecast_api import accept
from ssa.signed_forecasts import IntakeError, MAX_BYTES


class handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        try:
            if self.headers.get('Content-Type', '').split(';')[0].lower() != 'application/json':
                raise IntakeError('unsupported_media_type', 415)
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise IntakeError('invalid_body_size') from None
            if not 0 < length <= MAX_BYTES:
                raise IntakeError('invalid_body_size', 413)
            payload = accept(self.rfile.read(length), dict(self.headers))
            status = 200 if payload['status'] == 'accepted' else 422
        except IntakeError as error:
            status, payload = error.status, {'error': {'code': error.code, 'message': error.code.replace('_', ' ')}}
        except Exception:
            status, payload = 503, {'error': {'code': 'service_unavailable', 'message': 'Retry with the same request ID.'}}
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
