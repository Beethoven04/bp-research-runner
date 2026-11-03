"""Fit check for one company with a website. Code only, every decision stored as evidence.

Decides: alive? sells perfume? real country? segment (when unknown)? platform (for the range reader)?
Collects published emails from contact / about / imprint pages.
Result: status 'fit' or 'not_fit' (+ reason), never a guess without a page behind it.
"""
import re
from collections import Counter
from urllib.parse import urljoin, urlparse

import phonenumbers
import requests
from bs4 import BeautifulSoup

from . import brands as B
from . import db
from .crawl import UA
from .domains import domain_of
from .emails import FREE_MAIL, extract_emails, free_mail_ok, is_role

PERFUME = re.compile(
    r"\b(parfym\w*|parfyme\w*|parfum\w*|perfume\w*|profum\w*|perfum\w*|hajuve\w*|eau de (parfum|toilette)|"
    r"fragrance\w*|dofter|duft\w*|geur\w*|oud|attar|edp|edt|cologne|perfumy|parfem\w*|parfume\w*)\b", re.I)
# Beauty products also make a shop a lead (decided 1 Nov 2026, Sohaib): skincare, cosmetics, makeup, haircare.
BEAUTY = re.compile(
    r"\b(skin ?care|hudvård\w*|hudpleje\w*|hudpleie\w*|ihonhoito\w*|huidverzorging\w*|hautpflege\w*|soins? (?:du )?visage|"
    r"cuidado facial|cura del viso|pielęgnacj\w*|cosmetic\w*|cosmétique\w*|cosmétiques|cosmética\w*|cosmetici|cosmetica|kosmeti\w*|"
    r"kozmetik\w*|make-?up|smink\w*|sminke\w*|meikki\w*|maquillage|maquillaje|trucco|hair ?care|hårvård\w*|hårpleje\w*|"
    r"hårpleie\w*|hiustenhoito\w*|haarverzorging\w*|haarpflege\w*|soins? capillaires?|cuidado del cabello|shampoo\w*|schampo\w*|"
    r"shampoing\w*|champú|szampon\w*|serum|seerumi|moisturi[sz]er\w*|lipstick\w*|läppstift|mascara|nail polish|nagellack|"
    r"beauty|skönhet\w*|skjønnhet\w*|skønhed\w*|kauneu\w*|schoonheid\w*|beauté|belleza|bellezza|uroda|schönheit\w*)\b", re.I)
CART = re.compile(r"(add to cart|lägg i varukorg|legg i handlekurv|lisää ostoskoriin|læg i kurv|in winkelwagen|"
                  r"ajouter au panier|añadir al carrito|aggiungi al carrello|do koszyka|adicionar ao carrinho|"
                  r"cart|varukorg|handlekurv|ostoskori|kurv|winkelwagen|panier|carrito|carrello|koszyk|carrinho|checkout|kassa)", re.I)
MARKETPLACES = {"amazon", "fyndiq", "plick", "cdon", "tradera", "bol", "allegro", "emag", "zalando", "ebay", "finn",
                "blocket", "etsy", "vinted", "trendyol", "kaufland", "otto", "cdiscount", "fnac", "aliexpress", "temu",
                "wish", "marktplaats", "leboncoin", "subito", "wallapop", "olx", "tori", "dba", "guloggratis"}
# Distributors (official importer / brand agent): still leads, but contacted last (decided 2 Nov 2026, Louai).
DISTRIBUTOR = re.compile(
    r"\b(official (?:distributor|importer)|exclusive (?:distributor|importer)|authori[sz]ed distributor|"
    r"(?:sole|general) (?:distributor|importer|agent)|distributeur (?:officiel|exclusif)|importateur|"
    r"distribuidor (?:oficial|exclusivo)|importador|distributore (?:ufficiale|esclusivo)|importatore|"
    r"(?:offizieller|exklusiver|autorisierter) (?:distributor|importeur|vertriebspartner)|generalimporteur|importeur|"
    r"(?:officiële|exclusieve) (?:distributeur|importeur)|generalagent|enedistributør|eneforhandler|"
    r"(?:officiell|exklusiv) (?:distributör|importör)|maahantuoja|oficjalny dystrybutor|wyłączny dystrybutor|"
    r"brand distribution|fragrance distribution|perfume distributor|parfum distributeur)\b", re.I)
