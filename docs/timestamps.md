# Verifying that a forecast predates the answer

The arena's central claim is that at lock time the answer did not exist. The
evidence for it used to be a SHA-256 in this repository's git history — which is
fine for a reader who trusts us and worth nothing to one who does not, **because
we can rewrite that history.** Wrong trust model for the one claim everything
rests on.

Every locked round is now timestamped through
[OpenTimestamps](https://opentimestamps.org). The proof lands in a Bitcoin block
that nobody involved here controls.

---

## Checking a round yourself

Install the client (free, no account, no key):

```bash
pip install opentimestamps-client
```

Then, from a clone of this repository:

```bash
ots verify stamps/<round_id>.json.ots
```

Three answers are possible, and the difference matters:

| output | what it means |
|---|---|
| `Success! Bitcoin block <N> attests existence as of <date>` | **The strong claim.** This exact file existed before that block was mined. Verified against Bitcoin's chain, trusting no one here |
| `Pending confirmation in Bitcoin blockchain` | Submitted, not yet in a block. Currently rests on a calendar's word. Hours, not days |
| `File does not match original!` | The manifest was altered after stamping |

`ots verify` needs a Bitcoin node to check the block header itself. Without one,
add `--no-bitcoin` and it uses a public block explorer instead — which is a
weaker trust assumption than a node, but still not us.

`ots info stamps/<round_id>.json.ots` prints the whole Merkle path without any
network access.

## What the timestamp actually covers

**A manifest, not each forecast.** A round has thirty-four entrants; stamping
each would be thirty-four submissions per round, and the thing a skeptic needs is
"all of them when submissions closed, and nothing added later". So
`stamps/<round_id>.json` lists every forecast's canonical hash plus the lock
snapshot's, and *that file* is stamped. One proof covers the round.

```json
{
  "round_id": "umich-2026-08-prelim",
  "lock_at": "2026-08-12T14:00:00Z",
  "forecast_count": 34,
  "forecasts": {"claude-opus": "9f2c…", "persistence": "41a0…", …},
  "lock_snapshot_sha256": "0e48b12b…",
  "canonical": "json.dumps(obj, sort_keys=True, separators=(',',':')) utf-8, sha256"
}
```

To check a single forecast, hash it the canonical way and look for that hash in
the manifest:

```bash
python -c "import json,hashlib,sys;print(hashlib.sha256(json.dumps(
  json.load(open(sys.argv[1])),sort_keys=True,separators=(',',':')
  ).encode()).hexdigest())" forecasts/<round_id>/<entrant>.json
```

That is the same serialization `tools/validate_submission.py` prints on every
submission, and the same one the leaderboard and the paper cite. **It is fixed**
— changing it would orphan every proof.

`lock_snapshot_sha256` covers the history the round froze. Without it a stamp
would prove the forecasts existed but not what they were forecasting *from*, and
"the baselines were computed from strictly pre-lock data" is a claim too.

## Arena-collected forecasts stay sealed until the deadline

For rounds after the configured rollout boundary, the arena no longer commits
its model and Route A answers as plaintext during the filing window. It writes
an authenticated ciphertext to `sealed/<round>/<entrant>.json`. The receipt
binds the round, entrant, exact platform receipt time, ciphertext and a salted
commitment, and is signed by the live Ed25519 key already published in
`site/keys.json`. The random salt is inside the ciphertext, so a small numeric
answer cannot be guessed by enumerating hashes.

After the participant deadline, refresh decrypts the receipt and writes the
unchanged forecast to its existing `forecasts/<round>/<entrant>.json` path. It
also publishes the salt under `reveal-receipts/`, so the landing audit can
recompute the commitment without possessing the encryption key. The audit only
accepts this as an on-time reveal when the signed receipt was already present
in the parent commit, names the same round and entrant, predates the deadline,
and the revealed bytes match. It does not use a commit-message trailer as a
blanket deadline bypass.

Raw provider replies, failure excerpts, and model-selected search queries can
also disclose an answer. They are encrypted during the same window and opened
after the deadline. Baselines remain plaintext because they are deterministic
functions of public history.

This receipt time trusts the arena as the receiver. OpenTimestamps remains the
independent post-close proof over the completed plaintext manifest; it is not
claimed as a per-receipt pre-close timestamp.

### Security boundary for the first release

The first release uses Fernet authenticated symmetric encryption and treats the
platform as trusted. Its promise is that answers are not publicly disclosed
before the deadline. The receiver handles plaintext and holds the decryption
key, so this does not prevent platform operators or a compromised receiver
from reading answers early. Signed receipts support integrity checks, but do
not independently prove the platform's claimed receipt time or prevent a
dishonest platform from omitting submissions.

Replacing Fernet with X25519 alone would not remove that trust. Preventing the
receiver from reading answers would require entrants to encrypt before sending,
and an independently controlled reveal service to hold the private key. That
stronger model is outside this release; the current rollout retains Fernet.

Rollout is explicit: `SSA_SEAL_FORECASTS=1` and an ISO `SSA_SEAL_AFTER` select
future deadlines only. Enabling without the Fernet `SSA_SEAL_KEY` or the
published live signing key fails before provider calls. `SSA_SEAL_KEY` must not
be rotated while any receipt remains unopened; losing it makes those forecasts
unrecoverable. The feature flag must likewise remain enabled until every
pending receipt has been revealed; disabling it would make refresh ignore the
sealed queue.

## The two phases, and why the second one is not optional

`ots stamp` returns in about a second with a calendar's receipt. The Bitcoin
attestation appears **hours later**, once a block is mined and the calendar can
hand back the path to it. `ots upgrade` rewrites the `.ots` with that path.

A proof that is never upgraded still verifies — against a calendar. That means
trusting the calendar operators, which is better than trusting us and is not the
point. Every refresh calls `upgrade` on each existing proof, so a round is
calendar-attested within one refresh of submission close and Bitcoin-attested
by the next day. `site/data.json` carries `bitcoin_attested` per round so the
difference is visible rather than assumed.

## The honest limit

**A stamp proves existence at stamp time, not at the participant deadline.**

For rounds stamped when submissions close — the weekly batch deadline after the
cutover, and each round's own lock before the cutover — those are minutes apart
and the claim is tight. For rounds that closed *before* this landed, the
manifest was built afterwards, so the proof says "this existed on the day it
was stamped" and the earlier date still rests on git history alone. Those
rounds are not retroactively fixed and this file does not pretend otherwise;
the manifests carry `built_at` so the gap is readable.

A second limit worth stating: the manifest is built **once** and never rewritten,
because rewriting it after the stamp would invalidate the proof. So a forecast
that lands after the deadline is *not* in the manifest — which is the correct
outcome, and is independently rejected by the landing audit at merge/push time.
Bot-authored refresh commits run the same audit before push, because GitHub does
not recursively trigger workflows from `GITHUB_TOKEN` pushes.

## If the client is missing

`ssa/stamps.py` shells out to `ots` and degrades deliberately: the manifest is
still written and recorded as unstamped, because the manifest is useful on its
own and a refresh with forecasts to file must not die over a proof. Four public
calendars being briefly unreachable costs nothing — the next run retries.

What must never happen is silence. An unstamped round says so, in the file and
in the run log.

## Cost

Free. No account, no key, no rate limit worth planning around. A `.ots` proof is
a few hundred bytes; a season of them is smaller than one poll CSV. The calendars
aggregate everyone's submissions into one Merkle tree per block, so the arena's
rounds cost the Bitcoin network nothing beyond what it was already doing.
