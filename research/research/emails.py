"""Email helpers: extraction from HTML, role-address detection, name normalisation, format patterns."""
import base64
import html as htmlmod
import re
import urllib.parse
import unicodedata

EMAIL_RE = re.compile(r"[a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,24}", re.I)
# "name [at] domain [dot] com", "name(at)domain.com", "name at domain dot com"
OBFUSCATED_RE = re.compile(
    r"([a-z0-9][a-z0-9._%+-]{0,63})\s*(?:\[at\]|\(at\)|\{at\}|\[@\]|\s\(a\)\s|\(ät\)|\[ät\]|\(@\))\s*"
    r"([a-z0-9-]+(?:\s*(?:\[dot\]|\(dot\)|\s+dot\s+|\[punkt\]|\(punkt\)|\s+punkt\s+|\.)\s*[a-z0-9-]+)+)", re.I)
DOT_RE = re.compile(r"\s*(?:\[dot\]|\(dot\)|\s+dot\s+|\[punkt\]|\(punkt\)|\s+punkt\s+|\.)\s*", re.I)

ROLE_LOCALS = {
    "info", "hello", "hi", "hej", "kontakt", "contact", "support", "help", "sales", "salg", "careers", "career",
    "jobs", "job", "hr", "press", "media", "marketing", "office", "admin", "team", "mail", "post", "invoice",
    "faktura", "billing", "accounts", "legal", "privacy", "gdpr", "dpo", "security", "noreply", "no-reply",
    "friends", "business", "partners", "partner", "kundservice", "service", "webmaster", "booking", "events",
    "recruitment", "rekrytering", "ekonomi", "reception", "welcome", "general", "enquiries", "inquiries", "investors",
    "inkop", "innkjop", "indkob", "osto", "purchasing", "purchase", "buying", "buyer", "order", "orders", "shop",
    "store", "butik", "butikk", "webshop", "kundtjanst", "kundeservice", "asiakaspalvelu", "wholesale", "b2b",
}
# Free-mail providers: a small shop may publish one of these as its business address. Used only when the shop's own
# site publishes it and no own-domain address exists (decided 3 Nov 2026, Louai). Stored as kind 'role'.
FREE_MAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.uk", "hotmail.fr", "hotmail.it", "hotmail.es",
    "hotmail.de", "hotmail.se", "hotmail.nl", "hotmail.be", "outlook.com", "outlook.fr", "outlook.es", "outlook.it",
    "outlook.de", "live.com", "live.fr", "live.nl", "live.be", "live.se", "live.dk", "live.it", "msn.com", "yahoo.com",
    "yahoo.fr", "yahoo.it", "yahoo.es", "yahoo.de", "yahoo.co.uk", "ymail.com", "icloud.com", "me.com", "aol.com",
    "gmx.de", "gmx.net", "gmx.at", "gmx.ch", "web.de", "t-online.de", "orange.fr", "free.fr", "wanadoo.fr", "sfr.fr",
    "laposte.net", "libero.it", "virgilio.it", "alice.it", "tiscali.it", "telenet.be", "skynet.be", "ziggo.nl",
    "kpnmail.nl", "home.nl", "planet.nl", "abv.bg", "mail.bg", "wp.pl", "o2.pl", "onet.pl", "interia.pl",
    "seznam.cz", "centrum.cz", "azet.sk", "freemail.hu", "citromail.hu", "yandex.com", "mail.ru", "proton.me",
    "protonmail.com", "online.no", "telia.com", "bredband.net", "spray.se", "jubii.dk", "mail.dk", "sapo.pt",
    "hotmail.dk", "hotmail.no", "outlook.dk", "eircom.net", "btinternet.com", "sky.com", "inbox.lv", "inbox.lt",
    "mail.ee", "zoho.com", "zohomail.eu",
}


def free_mail_ok(email: str) -> bool:
    """Free-mail address usable as a shop contact (only for the free-mail fallback in fit.check)? Rejects form
    placeholders: a bare common first name (torben@gmail.com, louise@gmail.com) or test/your/example/name locals.
    3 Nov 2026: a first version (any 1-7 letter local) also hit real addresses like apoav@t-online.de; replaced."""
    local, dom = email.lower().split("@", 1)
    if dom not in FREE_MAIL:
        return False
    if local in PLACEHOLDER_NAMES:
        return False
    return not re.match(r"(test|your|example|sample|demo|name|email|e-?mail|mustermann)", local)