CONTACT_PATHS = ["/contact", "/kontakt", "/contact-us", "/kontakta-oss", "/kontakt-oss", "/ota-yhteytta", "/yhteystiedot",
                 "/om-oss", "/about", "/about-us", "/impressum", "/imprint", "/contacto", "/contatti", "/contato",
                 "/pages/contact", "/pages/kontakt", "/pages/about-us", "/pages/om-oss", "/sidor/kontakt"]
BAD_LOCALS = re.compile(r"^(no-?reply|noreply|donotreply|hr|rx|jobs?|careers?|rekrytering|press|media|privacy|gdpr|dpo|"
                        r"kundklubb|newsletter|nyhetsbrev|abuse|postmaster|webmaster|security|invoice|faktura|"
                        r"billing|ekonomi|accounting|returns?|retur)([._+-]|$)", re.I)
CC_FIX = {"uk": "GB", "eu": "", "com": "", "net": "", "org": "", "shop": "", "store": "", "online": "", "co": "", "io": "",
          "info": "", "biz": "", "nu": "SE"}


LANG_COUNTRY = {"sv": "SE", "nb": "NO", "nn": "NO", "no": "NO", "fi": "FI", "da": "DK", "nl": "NL", "fr": "FR", "es": "ES",
                "it": "IT", "pl": "PL", "pt": "PT", "bs": "BA", "hr": "HR", "sr": "RS", "sq": "AL", "de": "DE", "cs": "CZ",
                "sk": "SK", "sl": "SI", "hu": "HU", "ro": "RO", "bg": "BG", "el": "GR", "lt": "LT", "lv": "LV", "et": "EE",
                "mk": "MK", "ga": "IE"}
COUNTRY_LANGS = {}
for _l, _c in LANG_COUNTRY.items():
    COUNTRY_LANGS.setdefault(_c, set()).add(_l)
COUNTRY_LANGS["BE"] = {"nl", "fr", "de"}
COUNTRY_LANGS["CH"] = {"de", "fr", "it"}
COUNTRY_LANGS["LU"] = {"fr", "de"}
COUNTRY_LANGS["XK"] = {"sq", "sr"}
COUNTRY_LANGS["ME"] = {"sr", "bs", "hr"}
EUROPE = {"SE", "NO", "FI", "DK", "NL", "BE", "FR", "ES", "IT", "PL", "PT", "BA", "AL", "IE", "AT", "CZ", "GR", "HR", "RS",
          "SI", "SK", "HU", "RO", "BG", "LT", "LV", "EE", "LU", "CH", "GB", "ME", "MK", "XK", "DE", "IS", "MT", "CY"}


def _lang(lang):
    return (lang or "").lower().replace("_", "-").split("-")[0]


PHONES = {"46": "SE", "47": "NO", "45": "DK", "358": "FI", "31": "NL", "32": "BE", "33": "FR", "34": "ES", "39": "IT",
          "48": "PL", "351": "PT", "387": "BA", "355": "AL", "353": "IE", "44": "GB", "43": "AT", "420": "CZ", "30": "GR",
          "385": "HR", "381": "RS", "386": "SI", "421": "SK", "36": "HU", "40": "RO", "359": "BG", "370": "LT",
          "371": "LV", "372": "EE", "352": "LU", "41": "CH", "382": "ME", "389": "MK", "383": "XK", "354": "IS",
          "356": "MT", "357": "CY", "49": "DE", "1": "US", "971": "AE", "966": "SA", "91": "IN", "61": "AU", "212": "MA"}
PHONE_RE = re.compile(r"(?:\+|\b00)(" + "|".join(sorted(PHONES, key=len, reverse=True)) + r")[\s.\-()]*\d[\d\s.\-()]{5,}")
CURRENCY = [(re.compile(r"\bSEK\b"), "SE"), (re.compile(r"\bNOK\b"), "NO"), (re.compile(r"\bDKK\b"), "DK"),
            (re.compile(r"\bPLN\b|\d\s?zł"), "PL"), (re.compile(r"£\s?\d|\bGBP\b"), "GB"), (re.compile(r"\bCHF\b"), "CH"),
            (re.compile(r"\bBAM\b|\d\s?KM\b"), "BA"), (re.compile(r"\bALL\b\s?\d|\d\s?Lek\b"), "AL"),
            (re.compile(r"\bCZK\b|\d\s?Kč"), "CZ"), (re.compile(r"\bHUF\b|\d\s?Ft\b"), "HU"), (re.compile(r"\bRON\b|\d\s?lei\b"), "RO"),
            (re.compile(r"\bUSD\b|\$\s?\d"), "US")]


