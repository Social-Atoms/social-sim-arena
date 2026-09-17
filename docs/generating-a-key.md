# Generating your signing key

Registering binds one public key to your entrant. You keep the private half and
sign every forecast with it; we never see it and cannot recover it.

## Make a key for the arena

```sh
ssh-keygen -t ed25519 -C '' -f arena-key
```

Press enter at the passphrase prompt, or set one and see *Passphrases* below.
Two files appear: `arena-key` (private, keep it) and `arena-key.pub` (public).
Paste the whole line from `arena-key.pub` into the registration form:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHjEplgjQzqgXZef8hVSx0pEDXke3BhI5TUOasW3gBY0
```

Two details are deliberate. **`-f arena-key`** makes a key for this and nothing
else, so revoking it later costs you nothing — do not reuse `~/.ssh/id_ed25519`,
which is the key that opens your servers. **`-C ''`** drops the comment, which by
default is your user name and full host name:

```
octocat@their-laptop.example.com
```

Registrations are public files. The form strips the comment for you, but a key
made without `-C ''` still has it sitting in `arena-key.pub` on your disk.

## Signing with it

```sh
python tools/submit_signed_forecast.py --key arena-key ...
```

The client reads the OpenSSH key directly. It also reads PEM (`openssl genpkey
-algorithm ed25519`) and the raw 32 bytes that `--generate-key` writes, so a key
registered before this change keeps working unchanged.

## Passphrases

A passphrase-protected key is fine. The client takes it from
`SSA_KEY_PASSPHRASE`, and asks once if that is unset and you are at a terminal.
An unattended run with no passphrase available fails immediately rather than
hanging on a prompt.

One wrinkle: decrypting an OpenSSH key needs `pip install bcrypt`, which
`cryptography` does not pull in. The client says so if it hits it. For a key that
a cron job uses, `-N ''` and file permissions are usually the better trade.

## Three things that are not this key

Each looks plausible, and each leaves the **Submit registration** button grey.

**A fingerprint** — hex, 40 or 64 characters, no `=`:

```
bc041e513fc3e99eff8e6237ed5e429d49a85a8c
```

**An `ssh-rsa` key.** Only Ed25519 is accepted. `ssh-keygen -t ed25519`.

**The private key.** It never leaves your machine. If you paste something
beginning `-----BEGIN`, make a fresh pair and treat the old one as compromised.

## What lands in the repository

The form converts your `ssh-ed25519` line to the 32 bytes inside it, so the file
holds one canonical spelling and no comment:

```json
"keys": [{ "id": "arena-key-1", "alg": "ed25519",
           "public": "eMSmWCNDOqBdl5/yFVLHSkQNeR7cGEjlNQ5qxbeAFjQ=", "revoked": false }]
```

## Looking after the private key

- It stays on the machine that submits. Never commit it, never paste it into a
  page, an issue or a chat.
- Losing it means opening a pull request to register a new key: add an entry with
  a new `id` and set `"revoked": true` on the old one. Ownership stays with your
  GitHub account, so nobody else can do this for you.
- Answers you already filed stay verifiable: each records the registration commit
  it was checked against, so rotating a key does not invalidate history.
