"""Verify primary contacts in bulk on GitHub Actions (Reacher), then write verdicts back to the database.

Only primary contacts of fit companies whose domain has a mail server are sent. Results are cached, so an
address is never checked twice. Run: python -m research.verify_batch --limit 400
"""
import argparse
import json
import tempfile

from . import ci, config, db
from .verify import _cache


def pending(c, limit, countries=()):
    return [r["email"] for r in c.execute(
        "select k.email from contacts k join companies c on c.id=k.company_id "
        "where k.is_primary and (k.verdict='unchecked' or (k.verdict='unknown' and k.checks < 3 "
        " and k.checked_at < now() - interval '3 hours')) and coalesce(k.mx_provider,'') not in ('','none') "
        "and c.status in ('fit','review') and c.tenant_id=%s and (cardinality(%s::text[])=0 or c.country=any(%s)) "
        "order by (k.verdict='unknown'), c.priority desc, k.email limit %s",
        (config.TENANT, list(countries), list(countries), limit)).fetchall()]


def write_back(c):
    n = 0
    for r in c.execute("select id,email from contacts where verdict='unchecked'").fetchall():
        v = _cache.get(r["email"])
        if v and v.get("verdict") in ("valid", "invalid", "risky", "unknown", "no_mx"):
            c.execute("update contacts set verdict=%s, verdict_detail=%s, catch_all=%s, checked_at=now() where id=%s",
                      (v["verdict"], v.get("detail", "")[:500], v.get("catch_all"), r["id"]))
            n += 1
    return n


def main(limit, shards):
    with db.conn() as c:
        emails = [e for e in pending(c, limit) if e not in _cache]
    if emails:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("\n".join(emails) + "\n")
        print("checking", len(emails), "addresses on GitHub Actions")
        ci.run(f.name, shards=shards, repo="bp-verify-tmp")
    with db.conn() as c, c.transaction():
        n = write_back(c)
        print(json.dumps({"sent": len(emails), "written_back": n,
                          "verdicts": c.execute("select verdict, count(*) from contacts where is_primary group by 1").fetchall()}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--shards", type=int, default=6)
    a = ap.parse_args()
    main(a.limit, a.shards)
