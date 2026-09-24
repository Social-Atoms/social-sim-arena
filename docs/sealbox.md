# Sealbox: how a forecast is sealed, and how anyone can check it

The arena asks entrants to commit to a forecast before the answer exists, and
then asks the world to believe that they did. Sealbox is the part of the
pipeline that makes the second half checkable. It has three moments, and three
keys that never sit in the same place.

![Sealbox: during the window an entrant signs and the platform encrypts into the sealed branch; at the lock the reveal workflow decrypts, writes forecasts and a hash manifest to main, and the manifest is stamped into a Bitcoin block through OpenTimestamps; any time after, anyone can run ots verify and compare hashes](figures/sealbox.svg)

## During the window

The entrant signs the submission with their own Ed25519 key, the one whose
public half is registered in the entrant file on `main`. The platform API
checks that signature against the registry, then encrypts the whole signed
request with the arena's age public key (X25519) and, through the GitHub App,
writes one envelope to `sealed/<round>/<entrant>.json` on the `sealed` branch.
The envelope carries ciphertext, its hash, the receipt time and the registry
and round commits; there is no plaintext and no bare answer digest.

A receipt says *accepted* only when both the platform's receive time and the
GitHub commit time precede the deadline. Git history is the ledger: the same
request retried returns the original commit, a different request under the
same ID is a conflict, and nothing is ever rewritten.

Nobody can read a forecast during the window: not other entrants, not the
public, not the reveal workflow, which does not run yet. The platform itself is
a trusted plaintext receiver. It sees the request before encrypting it, so the
encryption protects against leaks, not against the operator.

## At the lock

The reveal workflow runs in GitHub Actions and is the only place the age
private key exists. It reads a fixed snapshot of `sealed`, takes each
entrant's newest on-time version, decrypts it, re-verifies the signature
against the registry as it stood at submission, checks the answer contract,
and writes the plaintext to `forecasts/<round>/<entrant>.json` on `main`.

Then the round's manifest is built: the canonical SHA-256 of every forecast,
plus the hash of the history snapshot the round froze. That file,
`stamps/<round>.json`, is submitted to [OpenTimestamps](https://opentimestamps.org).
Hours later the proof lands in a Bitcoin block that nobody here controls.

## Any time after

No account, no key, a clone of the repository:

```bash
pip install opentimestamps-client
ots verify stamps/<round_id>.json.ots
```

Success means this exact manifest existed before the named block was mined.
Hashing a forecast the canonical way and finding that hash in the manifest
extends the claim to the forecast itself. [timestamps.md](timestamps.md) walks
through both steps and what each possible output means.

## The three keys

| Key | Where it lives | What it answers |
| --- | --- | --- |
| Entrant Ed25519 | private half with the entrant; public half registered on `main` | who submitted this |
| Arena age X25519 | public half at the API, which can only encrypt; private half only in the reveal workflow, which can only decrypt | nobody read it during the window |
| GitHub App | at the API, scoped to writing the `sealed` branch | the platform can file an envelope but cannot quietly rewrite the ledger; a rewritten `sealed` history breaks public verification |

## What this does and does not prove

It proves that a specific forecast existed before a specific block, and that
its bytes have not changed since. It does not prove how the forecast was made,
and it cannot turn a stamp made after the deadline into evidence of an on-time
submission: the receipt times and the commit history carry that claim, and
they are trusted-writer records rather than independent timestamps.

Design and operating detail: [signed-submissions.md](signed-submissions.md).
Verification: [timestamps.md](timestamps.md).