def phone_country(code: str, raw: str) -> str:
    """Region of a real phone number (Google libphonenumber), or "" when the digits are not a valid number.
    Stops "+1 234567"-like noise (order ids, prices) from counting as the USA (3 Nov 2026)."""
    digits = re.sub(r"\D", "", raw)
    digits = digits[2:] if raw.lstrip().startswith("00") else digits
    try:
        num = phonenumbers.parse("+" + digits, None)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(num):
        # the regex can swallow trailing digits; try the shortest valid prefix length for this code
        for cut in range(len(digits) - 1, len(code) + 5, -1):
            try:
                num = phonenumbers.parse("+" + digits[:cut], None)
            except phonenumbers.NumberParseException:
                continue
            if phonenumbers.is_valid_number(num):
                break
        else:
            return ""
    return phonenumbers.region_code_for_number(num) or ""


def country_hints(text: str):
    """Countries named by phone numbers and currencies on the pages (Counter)."""
    from collections import Counter
    n = Counter()
    for m in PHONE_RE.finditer(text):
        cc = phone_country(m.group(1), m.group(0))
        if cc:
            n[cc] += 1
    cur = [cc for rx, cc in CURRENCY if rx.search(text)]
    if len(cur) <= 2:          # more = a currency selector, says nothing about the shop
        n.update(cur)
    return n


# A shop that names a non-European home market in its title/description (Oud n Musk "Chicago, USA").
NON_EU_WORDS = re.compile(r"\b(USA|U\.S\.|United States|Canada|Australia)(?![a-z])", re.I)
NON_EU_WORDS_CC = {"usa": "US", "us": "US", "united states": "US", "canada": "CA", "australia": "AU"}


def pick_country(hints, current: str) -> str:
    """Country from phone/currency hints, or "" when unclear. A non-European hint stronger than the best
    European one wins (the shop is then rejected as outside Europe). A tie between European countries is unclear,
    unless the current country is one of them. Fixes 2 Nov 2026: {'AL': 1, 'US': 1} picked Albania."""
    if not hints:
        return ""
    top = max(hints.values())
    best = [cc for cc, n in hints.items() if n == top]
    eu_top = max((n for cc, n in hints.items() if cc in EUROPE), default=0)
    non_eu = [cc for cc, n in hints.items() if cc not in EUROPE and n > eu_top]   # a tie is too weak ($ price, +1 number)
    if non_eu:
        return max(non_eu, key=lambda cc: hints[cc])
    if current in best:
        return current
    return best[0] if len(best) == 1 else ""


AMBIGUOUS_IN_DOMAIN = {"Emper", "Nusuk"}   # common word stems (emperor..., nusuk travel)


def brand_in_domain(base: str, our_brands, arabic):
    """A perfume brand name inside the domain (armaf.uk, themaison-alhambra.com): maybe the brand owner, not a buyer."""
    b = re.sub(r"[^a-z0-9]", "", base.lower())
    for x in our_brands:          # ambiguous names (French Avenue, Rave) only when the domain IS the name
        if x.get("ambiguous") and re.sub(r"[^a-z0-9]", "", x["name"].lower()) == b:
            return x["name"]
    names = [x["name"] for x in our_brands if not x.get("ambiguous")] + [a for a in arabic if a not in AMBIGUOUS_IN_DOMAIN]
    for name in names:
        n = re.sub(r"[^a-z0-9]", "", name.lower())
        if len(n) >= 5 and n in b:
            return name
    return ""


def lang_country(lang: str, current: str) -> str:
    """Page language -> country, only when it contradicts the current country. English never decides."""
    l = (lang or "").lower().replace("_", "-").split("-")[0]
    if not l or l == "en" or l in COUNTRY_LANGS.get(current, set()):
        return current
    return LANG_COUNTRY.get(l, current)


def tld_country(domain: str) -> str:
    tld = domain.rsplit(".", 1)[-1].lower()
    if tld in CC_FIX:
        return CC_FIX[tld]
    return tld.upper() if len(tld) == 2 else ""


