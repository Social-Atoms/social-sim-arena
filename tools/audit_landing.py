"""Validate every forecast/entrant file changed by one landed commit.

Both ``lock-audit.yml`` and the bot-authored refresh workflow call this exact
entry point. GitHub deliberately does not start a second workflow for a push
made with the repository ``GITHUB_TOKEN``; invoking the audit before that push
is therefore part of the refresh commit transaction, not optional duplication.

Usage:
    python tools/audit_landing.py HEAD^ HEAD
"""
import argparse
import os
import subprocess
import sys
import datetime
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def changed_contract_files(before="HEAD^", after="HEAD", root=ROOT):
    """Changed, non-deleted forecast/entrant JSON paths for one git range."""
    proc = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", before, after,
         "--", "forecasts", "entrants"],
        cwd=root, capture_output=True, text=True, check=True)
    out = []
    for raw in proc.stdout.splitlines():
        path = raw.strip()
        if not path.endswith(".json"):
            continue
        if path.startswith("forecasts/") or (
                path.startswith("entrants/") and path.count("/") == 1):
            out.append(path)
    return out


def received_at(commit="HEAD", root=ROOT):
    """The `Received-At:` trailer tools/auto_merge.py writes into a merge
    commit: the moment the submission reached GitHub. Without it, lateness is
    judged at landing, as before."""
    proc = subprocess.run(["git", "log", "-1", "--format=%B", commit], cwd=root,
                          capture_output=True, text=True, check=True)
    for line in proc.stdout.splitlines():
        if line.startswith("Received-At:"):
            return line.split(":", 1)[1].strip()
    return None


from ssa import batches, seal

def _git_json(commit, path, root=ROOT):
    proc = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=root,
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def _published_keys(commit, root=ROOT):
    doc = _git_json(commit, "site/keys.json", root)
    return {row["key_id"]: row["public_key"] for row in doc.get("keys", [])
            if row.get("key_id") and row.get("public_key")}


def _deadline(round_id, commit, root=ROOT):
    season = _git_json(commit, "questions/season0.json", root)
    row = next((r for r in season.get("rounds", season)
                if r.get("round_id") == round_id), None)
    if row is None:
        raise seal.SealError(f"unknown round {round_id}")
    return batches.effective_deadline(row["lock_at"])


def changed_seals(before="HEAD^", after="HEAD", root=ROOT):
    proc = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", before, after,
         "--", "sealed"], cwd=root, capture_output=True, text=True, check=True)
    return [p for p in proc.stdout.splitlines()
            if p.startswith("sealed/") and p.endswith(".json")]


def validate_seal(path, before, after, root=ROOT):
    receipt = _git_json(after, path, root)
    seal.verify_receipt(receipt, _published_keys(before, root))
    parts = path.split("/")
    if len(parts) != 3 or parts[1] != receipt.get("round_id") or \
            parts[2] != receipt.get("entrant", "") + ".json":
        raise seal.SealError("sealed receipt identity does not match its path")
    received = datetime.datetime.fromisoformat(
        receipt["received_at"].replace("Z", "+00:00"))
    if received >= _deadline(receipt["round_id"], after, root):
        raise seal.SealError(f"late sealed receipt: {path}")


