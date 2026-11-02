"""Verify unchecked primary contacts on the machine that runs the worker (GitHub Actions runner with a local Reacher
container, outbound port 25 allowed). The database is the cache: only verdict='unchecked' rows are sent.

Run: REACHER_URL=http://localhost:8080 python -m research.verify_inline --minutes 30
Logs print counts only (the runner repo is public): never an address.
"""
import argparse
import json
import time
from collections import Counter

from . import config, db, verify
from .verify_batch import pending


def main(minutes, limit):
    end = time.time() + minutes * 60
    with db.conn() as c:
        emails = pending(c, limit)
    n = Counter()
    for e in emails:
        if time.time() > end:
            break
        v = verify.verify(e, use_reoon_on_unknown=True, force=True)
        with db.conn() as c, c.transaction():
            c.execute("update contacts set verdict=%s, verdict_detail=%s, catch_all=%s, checked_at=now() "
                      "where email=%s and verdict='unchecked'",
                      (v["verdict"], (v.get("detail") or "")[:500], v.get("catch_all"), e))
        n[v["verdict"]] += 1
    print(json.dumps({"pending": len(emails), "checked": sum(n.values()), "verdicts": dict(n)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--limit", type=int, default=400)
    a = ap.parse_args()
    if not config.REACHER_URL:
        raise SystemExit("REACHER_URL not set")
    main(a.minutes, a.limit)
