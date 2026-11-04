"""Second-pass email finder for shops where the home + contact pages showed no address.

EU web shops must publish an email address (E-Commerce Directive art. 5), usually on a legal page: terms, privacy,
imprint, mentions légales, aviso legal, Shopify /policies/... This module reads those pages. It is called by
`fit.check` when the first pass found nothing, and by the backfill below for leads checked before 3 Nov 2026.

Only addresses on the shop's own domain are kept (same rule as `fit.check`).
Run (backfill): python -m research.contacts_more [--limit N] [--dry]
"""
import argparse
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .crawl import UA
from .emails import extract_emails, is_role

LEGAL_LINK = re.compile(
    r"terms|conditions|privacy|policy|policies|impressum|imprint|legal|mentions|aviso|villkor|vilk[aå]r|betingelser|"
    r"ehdot|tietosuoja|voorwaarden|colofon|agb|datenschutz|cgv|condiciones|termini|condizioni|regulamin|polityka|"
    r"privacidade|termos|obchodn|podmienky|uvjeti|feltetel|kontakt|contact|about|om-oss|faq|shipping|leverans|"
    r"levering|frakt|returns?|retur", re.I)
LEGAL_PATHS = [
    "/policies/contact-information", "/policies/legal-notice", "/policies/terms-of-service", "/policies/privacy-policy",
    "/policies/refund-policy", "/policies/shipping-policy",
    "/impressum", "/imprint", "/legal-notice", "/mentions-legales", "/aviso-legal", "/note-legali", "/colofon",
    "/terms", "/terms-and-conditions", "/privacy-policy", "/kopvillkor", "/kjopsbetingelser", "/handelsbetingelser",
    "/toimitusehdot", "/algemene-voorwaarden", "/agb", "/datenschutz", "/cgv", "/condiciones-generales",
    "/termini-e-condizioni", "/regulamin", "/polityka-prywatnosci",
]
CF_HREF = re.compile(r"/cdn-cgi/l/email-protection#([0-9a-f]+)", re.I)
MAX_PAGES = 12


def _fetch(s, url):
    try:
        r = s.get(url, timeout=12, allow_redirects=True)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
            return r.url, r.text
    except requests.RequestException:
        pass
    return None, ""


def _emails(html):
    from .emails import decode_cfemail
    found = extract_emails(html)
    for m in CF_HREF.finditer(html):
        e = decode_cfemail(m.group(1)).lower()
        if "@" in e:
            found.add(e)
    return found


def own(email, real, dom):
    ed = email.split("@")[1]
    base = real.split(".")[0]
    return ed.endswith(real) or ed.endswith(dom) or ed.split(".")[0] == base


GENERIC = {
    "beauty", "kosmetik", "cosmetic", "cosmetics", "cosmetique", "parfum", "parfums", "perfume", "perfumes",
    "parfumerie", "perfumery", "profumeria", "perfumeria", "salon", "studio", "shop", "store", "online", "apotheke",
    "pharmacy", "pharmacie", "farmacia", "apteka", "nails", "nail", "hair", "haar", "friseur", "coiffure", "spa",
    "solutions", "group", "gmbh", "haus", "house", "center", "centre", "centrum", "welt", "world", "care", "medical",
    "health", "natur", "nature", "style", "mode", "fashion", "home", "life", "design", "service", "services", "europe",
    "germany", "deutschland", "france", "italia", "espana", "polska", "official", "boutique", "mail", "info", "team",
    "kosmetikstudio", "beautysalon", "derma", "skin", "body", "wellness", "drogerie", "markt", "versand", "handel",
}


