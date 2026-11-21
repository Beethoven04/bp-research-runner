"""Sales channel of a company: physical store, online shop, both, or unknown (4 Nov 2026, Louai: store type is the
most important part of the lead file). Code only, every result stored as evidence fact 'sales_channel'.

store  = OSM shop tag (the shop exists on the map), or a page shows opening hours (word + a time range) that are
         not phone/support hours, or schema.org opening hours / a physical-store phrase ("Ladengeschäft", "visit our
         store"). A street address alone is NOT enough: every German Impressum has one.
online = a shop platform (Shopify, WooCommerce, Shopware, ...) or a real cart: add-to-cart words or a cart link.
Neither -> 'unknown'. Never a guess.

Backfill: python -m research.channel   (re-reads home + contact pages of every exported lead, writes evidence)
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

STORE, ONLINE, BOTH, UNKNOWN = "store", "online", "store+online", "unknown"
LABEL = {STORE: "Physical store", ONLINE: "Online shop", BOTH: "Store + online shop", UNKNOWN: "Unknown"}

PLATFORMS = [  # (name, regex on raw html)
    ("shopify", r"cdn\.shopify\.com|Shopify\.theme"),
    ("woocommerce", r"woocommerce|wc-block|add_to_cart_button"),
    ("magento", r"Magento|mage-init|/static/version\d+/"),
    ("prestashop", r"prestashop"),
    ("shopware", r"shopware|/bundles/storefront/|sw-csrf"),
    ("jtl", r"jtl-shop|JTL-Shop|jtlshop"),
    ("gambio", r"gambio|GXModules"),
    ("oxid", r"OXID eShop|oxid-esales|/out/flow/|oxidEsales"),
    ("plentymarkets", r"plentymarkets|plenty-shop|plentyShop"),
    ("wix_stores", r"wixstores|wix-ecommerce|ecom\.wixapps"),
    ("ecwid", r"app\.ecwid\.com|ecwid_"),
    ("squarespace_commerce", r"squarespace-commerce|commerce\.squarespace|sqs-add-to-cart"),
    ("bigcommerce", r"cdn\d*\.bigcommerce\.com"),
    ("lightspeed", r"webshopapp\.com|lightspeed(?:hq)?"),
    ("opencart", r"route=checkout/cart|catalog/view/theme"),
    ("shoptet", r"shoptet"),
    ("shoper", r"shoper\.pl|shoper-"),
    ("idosell", r"idosell|iai-shop"),
    ("mystore", r"mystore\.no|mystore4"),
    ("abicart", r"abicart|textalk"),
    ("wikinggruppen", r"wikinggruppen|wgr\.se"),
    ("dandomain", r"dandomain|smartweb"),
]
PLATFORM_RE = [(n, re.compile(p, re.I)) for n, p in PLATFORMS]

# phrases only: bare "cart"/"kassa"/"kurv" also match "Cartier", Swedish "kassa" texts, ...
ADD_TO_CART = re.compile(
    r"add to (?:cart|bag|basket)|in den warenkorb|zum warenkorb|in winkelwagen|ajouter au panier|añadir al carrito|"
    r"aggiungi al carrello|dodaj do koszyka|do koszyka|adicionar ao carrinho|lägg i (?:varukorg|kundvagn)|"
    r"legg i (?:handlekurv|handlevogn)|læg i kurv(?:en)?|lisää ostoskoriin|vložit do košíku|do košíku|"
    r"kosárba|adaugă în coș|adauga in cos|dodaj u košaricu|u košaricu|dodaj v košarico|v košarico|"
    r"προσθήκη στο καλάθι|в корзину|sepete ekle|pridať do košíka|lisa ostukorvi|į krepšelį|pievienot grozam|"
    r"in den einkaufswagen|ajouter à mon panier|pridėti į krepšelį|добави в (?:количката|кошницата)|"
    r"στο καλάθι|dodaj do košarice|in den korb", re.I)
# shop widgets in the markup: class/id names of a cart button or mini cart
CART_MARKUP = re.compile(r"(?:class|id)=[\"'][^\"']*(?:add-?to-?cart|addtocart|add_to_cart|cart-?button|minicart|"
                         r"mini-cart|cart[-_]count|cart[-_]total|basket[-_]count|header-cart|shopping-cart)", re.I)
CART_LINK = re.compile(
    r"href=[\"'][^\"']*/(?:cart|basket|bag|warenkorb|checkout|kasse|panier|carrito|carrello|koszyk|winkelwagen|"
    r"varukorg|kundvagn|handlekurv|handlevogn|ostoskori|kurv|kosik|kosar|kosarica|kosarica|cesta|carrinho|"
    r"shopping-?cart|mein-warenkorb|basket\.php|cart\.php)(?:[/?.#\"']|\.html)", re.I)

HOURS_WORD = re.compile(
    r"öffnungszeiten|opening (?:hours|times)|store hours|shop hours|business hours|horaires? d'ouverture|"
    r"heures d'ouverture|horaires|openingstijden|openingsuren|orari(?: di apertura)?|horario(?:s)?(?: de apertura)?|"
    r"horário(?:s)?(?: de funcionamento)?|öppettider|åpningstider|åbningstider|aukioloajat|avoinna|"
    r"godziny otwarcia|otevírací doba|nyitvatartás|nyitva tartás|program de lucru|orar|radno vrijeme|"
    r"radno vreme|delovni čas|ωράριο λειτουργίας|ladenöffnungszeiten|geschäftszeiten|opening tijden", re.I)
TIME_RANGE = re.compile(
    r"\b(?:[01]?\d|2[0-3])(?:[:.h][0-5]\d)?\s*(?:uhr|h|am|pm)?\s*(?:-|–|—|bis|to|à|a|tot|till|til|do|until)\s*"
    r"(?:[01]?\d|2[0-3])(?:[:.h][0-5]\d)?\s*(?:uhr|h|am|pm)?\b", re.I)
PHONE_HOURS = re.compile(
    r"telefon|phone|hotline|kundenservice|kundendienst|customer (?:service|care|support)|support|servicezeiten|"
    r"erreichbar|service client|klantenservice|kundtjänst|kundeservice|kundeservice|asiakaspalvelu|"
    r"servizio clienti|atención al cliente|biuro obsługi|chat|e-?mail|versand|shipping|lieferung", re.I)
STORE_PHRASE = re.compile(
    r"ladengeschäft|unser (?:laden|geschäft|fachgeschäft)|in unserem (?:laden|geschäft|store)|besuchen sie uns|"
    r"filialen|filiale in|visit (?:us|our (?:store|shop|boutique))|our (?:store|shop|boutique) in|in-store|"
    r"store locator|find (?:a|our) store|boutique physique|notre boutique|venez nous rendre visite|"
    r"bezoek onze winkel|onze winkel|fysieke winkel|besök (?:oss|vår butik)|vår butik i|fysisk butik|"
    r"besøk (?:oss|vår butikk)|butikken vår|vores butik|besøg (?:os|butikken)|myymälämme|tervetuloa myymälään|"
    r"visita il nostro negozio|nostro negozio|visita nuestra tienda|nuestra tienda física|tienda física|"
    r"nasz sklep stacjonarny|sklep stacjonarny|kamenná prodejna|kamenný obchod|üzletünk|magazinul nostru fizic|"
    r"naša prodavaonica|apothekenteam|unsere apotheke|unser salon|in unserem salon", re.I)
SCHEMA_HOURS = re.compile(r"\"openingHours(?:Specification)?\"\s*:", re.I)
SCHEMA_STORE = re.compile(r"\"@type\"\s*:\s*\"(?:Store|LocalBusiness|Pharmacy|BeautySalon|HealthAndBeautyBusiness|"
                          r"DaySpa|HairSalon|NailSalon|DepartmentStore|ShoppingCenter)\"", re.I)
TAGS = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S | re.I)
WS = re.compile(r"\s+")

PAGE_LINK = re.compile(r"kontakt|contact|impressum|imprint|about|ueber-uns|uber-uns|om-oss|om-os|meist|filial|"
                       r"standort|store|butik|winkel|negozio|tienda|boutique|anfahrt|oeffnungszeiten|opening|"
                       r"find-us|visit|locat|yhteys|myymal", re.I)


def platform(html: str) -> str:
    h = html[:400000]
    for name, rx in PLATFORM_RE:
        if rx.search(h):
            return name
    return "other"


def _text(html):
    return WS.sub(" ", TAGS.sub(" ", html[:600000]))


def store_signal(html: str):
    """-> (kind, snippet) of the first physical-store evidence on one page, or None."""
    if SCHEMA_HOURS.search(html) and SCHEMA_STORE.search(html):
        return "schema_opening_hours", SCHEMA_STORE.search(html).group(0)
    t = _text(html)
    for m in HOURS_WORD.finditer(t):
        win = t[m.start(): m.end() + 220]
        before = t[max(0, m.start() - 60): m.start()]
        if TIME_RANGE.search(win) and not PHONE_HOURS.search(before + win[:120]):
            return "opening_hours", win[:200]
    m = STORE_PHRASE.search(t)
    if m:
        return "store_phrase", t[max(0, m.start() - 60): m.end() + 80]
    return None


def online_signal(html: str):
    """-> (kind, snippet) of the first online-shop evidence on one page, or None."""
    p = platform(html)
    if p != "other":
        return "platform", p
    m = ADD_TO_CART.search(html[:600000])     # raw html: shops often put the button label in JSON/JS
    if m:
        return "add_to_cart", m.group(0)
    m = CART_MARKUP.search(html[:600000])
    if m:
        return "cart_markup", m.group(0)[:120]
    m = CART_LINK.search(html)
    if m:
        return "cart_link", m.group(0)[:120]
    return None


def decide(pages: dict, osm_shop: bool):
    """pages {url: html}. -> (channel, source_url, snippet)."""
    store = ("osm_shop_tag", "on the map as a shop", None) if osm_shop else None
    online = None
    for url, html in pages.items():
        if not store:
            s = store_signal(html)
            if s:
                store = (s[0], s[1], url)
        if not online:
            o = online_signal(html)
            if o:
                online = (o[0], o[1], url)
        if store and online:
            break
    if store and online:
        ch = BOTH
    elif store:
        ch = STORE
    elif online:
        ch = ONLINE
    else:
        ch = UNKNOWN
    parts = [f"{k}: {v}" for k, v, _ in filter(None, (store, online))]
    url = next((u for _, _, u in filter(None, (online, store)) if u), next(iter(pages), ""))
    return ch, url, " | ".join(parts)[:480]


def links(final: str, html: str, dom: str, limit=3):
    out = []
    for m in re.finditer(r"href=[\"']([^\"'#]+)[\"']", html[:600000], re.I):
        u = urljoin(final, m.group(1))
        host = urlparse(u).netloc.lower().removeprefix("www.")
        if u.startswith("http") and host.endswith(dom) and PAGE_LINK.search(urlparse(u).path) and u not in out:
            out.append(u)
        if len(out) >= limit:
            break
    return out


# ---------- backfill ----------

def _one(company, osm_shop):
    import requests

    from . import fit
    s = requests.Session()
    s.headers["User-Agent"] = fit.UA
    dom = company["domain"]
    pages = {}
    final, html = fit.fetch_home(s, dom)
    if html:
        pages[final] = html
    real = urlparse(final).netloc.lower().removeprefix("www.") if final else dom
    extra = []
    if company.get("source_url") and company["source_url"].startswith("http"):
        extra.append(company["source_url"])
    if html:
        extra += links(final, html, real.split(".", 1)[-1] if real.count(".") > 1 else real)
    for u in list(dict.fromkeys(extra))[:4]:
        if u in pages:
            continue
        f2, h2 = fit.fetch(s, u, timeout=12)
        if h2:
            pages[f2] = h2
    if not pages:
        return company["id"], None
    return company["id"], decide(pages, osm_shop)


def main(workers=24):
    from . import db, export_leads
    with db.conn() as c:
        rows = c.execute(export_leads.SQL).fetchall()
        done = {r["company_id"] for r in c.execute(
            "select distinct company_id from company_evidence where fact='sales_channel'").fetchall()}
        osm = {r["company_id"] for r in c.execute(
            "select distinct company_id from company_evidence where fact='osm_tags'").fetchall()}
    todo = [dict(r, id=r["company_id"]) for r in rows if r["company_id"] not in done]
    print(f"{len(rows)} exported leads, {len(todo)} without a sales channel")
    stats, n = {}, 0
    with ThreadPoolExecutor(workers) as ex, db.conn() as c:
        futs = [ex.submit(_one, r, r["first_source"] == "osm" or r["company_id"] in osm) for r in todo]
        for f in as_completed(futs):
            n += 1
            try:
                cid, res = f.result()
            except Exception as e:   # one bad site must not stop the backfill
                print("error", e)
                continue
            if res is None:
                r = next(x for x in todo if x["id"] == cid)
                osm_shop = r["first_source"] == "osm" or cid in osm
                res = (STORE if osm_shop else UNKNOWN, f"https://{r['domain']}/",
                       "osm_shop_tag: on the map as a shop | site not reachable" if osm_shop else "site not reachable")
            ch, url, snip = res
            db.evidence(c, cid, "sales_channel", ch, url or "https://unknown/", snip)
            stats[ch] = stats.get(ch, 0) + 1
            if n % 200 == 0:
                c.commit()
                print(n, stats, flush=True)
        c.commit()
    print("done", stats)


if __name__ == "__main__":
    main()
