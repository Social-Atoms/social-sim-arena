"""Entrant public-key schema and the registration form's two route modes."""

import base64
import contextlib
import io
import json
import os
import re
import subprocess
import tempfile

from jsonschema import Draft7Validator


ROOT = os.path.dirname(os.path.dirname(__file__))
SCHEMA_PATH = os.path.join(ROOT, "schema", "entrant.schema.json")
SUBMIT_PATH = os.path.join(ROOT, "site", "submit.html")


def schema():
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def errors(document):
    return list(Draft7Validator(schema()).iter_errors(document))


def entrant(**extra):
    document = {"entrant_id": "signed-one", "name": "Signed One",
                "type": "participant", "github": "signed-one"}
    document.update(extra)
    return document


def test_existing_keyless_registrations_remain_valid():
    assert not errors(entrant(route={"kind": "agent_api",
                                    "url": "https://example.test/forecast"}))


def test_ed25519_public_keys_are_bounded_and_closed():
    public = base64.b64encode(bytes(range(32))).decode("ascii")
    key = {"id": "forecast-key-1", "alg": "ed25519", "public": public}
    assert not errors(entrant(keys=[key]))
    assert not errors(entrant(keys=[dict(key, revoked=True)]))
    assert errors(entrant(keys=[]))
    assert errors(entrant(keys=[key] * 11))
    assert errors(entrant(keys=[dict(key, alg="RSA")]))
    assert errors(entrant(keys=[dict(key, public=base64.b64encode(b"short").decode())]))
    assert errors(entrant(keys=[dict(key, private="must never be uploaded")]))


def test_validator_rejects_duplicate_key_ids_semantically():
    from tools import validate_submission

    public = base64.b64encode(bytes(range(32))).decode("ascii")
    document = entrant(keys=[
        {"id": "same-id", "alg": "ed25519", "public": public},
        {"id": "same-id", "alg": "ed25519",
         "public": base64.b64encode(bytes(reversed(range(32)))).decode("ascii")},
    ])
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "signed-one.json")
        with open(path, "w") as f:
            json.dump(document, f)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                validate_submission.validate_entrant(path)
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("duplicate key ids passed semantic validation")


def test_registration_form_builds_signed_and_endpoint_records():
    """Run the page's real registration JavaScript against a tiny DOM shim."""
    with open(SUBMIT_PATH) as f:
        html = f.read()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    page_script = next(s for s in scripts if "function registration()" in s)
    values = {
        "entrant-id": "signed-one", "entrant-name": "Signed One",
        "entrant-org": "Example Lab", "entrant-contact": "",
        "entrant-github": "signed-one", "api-url": "https://example.test/forecast",
        "entrant-key-id": "forecast-key-1",
        "entrant-public-key": base64.b64encode(bytes(range(32))).decode("ascii"),
    }
    harness = r"""
const values = JSON.parse(process.argv[1]);
const elements = {};
function element(id) {
  if (!elements[id]) elements[id] = {
    id, value: values[id] || '', hidden: false, disabled: false, required: false,
    textContent: '', className: '', innerHTML: '', href: '',
    addEventListener() {}, setAttribute(name, value) { this[name] = value; },
    checkValidity() { return true; }, reportValidity() {}
  };
  return elements[id];
}
const apiRadio = {value: 'agent_api', checked: true, addEventListener() {}};
const signedRadio = {value: 'signed_post', checked: false, addEventListener() {}};
global.document = {
  getElementById: element,
  querySelector() { return signedRadio.checked ? signedRadio : apiRadio; },
  querySelectorAll() { return [apiRadio, signedRadio]; }
};
global.navigator = {clipboard: {writeText() {}}};
""" + page_script + r"""
const endpoint = registration();
apiRadio.checked = false; signedRadio.checked = true; syncRoute();
const signed = registration();
process.stdout.write(JSON.stringify({endpoint, signed, ui: {
  endpointHidden: element('endpoint-field').hidden,
  keyHidden: element('public-key-field').hidden,
  testHidden: element('api-test').hidden,
  copyDisabled: element('reg-copy').disabled
}}));
"""
    result = subprocess.run(["node", "-e", harness, json.dumps(values)],
                            check=True, capture_output=True, text=True)
    observed = json.loads(result.stdout)
    assert observed["endpoint"]["route"] == {
        "kind": "agent_api", "url": "https://example.test/forecast"}
    assert "keys" not in observed["endpoint"]
    assert "route" not in observed["signed"]
    assert observed["signed"]["keys"] == [{
        "id": "forecast-key-1", "alg": "ed25519",
        "public": values["entrant-public-key"], "revoked": False}]
    assert observed["ui"] == {"endpointHidden": True, "keyHidden": False,
                              "testHidden": True, "copyDisabled": False}


if __name__ == "__main__":
    test_existing_keyless_registrations_remain_valid()
    test_ed25519_public_keys_are_bounded_and_closed()
    test_validator_rejects_duplicate_key_ids_semantically()
    test_registration_form_builds_signed_and_endpoint_records()
    print("all signed registration tests passed")
