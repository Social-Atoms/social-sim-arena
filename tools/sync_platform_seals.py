#!/usr/bin/env python3
"""Move platform-generated encrypted receipts through the dedicated sealed branch.

The sealed branch is treated as an inert object store.  This tool only reads
fixed JSON paths and never imports or executes content from that branch.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)

from ssa import batches, seal


PLATFORM_DOMAIN = seal.DOMAIN
PLATFORM_CIPHER = "age-v1-x25519"
SAFE_PATH = re.compile(
    r"^sealed/[a-z0-9][a-z0-9-]{2,63}/[a-z0-9][a-z0-9_.-]{1,47}\.json$")


class SyncError(RuntimeError):
    pass


def _git(root, *args, check=True, text=True):
    return subprocess.run(["git", *args], cwd=root, check=check,
                          capture_output=True, text=text)


def _json_bytes(body):
    return (json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n").encode("utf-8")


def _parse_json(raw, source):
    try:
        body = json.loads(raw)
    except (TypeError, ValueError, UnicodeError) as err:
        raise SyncError(f"invalid JSON in {source}") from err
    if not isinstance(body, dict):
        raise SyncError(f"JSON object required in {source}")
    return body


def is_platform_receipt(body):
    """True only for the internal arena receipt format, never signed API data."""
    return (isinstance(body, dict) and body.get("version") == 1
            and body.get("domain") == PLATFORM_DOMAIN
            and body.get("cipher") == PLATFORM_CIPHER
            and isinstance(body.get("ciphertext"), str))


def _published_keys(root):
    with open(os.path.join(root, "site", "keys.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    return {row["key_id"]: row["public_key"] for row in doc.get("keys", [])
            if row.get("key_id") and row.get("public_key")}


def _rounds(root):
    with open(os.path.join(root, "questions", "season0.json"),
              encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = doc.get("rounds", doc) if isinstance(doc, dict) else doc
    return {row["round_id"]: row for row in rows}


def validate_platform_receipt(path, body, root=ROOT):
    if not SAFE_PATH.fullmatch(path) or not is_platform_receipt(body):
        raise SyncError(f"not a platform sealed receipt: {path}")
    parts = path.split("/")
    if body.get("round_id") != parts[1] or body.get("entrant") + ".json" != parts[2]:
        raise SyncError(f"receipt identity does not match path: {path}")
    try:
        seal.verify_receipt(body, _published_keys(root))
        received = datetime.datetime.fromisoformat(
            body["received_at"].replace("Z", "+00:00"))
        row = _rounds(root)[body["round_id"]]
    except (KeyError, TypeError, ValueError, seal.SealError) as err:
        raise SyncError(f"invalid platform sealed receipt: {path}") from err
    if received >= batches.effective_deadline(row["lock_at"]):
        raise SyncError(f"late platform sealed receipt: {path}")


def _deadline_for(body, root):
    try:
        return batches.effective_deadline(_rounds(root)[body["round_id"]]["lock_at"])
    except (KeyError, TypeError, ValueError) as err:
        raise SyncError(f"unknown round in platform receipt: {body.get('round_id')}") from err


def _received(body):
    try:
        return datetime.datetime.fromisoformat(
            body["received_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as err:
        raise SyncError("platform receipt has invalid received_at") from err


def _tracked_platform_paths(root):
    proc = _git(root, "ls-files", "-z", "--", "sealed", text=False)
    paths = proc.stdout.decode("utf-8").split("\0")
    found = []
    for path in filter(None, paths):
        full = os.path.join(root, path)
        try:
            with open(full, "rb") as fh:
                body = _parse_json(fh.read(), path)
        except OSError:
            continue
        if is_platform_receipt(body):
            found.append(path)
    return found


def refuse_main_tracked_platform_receipts(root=ROOT):
    found = _tracked_platform_paths(root)
    if found:
        raise SyncError("platform receipts must not be tracked on main: "
                        + ", ".join(found))


def _fetch(root, remote, branch):
    proc = _git(root, "fetch", remote,
                f"{branch}:refs/remotes/{remote}/{branch}", check=False)
    if proc.returncode:
        raise SyncError(proc.stderr.strip() or "could not fetch sealed branch")


def _branch_records(root, ref):
    proc = _git(root, "ls-tree", "-r", "--name-only", ref, "--", "sealed")
    out = {}
    for path in proc.stdout.splitlines():
        if not SAFE_PATH.fullmatch(path):
            continue
        raw = _git(root, "show", f"{ref}:{path}", text=False).stdout
        body = _parse_json(raw, f"{ref}:{path}")
        if is_platform_receipt(body):
            validate_platform_receipt(path, body, root)
            out[path] = body
    return out


def _local_records(root):
    base = os.path.join(root, "sealed")
    out = {}
    for directory, _dirs, names in os.walk(base) if os.path.isdir(base) else []:
        for name in names:
            full = os.path.join(directory, name)
            path = os.path.relpath(full, root).replace(os.sep, "/")
            if not SAFE_PATH.fullmatch(path):
                continue
            with open(full, "rb") as fh:
                body = _parse_json(fh.read(), path)
            if is_platform_receipt(body):
                validate_platform_receipt(path, body, root)
                out[path] = body
    return out


def _atomic_write(root, path, body):
    dest = os.path.join(root, *path.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=name_for(path) + ".", suffix=".tmp",
                               dir=os.path.dirname(dest))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(_json_bytes(body))
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def name_for(path):
    return path.rsplit("/", 1)[-1]


def pull(root=ROOT, remote="origin", branch="sealed"):
    refuse_main_tracked_platform_receipts(root)
    _fetch(root, remote, branch)
    ref = f"refs/remotes/{remote}/{branch}"
    records = _branch_records(root, ref)
    local = _local_records(root)
    for path, body in records.items():
        if path in local and local[path] != body:
            raise SyncError(f"local platform receipt conflicts with sealed branch: {path}")
        _atomic_write(root, path, body)
    return len(records)


def _install_records(worktree, records, root, now):
    changed = []
    for path, body in records.items():
        dest = os.path.join(worktree, *path.split("/"))
        if os.path.exists(dest):
            with open(dest, "rb") as fh:
                branch_body = _parse_json(fh.read(), path)
            if branch_body == body:
                continue
            old = branch_body
            if body.get("entrant") != "crowd" or not is_platform_receipt(old):
                raise SyncError(f"sealed branch receipt is immutable: {path}")
            if _received(body) <= _received(old):
                raise SyncError(f"crowd receipt replacement is not newer: {path}")
        if now >= _deadline_for(body, root):
            raise SyncError(f"refusing new or changed receipt after deadline: {path}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(_json_bytes(body))
        changed.append(path)
    return changed


def push(root=ROOT, remote="origin", branch="sealed", attempts=3, now=None):
    refuse_main_tracked_platform_receipts(root)
    records = _local_records(root)
    if not records:
        return 0
    ref = f"refs/remotes/{remote}/{branch}"
    for attempt in range(attempts):
        attempt_now = now or datetime.datetime.now(datetime.timezone.utc)
        _fetch(root, remote, branch)
        # Validate the existing branch's platform subset before building on it.
        _branch_records(root, ref)
        with tempfile.TemporaryDirectory(prefix="ssa-sealed-worktree-") as td:
            _git(root, "worktree", "add", "--detach", td, ref)
            try:
                changed_paths = _install_records(td, records, root, attempt_now)
                _git(td, "add", "--", "sealed")
                changed = _git(td, "diff", "--cached", "--quiet", check=False)
                if changed.returncode == 0:
                    return 0
                _git(td, "-c", "user.name=SSA sealed sync", "-c",
                     "user.email=sealed-sync@invalid", "commit", "-m",
                     "Store platform sealed receipts")
                committed_at = datetime.datetime.fromisoformat(
                    _git(td, "show", "-s", "--format=%cI", "HEAD").stdout.strip())
                push_time = now or datetime.datetime.now(datetime.timezone.utc)
                for path in changed_paths:
                    deadline = _deadline_for(records[path], root)
                    if committed_at >= deadline or push_time >= deadline:
                        raise SyncError(
                            f"refusing receipt commit or push after deadline: {path}")
                result = _git(td, "push", remote,
                              f"HEAD:refs/heads/{branch}", check=False)
                if result.returncode == 0:
                    _fetch(root, remote, branch)
                    return len(records)
            finally:
                _git(root, "worktree", "remove", "--force", td, check=False)
        if attempt + 1 == attempts:
            raise SyncError("sealed branch changed during all push attempts")
    raise AssertionError("unreachable")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("pull", "push"))
    parser.add_argument("--root", default=ROOT)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="sealed")
    args = parser.parse_args(argv)
    try:
        count = (pull if args.action == "pull" else push)(
            os.path.abspath(args.root), args.remote, args.branch)
    except (OSError, subprocess.CalledProcessError, SyncError) as err:
        print(f"sealed sync failed: {err}", file=sys.stderr)
        return 1
    print(f"{args.action}: {count} platform sealed receipt(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
