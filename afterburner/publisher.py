"""Unattended, read-only-market GitHub Pages snapshot publisher.

Publishes ONLY site/data.json from a separate, dedicated git clone. Never
modifies the user's main development checkout or reads JAFAR resources.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from .config import assets, data_dir, settings
from .public import public_payload
from .storage import Storage

REPO = "https://github.com/Leshan3D/GTA-6-Afterburner.git"
REMOTE = "origin"
BRANCH = "main"


def now():
    return datetime.now(timezone.utc).isoformat()


def git(directory, *args):
    cmd = ["git", "-C", str(directory), *args]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=90, check=False,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode:
        # Git stderr could contain credential/helper details; never echo it.
        raise RuntimeError(f"git {args[0]} failed (exit {result.returncode}); inspect Git authentication/network locally")
    return result.stdout.strip()


def publishable_difference(previous, current):
    """Publish when observed market/news or status changes, not just clock time.

    The freshness indicator is calculated by the browser using observed_at,
    so suppressing duplicate data cannot disguise stale collector feeds.
    """
    if previous is None:
        return True
    def cleaned(value):
        out = dict(value)
        out.pop("generated_at", None)
        for asset in out.get("assets", []):
            asset.pop("stale_at_export", None)
            # State may change only because of clock time; per-card client checks actual age.
            if asset.get("research_state") == "STALE":
                asset["research_state"] = None
        for asset in out.get("assets", []):
            if asset.get("research_state") is None:
                asset.pop("research_state", None)
        return out
    return cleaned(json.loads(json.dumps(previous))) != cleaned(json.loads(json.dumps(current)))


def validate_payload(payload, expected_symbols):
    if not isinstance(payload, dict) or set(payload) != {"product", "generated_at", "execution_enabled", "research_only", "assets", "news"}:
        raise ValueError("Unexpected public payload keys")
    if payload["execution_enabled"] is not False or payload["research_only"] is not True:
        raise ValueError("Execution safety invariant failed")
    if not isinstance(payload["assets"], list) or {a.get("symbol") for a in payload["assets"] if isinstance(a, dict)} != set(expected_symbols):
        raise ValueError("Watchlist incomplete or duplicated")
    if len(payload["assets"]) != len(expected_symbols):
        raise ValueError("Duplicate market symbols")
    allowed_asset = {"symbol", "ticker", "label", "observed_at", "stale_at_export", "stale_after_seconds", "contract_active", "price", "change_24h_pct", "volume_24h_usd", "funding_rate_pct", "spread_pct", "dex_liquidity_usd", "long_score", "short_score", "research_state", "blocking_flags", "mint_verified", "trade_url", "history"}
    for row in payload["assets"]:
        if set(row) != allowed_asset:
            raise ValueError("Unexpected asset fields")
        if not isinstance(row["history"], list) or any(set(p) != {"ts", "price"} for p in row["history"]):
            raise ValueError("Unexpected history fields")
        if any(not isinstance(x, str) or len(x) > 90 for x in row["blocking_flags"]):
            raise ValueError("Invalid risk flags")
    if not isinstance(payload["news"], list) or any(set(n) != {"headline", "source", "url", "first_seen"} for n in payload["news"]):
        raise ValueError("Unexpected news fields")
    # Explicit schema validation prevents extending the exporter to leak a
    # private field without updating this independent security boundary.
    json.dumps(payload, allow_nan=False)
    return True


def status_file(base):
    return base / "publisher-status.json"


def write_status(base, **fields):
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = status_file(base)
    state = {}
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    state.update(fields)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2) + "\n")
    os.chmod(temp, 0o600)
    temp.replace(path)


def publisher_run(base, repo=REPO, *, dry_run=False):
    base = Path(base).expanduser().resolve()
    if repo != REPO:
        raise ValueError("Repository URL is pinned; do not publish to an arbitrary destination")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = base / "publisher.lock"
    with lock.open("a+") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "already running"
        write_status(base, last_attempt_at=now(), state="running", error=None)
        try:
            result = _publish_locked(base, repo, dry_run=dry_run)
            write_status(base, state=result, error=None, last_checked_at=now())
            return result
        except Exception as exc:
            # The status store is private; don't save sensitive subprocess stderr.
            write_status(base, state="error", error=str(exc)[:200], last_failed_at=now())
            raise


def _publish_locked(base, repo, *, dry_run=False):
    expected = [a.symbol for a in assets()]
    store = Storage(data_dir() / "afterburner.db")
    payload = public_payload(store, assets(), settings())
    validate_payload(payload, expected)
    if not any(a.get("observed_at") for a in payload["assets"]):
        raise RuntimeError("No market samples collected: refusing to publish empty dashboard")
    latest = max((a.get("observed_at") or "") for a in payload["assets"])
    work = base / "publisher-repo"
    if not (work / ".git").is_dir():
        if work.exists():
            raise RuntimeError("Dedicated publisher checkout path is occupied; refusing to overwrite")
        # Clone only public repository. Git credentials are handled by the host.
        r = subprocess.run(["git", "clone", "--quiet", "--branch", BRANCH, repo, str(work)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           timeout=120, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode:
            raise RuntimeError("Git clone failed; verify noninteractive GitHub authentication")
    if git(work, "remote", "get-url", REMOTE) not in (REPO, "git@github.com:Leshan3D/GTA-6-Afterburner.git"):
        raise RuntimeError("Publisher checkout origin does not match the authorized repository")
    if git(work, "status", "--porcelain"):
        raise RuntimeError("Publisher checkout has uncommitted changes; resolve manually before retrying")
    git(work, "fetch", REMOTE, BRANCH)
    git(work, "checkout", "-B", "afterburner-public-publish", "origin/main")
    destination = work / "site" / "data.json"
    if not destination.parent.is_dir():
        raise RuntimeError("Expected site/ missing in published repository")
    previous = None
    if destination.is_file():
        try:
            previous = json.loads(destination.read_text())
        except ValueError:
            raise RuntimeError("Existing public snapshot is invalid JSON; refusing to replace")
    if not publishable_difference(previous, payload):
        return "unchanged"
    # No changes to site/index.html or any nonpublic file.
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    git(work, "add", "--", "site/data.json")
    if not git(work, "diff", "--cached", "--name-only"):
        return "unchanged"
    if dry_run:
        git(work, "restore", "--staged", "--worktree", "--", "site/data.json")
        return "dry_run_validated"
    git(work, "-c", "user.name=AFTERBURNER Publisher", "-c", "user.email=afterburner-publisher@users.noreply.github.com",
        "commit", "-m", "data: refresh AFTERBURNER public snapshot")
    git(work, "push", REMOTE, "HEAD:main")
    write_status(base, last_push_at=now(), last_observation_at=latest,
                 last_commit=git(work, "rev-parse", "--short", "HEAD"),
                 deployment_state="not_verified")
    return "pushed"


def main(argv=None):
    parser = argparse.ArgumentParser(description="AFTERBURNER GitHub Pages data publisher")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    opts = parser.parse_args(argv)
    from .config import load_env_file
    load_env_file()
    state = data_dir() / "publisher"
    if opts.status:
        path = status_file(state)
        print(path.read_text() if path.exists() else "Publisher has not run")
        return 0
    try:
        print("AFTERBURNER publisher:", publisher_run(state, dry_run=opts.dry_run))
        return 0
    except (RuntimeError, ValueError, subprocess.TimeoutExpired, OSError) as exc:
        print("AFTERBURNER publisher failed:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