def fetch(session, url, timeout=20):
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ("html" in ct or not ct):
            return r.url, r.text
    except requests.RequestException:
        pass
    return None, ""


def platform(html: str) -> str:
    h = html[:400000]
    if "cdn.shopify.com" in h or "Shopify.theme" in h:
        return "shopify"
    if "woocommerce" in h.lower() or "wc-block" in h:
        return "woocommerce"
    if re.search(r"Magento|mage-init|/static/version\d+/", h):
        return "magento"
    if "prestashop" in h.lower():
        return "prestashop"
    return "other"


def site_name(soup, domain):
    for sel in (("meta", {"property": "og:site_name"}), ("meta", {"name": "application-name"})):
        m = soup.find(*sel)
        if m and m.get("content", "").strip():
            return m["content"].strip()[:120]
    t = (soup.title.string if soup.title and soup.title.string else "") or ""
    parts = [p.strip() for p in re.split(r"[|\-–—•:]", t) if p.strip()]
    for p in parts[::-1]:          # site name is usually the last part of the title
        if domain.split(".")[0].lower() in p.lower().replace(" ", ""):
            return p[:120]
    return domain


# Price-comparison sites list other shops' prices; they do not buy stock. Matched on the page title/description.
COMPARISON_RE = re.compile(r"(?:sammenlign|jämför|vergelijk)\w*\s+(?:\w+\s+)?\w*(?:pris|prijz)|prisjämförelse|compare (?:\w+ )?prices|price comparison|"
                           r"preisvergleich|comparer les prix|comparateur de prix|vergelijk (?:\w+ )?prijzen|vertaa hintoja|"
                           r"porównaj ceny|porównywarka|confronta (?:i )?prezzi|compara precios|comparador de precios", re.I)


def is_comparison(head):
    return bool(COMPARISON_RE.search(head or ""))


