"""Copy WooCommerce orders into Supabase (table woo_orders, db/dashboard.sql) for the dashboard's Orders page.

Each order is matched to a lead: billing email = a known contact, else email domain = a company's domain
(free-mail domains never match by domain). Read-only on WooCommerce. Runs every 30 min on GitHub Actions
(runner/.github/workflows/woo-orders.yml); locally:  python -m research.woo_orders [--days N]
Logs print counts only (the runner repo is public).
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import requests

from . import config, db

UA = "curl/8.7.1"   # Cloudflare blocks Python's default User-Agent (same as shop.py)
FREE = {"gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com", "icloud.com", "me.com",
        "msn.com", "aol.com", "gmx.de", "gmx.net", "web.de", "t-online.de", "hotmail.nl", "live.nl", "ziggo.nl",
        "kpnmail.nl", "proton.me", "protonmail.com", "orange.fr", "free.fr", "libero.it", "seznam.cz", "wp.pl"}


def _creds():
    base, key, secret = (os.environ.get(k, "") for k in ("WC_URL", "WC_KEY", "WC_SECRET"))
    if not (base and key and secret):
        raise SystemExit("WC_URL / WC_KEY / WC_SECRET missing")
    return base.rstrip("/"), (key, secret)


def fetch(days=None):
    base, auth = _creds()
    params = {"per_page": 100, "orderby": "date", "order": "desc"}
    if days:
        params["modified_after"] = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    page = 1
    while True:
        r = requests.get(f"{base}/wp-json/wc/v3/orders", params={**params, "page": page}, auth=auth,
                         headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return
        yield from rows
        if page >= int(r.headers.get("X-WP-TotalPages", page)):
            return
        page += 1


def _match(c, email):
    if not email or "@" not in email:
        return None
    email = email.strip().lower()
    row = c.execute("select company_id from contacts where lower(email) = %s order by is_primary desc limit 1",
                    (email,)).fetchone()
    if row:
        return row["company_id"]
    dom = email.split("@", 1)[1]
    if dom in FREE:
        return None
    row = c.execute("select id from companies where tenant_id = %s and domain = %s limit 1",
                    (config.TENANT, dom)).fetchone()
    return row["id"] if row else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None, help="only orders changed in the last N days (default: all)")
    a = ap.parse_args()
    n = matched = 0
    with db.conn() as c:
        for o in fetch(a.days):
            bill = o.get("billing") or {}
            email = (bill.get("email") or "").strip().lower() or None
            cid = _match(c, email)
            items = [{"name": i.get("name"), "qty": i.get("quantity"), "total": i.get("total")}
                     for i in o.get("line_items") or []]
            created = o.get("date_created_gmt") or o.get("date_created")
            c.execute("""
              insert into woo_orders (id, tenant_id, number, status, total, currency, created_at, email, company_name,
                                      country, items, company_id, synced_at)
              values (%s, %s, %s, %s, %s, %s, %s::timestamp at time zone 'UTC', %s, %s, %s, %s, %s, now())
              on conflict (id) do update set status = excluded.status, total = excluded.total,
                email = excluded.email, company_name = excluded.company_name, country = excluded.country,
                items = excluded.items, company_id = coalesce(excluded.company_id, woo_orders.company_id),
                synced_at = now()""",
                      (o["id"], config.TENANT, o.get("number"), o.get("status") or "unknown", o.get("total") or None,
                       o.get("currency"), created, email, bill.get("company") or None,
                       (bill.get("country") or "").upper() or None, json.dumps(items), cid))
            n += 1
            matched += cid is not None
        c.commit()
    print(f"orders synced: {n}, matched to leads: {matched}")


if __name__ == "__main__":
    main()
