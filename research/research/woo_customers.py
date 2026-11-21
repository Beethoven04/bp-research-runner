"""Copy the clients registered on b2bperfume.com (WooCommerce customers) into Supabase (table woo_customers,
db/marketing.sql). They are the main audience of the marketing email (decision 21 Nov 2026).

Read-only on WooCommerce. Runs with the orders sync (runner/.github/workflows/woo-orders.yml); locally:
python -m research.woo_customers. Logs print counts only (the runner repo is public).
"""
import html

import requests

from . import db
from . import config
from .woo_orders import UA, _creds, _match


def fetch():
    base, auth = _creds()
    page = 1
    while True:
        r = requests.get(f"{base}/wp-json/wc/v3/customers", params={"per_page": 100, "role": "all", "page": page},
                         auth=auth, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return
        yield from rows
        if page >= int(r.headers.get("X-WP-TotalPages", page)):
            return
        page += 1


def main():
    n = matched = 0
    with db.conn() as c:
        for u in fetch():
            email = (u.get("email") or "").strip().lower()
            if not email:
                continue
            bill = u.get("billing") or {}
            cid = _match(c, email)
            c.execute("""
              insert into woo_customers (id, tenant_id, email, first_name, last_name, company, country, role,
                                         registered_at, company_id, synced_at)
              values (%s, %s, %s, %s, %s, %s, %s, %s, %s::timestamp at time zone 'UTC', %s, now())
              on conflict (id) do update set email = excluded.email, first_name = excluded.first_name,
                last_name = excluded.last_name, company = excluded.company, country = excluded.country,
                role = excluded.role, company_id = coalesce(excluded.company_id, woo_customers.company_id),
                synced_at = now()""",
                      (u["id"], config.TENANT, email, " ".join(html.unescape(u.get("first_name") or "").split()) or None,
                       " ".join(html.unescape(u.get("last_name") or "").split()) or None,
                       " ".join(html.unescape(bill.get("company") or "").split()) or None, (bill.get("country") or "").upper() or None, u.get("role"),
                       u.get("date_created_gmt") or u.get("date_created"), cid))
            n += 1
            matched += cid is not None
        c.commit()
    print(f"customers synced: {n}, matched to leads: {matched}")


if __name__ == "__main__":
    main()
