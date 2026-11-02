"""Our own shop (WooCommerce, read-only key). Source of truth for which brands we can offer right now.

A brand is offered in emails only if it has at least one product in stock in the shop.
"""
import os
from pathlib import Path

import requests

from . import config

UA = "curl/8.7.1"   # Cloudflare blocks Python's default User-Agent


def _creds():
    env = config.REPO / "private" / "woocommerce.env"
    vals = {}
    for line in Path(env).read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals["WC_URL"].rstrip("/"), (vals["WC_KEY"], vals["WC_SECRET"])


def _get(path, params=None):
    base, auth = _creds()
    r = requests.get(f"{base}/wp-json/wc/v3/{path}", params=params or {}, auth=auth,
                     headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r


def brands_in_stock():
    """-> {brand: {"products": n_total, "in_stock": n_in_stock}} for every shop brand."""
    out = {}
    for b in _get("products/brands", {"per_page": 100}).json():
        n_in = int(_get("products", {"brand": b["id"], "stock_status": "instock", "per_page": 1}).headers.get("X-WP-Total", 0))
        out[b["name"]] = {"id": b["id"], "products": b["count"], "in_stock": n_in}
    return out


def customer_domains():
    """Email domains of current customers (for the exclusion list). Free-mail domains are skipped."""
    free = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "live.com", "hotmail.nl", "msn.com"}
    emails, page = set(), 1
    while True:
        rows = _get("customers", {"per_page": 100, "page": page}).json()
        if not rows:
            break
        for c in rows:
            for e in (c.get("email"), (c.get("billing") or {}).get("email")):
                if e and "@" in e:
                    emails.add(e.strip().lower())
        page += 1
    return emails, {e.split("@")[1] for e in emails if e.split("@")[1] not in free}


GENERIC = {"oud", "musk", "rose", "amber", "intense", "man", "men", "women", "woman", "for", "him", "her", "unisex",
           "pour", "homme", "femme", "edition", "limited", "gift", "set", "tester", "new", "black", "white", "gold",
           "silver", "blue", "red", "pink", "vanilla", "spray", "perfume", "oil", "body", "mist", "deodorant", "the",
           "de", "la", "le", "and", "eau", "parfum", "toilette", "edp", "edt", "ml", "extrait", "concentrated"}


def product_names_by_brand():
    """-> {brand: [distinctive product names]} from OUR shop (e.g. Lattafa: ["khamrah", "asad", ...]).

    Used by the range reader: a shop whose URLs name one of these products sells that brand, even when the brand
    name itself is missing from the URL. Names that are only generic words ("oud", "rose") are dropped.
    """
    from . import brands as B
    import re
    out = {}
    for b in _get("products/brands", {"per_page": 100}).json():
        names, page = set(), 1
        while True:
            rows = _get("products", {"brand": b["id"], "per_page": 100, "page": page}).json()
            if not rows:
                break
            for p in rows:
                n = B.norm(p.get("name") or "")
                n = re.sub(r"(?<![a-z0-9])" + re.escape(B.norm(b["name"])) + r"(?![a-z0-9])", " ", n)
                n = re.sub(r"\b\d+(\s?ml)?\b|[^a-z0-9 ]", " ", n)
                words = [w for w in n.split() if w not in ("eau", "de", "parfum", "toilette", "edp", "edt", "ml",
                                                            "extrait", "spray", "perfume", "by", "for", "men",
                                                            "women", "unisex", "man", "woman", "pour", "homme", "femme")]
                phrase = " ".join(words)
                if len(phrase) >= 5 and not all(w in GENERIC for w in words):
                    names.add(phrase)
            page += 1
        out[b["name"]] = sorted(names)
    return out
