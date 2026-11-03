"""One-off (4 Nov 2026): send every `site_down` reject back to the fit check after the fetch fix in `fit.fetch`.

Up to ~35% of site_down rejects were alive (SSL errors, 403 bot walls, www-only hosts). For each such company:
status -> 'new', its site_down evidence row is deleted, and its existing 'fit:<id>' job is re-queued (worker.feed()
uses ON CONFLICT DO NOTHING, so a done job would never run again).
Run only after the fixed fit.py is published to the runner. Run: python -m research.reset_site_down [--dry]
"""
import argparse

from . import db


def main(dry):
    with db.conn() as c:
        c.execute("set statement_timeout = '300s'")
        ids = [r["id"] for r in c.execute(
            "select distinct co.id from company_evidence e join companies co on co.id=e.company_id "
            "where e.fact='not_fit_reason' and e.value='site_down' and co.status='not_fit' and co.domain is not null"
        ).fetchall()]
        print(f"{len(ids)} site_down rejects", flush=True)
        if dry or not ids:
            return
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            c.execute("delete from company_evidence where fact='not_fit_reason' and value='site_down' and company_id = any(%s)", (part,))
            c.execute("update companies set status='new', updated_at=now() where id = any(%s)", (part,))
            c.execute("update jobs set status='queued', attempts=0, last_error=null, run_after=now(), updated_at=now() "
                      "where kind='fit' and dedupe = any(%s)", ([f"fit:{x}" for x in part],))
            c.commit()
        print(f"reset {len(ids)} companies to 'new' and re-queued their fit jobs", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    main(ap.parse_args().dry)
