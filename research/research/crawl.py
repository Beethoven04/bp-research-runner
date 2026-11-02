"""Polite crawler: finds published email addresses on a company's own website."""
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .emails import extract_emails

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
KEYWORDS = re.compile(
    r"team|people|about|om-oss|omoss|om_os|om-os|contact|kontakt|imprint|impressum|press|medarbetare|"
    r"medarbejdere|staff|ledelse|ledning|management|founders|who-we-are|vi-er|career|karriar|job|partners", re.I)
COMMON_PATHS = ["", "/about", "/about-us", "/team", "/our-team", "/people", "/contact", "/contact-us", "/kontakt",
                "/om-oss", "/om-os", "/en/about", "/en/team", "/en/contact", "/company", "/imprint", "/medarbetare",
                "/medarbejdere", "/careers"]


def _get(session, url, timeout=15):
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", "text/html"):
            return r.url, r.text
    except requests.RequestException:
        pass
    return None, ""


def crawl(domain: str, max_pages: int = 18, delay: float = 0.4):
    """Returns {"emails": {email: [urls]}, "texts": {url: text}, "pages": n}. Only same-site pages."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    base = f"https://{domain}"
    queue, seen, emails, texts = [], set(), {}, {}
    for p in COMMON_PATHS:
        queue.append(base + p)
    site_hosts = {domain, "www." + domain}
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        final, html = _get(s, url)
        if not html:
            continue
        host = urlparse(final).netloc.lower()
        if host not in site_hosts and not host.endswith("." + domain):
            site_hosts.add(host)  # redirect to another brand domain: allow it once (e.g. n5.team -> northfive.dk)
        for e in extract_emails(html):
            emails.setdefault(e, []).append(final)
        soup = BeautifulSoup(html, "lxml")
        texts[final] = soup.get_text(" ", strip=True)[:200000]
        for a in soup.find_all("a", href=True):
            href = urljoin(final, a["href"].split("#")[0])
            h = urlparse(href)
            if h.scheme.startswith("http") and h.netloc.lower() in site_hosts:
                if KEYWORDS.search(h.path) or KEYWORDS.search(a.get_text(" ", strip=True)[:40]):
                    if href.rstrip("/").lower() not in seen:
                        queue.insert(0, href)  # prioritise team/contact pages found on the site
        time.sleep(delay)
    return {"emails": emails, "texts": texts, "pages": len(seen)}


def mentions_person(texts: dict, email: str, first: str, last: str, window: int = 250) -> bool:
    """True if the person's name appears near the address on some page (helps attribute generic-looking locals)."""
    for t in texts.values():
        low = t.lower()
        i = low.find(email)
        while i != -1:
            chunk = low[max(0, i - window): i + window]
            if first and first.lower() in chunk and (not last or last.lower().split()[-1] in chunk):
                return True
            i = low.find(email, i + 1)
    return False
