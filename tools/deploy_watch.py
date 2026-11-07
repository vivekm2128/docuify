#!/usr/bin/env python3
"""Keep this checkout in step with GitHub, and restart the app when it moves.

The app is edited by a cloud session that merges pull requests. This runs on
the Mac, notices the new commit, pulls it, and restarts the service. Three
things make that safe to leave running unattended:

  * it only ever fast-forwards, so it can never rewrite local work;
  * it refuses to touch a dirty tree, so an interrupted edit is never lost;
  * it rolls back and files an incident if the app does not come back healthy.

    python3 tools/deploy_watch.py --once     # check once and exit
    python3 tools/deploy_watch.py            # loop forever (default 10s)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(
    os.environ.get("DOCUFY_ROOT") or Path(__file__).resolve().parent.parent
).resolve()
STATE = ROOT / "data" / "deploy-watch.json"
INCIDENTS = ROOT / "data" / "deploy-incidents"
LOG_PATH = ROOT / "data" / "deploy-watch.log"
HEALTH_URL = os.environ.get("DOCUFY_HEALTH_URL", "http://127.0.0.1:8800/api/health")


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def log(message: str) -> None:
    # flush: this runs unattended, usually watched through a pipe or log file,
    # where without it nothing appears until the buffer happens to fill.
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def remote_head() -> str:
    """Ask GitHub for main's commit without fetching any objects.

    One lightweight request is what makes a short poll interval affordable.
    """
    out = git("ls-remote", "origin", "main")
    return out.split()[0] if out else ""


def local_head() -> str:
    return git("rev-parse", "HEAD")


def tree_is_clean() -> bool:
    return git("status", "--porcelain", "--untracked-files=no") == ""


def app_healthy(timeout: float = 8.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def restart_app(port: int, wait_seconds: int = 60) -> bool:
    """Start the app fresh and wait for it to answer.

    Starting the app also starts the model server, so a cold start needs real
    time before giving up.
    """
    subprocess.run(
        ["bash", "-lc", f"lsof -ti tcp:{port} | xargs kill 2>/dev/null || true"],
        capture_output=True,
    )
    time.sleep(2)  # let the old process release the port
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as logfile:
        subprocess.Popen(
            [
                "bash",
                "-lc",
                f"cd {ROOT} && exec .venv/bin/python run.py --port {port} --no-browser",
            ],
            stdout=logfile,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(wait_seconds // 2):
        time.sleep(2)
        if app_healthy():
            return True
    return False


def file_incident(previous: str, failed: str, reason: str) -> Path:
    """Write the failure where the investigating automation can find it."""
    INCIDENTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = INCIDENTS / f"{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "at": stamp,
                "reason": reason,
                "rolled_back_to": previous[:12],
                "failed_commit": failed[:12],
                "repo": str(ROOT),
                "health_url": HEALTH_URL,
                "log": str(LOG_PATH),
                "status": "open",
            },
            indent=2,
        )
    )
    return path


def check_once(port: int) -> int:
    state = load_state()
    remote = remote_head()
    local = local_head()

    if not remote:
        log("could not read origin/main; skipping")
        return 1
    if remote == local:
        return 0

    log(f"origin/main moved: {local[:7]} -> {remote[:7]}")

    if not tree_is_clean():
        log("working tree has changes; not pulling until it is clean")
        return 1

    if not app_healthy():
        # Not a reason to skip the update: a stopped app should still receive
        # the new commit, and the restart below is what brings it back.
        log("app is not healthy before the pull; updating and restarting anyway")

    try:
        git("pull", "--ff-only", "origin", "main")
    except RuntimeError as exc:
        log(f"fast-forward failed, leaving the checkout alone: {exc}")
        return 1

    log("pulled; restarting the app")
    if restart_app(port):
        log("healthy on the new commit")
        state["last_good"] = local_head()
        save_state(state)
        return 0

    log("app did not come back healthy; rolling back")
    git("reset", "--hard", local, check=False)
    recovered = restart_app(port)
    incident = file_incident(local, remote, "unhealthy after deploy")
    log(f"incident filed at {incident}")
    if recovered:
        log(f"rolled back to {local[:7]} and healthy again")
    else:
        log("still unhealthy after rollback; needs a human")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args()

    if args.once:
        return check_once(args.port)

    log(f"watching origin/main every {args.interval:.0f}s")
    while True:
        try:
            check_once(args.port)
        except Exception as exc:  # noqa: BLE001 - a watcher must not die
            log(f"check failed: {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
