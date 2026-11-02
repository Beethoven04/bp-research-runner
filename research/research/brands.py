"""Brand detection with evidence. Pure functions, no network.

A brand counts as "stocked" only with evidence we can show: the URL and the exact snippet where it was seen.
Evidence kinds, strongest first:
  vendor   - brand/vendor field of a product (Shopify products.json, WooCommerce Store API)
  slug     - product or brand URL path (/products/lattafa-khamrah-100ml, /brand/armaf/)
  title    - product title
  context  - page text, with perfume words nearby
Ambiguous names (Rave, Risala, French Avenue, Niche Emarati) never count from `context` alone.
"""
import re
import unicodedata

PERFUME_WORDS = re.compile(
    r"\b(edp|edt|eau de parfum|eau de toilette|parfum|parfym|perfume|profumo|perfum|fragrance|cologne|"
    r"oud|attar|\d{2,3}\s?ml)\b", re.I)
STRONG = ("vendor", "slug", "title")
CONTEXT_WINDOW = 120


def norm(s: str) -> str:
    """Lowercase, strip accents, turn - _ / + into spaces, collapse spaces."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("%20", " ")
    s = re.sub(r"[-_/+]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _names(brand: dict):
    return [norm(brand["name"])] + [norm(a) for a in brand.get("aliases", [])]


def _pattern(name: str):
    return re.compile(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])")


def _sub_brand_names(brand: dict, all_brands: list):
    """'Lattafa' must not be counted from text that only says 'Lattafa Pride' (that is its own brand)."""
    me = norm(brand["name"])
    return [norm(b["name"]) for b in all_brands if norm(b["name"]) != me and norm(b["name"]).startswith(me + " ")]


def find(brands: list, text: str, kind: str, url: str):
    """Returns [{brand, kind, url, snippet}] for every brand seen in `text` with valid evidence."""
    t = norm(text)
    hits = []
    for b in brands:
        subs = _sub_brand_names(b, brands)
        cleaned = t
        for sb in subs:
            cleaned = _pattern(sb).sub(" ", cleaned)
        for name in _names(b):
            m = _pattern(name).search(cleaned)
            if not m:
                continue
            start, end = max(0, m.start() - CONTEXT_WINDOW), m.end() + CONTEXT_WINDOW
            snippet = cleaned[start:end]
            if kind == "context":
                if b.get("ambiguous") or not PERFUME_WORDS.search(snippet):
                    continue
            hits.append({"brand": b["name"], "kind": kind, "url": url, "snippet": snippet[:240]})
            break
    return hits


def summarise(brands: list, hits: list, country: str = "", in_stock=None):
    """-> {stocked: {brand: best_hit}, gap: [our brands not stocked, allowed for this country, in stock with us]}

    in_stock: {brand: n_in_stock} from shop.brands_in_stock(); a brand with 0 in stock is never offered.
    """
    rank = {k: i for i, k in enumerate(("vendor", "slug", "title", "context", "product_name"))}
    stocked = {}
    for h in hits:
        cur = stocked.get(h["brand"])
        if cur is None or rank[h["kind"]] < rank[cur["kind"]]:
            stocked[h["brand"]] = h
    gap = []
    for b in brands:
        only = b.get("only_countries")
        if only and country.upper() not in only:
            continue
        if in_stock is not None and in_stock.get(b["name"], 0) <= 0:
            continue
        if b["name"] not in stocked:
            gap.append(b["name"])
    return {"stocked": stocked, "gap": gap}
