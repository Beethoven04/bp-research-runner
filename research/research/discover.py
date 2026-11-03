"""Discovery: find candidate businesses. Two sources:
  osm    - OpenStreetMap places (perfumeries, cosmetics, salons, chemists, pharmacies, wholesalers).
           Chain branches (same `brand` tag) collapse into ONE company: the chain's head office.
  search - web search (self-hosted SearXNG) for shops selling our / Arabic brands, per country language.
Every company is stored with the URL it was found on.
"""
import time
from collections import Counter, defaultdict

import requests

from . import config, db
from .domains import domain_of, is_not_lead

UA = {"User-Agent": "b2bperfume-research/0.1 (contact@b2bperfume.com)"}
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
OSM_KINDS = {  # tag -> segment for an independent place / for a chain
    ("shop", "perfumery"): ("perfumery", "retail_chain"),
    ("shop", "cosmetics"): ("beauty_store", "retail_chain"),
    ("shop", "beauty"): ("beauty_salon", "retail_chain"),
    ("shop", "chemist"): ("drugstore", "drugstore_chain"),
    ("amenity", "pharmacy"): ("pharmacy", "pharmacy_chain"),
    ("shop", "wholesale"): ("wholesaler", "wholesaler"),
}
CHAIN_MIN = 3   # a brand tag on 3+ places = chain


def email_rule(c, country):
    rules = db.cfg(c, "countries") or {}
    return (rules.get("email_rules") or {}).get(country, rules.get("email_rule_default", "check"))


def _overpass(query):
    last = None
    for url in OVERPASS:
        for attempt in range(3):
            try:
                r = requests.post(url, data={"data": query}, headers=UA, timeout=300)
                if r.status_code == 200:
                    return r.json()["elements"]
                last = f"{url} http {r.status_code}"
            except requests.RequestException as e:
                last = f"{url} {e}"
            time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"overpass failed: {last}")