def check(company: dict, our_brands: list, arabic: list):
    """-> dict with decision + evidence list + contacts. No DB writes here (pure-ish, easy to test)."""
    dom = company["domain"]
    s = requests.Session()
    s.headers["User-Agent"] = UA
    final, html = fetch(s, f"https://{dom}/")
    if not html:
        final, html = fetch(s, f"http://www.{dom}/")
    out = {"evidence": [], "contacts": {}, "name": None, "country": company["country"], "segment": company["segment"]}
    if not html:
        out.update(status="not_fit", reason="site_down")
        return out
    real = domain_of(final) or dom
    if real != dom:
        out["evidence"].append(("redirects_to", real, final, ""))
    soup = BeautifulSoup(html, "lxml")
    home = soup.get_text(" ", strip=True)
    head = " ".join([soup.title.string if soup.title and soup.title.string else ""] +
                    [m.get("content", "") for m in soup.find_all("meta", attrs={"name": "description"})])
    extra = ""
    src = company.get("first_source_url") or ""
    if company.get("first_source") == "search" and domain_of(src) == dom and src.rstrip("/") != final.rstrip("/"):
        f3, h3 = fetch(s, src)
        if h3:
            extra = BeautifulSoup(h3, "lxml").get_text(" ", strip=True)
            out["evidence"].append(("search_page_read", "yes", f3, ""))
    out["name"] = site_name(soup, dom)
    out["platform"] = platform(html)
    lang = (soup.html.get("lang") if soup.html else "") or ""
    # country: ccTLD wins; generic TLD: page language, then phone/currency (checked after contact pages are read)
    cc = tld_country(real)
    lang_decides = False
    if cc and cc != company["country"]:
        out["country"] = cc
        out["evidence"].append(("country_from_tld", cc, final, f"tld .{real.rsplit('.',1)[-1]}; lang={lang}"))
    elif not cc:
        lc = lang_country(lang, company["country"])
        lang_decides = _lang(lang) not in ("", "en")
        if lc != company["country"]:
            out["country"] = lc
            out["evidence"].append(("country_from_lang", lc, final, f"html lang={lang}; generic tld"))
    # sells perfume? The SITE's own home page decides. A deep search-hit page can be a directory, a discount portal or
    # one product of a celebrity line, so it only adds brand evidence. JS-only home pages (thin text) may use it.
    thin = len(home) < 1500
    site_text = home + " " + head
    n_perf = len(PERFUME.findall(site_text))
    n_beauty = len(BEAUTY.findall(site_text))
    if thin:
        site_text += " " + extra + " " + (company.get("search_snippet") or "")
    hits = PERFUME.findall(site_text + " " + extra)
    brand_hits = B.find(our_brands, site_text + " " + extra, "context", final)
    arabic_hits = B.find([{"name": a} for a in arabic], site_text + " " + extra, "context", final)
    out["brand_hits"] = brand_hits + [h for h in arabic_hits if h["brand"] not in {x["brand"] for x in brand_hits}]
    is_shop = bool(CART.search(home + " " + extra)) or out["platform"] in ("shopify", "woocommerce", "magento", "prestashop")
    base = real.split(".")[0]
    if base in MARKETPLACES:
        out["segment"] = "marketplace"
    elif not out["segment"]:
        if out["brand_hits"] and is_shop:
            out["segment"] = "arabic_perfume_seller"
        elif is_shop:
            out["segment"] = "online_store"
    perf_home = n_perf >= 5 or (thin and len(hits) >= 5)
    out["sells_perfume"] = bool(perf_home or out["brand_hits"])
    if DISTRIBUTOR.search(head + " " + home[:4000]) and not company.get("is_chain"):
        out["segment"] = "distributor"
        out["evidence"].append(("distributor_words", DISTRIBUTOR.search(head + " " + home[:4000]).group(0), final, ""))
    beauty_hits = BEAUTY.findall(site_text + " " + extra) if thin else []
    beauty_home = n_perf + n_beauty >= 5 or (thin and len(hits) + len(beauty_hits) >= 5)
    seg0 = company.get("segment") or ""
    if company.get("first_source") == "osm" and seg0 in ("perfumery", "beauty_store", "drugstore"):
        beauty_home = True          # the OSM shop tag (perfumery / cosmetics / chemist) already says beauty products
    elif seg0 == "pharmacy" and n_perf + n_beauty >= 3:
        beauty_home = True          # pharmacy with a visible cosmetics / skincare section
    perfume_ok = perf_home or beauty_home or (bool(out["brand_hits"]) and is_shop) or out["segment"] == "marketplace"
    if out["segment"] == "marketplace":
        out["range_unknown"] = True
    if perfume_ok and not out["segment"]:
        out["segment"] = "online_store" if is_shop else "small_retailer"
    if n_beauty:
        out["evidence"].append(("beauty_words", n_beauty, final, ", ".join(sorted({h for h in BEAUTY.findall(site_text)})[:12])))
    out["evidence"].append(("perfume_words", n_perf, final, ", ".join(sorted({h[0] if isinstance(h, tuple) else h for h in hits})[:12])))
    if company["segment"] == "beauty_salon" and not perfume_ok:
        # salons: perfume words rarely on the site; keep as fit with range unknown (decided 31 Oct)
        perfume_ok = True
        out["range_unknown"] = True
    brand_site = brand_in_domain(base, our_brands, arabic)
    # contacts: home page + contact/about pages
    pages = {final: html}
    links = [urljoin(final, a["href"]) for a in soup.find_all("a", href=True)
             if re.search(r"kontakt|contact|about|om-oss|impressum|imprint|yhteys|contatt|contato|contacto", a["href"], re.I)]
    for u in list(dict.fromkeys(links))[:4] + [f"https://{real}{p}" for p in CONTACT_PATHS[:8]]:
        if len(pages) >= 7:
            break
        if urlparse(u).netloc.lower().removeprefix("www.") not in (real, "www." + real, dom):
            continue
        f2, h2 = fetch(s, u, timeout=12)
        if h2 and f2 not in pages:
            pages[f2] = h2
    for url, h in pages.items():
        for e in extract_emails(h):
            ed = e.split("@")[1]
            if BAD_LOCALS.search(e.split("@")[0]):
                continue
            if ed.endswith(real) or ed.endswith(dom) or (ed.split(".")[0] == base):
                out["contacts"].setdefault(e, url)
    if not out["contacts"]:
        # second pass: legal pages (terms, privacy, imprint, Shopify /policies/...), added 3 Nov 2026
        from . import contacts_more
        mine, other, _ = contacts_more.find(dom, real, html, final, s)
        for e, u in mine.items():
            if not BAD_LOCALS.search(e.split("@")[0]):
                out["contacts"].setdefault(e, u)
        if not out["contacts"]:
            # free-mail address the shop publishes as its contact (gmail...), only when no own-domain one exists
            for e, u in other.items():
                if free_mail_ok(e) and not BAD_LOCALS.search(e.split("@")[0]):
                    out["contacts"].setdefault(e, u)
        for e, u in list(other.items())[:5]:
            if e not in out["contacts"]:
                out["evidence"].append(("other_domain_email", e, u, "not on the shop's own domain; not used"))
    out["real_domain"] = real
    country_unclear = False
    if not cc and not lang_decides:
        # generic TLD in English (or no language): phone prefix / currency must confirm the country
        hints = country_hints(" ".join(BeautifulSoup(h, "lxml").get_text(" ", strip=True) for h in pages.values()))
        hints.update(NON_EU_WORDS_CC[m.group(1).lower().replace(".", "")] for m in NON_EU_WORDS.finditer(head))
        pick = pick_country(hints, out["country"])
        if pick:
            out["country"] = pick
            out["evidence"].append(("country_from_phone", pick, final, f"hints={dict(hints)}"))
        else:
            country_unclear = True
    if out["country"] not in EUROPE:
        out["status"], out["reason"] = "not_fit", "outside_europe"
        return out
    if is_comparison(head):
        out["status"], out["reason"] = "not_fit", "price_comparison_site"
    elif perfume_ok and brand_site:
        out["status"], out["reason"] = "review", f"brand_in_domain:{brand_site}"   # brand owner or official store?
    elif perfume_ok and country_unclear:
        out["status"], out["reason"] = "review", "country_unclear"
    elif perfume_ok:
        out["status"], out["reason"] = "fit", ""
    elif company.get("is_chain") or (company.get("segment") or "").endswith("_chain"):
        out["status"], out["reason"] = "review", "chain_no_perfume_on_home"   # range check decides
    else:
        out["status"], out["reason"] = "not_fit", "no_beauty_signal"
    return out


