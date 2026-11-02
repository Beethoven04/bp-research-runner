"""Run Reacher on GitHub Actions when no machine with outbound port 25 is available.

GitHub-hosted runners allow outbound SMTP (tested 10 Oct 2026: Google + Microsoft 365 MX answer correctly).
Flow: create a PRIVATE throw-away repo → push the workflow + the address list → dispatch N shards (addresses are
sharded by domain, so one mail server is never probed in parallel) → wait → download results → import into the
verification cache → delete the repo (needs `gh auth refresh -s delete_repo`, otherwise it is left private).

Keep volumes small (hundreds, not thousands): this is a one-off verification, not a service.
"""
import json
import pathlib
import shutil
import subprocess
import tempfile
import time

from . import config
from .verify import _cache, _save

WORKFLOW = config.REPO / "ci" / "verify.yml"
MAP = {"safe": "valid", "invalid": "invalid", "risky": "risky"}


def sh(*cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)} → {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def import_results(folder):
    """Reacher JSON lines (res_*.jsonl) → data/research_cache/verify.json."""
    n = {"valid": 0, "invalid": 0, "risky": 0, "unknown": 0}
    for f in pathlib.Path(folder).rglob("res_*.jsonl"):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            e = r["input"].lower()
            smtp = r.get("smtp") or {}
            verdict = MAP.get(r.get("is_reachable"), "unknown")
            if r.get("error"):
                verdict = "unknown"
            _cache[e] = {"email": e, "checked_at": time.strftime("%Y-%m-%d %H:%M"), "verdict": verdict,
                         "catch_all": smtp.get("is_catch_all"), "via": "reacher@github-actions",
                         "detail": f"reacher(ci): {r.get('is_reachable')}, catch_all={smtp.get('is_catch_all')}, "
                                   f"deliverable={smtp.get('is_deliverable')}, disabled={smtp.get('is_disabled')}"
                                   + (f", error={str(r.get('error'))[:80]}" if r.get("error") else "")}
            n[verdict] += 1
    _save()
    return n


def run(emails_file, shards=4, repo="outreach-verify-tmp", keep=False):
    user = sh("gh", "api", "user", "-q", ".login")
    full = f"{user}/{repo}"
    work = pathlib.Path(tempfile.mkdtemp())
    (work / ".github" / "workflows").mkdir(parents=True)
    (work / "data").mkdir()
    shutil.copy(WORKFLOW, work / ".github" / "workflows" / "verify.yml")
    shutil.copy(emails_file, work / "data" / "batch.txt")
    sh("git", "init", "-q", "-b", "main", cwd=work)
    sh("git", "add", "-A", cwd=work)
    sh("git", "-c", "user.name=verifier", "-c", "user.email=verifier@example.com", "commit", "-qm", "batch", cwd=work)
    if sh("gh", "repo", "view", full, "--json", "name", check=False):
        sh("git", "remote", "add", "origin", f"https://github.com/{full}.git", cwd=work)
        sh("git", "push", "-qf", "origin", "main", cwd=work)
    else:
        sh("gh", "repo", "create", repo, "--private", "--source=.", "--push", cwd=work)
    for var, key in (("HELLO_NAME", "CI_HELLO_NAME"), ("FROM_EMAIL", "CI_FROM_EMAIL")):
        if config.get(key):
            sh("gh", "variable", "set", var, "-R", full, "-b", config.get(key))
    time.sleep(5)
    sh("gh", "workflow", "run", "verify", "-R", full, "-f", "file=batch.txt", "-f", f"shards={shards}")
    time.sleep(8)
    rid = sh("gh", "run", "list", "-R", full, "-w", "verify", "--json", "databaseId", "-q", ".[0].databaseId")
    print(f"run {rid} started: https://github.com/{full}/actions/runs/{rid}")
    while sh("gh", "run", "view", rid, "-R", full, "--json", "status", "-q", ".status") != "completed":
        time.sleep(20)
    out = work / "results"
    sh("gh", "run", "download", rid, "-R", full, "-D", str(out))
    n = import_results(out)
    print("imported:", n)
    if not keep:
        if sh("gh", "repo", "delete", full, "--yes", check=False) == "" and \
                not sh("gh", "repo", "view", full, "--json", "name", check=False):
            print("temp repo deleted")
        else:
            print(f"could not delete {full} (run `gh auth refresh -s delete_repo` once). It stays private.")
    return n
