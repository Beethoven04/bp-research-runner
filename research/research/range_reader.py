"""Range reader: which of our brands (and other Arabic brands) does a fit company already sell?

Evidence by platform, strongest first:
  shopify      /products.json  -> vendor field (kind=vendor) and product title (kind=title)
  woocommerce  Store API search -> product names (kind=title)
  any site     on-site search   -> only product LINKS whose URL slug names the brand (kind=slug).
               A search page repeats the query ("results for Lattafa"), so its text is NEVER used as evidence.
Gap = our brands with stock in our shop that the company does not sell (Afnan only SE/NO/FI).
"""
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import brands as B
from .crawl import UA

# Well-known brands: if a shop's URLs name at least REF_MIN of them, its URLs carry brand names, so a brand that
# appears in NO url of a complete sitemap is really not sold.
REF_BRANDS = ["dior", "chanel", "yves saint laurent", "ysl", "armani", "versace", "hugo boss", "calvin klein", "lancome",
              "guerlain", "paco rabanne", "rabanne", "carolina herrera", "gucci", "prada", "dolce gabbana", "jean paul gaultier",
              "givenchy", "tom ford", "burberry", "montblanc", "azzaro", "valentino", "narciso rodriguez", "mugler",
              "lattafa", "armaf", "afnan", "rasasi", "al haramain", "ajmal", "maison alhambra", "alhambra", "swiss arabian",
              "fragrance world", "arabian oud", "paris corner", "khadlaj", "zimaya", "ard al zaafaran", "nabeel"]
REF_MIN = 5
SLUG_ALIASES = {"Maison Alhambra": ["alhambra"], "Ard Al Zaafaran": ["zaafaran", "ard al zafaran"],
                "Swiss Arabian": ["swissarabian"], "Lattafa Pride": ["lattafa pride"]}
MAX_URLS = 300000

SEARCH_URLS = ["/search?q={q}", "/catalogsearch/result/?q={q}", "/?s={q}&post_type=product", "/sok?q={q}",
               "/search?query={q}", "/zoeken?q={q}", "/recherche?q={q}", "/buscar?q={q}", "/cerca?q={q}"]


def _json(s, url, params=None):
    try:
        r = s.get(url, params=params, timeout=25)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            return r.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def shopify(s, base, brands):
    """Reads the whole catalogue (up to 10,000 products). Returns (hits, n, complete, vendors)."""
    from collections import Counter
    hits, n, complete, vendors = [], 0, False, Counter()
    for page in range(1, 41):
        d = _json(s, f"{base}/products.json", {"limit": 250, "page": page})
        if d is None:
            break
        if not d.get("products"):
            complete = page > 1
            break
        for p in d["products"]:
            n += 1
            vendors[(p.get("vendor") or "").strip().lower()] += 1
            url = f"{base}/products/{p.get('handle','')}"
            hits += B.find(brands, p.get("vendor") or "", "vendor", url)
            hits += B.find(brands, p.get("title") or "", "title", url)
        if len(d["products"]) < 250:
            complete = True
            break
        time.sleep(1)
    return hits, n, complete, vendors


def woocommerce(s, base, brands):
    hits, n = [], 0
    for b in brands:
        d = _json(s, f"{base}/wp-json/wc/store/v1/products", {"search": b["name"], "per_page": 20})
        if d is None:
            return None, 0          # Store API closed -> fall back to generic search
        for p in d:
            n += 1
            hits += B.find([b], p.get("name") or "", "title", p.get("permalink") or base)
        time.sleep(0.7)
    return hits, n


def generic(s, base, brands, host):
    """On-site search; only product links with the brand in the URL slug count."""
    hits, tried, pattern = [], 0, None
    for b in brands:
        q = b["name"].replace(" ", "+")
        urls = [pattern.format(q=q)] if pattern else [p.format(q=q) for p in SEARCH_URLS]
        for path in urls:
            try:
                r = s.get(base + path, timeout=20)
            except requests.RequestException:
                continue
            tried += 1
            if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                continue
            soup = BeautifulSoup(r.text, "lxml")
            found = False
            for a in soup.find_all("a", href=True):
                href = urljoin(r.url, a["href"])
                u = urlparse(href)
                if u.netloc.lower().removeprefix("www.") != host or re.search(r"search|[?&](q|s|query)=", href, re.I):
                    continue
                h = B.find([b], u.path, "slug", href)
                if h:
                    hits += h
                    found = True
                    break
            if pattern is None and (found or len(soup.find_all("a")) > 30):
                pattern = path.replace(q, "{q}")   # this search URL works on this site: reuse it
            if found or pattern:
                break
            time.sleep(0.5)
        if pattern is None:
            break            # no working search URL on this site: range unknown, don't hammer it
        time.sleep(0.7)
    return hits, (1 if pattern else 0)


SITEMAP_SECONDS = 600     # a whole-site read may take long; past this, no claim (range stays unknown)


def _sitemap_paths(base, host, q):
    import logging
    from usp.objects.sitemap import InvalidSitemap
    from usp.tree import sitemap_tree_for_homepage
    logging.getLogger("usp").setLevel(logging.CRITICAL)
    try:
        tree = sitemap_tree_for_homepage(base + "/")
        invalid = sum(isinstance(x, InvalidSitemap) for x in tree.all_sitemaps())
        paths = set()
        for pg in tree.all_pages():
            u = urlparse(pg.url)
            if u.netloc.lower().removeprefix("www.") == host:
                paths.add(B.norm(requests.utils.unquote(u.path)))
            if len(paths) >= MAX_URLS:
                break
        q.put((invalid, paths))
    except Exception as e:
        q.put(f"sitemap error {type(e).__name__}")


