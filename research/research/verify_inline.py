"""Verify unchecked primary contacts on the machine that runs the worker (GitHub Actions runner with a local Reacher
container, outbound port 25 allowed). The database is the cache: only verdict='unchecked' rows are sent, plus
'unknown' rows again after 3 hours (max 3 checks), usually from another runner IP (greylisting).

Run: REACHER_URL=http://localhost:8080 python -m research.verify_inline --minutes 30
Logs print counts only (the runner repo is public): never an address.
"""
import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter

from . import config, db, verify
from .verify_batch import pending


def main(minutes, limit, countries=(), shard=(0, 1), threads=6):
    end = time.time() + minutes * 60
    with db.conn() as c:
        emails = pending(c, limit * shard[1], countries)
    emails = [e for i, e in enumerate(emails) if i % shard[1] == shard[0]][:limit]   # parallel runners split the list
    n = Counter()
    lock = threading.Lock()

    def one(e):
        # each check can take ~45 s (SMTP), so stop starting new ones 2 min before the end
        if time.time() > end - 120:
            return
        v = verify.verify(e, use_reoon_on_unknown=True, force=True)
        with db.conn() as c, c.transaction():
            c.execute("update contacts set verdict=%s, verdict_detail=%s, catch_all=%s, checked_at=now(), "
                      "checks=checks+1 where email=%s and verdict in ('unchecked','unknown')",
                      (v["verdict"], (v.get("detail") or "")[:500], v.get("catch_all"), e))
        with lock:
            n[v["verdict"]] += 1

    # different shops = different mail servers, so parallel checks do not hammer one server (3 Nov 2026)
    with ThreadPoolExecutor(threads) as ex:
        list(ex.map(one, emails))
    print(json.dumps({"pending": len(emails), "checked": sum(n.values()), "verdicts": dict(n)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--focus", default="", help="only contacts of these countries, e.g. DE")
    ap.add_argument("--shard", default="0/1", help="i/n: this runner checks every n-th address starting at i")
    a = ap.parse_args()
    if not config.REACHER_URL:
        raise SystemExit("REACHER_URL not set")
    i, n = (int(x) for x in a.shard.split("/"))
    main(a.minutes, a.limit, [x.strip().upper() for x in a.focus.split(",") if x.strip()], (i, n), a.threads)
