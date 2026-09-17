# Signed forecast intake

Implementation proposal for #119 / #125. Opt-in; do not enable production until
fork rehearsal and branch permissions have been verified. The platform is a
trusted plaintext receiver. No participant token, account database or second
forecast directory is introduced.

## Exact revert set

Base: dev `392b570df6d2cce31e3c403e7d026e5ebfdb571a`.
Revert newest first:

| Commit | Reason |
| --- | --- |
| `392b570d` | Describes the issue-only automated registration door |
| `052769f5` | Issue-driven update/retire writes directly to main |
| `c923f604` | Probe and Contents API additions to that issue workflow |
| `3a91bfd1` | Introduces issue-to-main registration workflow/parser |

Keep `3b8453a8`: fixes the external contributor fork -> PR flow. Keep earlier
contact/UI/data changes, PR #124, and source archives. PR #126 remains a separate
unmerged proposal; this change does not silently merge or close it. These are
revert commits on the implementation branch, not a rewrite of shared dev.

## Encryption choice

Use **age v1, native X25519 recipient**, via pinned `pyrage==1.4.0` (Rust age
bindings). Ciphertext is binary age v1, base64-encoded in the JSON envelope.
Public recipients use `age1...`; identities use `AGE-SECRET-KEY-...`.
No homemade X25519/HKDF/AEAD composition; no Fernet and no reuse of signing keys.
Specification: https://age-encryption.org/v1
Implementation: https://github.com/woodruffw/pyrage

The API has only the age public recipient. Reveal Actions have the secret
identity. This separates service capabilities; it does not prevent a trusted
operator from reading the incoming plaintext or using its own identity early.

## Register once

1. Generate a key for the arena: `ssh-keygen -t ed25519 -C '' -f arena-key`.
   `-C ''` matters: the default comment is your user name and host name, and a
   registration is a public file. See [generating-a-key.md](generating-a-key.md).
2. Keep `arena-key` private. Only `arena-key.pub` is ever published.
3. Use the onboarding page's **Signed POST** mode and paste the whole
   `ssh-ed25519 AAAA...` line, or add `keys` to your entrant file in a fork and
   open a PR. Never upload the private key.
4. A maintainer approves and merges the registration. Existing ownership checks
   apply to new keys and revocations. A keyless historical entrant stays valid,
   but cannot use signed POST until a key is approved.

The file stores the 32 bytes inside that line, so the repository keeps one
spelling of a key and no comment follows it in:

```json
"keys": [{"id":"k1", "alg":"ed25519", "public":"BASE64_32_BYTE_PUBLIC_KEY", "revoked":false}]
```

The raw base64 form is still accepted everywhere it was before, and the client
reads OpenSSH, PEM or raw private keys, so a key registered earlier is unaffected.

Key IDs are unique within an entrant. Revoke by setting `revoked:true`; add a
new ID for a new key. Historical submissions refer to their approved registry
commit, so rotation does not destroy verification evidence.

## Submit

```sh
python tools/submit_signed_forecast.py \
  --entrant my-agent --key-id k1 --key entrant.key \
  --answer answer.json --request-file request-private.json
```

Five arguments, all of them yours. Where to send it and what the signature is
scoped to are ours, so `ssa/signed_forecasts.py` carries them as `ORIGIN` and
`AUDIENCE` and the client fills them in; `--url` and `--audience` override them,
which only the isolated rehearsal fork needs to do.

`AUDIENCE` is not a secret. It is domain separation: it goes into the signed
bytes, so a request captured against the rehearsal fork cannot be replayed here,
and that holds however many people know the value. It has to equal
`SSA_INTAKE_AUDIENCE` on the deployment — changing one without the other
invalidates every signature at once, and the failure reads `invalid_signature`,
which is indistinguishable from a wrong key. Change them in the same commit.

The private request file preserves the exact signed bytes for retries. Keep it
out of Git: it contains plaintext. Repeating the command with the same file
retrieves the original receipt, including after the cutoff. A new answer needs
a new request file/ID. No provider is called by this tool or the API.

The official tool is optional. POST `/api/v1/forecasts` with JSON and headers:
`X-SSA-Entrant`, `X-SSA-Key-Id`, `X-SSA-Timestamp`, `X-SSA-Request-Id`,
`X-SSA-Signature` (base64 Ed25519). The exact signing input is
`ssa.signed_forecasts.signing_bytes`: canonical JSON containing protocol,
audience, method, path, entrant, key ID, request ID, signed_at, body SHA-256.
Fresh writes require the signed timestamp within 300 seconds of server time.
Timestamps do not bypass the round deadline. An already accepted retry may be
older, but must still authenticate with a currently active registered key.

## Persistence and acceptance

The GitHub App writes one JSON envelope at
`sealed/<round>/<entrant>.json` on branch `sealed`. Exact signed request bytes
and signature are encrypted. Public metadata includes IDs, registry/round
commit, receipt time, encryption key ID, ciphertext and its hash. There is no
public bare answer digest. Receipt and ciphertext therefore cannot split
across partially successful file writes.

