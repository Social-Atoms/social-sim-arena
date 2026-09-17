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
        "api-url": "https://example.test/forecast",
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
syncRegistration();
const endpoint = registration();
apiRadio.checked = false; signedRadio.checked = true; syncRoute();
const signed = registration();
const externalUrl = element('reg-open').href;
syncRegistration();
const ownerUrl = element('reg-open').href;
process.stdout.write(JSON.stringify({endpoint, signed, externalUrl, ownerUrl, ui: {
  endpointHidden: element('endpoint-field').hidden,
  keyHidden: element('public-key-field').hidden,
  testHidden: element('api-test').hidden,
  copyDisabled: element('reg-copy').disabled
}}));
"""
    result = subprocess.run(["node", "-e", harness, json.dumps(values)],
                            check=True, capture_output=True, text=True)
    observed = json.loads(result.stdout)
    assert observed["externalUrl"] == observed["ownerUrl"]
    assert observed["signed"]["github"] == ""
    assert 'id="entrant-github"' not in html
    assert "/Social-Atoms/social-sim-arena/new/main?" in observed["ownerUrl"]
    assert observed["endpoint"]["route"] == {
        "kind": "agent_api", "url": "https://example.test/forecast"}
    assert "keys" not in observed["endpoint"]
    assert "route" not in observed["signed"]
    assert observed["signed"]["keys"] == [{
        "id": "forecast-key-1", "alg": "ed25519",
        "public": values["entrant-public-key"], "revoked": False}]
    assert observed["ui"] == {"endpointHidden": True, "keyHidden": False,
                              "testHidden": True, "copyDisabled": False}


def ssh_public_line(raw, comment="someone@their-laptop.example"):
    """What ssh-keygen writes: a length-prefixed type tag, the 32 bytes, a comment."""
    blob = (b"\x00\x00\x00\x0bssh-ed25519" + len(raw).to_bytes(4, "big") + raw)
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii") + " " + comment


def page_public_key(pasted):
    """The value the page would put in the file for whatever was pasted."""
    with open(SUBMIT_PATH) as f:
        html = f.read()
    converter = re.search(r"const SSH_ED25519_HEAD.*?\n}\n", html, re.DOTALL).group(0)
    harness = converter + "\nprocess.stdout.write(publicKeyRaw(process.argv[1]));"
    return subprocess.run(["node", "-e", harness, pasted],
                          check=True, capture_output=True, text=True).stdout


def test_the_form_takes_an_openssh_public_key_and_files_the_raw_bytes():
    """ssh-keygen is what participants have, so the form accepts its output --
    but the file keeps one spelling, and the ssh comment (a user name and a host
    name) must not follow it into a public repository."""
    raw = bytes(range(32))
    canonical = base64.b64encode(raw).decode("ascii")

    assert page_public_key(ssh_public_line(raw)) == canonical
    assert page_public_key(ssh_public_line(raw, "")) == canonical
    assert page_public_key("  " + ssh_public_line(raw) + "  ") == canonical
    assert page_public_key(canonical) == canonical, "the old raw form still registers"

    filed = ssh_public_line(raw)
    assert "someone@their-laptop.example" not in page_public_key(filed)
    assert not errors(entrant(keys=[{"id": "k1", "alg": "ed25519",
                                     "public": page_public_key(filed)}]))


def test_the_form_refuses_what_is_not_an_ed25519_public_key():
    """Each of these has been pasted by somebody, and each must leave the button
    disabled rather than file a registration nobody can verify."""
    shape = re.compile(r"^[A-Za-z0-9+/]{43}=$")
    for pasted in ("bc041e513fc3e99eff8e6237ed5e429d49a85a8c",   # a hex fingerprint
                   "ssh-rsa AAAAB3NzaC1yc2EAAAADAQAB",           # the wrong algorithm
                   "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5",           # truncated
                   "ssh-ed25519 not-base64-at-all",
                   ""):
        assert not shape.match(page_public_key(pasted)), pasted


def test_the_client_reads_every_key_file_a_participant_might_have():
    """ssh-keygen, openssl and this tool write three different files for the
    same key; all three must sign as the same entrant."""
    import importlib.util
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    spec = importlib.util.spec_from_file_location(
        "ssa_submit_client", os.path.join(ROOT, "tools", "submit_signed_forecast.py"))
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)

    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw,
                            serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    ssh = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    want = key.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    with tempfile.TemporaryDirectory() as directory:
        for name, blob in (("raw.key", raw), ("key.pem", pem), ("id_ed25519", ssh)):
            path = os.path.join(directory, name)
            with open(path, "wb") as f:
                f.write(blob)
            loaded = client.load_private_key(path)
            assert loaded.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw) == want, name

def test_the_file_opens_against_this_repository_so_a_pull_request_is_the_only_way():
    """In their own fork the editor preselects a direct commit, which opens no
    pull request and reports no error. Aimed here, where they have no write
    access, GitHub offers only "create a new branch and start a pull request"."""
    with open(SUBMIT_PATH) as f:
        html = f.read()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    page_script = next(s for s in scripts if "function registration()" in s)
    values = {
        "entrant-id": "signed-one", "entrant-name": "Signed One",
        "entrant-org": "Example Lab", "entrant-contact": "",
        "api-url": "https://example.test/forecast", "entrant-key-id": "forecast-key-1",
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
const apiRadio = {value: 'agent_api', checked: false, addEventListener() {}};
const signedRadio = {value: 'signed_post', checked: true, addEventListener() {}};
global.document = {
  getElementById: element,
  querySelector() { return signedRadio.checked ? signedRadio : apiRadio; },
  querySelectorAll() { return [apiRadio, signedRadio]; }
};
global.navigator = {clipboard: {writeText() {}}};
""" + page_script + r"""
syncRoute(); syncRegistration();
process.stdout.write(JSON.stringify({record: registration(),
  forkUrl: element('reg-fork').href, openUrl: element('reg-open').href}));
"""
    seen = json.loads(subprocess.run(["node", "-e", harness, json.dumps(values)],
                                     check=True, capture_output=True, text=True).stdout)
    assert seen["openUrl"].startswith(
        "https://github.com/Social-Atoms/social-sim-arena/new/main?")
    assert seen["forkUrl"].endswith("/social-sim-arena/fork")
    assert seen["record"]["github"] == "", "the bot binds the owner, the form never claims one"


if __name__ == "__main__":
    test_existing_keyless_registrations_remain_valid()
    test_ed25519_public_keys_are_bounded_and_closed()
    test_validator_rejects_duplicate_key_ids_semantically()
    test_registration_form_builds_signed_and_endpoint_records()
    test_the_form_takes_an_openssh_public_key_and_files_the_raw_bytes()
    test_the_form_refuses_what_is_not_an_ed25519_public_key()
    test_the_client_reads_every_key_file_a_participant_might_have()
    test_the_file_opens_against_this_repository_so_a_pull_request_is_the_only_way()
    print("all signed registration tests passed")