def _sitemap_paths_limited(base, host, seconds=None):
    """Runs the sitemap read in a child process so a huge or hanging site can be stopped after SITEMAP_SECONDS."""
    import multiprocessing as mp
    import queue
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_sitemap_paths, args=(base, host, q), daemon=True)
    p.start()
    try:
        return q.get(timeout=seconds or SITEMAP_SECONDS)
    except queue.Empty:
        return f"sitemap timeout ({seconds or SITEMAP_SECONDS}s)"
    finally:
        if p.is_alive():
            p.terminate()
        p.join(5)


def sitemap(base, brands, host, product_names=None, known=()):
    """Whole-site read from the sitemaps (ultimate-sitemap-parser). Returns (hits, n_urls, complete, note).

    complete = every sitemap parsed + URLs carry brand names (>= REF_MIN known brands) + every brand already seen on
    the site (`known`, e.g. from on-site search) is also found here. Our own product names (e.g. "khamrah") only
    block a gap claim (kind=product_name); they never make a brand count as stocked.
    """
    got = _sitemap_paths_limited(base, host)
    if isinstance(got, str):                    # timeout / network / parser failure: no claim
        return [], 0, False, got
    invalid, paths = got
    if len(paths) < 30:
        return [], len(paths), False, f"sitemap too small ({len(paths)} urls)"
    blob = " | ".join(sorted(paths))
    pat = lambda n: re.compile(r"(?<![a-z0-9])" + re.escape(B.norm(n)) + r"(?![a-z0-9])")
    ref = {r for r in REF_BRANDS if pat(r).search(blob)}
    hits = []
    for b in brands:
        names = [b["name"]] + b.get("aliases", []) + SLUG_ALIASES.get(b["name"], [])
        subs = [x["name"] for x in brands if x["name"] != b["name"] and B.norm(x["name"]).startswith(B.norm(b["name"]) + " ")]
        text = blob
        for sb in subs:                          # "lattafa pride" urls do not prove "lattafa"
            text = pat(sb).sub(" ", text)
        m = next((m for n in names for m in [pat(n).search(text)] if m), None)
        if m:
            a = text.rfind(" | ", 0, m.start()) + 3
            z = text.find(" | ", m.end())
            hits.append({"brand": b["name"], "kind": "slug", "url": base, "snippet": text[a:z if z > 0 else None][:240]})
            continue
        for pn in (product_names or {}).get(b["name"], []):
            m = pat(pn).search(blob)
            if m:
                hits.append({"brand": b["name"], "kind": "product_name", "url": base,
                             "snippet": f"our product '{pn}' in a url"})
                break
    found = {h["brand"] for h in hits}
    missed_known = [k for k in known if k not in found]
    complete = not invalid and len(ref) >= REF_MIN and not missed_known
    note = f"urls={len(paths)} invalid_sitemaps={invalid} ref_brands={len(ref)} missed_known={missed_known}"
    return hits, len(paths), complete, note


def read(company: dict, platform: str, our_brands: list, arabic: list, in_stock: dict, product_names=None):
    host = company["domain"]
    base = f"https://{host}"
    s = requests.Session()
    s.headers["User-Agent"] = UA
    detect = our_brands + [{"name": a} for a in arabic if a not in {b["name"] for b in our_brands}]
    method, hits, n, complete, vendors = None, [], 0, False, {}
    if platform == "shopify":
        hits, n, complete, vendors = shopify(s, base, detect)
        method = "shopify_products_json" if n else None
    if method is None and platform in ("woocommerce", "other", "magento", "prestashop", None):
        if platform == "woocommerce":
            r, n = woocommerce(s, base, detect)
            if r is not None:
                hits, method, complete = r, "woo_store_api", True   # API search per brand = reliable absence
    note = ""
    if method is None or not complete:
        # sitemap: a whole-site read for any platform (also tried when Shopify/Woo reads were incomplete)
        sh, sn, sc, note = sitemap(base, detect, host, product_names, known=[h["brand"] for h in hits])
        if sc:
            hits, n, complete, method = hits + sh, sn, True, "sitemap"
        elif method is None:
            hits = hits + [h for h in sh if h["kind"] != "product_name"]
    if method is None:
        gh, ok = generic(s, base, our_brands, host)
        hits += gh
        method = "onsite_search" if ok else ("sitemap_partial" if hits else None)
    ours = {b["name"] for b in our_brands}
    summary = B.summarise(our_brands, [h for h in hits if h["brand"] in ours], company["country"], in_stock)
    stocked_all = {}
    for h in hits:
        if h["kind"] != "product_name":         # product names block an offer, they never prove stock
            stocked_all.setdefault(h["brand"], h)
    # Absence of a brand is only trusted from a complete read. Otherwise we know what they sell, not what they lack.
    return {"method": method, "products_seen": n, "hits": list(stocked_all.values()), "complete": complete,
            "gap": summary["gap"] if (method and complete) else [],
            "status": "read" if (method and complete) else "unknown", "note": note,
            "own_brand_only": own_brand_only(vendors, host, company.get("name") or "") if complete else False}


def own_brand_only(vendors, host, name):
    """Shop sells only its own label (one vendor = the shop itself, e.g. glossier, tatemcrae): not a reseller."""
    v = {k: c for k, c in vendors.items() if k}
    if not v or len(v) > 1:
        return False
    only = re.sub(r"[^a-z0-9]", "", next(iter(v)))
    site = re.sub(r"[^a-z0-9]", "", host.split(".")[0].lower())
    nm = re.sub(r"[^a-z0-9]", "", name.lower())
    return bool(only) and (only in site or site in only or (nm and (only in nm or nm in only)))