PLACEHOLDER_NAMES = {"john", "jane", "johndoe", "janedoe", "max", "maxmustermann", "torben", "louise", "sophie",
                     "anna", "peter", "mario", "maria", "jean", "pierre", "marie", "jan", "lars", "erik", "info",
                     "contact", "mail", "user", "admin", "someone", "me", "you"}


JUNK_DOMAINS = ("example.", "sentry", "wixpress", "domain.com", "email.com", "company.com", "acme.com", "yourdomain")


def is_role(email: str) -> bool:
    local = email.split("@")[0].lower()
    base = re.split(r"[+.\-_]", local)[0]
    return local in ROLE_LOCALS or base in ROLE_LOCALS


def decode_cfemail(hex_str: str) -> str:
    """Cloudflare 'email protection' obfuscation (data-cfemail="...")."""
    try:
        key = int(hex_str[:2], 16)
        return "".join(chr(int(hex_str[i:i + 2], 16) ^ key) for i in range(2, len(hex_str), 2))
    except ValueError:
        return ""


JS_ESCAPE = re.compile(r"\\u00[0-9a-f]{2}|\\x[0-9a-f]{2}", re.I)


def clean_text(html: str) -> str:
    """Decode the page before the email regex, so encoding leftovers do not stick to an address (4 Nov 2026: 435
    contacts like "20info@..." from %20, "59info@..." from &#59;, "u003einfo@..." from \\u003e).
    JS escapes -> space; URL-decoding twice (handles %2520); HTML entities unescaped."""
    text = JS_ESCAPE.sub(" ", html)
    for _ in range(2):
        text = urllib.parse.unquote(text)
    return htmlmod.unescape(text)


JOOMLA_RE = re.compile(r"<joomla-hidden-mail[^>]*?\sfirst=\"([A-Za-z0-9+/=]+)\"[^>]*?\slast=\"([A-Za-z0-9+/=]+)\"", re.I)
TYPO3_RE = re.compile(r"(?:linkTo_UnCryptMailto\(\s*['\"](?:javascript:)?|data-mailto-token=\")([^'\")]{6,200})", re.I)
RTL_RE = re.compile(r"<(?:span|bdo|div|p|a)[^>]*(?:direction\s*:\s*rtl|bidi-override|dir=\"rtl\")[^>]*>([^<]{5,120})<", re.I)
JS_CONCAT = re.compile(r"['\"]\s*\+\s*['\"]")


def _typo3_decrypt(enc: str, offset: int) -> str:
    """TYPO3 spamProtectEmailAddresses: shifts chars inside 3 ranges ('+'..':', '@'..'Z', 'a'..'z') with wrap."""
    out = []
    for ch in enc:
        n = ord(ch)
        for lo, hi in ((0x2B, 0x3A), (0x40, 0x5A), (0x61, 0x7A)):
            if lo <= n <= hi:
                n += offset
                if n > hi:
                    n = lo + (n - hi - 1)
                elif n < lo:
                    n = hi - (lo - n - 1)
                break
        out.append(chr(n))
    return "".join(out)


def decode_protected(html: str) -> set:
    """German/EU site builders hide the Impressum address from bots (4 Nov 2026, 20-shop DE sample):
    Joomla <joomla-hidden-mail first/last base64>, TYPO3 linkTo_UnCryptMailto / data-mailto-token (shifted chars),
    reversed text shown with CSS direction:rtl, and JS string pieces ('info' + '@' + 'shop.de')."""
    found = set()
    for first, last in JOOMLA_RE.findall(html):
        try:
            found.add(base64.b64decode(first).decode() + "@" + base64.b64decode(last).decode())
        except (ValueError, UnicodeDecodeError):
            pass
    for enc in TYPO3_RE.findall(html):
        enc = htmlmod.unescape(enc)
        for off in range(-10, 11):
            dec = _typo3_decrypt(enc, off)
            if dec.lower().startswith("mailto:"):
                found.add(urllib.parse.unquote(dec[7:]).split("?")[0])
                break
    for txt in RTL_RE.findall(html):
        found.update(EMAIL_RE.findall(htmlmod.unescape(txt)[::-1]))
    if JS_CONCAT.search(html):
        found.update(EMAIL_RE.findall(JS_CONCAT.sub("", clean_text(html))))
    return found


