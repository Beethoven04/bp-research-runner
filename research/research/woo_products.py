"""Copy the published WooCommerce products into Supabase (table woo_products, db/marketing.sql). The marketing email
picks its products from this table (in stock, visible, with a photo; Afnan only for Scandinavia).

Read-only on WooCommerce. Runs with the orders sync (runner/.github/workflows/woo-orders.yml); locally:
python -m research.woo_products. Products no longer published are marked status 'gone' (never shown again).
Logs print counts only (the runner repo is public).
"""
import html
import re

import requests

from . import config, db
from .woo_orders import UA, _creds


def fetch():
    base, auth = _creds()
    page = 1
    while True:
        r = requests.get(f"{base}/wp-json/wc/v3/products", params={"per_page": 100, "status": "publish", "page": page},
                         auth=auth, headers={"User-Agent": UA}, timeout=90)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return
        yield from rows
        if page >= int(r.headers.get("X-WP-TotalPages", page)):
            return
        page += 1


def thumb(src: str, known: dict) -> str:
    """The 300x300 copy WordPress makes of every upload (smaller email), if it exists; else the original."""
    if not src:
        return ""
    if src in known:
        return known[src]
    small = re.sub(r"(\.[a-zA-Z]{3,4})$", r"-300x300\1", src)
    try:
        ok = requests.head(small, headers={"User-Agent": UA}, timeout=20, allow_redirects=True).status_code == 200
    except requests.RequestException:
        ok = False
    return small if ok else src


def main():
    n = 0
    with db.conn() as c:
        known = {r["image_src"]: r["image"] for r in
                 c.execute("select image_src, image from woo_products where tenant_id = %s and image_src is not null",
                           (config.TENANT,)).fetchall()}
        seen = []
        for p in fetch():
            imgs = p.get("images") or []
            src = imgs[0].get("src") if imgs else None
            brands = [b.get("name") for b in p.get("brands") or [] if b.get("name")]
            c.execute("""
              insert into woo_products (id, tenant_id, name, brand, permalink, image_src, image, stock_status, visibility,
                                        categories, total_sales, created_at, status, synced_at)
              values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::timestamp at time zone 'UTC', 'publish', now())
              on conflict (id) do update set name = excluded.name, brand = excluded.brand,
                permalink = excluded.permalink, image_src = excluded.image_src, image = excluded.image,
                stock_status = excluded.stock_status, visibility = excluded.visibility,
                categories = excluded.categories, total_sales = excluded.total_sales, created_at = excluded.created_at,
                status = 'publish', synced_at = now()""",
                      (p["id"], config.TENANT, " ".join(html.unescape(p.get("name") or "").split()), html.unescape(brands[0]) if brands else None, p.get("permalink"),
                       src, thumb(src, known) if src else None, p.get("stock_status"), p.get("catalog_visibility"),
                       [x.get("slug") for x in p.get("categories") or []], int(p.get("total_sales") or 0),
                       p.get("date_created_gmt") or p.get("date_created")))
            seen.append(p["id"])
            n += 1
        if n:   # a product that is no longer published must never be offered
            c.execute("update woo_products set status = 'gone', synced_at = now() where tenant_id = %s "
                      "and status = 'publish' and not (id = any(%s))", (config.TENANT, seen))
        c.commit()
    print(f"products synced: {n}")


if __name__ == "__main__":
    main()