def reveal_received_at(path, before, after, root=ROOT):
    """Trusted receipt time for one reveal, or None for an ordinary filing."""
    rel = path.split("/", 2)
    if len(rel) != 3 or not rel[2].endswith(".json"):
        return None
    round_id, entrant = rel[1], rel[2][:-5]
    sealed_path = f"sealed/{round_id}/{entrant}.json"
    disclosure_path = f"reveal-receipts/{round_id}/{entrant}.json"
    receipt_ref = before
    try:
        receipt = _git_json(before, sealed_path, root)
    except (subprocess.CalledProcessError, ValueError):
        try:
            receipt_ref = os.environ.get('SSA_SEALED_REF', 'refs/remotes/origin/sealed')
            receipt = _git_json(receipt_ref, sealed_path, root)
        except (subprocess.CalledProcessError, ValueError):
            return None
    try:
        disclosure = _git_json(after, disclosure_path, root)
        forecast = _git_json(after, path, root)
    except (subprocess.CalledProcessError, ValueError) as err:
        raise seal.SealError("sealed forecast is missing its reveal receipt") from err
    # A reveal can only consume a receipt already present in the parent.  It
    # may not replace that receipt in the same commit it uses as authority.
    changed = subprocess.run(
        ["git", "diff", "--quiet", before, after, "--", sealed_path],
        cwd=root).returncode
    if changed:
        raise seal.SealError("reveal also changes its sealed receipt")
    seal.verify_receipt(receipt, _published_keys(before, root))
    if receipt.get("round_id") != round_id or receipt.get("entrant") != entrant:
        raise seal.SealError("reveal identity does not match its sealed path")
    seal.verify_disclosure(receipt, forecast, disclosure)
    received = datetime.datetime.fromisoformat(
        receipt["received_at"].replace("Z", "+00:00"))
    # The receipt must have existed in Git before the lock, not merely claim
    # an earlier receive time. Git dates assume the trusted writer model.
    persisted = subprocess.check_output(
        ['git', 'log', '-1', '--format=%cI', receipt_ref, '--', sealed_path],
        cwd=root, text=True).strip()
    persisted = datetime.datetime.fromisoformat(persisted.replace('Z', '+00:00'))
    if persisted >= _deadline(round_id, after, root):
        raise seal.SealError('sealed forecast was persisted after its deadline')
    if received >= _deadline(round_id, after, root):
        raise seal.SealError("sealed forecast was received after its deadline")
    if datetime.datetime.now(datetime.timezone.utc) < _deadline(
            round_id, after, root):
        raise seal.SealError("sealed forecast was revealed before its deadline")
    return receipt["received_at"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("before", nargs="?", default="HEAD^")
    ap.add_argument("after", nargs="?", default="HEAD")
    args = ap.parse_args(argv)
    files = changed_contract_files(args.before, args.after)
    try:
        for path in changed_seals(args.before, args.after):
            validate_seal(path, args.before, args.after)
    except Exception:
        print('FAIL: invalid platform sealed receipt')
        return 1
    print("\n".join(files))
    # Evidence is immutable and cannot be changed without a new forecast reveal.
    changed_proofs = subprocess.check_output(
        ['git', 'diff', '--name-only', args.before, args.after, '--', 'reveal-receipts'],
        cwd=ROOT, text=True).splitlines()
    for proof_path in changed_proofs:
        counterpart = proof_path.replace('reveal-receipts/', 'forecasts/', 1)
        if counterpart not in files:
            print('FAIL: detached reveal evidence change:', proof_path)
            return 1
    remaining = []
    for path in files:
        if path.startswith('forecasts/'):
            proof_path = path.replace('forecasts/', 'reveal-receipts/', 1)
            try:
                import json
                proof = json.loads(subprocess.check_output(
                    ['git', 'show', f'{args.after}:{proof_path}'], cwd=ROOT, text=True))
            except (subprocess.CalledProcessError, ValueError):
                proof = None
            if proof and 'source_commit' in proof:
                from ssa.forecast_reveal import document, verify_reveal
                try:
                    body = document(ROOT, args.after, path)
                    old = subprocess.run(['git', 'cat-file', '-e', f'{args.before}:{path}'],
                                         cwd=ROOT, capture_output=True)
                    if old.returncode == 0:
                        raise ValueError('revealed forecasts are immutable')
                    verify_reveal(ROOT, body, proof, args.before,
                                  os.environ.get('SSA_SEALED_REF', 'refs/remotes/origin/sealed'),
                                  datetime.datetime.now(datetime.timezone.utc))
                except Exception:
                    print('FAIL: invalid signed reveal evidence:', path)
                    return 1
                print('OK: signed reveal', path)
                continue
        if path.startswith('forecasts/'):
            try:
                received = reveal_received_at(path, args.before, args.after)
            except Exception:
                print('FAIL: invalid platform reveal:', path)
                return 1
            if received is not None:
                rc = subprocess.call([sys.executable, os.path.join(ROOT, 'tools', 'validate_submission.py'),
                                      '--now', received, path], cwd=ROOT)
                if rc:
                    return rc
                continue
        if path.startswith('forecasts/'):
            try:
                entrant = path.rsplit('/', 1)[-1][:-5]
                registered = _git_json(args.before, f'entrants/{entrant}.json')
            except (subprocess.CalledProcessError, ValueError):
                registered = {}
            if registered.get('keys'):
                print('FAIL: signed entrant forecast requires reveal evidence:', path)
                return 1
        remaining.append(path)
    if not remaining:
        return 0
    received = received_at(args.after)
    extra = ['--now', received] if received else []
    return subprocess.call(
        [sys.executable, os.path.join(ROOT, 'tools', 'validate_submission.py'),
         *extra, *remaining], cwd=ROOT)


if __name__ == '__main__':
    raise SystemExit(main())
