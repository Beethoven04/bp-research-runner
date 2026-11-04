"""One-off (4 Nov 2026): repair contacts that carry an encoding leftover ("20info@x.de" from %20, "59info@" from
&#59;, "u003einfo@" from \\u003e, "3ainfo@" from %3A ...), found by b2bperfum-ed.

A prefix is never stripped blindly (a real "20minutes@" would be damaged). For each suspect, its source page is read
again with the fixed `emails.extract_emails`:
- the cleaned address is on the page -> fix the row (verdict 'unchecked', checks 0), or delete it if the company
  already has that address;
- the suspect form itself is still extracted -> it is real, keep it;
- page gone / neither found -> keep (logged), nothing guessed.
Affected companies get `worker.choose_primary` + `worker.score` again.
Run: python -m research.fix_email_prefix [--dry]
"""
import argparse
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from . import db, fit, worker
from .crawl import UA
from .emails import extract_emails

SUSPECT = r"^(2520|20|3[a-c]|59|u003e|u003c|x20)[a-z]"
PREFIX = re.compile(r"^(2520|20|3[a-c]|59|u003e|u003c|x20)(?=[a-z])")


def check(row):
    s = requests.Session()
    s.headers["User-Agent"] = UA
    _, html = fit.fetch(s, row["source_url"], timeout=15)
    if not html:
        return row, "page_gone"
    found = extract_emails(html)
    clean = PREFIX.sub("", row["email"])
    if row["email"] in found:
        return row, "real"
    if clean in found:
        return row, clean
    return row, "not_found"


def main(dry):
    with db.conn() as c:
        rows = c.execute(f"select id, company_id, email, source_url from contacts where email ~ '{SUSPECT}'").fetchall()
    print(f"{len(rows)} suspect contacts", flush=True)
    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(check, rows))
    stats = {"fixed": 0, "deleted_duplicate": 0, "real": 0, "page_gone": 0, "not_found": 0}
    with db.conn() as c:
        touched = set()
        for row, res in results:
            if res in ("real", "page_gone", "not_found"):
                stats[res] += 1
                continue
            dup = c.execute("select 1 from contacts where company_id=%s and email=%s", (row["company_id"], res)).fetchone()
            if dup:
                stats["deleted_duplicate"] += 1
                if not dry:
                    c.execute("delete from contacts where id=%s", (row["id"],))
            else:
                stats["fixed"] += 1
                if not dry:
                    c.execute("update contacts set email=%s, verdict='unchecked', verdict_detail=null, catch_all=null, "
                              "checked_at=null, checks=0 where id=%s", (res, row["id"]))
            touched.add(row["company_id"])
        if not dry:
            for cid in touched:
                worker.choose_primary(c, cid)
                worker.score(c, cid)
            c.commit()
    print(stats, "(dry run, nothing saved)" if dry else "", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    main(ap.parse_args().dry)