def _tokens(label: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+|\d+", label.lower()) if len(t) >= 4 and t not in GENERIC}


def same_business(email: str, real: str) -> bool:
    """Address on another domain of the same business (yves-meerbusch.de -> info@yves-kosmetik.de,
    staggenborg-apotheke.de -> info@staggenborg.com): the two domain names share a distinctive word (>= 4 letters,
    not a generic trade word). Agencies/hosts (finance-solutions.net for beauty-solutions.net) do not match."""
    ed = email.split("@")[1].lower()
    return bool(_tokens(ed.split(".")[0]) & _tokens(real.split(".")[0]))


def pick(mine: dict, other: dict, real: str) -> tuple:
    """Contacts to keep, in order of trust (4 Nov 2026): own domain > same business on another domain > free-mail
    published by the shop. -> (contacts {email: url}, how) with how in own | same_business | free_mail | none."""
    from .emails import free_mail_ok
    from .fit import BAD_LOCALS
    ok = lambda e: not BAD_LOCALS.search(e.split("@")[0])
    c = {e: u for e, u in mine.items() if ok(e)}
    if c:
        return c, "own"
    c = {e: u for e, u in other.items() if ok(e) and same_business(e, real)}
    if c:
        return c, "same_business"
    c = {e: u for e, u in other.items() if ok(e) and free_mail_ok(e)}
    return (c, "free_mail") if c else ({}, "none")


def find(dom, real=None, home_html=None, home_url=None, session=None):
    """-> ({email: source_url} on the shop's own domain, {email: url} elsewhere, pages read)."""
    real = real or dom
    s = session or requests.Session()
    s.headers["User-Agent"] = UA
    if not home_html:
        home_url, home_html = _fetch(s, f"https://{real}/")
    if not home_html:
        return {}, {}, 0
    soup = BeautifulSoup(home_html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        u = urljoin(home_url, a["href"])
        if urlparse(u).netloc.lower().removeprefix("www.") not in (real, dom):
            continue
        if LEGAL_LINK.search(a["href"]) or LEGAL_LINK.search(a.get_text(" ", strip=True)[:60]):
            links.append(u.split("#")[0])
    urls = list(dict.fromkeys(links))[:8] + [f"https://{real}{p}" for p in LEGAL_PATHS]
    pages = {home_url: home_html}
    for u in urls:
        if len(pages) >= MAX_PAGES:
            break
        if u in pages:
            continue
        f, h = _fetch(s, u)
        if h and f not in pages:
            pages[f] = h
    mine, other = {}, {}
    for url, h in pages.items():
        for e in _emails(h):
            (mine if own(e, real, dom) else other).setdefault(e, url)
    return mine, other, len(pages)


def main(limit, dry):
    from . import db, worker
    with db.conn() as c:
        rows = c.execute("select id, domain from companies co where status in ('fit','review') and domain is not null "
                         "and not exists (select 1 from contacts k where k.company_id=co.id) order by priority desc limit %s",
                         (limit,)).fetchall()
    print(f"{len(rows)} leads without an email", flush=True)

    def one(r):
        try:
            return r, find(r["domain"])
        except Exception:                # one bad site must not stop the pass
            return r, ({}, {}, 0)

    got = n_other = 0
    stats = Counter()
    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(one, rows))
    with db.conn() as c:
        for r, (mine, other, n) in results:
            mine, how = pick(mine, other, r["domain"])
            if how != "none":
                stats[how] += 1
            if other:
                n_other += 1
                if not dry:
                    for e, u in list(other.items())[:5]:
                        db.evidence(c, r["id"], "other_domain_email", e, u, "not on the shop's own domain; not used")
            if not mine:
                continue
            got += 1
            print(f"{r['domain']}: {len(mine)} own-domain address(es), {n} pages", flush=True)
            if dry:
                continue
            for e, u in mine.items():
                kind = "role" if is_role(e) or how == "free_mail" else "named"
                c.execute("insert into contacts(company_id,email,kind,source_url) values (%s,%s,%s,%s) on conflict do nothing",
                          (r["id"], e, kind, u))
            worker.choose_primary(c, r["id"])
            worker.score(c, r["id"])
        if not dry:
            c.commit()
    print(f"by source: {dict(stats)}", flush=True)
    print(f"done: {got} of {len(rows)} now have an email; {n_other} show only/also other-domain "
          f"addresses (gmail, parent company...){' (dry run, nothing saved)' if dry else ''}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10000)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    main(a.limit, a.dry)
