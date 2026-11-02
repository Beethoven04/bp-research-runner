"""Email helpers: extraction from HTML, role-address detection, name normalisation, format patterns."""
import re
import unicodedata

EMAIL_RE = re.compile(r"[a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,24}", re.I)
# "name [at] domain [dot] com", "name(at)domain.com", "name at domain dot com"
OBFUSCATED_RE = re.compile(
    r"([a-z0-9][a-z0-9._%+-]{0,63})\s*(?:\[at\]|\(at\)|\{at\}|\[@\]|\s\(a\)\s)\s*"
    r"([a-z0-9-]+(?:\s*(?:\[dot\]|\(dot\)|\s+dot\s+|\.)\s*[a-z0-9-]+)+)", re.I)
DOT_RE = re.compile(r"\s*(?:\[dot\]|\(dot\)|\s+dot\s+|\.)\s*", re.I)

ROLE_LOCALS = {
    "info", "hello", "hi", "hej", "kontakt", "contact", "support", "help", "sales", "salg", "careers", "career",
    "jobs", "job", "hr", "press", "media", "marketing", "office", "admin", "team", "mail", "post", "invoice",
    "faktura", "billing", "accounts", "legal", "privacy", "gdpr", "dpo", "security", "noreply", "no-reply",
    "friends", "business", "partners", "partner", "kundservice", "service", "webmaster", "booking", "events",
    "recruitment", "rekrytering", "ekonomi", "reception", "welcome", "general", "enquiries", "inquiries", "investors",
    "inkop", "innkjop", "indkob", "osto", "purchasing", "purchase", "buying", "buyer", "order", "orders", "shop",
    "store", "butik", "butikk", "webshop", "kundtjanst", "kundeservice", "asiakaspalvelu", "wholesale", "b2b",
}
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


def extract_emails(html: str) -> set:
    found = set()
    for m in re.finditer(r'data-cfemail="([0-9a-f]+)"', html, re.I):
        found.add(decode_cfemail(m.group(1)))
    text = re.sub(r"&#64;|&commat;|%40", "@", html)
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