def extract_emails(html: str) -> set:
    found = set()
    for m in re.finditer(r'data-cfemail="([0-9a-f]+)"', html, re.I):
        found.add(decode_cfemail(m.group(1)))
    found |= decode_protected(html)
    text = clean_text(html)
    found.update(EMAIL_RE.findall(text))
    for local, dom in OBFUSCATED_RE.findall(re.sub(r"<[^>]+>", " ", text)):
        found.add(f"{local}@{DOT_RE.sub('.', dom)}")
    out = set()
    for e in found:
        e = e.strip(".").lower()
        if not EMAIL_RE.fullmatch(e) or any(j in e for j in JUNK_DOMAINS):
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", e):
            continue
        out.add(e)
    return out


def ascii_name(s: str) -> str:
    """'Bjørn Ærø' -> 'bjorn aero'. Nordic letters mapped the way companies usually do."""
    s = (s or "").strip().lower()
    for a, b in (("æ", "ae"), ("ø", "o"), ("å", "a"), ("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z \-]", "", s)


def name_parts(first: str, last: str):
    """-> (first, last token, full last joined). 'Anders','Kofod-Petersen' -> ('anders','petersen','kofod-petersen')"""
    f = ascii_name(first).split()
    raw = ascii_name(last).split()
    l_ = " ".join(raw).replace("-", " ").split()
    f0 = f[0] if f else ""
    lastname = l_[-1] if l_ else ""
    full = "-".join(raw) if any("-" in r for r in raw) else " ".join(l_)
    return f0, lastname, full


# Format name -> builder(first, last, full_last)
FORMATS = {
    "first": lambda f, l, L: f,
    "first.last": lambda f, l, L: f"{f}.{l}",
    "firstlast": lambda f, l, L: f"{f}{l}",
    "flast": lambda f, l, L: f"{f[:1]}{l}",
    "f.last": lambda f, l, L: f"{f[:1]}.{l}",
    "first_last": lambda f, l, L: f"{f}_{l}",
    "first-last": lambda f, l, L: f"{f}-{l}",
    "firstl": lambda f, l, L: f"{f}{l[:1]}",
    "fl": lambda f, l, L: f"{f[:1]}{l[:1]}",
    "last": lambda f, l, L: l,
    "last.first": lambda f, l, L: f"{l}.{f}",
    "first.fulllast": lambda f, l, L: f"{f}.{L.replace(' ', '')}",
    "initials": lambda f, l, L: f[:1] + "".join(p[:1] for p in L.replace("-", " ").split()) if " " in L.replace("-", " ") else "",   # double / hyphenated surnames: anders.kofod-petersen
    "fla": lambda f, l, L: f"{f[:1]}{l[:2]}",       # DTU-style 3-4 letter initials (approximate)
    "fil": lambda f, l, L: f"{f[:2]}{l[:1]}",
}
# Order used when the domain's format is unknown (most common first, Nordic SMEs)
DEFAULT_ORDER = ["first", "first.last", "flast", "firstl", "fl", "firstlast", "f.last"]


def build(fmt: str, first: str, last: str) -> str:
    f, l, L = name_parts(first, last)
    if not f or (not l and fmt != "first"):
        return ""
    return FORMATS[fmt](f, l, L)


def infer_format(known):
    """known: list of (email, first, last) where the person is identified. Returns best format or None."""
    votes = {}
    for email, first, last in known:
        local = email.split("@")[0].lower()
        for fmt in FORMATS:
            if build(fmt, first, last) == local:
                votes[fmt] = votes.get(fmt, 0) + 1
    return max(votes, key=votes.get) if votes else None


def local_shape(local: str) -> str:
    """Rough shape of a local part when we don't know whose it is: 'first.last', 'first', 'initials'…"""
    if "." in local and all(p.isalpha() for p in local.split(".")):
        return "first.last" if len(local.split(".")[0]) > 1 else "f.last"
    if "_" in local:
        return "first_last"
    if local.isalpha() and len(local) <= 3:
        return "initials"
    return "single"
