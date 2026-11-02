"""Domain normalisation and the list of sites that are never leads (search results noise)."""
from urllib.parse import urlparse

import tldextract

_extract = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())   # offline: bundled suffix list

# Sites that show up in searches but are not buyers: price comparison, social, reviews, encyclopaedias,
# fragrance databases, video, the brands themselves, and us.
NOT_LEADS = {
    "pricerunner.se", "pricerunner.com", "prisjakt.nu", "prisjakt.no", "hintaopas.fi", "kelkoo.com", "idealo.de",
    "idealo.fr", "idealo.it", "idealo.es", "kieskeurig.nl", "tweakers.net", "beslist.nl", "google.com",
    "facebook.com", "instagram.com", "tiktok.com", "youtube.com", "pinterest.com", "reddit.com", "x.com",
    "twitter.com", "linkedin.com", "wikipedia.org", "trustpilot.com", "fragrantica.com", "fragrantica.se",
    "parfumo.com", "parfumo.net", "basenotes.com", "b2bperfume.com", "lattafa.com", "lattafa-usa.com",
    "armaf.com", "afnan.com", "swissarabian.com", "maisonalhambra.com", "ardalzaafaran.com", "rasasi.com",
    "alharamainperfumes.com", "ajmalperfume.com", "blogspot.com", "medium.com", "quora.com", "flashback.org",
    # price comparison / deal / directory sites: they list shops, they do not buy stock
    "beslist.be", "kieskeurig.be", "vergelijk.nl", "vergelijk.be", "idealo.co.uk", "idealo.at", "pricespy.co.uk",
    "ledenicheur.fr", "kelkoo.fr", "kelkoo.es", "kelkoo.it", "kelkoo.nl", "kelkoo.se", "kelkoo.no", "kelkoo.dk",
    "pricerunner.dk", "prisjagt.dk", "prisjakt.dk", "hintaseuranta.fi", "ceneo.pl", "skapiec.pl", "trovaprezzi.it",
    "kuantokusta.pt", "shopping.com", "mecenat.com", "studentkortet.se", "dealabs.com", "pepper.com", "mydealz.de",
    "monsieurparfum.com", "glamour.fr", "elle.fr", "vogue.fr", "marieclaire.fr",
}
SOCIAL_HOSTS = ("facebook.", "instagram.", "linktr.ee", "tiktok.", "wix.com", "business.site", "google.")


def domain_of(url: str) -> str:
    """'https://www.Shop.se/x' -> 'shop.se'. Empty for social pages and junk."""
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    host = urlparse(url.strip()).netloc.lower().split("@")[-1].split(":")[0]
    if not host or any(s in host for s in SOCIAL_HOSTS):
        return ""
    ext = _extract(host)
    return f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else ""


NOT_LEAD_NAMES = ("fragrantica", "parfumo", "basenotes", "pricerunner", "prisjakt", "idealo", "kelkoo", "trustpilot",
                  "beslist", "kieskeurig", "ledenicheur", "trovaprezzi", "ceneo", "wikipedia")   # any country TLD


def is_not_lead(domain: str) -> bool:
    return not domain or domain in NOT_LEADS or domain.split(".")[0] in NOT_LEAD_NAMES