def osm(country: str):
    """Import all OSM places of the target kinds in one country. Returns counts."""
    parts = "".join(f'nwr["{k}"="{v}"](area.a);' for k, v in OSM_KINDS)
    els = _overpass(f'[out:json][timeout:280];area["ISO3166-1"="{country}"][admin_level=2]->.a;({parts});out tags center;')
    by_brand = defaultdict(list)
    singles = []
    for e in els:
        t = e.get("tags", {})
        kind = next(((k, v) for k, v in OSM_KINDS if t.get(k) == v), None)
        if not kind:
            continue
        b = (t.get("brand") or "").strip()
        (by_brand[b.lower()] if b else singles).append((e, t, kind))
    # brands with few places are not chains: treat them as single places
    for b in [b for b, items in by_brand.items() if len(items) < CHAIN_MIN]:
        singles.extend(by_brand.pop(b))
    n = Counter()
    with db.conn() as c:
        rule = email_rule(c, country)
        # one read of what we already have: re-runs and big countries skip known places without a DB round trip
        known_domains = {r["domain"] for r in c.execute(
            "select domain from companies where tenant_id=%s and domain is not null", (config.TENANT,))}
        known_places = {(r["n"], r["city"]) for r in c.execute(
            "select lower(name) n, coalesce(city,'') city from companies where tenant_id=%s and domain is null and country=%s",
            (config.TENANT, country))}
        for b, items in by_brand.items():
            tags = [t for _, t, _ in items]
            name = Counter(t.get("brand") for t in tags).most_common(1)[0][0]
            sites = Counter(domain_of(t.get("brand:website") or t.get("website") or t.get("contact:website") or "") for t in tags)
            sites.pop("", None)
            domain = sites.most_common(1)[0][0] if sites else None
            seg = OSM_KINDS[items[0][2]][1]
            url = f"https://www.openstreetmap.org/{items[0][0]['type']}/{items[0][0]['id']}"
            cid, new = db.upsert_company(c, name=name, domain=domain, country=country, segment=seg, source="osm",
                                         source_url=url, is_chain=True, store_count=len(items), email_rule=rule)
            if new:
                db.evidence(c, cid, "store_count_osm", len(items), url, f"brand={name}; {len(items)} places in OSM")
            n["chains_new" if new else "chains_seen"] += 1
        for e, t, kind in singles:
            name = t.get("name") or t.get("brand")
            if not name:
                n["no_name"] += 1
                continue
            site = t.get("website") or t.get("contact:website") or ""
            domain = domain_of(site) or None
            if domain and is_not_lead(domain):
                domain = None
            url = f"https://www.openstreetmap.org/{e['type']}/{e['id']}"
            city = t.get("addr:city") or t.get("addr:place")
            mail = t.get("email") or t.get("contact:email")
            if not domain and not (mail and "@" in mail):
                n["no_site_no_email"] += 1      # nothing to check or contact: not stored
                continue
            if (domain and domain in known_domains) or (not domain and (name.lower(), city or "") in known_places):
                n["places_seen"] += 1
                continue
            cid, new = db.upsert_company(c, name=name, domain=domain, country=country, segment=OSM_KINDS[kind][0],
                                         source="osm", source_url=url, city=city, is_chain=False, email_rule=rule)
            if new:
                db.evidence(c, cid, "osm_tags", f"{kind[0]}={kind[1]}", url,
                            "; ".join(f"{k}={v}" for k, v in t.items() if k in ("name", "website", "email", "contact:email", "addr:city")))
                if mail and "@" in mail:
                    c.execute("insert into contacts(company_id,email,kind,source_url) values (%s,%s,'role',%s) "
                              "on conflict do nothing", (cid, mail.strip().lower(), url))
            n["places_new" if new else "places_seen"] += 1
            n["with_website"] += bool(domain)
            if domain:
                known_domains.add(domain)
            else:
                known_places.add((name.lower(), city or ""))
            if (n["places_new"] + n["places_seen"]) % 300 == 0:
                c.commit()      # small commits: long transactions block the parallel site checks
        c.execute("insert into research_runs(tenant_id,stage,country,source,finished_at,counts) values (%s,'discover',%s,'osm',now(),%s)",
                  (config.TENANT, country, __import__("json").dumps(dict(n))))
    return dict(n)


def search_queries(c, country: str):
    s = db.cfg(c, "search") or {}
    conf = {**s.get("default", {}), **s.get(country, {})}
    cname = conf.get("country_name", country)
    qs = [p.format(country_name=cname) for p in conf.get("phrases", [])]
    for b in db.cfg(c, "search_brands") or []:
        qs += [t.format(brand=b, country_name=cname) for t in conf.get("brand_templates", [])]
    for city in conf.get("cities", []):        # city x shop type: finds shops that rank only locally
        qs += [t.format(city=city, country_name=cname) for t in conf.get("city_templates", [])]
    return conf.get("lang", "en"), qs


def search(country: str, query: str, lang: str, pages: int = 2):
    """One query through SearXNG. New domains become companies (segment decided later by the fit check)."""
    n = Counter()
    seen = set()
    for page in range(1, pages + 1):
        r = requests.get(config.SEARXNG_URL.rstrip("/") + "/search",
                         params={"q": query, "format": "json", "language": lang, "pageno": page}, timeout=60)
        r.raise_for_status()
        res = r.json().get("results", [])
        if not res:
            break
        with db.conn() as c, c.transaction():
            rule = email_rule(c, country)
            for x in res:
                d = domain_of(x.get("url", ""))
                if is_not_lead(d) or d in seen:
                    n["skipped"] += 1
                    continue
                seen.add(d)
                cid, new = db.upsert_company(c, name=(x.get("title") or d)[:120], domain=d, country=country,
                                             segment=None, source="search", source_url=x["url"], email_rule=rule)
                if new:
                    db.evidence(c, cid, "search_hit", query, x["url"], f"{x.get('title','')} | {x.get('content','')}")
                n["new" if new else "seen"] += 1
        time.sleep(3)
    return dict(n)
