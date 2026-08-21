"""HTTP contract checks for the framework-free Vercel WSGI entrypoint."""

import io
import json
import unittest

from api.app import application


def request(path, method="GET", body=b"", headers=None):
    headers = headers or {}
    environ = {
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": headers.get("Content-Type", ""),
        "HTTP_HOST": headers.get("Host", "arena.example"),
        "HTTP_ORIGIN": headers.get("Origin", ""),
        "HTTP_IDEMPOTENCY_KEY": headers.get("Idempotency-Key", ""),
        "wsgi.input": io.BytesIO(body),
    }
    response = {}

    def start_response(status, response_headers):
        response["status"] = int(status.split()[0])
        response["headers"] = dict(response_headers)

    response["body"] = b"".join(application(environ, start_response))
    return response


class QuestionnaireWsgiContracts(unittest.TestCase):
    def test_manifest_is_available_at_public_and_rewrite_paths(self):
        for path in ("/api/v1/questionnaire", "/api/app"):
            response = request(path)
            payload = json.loads(response["body"])
            self.assertEqual(200, response["status"])
            self.assertEqual("ssa-questionnaire-manifest-v1",
                             payload["schema_version"])
            self.assertEqual("*", response["headers"]
                             ["Access-Control-Allow-Origin"])

    def test_submission_requires_json(self):
        response = request(
            "/api/v1/questionnaire-submissions", method="POST",
            body=b"not json", headers={"Content-Type": "text/plain"})
        payload = json.loads(response["body"])
        self.assertEqual(415, response["status"])
        self.assertEqual("unsupported_media_type", payload["error"]["code"])

    def test_submission_rejects_cross_origin_browser_request(self):
        response = request(
            "/api/app", method="POST", body=b"{}",
            headers={"Content-Type": "application/json",
                     "Origin": "https://outside.example",
                     "Host": "arena.example"})
        payload = json.loads(response["body"])
        self.assertEqual(403, response["status"])
        self.assertEqual("origin_not_allowed", payload["error"]["code"])


if __name__ == "__main__":
    unittest.main()