def save(c, company: dict, res: dict, our_names=(), rules=None):
    """Write the fit result. Handles redirect duplicates and country changes (email rule re-applied)."""
    cid = company["id"]
    res["_our_names"] = set(our_names)
    real = res.get("real_domain")
    if real and real != company["domain"]:
        other = c.execute("select id from companies where tenant_id=%s and domain=%s and id<>%s",
                          (company["tenant_id"], real, cid)).fetchone()
        if other:
            db.evidence(c, cid, "duplicate_of", other["id"], f"https://{company['domain']}/", f"redirects to {real}")
            c.execute("update companies set status='duplicate', parent_id=%s, updated_at=now() where id=%s", (other["id"], cid))
            return
    rules = rules or {}
    rule = (rules.get("email_rules") or {}).get(res["country"], rules.get("email_rule_default", "check"))
    if rule == "skip":
        res["status"], res["reason"] = "excluded", "country_skip"
    for fact, value, url, snip in res["evidence"]:
        db.evidence(c, cid, fact, value, url, snip)
    if res.get("reason"):
        db.evidence(c, cid, "not_fit_reason", res["reason"], f"https://{company['domain']}/")
    for h in res.get("brand_hits", []):
        c.execute("insert into company_brands(company_id,brand,ours,kind,url,snippet) values (%s,%s,%s,%s,%s,%s) "
                  "on conflict (company_id,brand) do nothing",
                  (cid, h["brand"], h["brand"] in res.get("_our_names", ()), h["kind"], h["url"], h["snippet"]))
    for e, url in res["contacts"].items():
        c.execute("insert into contacts(company_id,email,kind,source_url) values (%s,%s,%s,%s) on conflict do nothing",
                  (cid, e, "role" if is_role(e) or e.split("@")[1] in FREE_MAIL else "named", url))
    sells_arabic = True if res.get("brand_hits") else None
    name = res.get("name") if company.get("first_source") == "search" else None
    c.execute("update companies set email_rule=%s where id=%s", (rule, cid))
    c.execute("update companies set status=%s, segment=case when %s='distributor' then 'distributor' else coalesce(segment,%s) end, country=%s, name=coalesce(%s,name), "
              "sells_perfume=%s, sells_arabic=coalesce(%s,sells_arabic), range_status=case when %s then 'unknown' else range_status end, "
              "updated_at=now() where id=%s",
              (res["status"], res.get("segment"), res.get("segment"), res["country"], name, res.get("sells_perfume"), sells_arabic,
               bool(res.get("range_unknown")), cid))