Git history is the deduplication ledger, pinned to a head for every attempt.
Request IDs are scoped to entrant and round. Same ID + same signed request
returns the original commit; same ID + different request is a conflict. A/B/A
retries do not roll B back. Writes use the old blob SHA, with at most three
conflict retries. Different file paths reduce collisions but do not guarantee
conflict-free GitHub writes; the retry path remains required. No in-memory-only
state is relied upon. An entrant/round already filed through the platform pull
route cannot switch to POST in that round (`submission_route_conflict`).

Initial operational bounds: at most 120 accepted writes per entrant/round,
minimum two seconds between new writes; history traversal fails closed beyond
300 versions. Anonymous/IP abuse controls must also be configured at the host
before public activation; authenticated limits alone are not DDoS protection.

A receipt reports accepted only when both platform receipt and GitHub commit
time precede the deadline. The writer must not supply custom Git commit dates.
A request crossing the cutoff may remain archived as `late`; it never replaces
an earlier valid answer for scoring. These times are trusted-writer records,
not independent timestamps. GitHub outages mean no acceptance until persistence
is confirmed. A response lost after persistence is recovered by identical retry.

## Reveal

`tools/reveal_signed_forecasts.py --snapshot refs/remotes/origin/sealed` reads a
fixed snapshot. For each locked round it selects the newest version that was
on time, decrypts, verifies the historical registered signature and semantic
answer contract, and writes the original `forecasts/<round>/<entrant>.json`.
Evidence lives at `reveal-receipts/`; this is not a second answer directory.
Existing scoring/readers continue reading forecasts. The next normal refresh
updates scores and site data without calling the participant to re-answer.
The refresh also performs signed reveal before creating its round manifests.
For newly sealed rounds, the derived Crowd is built once after reveal, from
the final answers, before the manifest is frozen. Historical Crowd behavior
stays unchanged.

`audit_landing.py` verifies source ancestry and final selection against the
fetched sealed branch, rejects early reveal or changed plaintext, and forbids
overwriting a revealed answer. The dedicated reveal workflow audits before
push because GITHUB_TOKEN pushes do not recursively start other workflows.
Do not force-push/delete the sealed history: public verification needs it.

## Vercel and Actions configuration

API disabled unless `SSA_SIGNED_INTAKE_ENABLED=1`.

Vercel:
- `SSA_INTAKE_REPO` (owner/repository, no inferred official default)
- `SSA_INTAKE_BRANCH=sealed`
- `SSA_INTAKE_REGISTRY_BRANCH=main` (isolated QA may use its approved fixture branch)
- `SSA_INTAKE_AUDIENCE` (different for fork/production)
- `SSA_GITHUB_APP_ID`, `SSA_GITHUB_INSTALLATION_ID`, `SSA_GITHUB_APP_PRIVATE_KEY`
- `SSA_AGE_RECIPIENT`, `SSA_AGE_KEY_ID`

Actions:
- secret `SSA_AGE_IDENTITIES`: JSON list of age identities, retain old keys
- variable `SSA_SIGNED_REVEAL_ENABLED=1` only after rehearsal
- for platform-collected answers: `SSA_SEAL_FORECASTS=1`, a future
  `SSA_SEAL_AFTER` cutoff, `SSA_AGE_RECIPIENT`, `SSA_AGE_KEY_ID`, and the existing
  published platform signing key (`SSA_SIGNING_KEY`, `SSA_SIGNING_KEY_ID`)

Create the empty `sealed` branch before activation. The refresh pulls platform
receipts from it before filing and pushes new receipts to it before publishing
main. A failed sync blocks main publication. `sealed/` is never staged on main;
provider replies/search evidence remain encrypted until the deadline. Activate
only for future rounds whose answers have not already been published. Turning
sealing off with unrevealed receipts fails closed; keep the reveal identity until
all rounds encrypted with it have been opened.

The App requests contents:write for this repository only. It must not bypass
main protection. A contents permission is repository-wide, not branch-scoped;
verify actual rulesets with the test App before rollout. Protect sealed against
force pushes/deletion and unauthorized writers. Exclude sealed from Vercel
deployments (configured in vercel.json). Do not execute sealed-branch content.
`main-guard` is an after-push alarm, not write authorization.

## Acceptance gates

- Registration owner checks, duplicate key IDs and invalid public keys rejected.
- Signature/body tampering, wrong audience, revoked key, wrong entrant rejected.
- A/B/A, same-ID conflict, concurrent versions, timeout-after-write tested.
- Invalid answers never overwrite accepted answers.
- Pre/post cutoff and late latest-version selection tested.
- Ciphertext/public metadata/logs do not disclose answers.
- Early reveal, changed evidence, stale snapshot, changed answer rejected.
- Fork cloud run, real GitHub App permissions and Vercel POST tested before live use.

No live refresh, paid LLM call, production rollout, or main merge is part of
running these tests. Existing baseline CI failures must be reported separately.


## Implementation validation status

Local tests cover the real HTTP handler, real age encryption, signature errors,
retry/conflict cases, temporary Git histories, reveal/audit, platform branch
synchronization, and registration UI generation. They use synthetic answers and
call no paid model. GitHub App installation and Vercel deployment credentials
are separate activation requirements: offline or fork CI passing does not mean
those permissions or the hosted POST have been verified.
