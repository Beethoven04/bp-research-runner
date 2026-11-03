"""One-off pass: tag existing fit/review leads as `distributor` (contacted last, decided 2 Nov 2026).

New leads get this tag inside `fit.check`. This pass reads the home page of leads checked before the rule existed,
applies the same `fit.DISTRIBUTOR` words, and changes only the segment and priority. Status is not touched.
Run: python -m research.tag_distributors [--dry]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

from . import db, fit, worker
from .crawl import UA


def check(row):
    s = requests.Session()
    s.headers["User-Agent"] = UA
    final, html = fit.fetch(s, f"https://{row['domain']}/", timeout=15)
    if not html:
        return row, None, None
    soup = BeautifulSoup(html, "lxml")
    head = " ".join([soup.title.string if soup.title and soup.title.string else ""] +
                    [m.get("content", "") for m in soup.find_all("meta", attrs={"name": "description"})])
    m = fit.DISTRIBUTOR.search(head + " " + soup.get_text(" ", strip=True)[:4000])
    return row, final, (m.group(0) if m else None)


def main(dry):
    with db.conn() as c:
        rows = c.execute("select id, domain, segment from companies where status in ('fit','review') "
                         "and domain is not null and not coalesce(is_chain,false) "
                         "and coalesce(segment,'') <> 'distributor'").fetchall()
    print(f"{len(rows)} leads to check", flush=True)
    hits = []
    with ThreadPoolExecutor(16) as ex:
        for i, (row, final, word) in enumerate(ex.map(check, rows), 1):
            if word:
                hits.append((row, final, word))
                print(f"distributor: {row['domain']} ({row['segment']}) word='{word}'", flush=True)
            if i % 200 == 0:
                print(f"{i}/{len(rows)} checked", flush=True)
    if not dry:
        with db.conn() as c:
            for row, final, word in hits:
                db.evidence(c, row["id"], "distributor_words", word, final, f"was segment {row['segment']}")
                c.execute("update companies set segment='distributor', updated_at=now() where id=%s", (row["id"],))
                worker.score(c, row["id"])
            c.commit()
    print(f"done: {len(hits)} distributors{' (dry run, nothing saved)' if dry else ''}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    main(ap.parse_args().dry)
