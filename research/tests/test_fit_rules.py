import unittest

from research import fit
from research.range_reader import own_brand_only


class CountryHints(unittest.TestCase):
    def test_phone_and_currency(self):
        self.assertEqual(fit.country_hints("Ring oss +46 8 123 45 67, 299 SEK").most_common(1)[0][0], "SE")
        self.assertEqual(fit.country_hints("Call +44 20 7946 0958, from £25").most_common(1)[0][0], "GB")

    def test_no_hint(self):
        self.assertFalse(fit.country_hints("2000 products, free returns"))

    def test_currency_selector_ignored(self):
        self.assertFalse(fit.country_hints("SEK NOK DKK PLN GBP CHF USD"))

    def test_km_distance_is_not_bosnia(self):
        self.assertNotIn("BA", fit.country_hints("5 km from the station"))

    def test_longest_prefix_wins(self):
        self.assertIn("FI", fit.country_hints("puh. +358 40 123 4567"))
        self.assertNotIn("PT", fit.country_hints("puh. +358 40 123 4567"))


class BrandInDomain(unittest.TestCase):
    ours = [{"name": "Armaf"}, {"name": "Maison Alhambra"}, {"name": "Rave", "ambiguous": True}]

    def test_brand_owner_domains(self):
        self.assertEqual(fit.brand_in_domain("armaf", self.ours, []), "Armaf")
        self.assertEqual(fit.brand_in_domain("themaison-alhambra", self.ours, []), "Maison Alhambra")

    def test_ambiguous_brand_exact_domain(self):
        ours = [{"name": "French Avenue", "ambiguous": True}]
        self.assertEqual(fit.brand_in_domain("frenchavenue", ours, []), "French Avenue")
        self.assertEqual(fit.brand_in_domain("frenchavenueboutique", ours, []), "")

    def test_ambiguous_and_common_stems_ignored(self):
        self.assertEqual(fit.brand_in_domain("ravelli", self.ours, []), "")
        self.assertEqual(fit.brand_in_domain("emperia", self.ours, ["Emper"]), "")


class OwnBrand(unittest.TestCase):
    def test_single_vendor_is_shop(self):
        self.assertTrue(own_brand_only({"glossier": 114}, "glossier.com", "Glossier"))
        self.assertTrue(own_brand_only({"tate mcrae": 8}, "tatemcrae.store", ""))

    def test_reseller(self):
        self.assertFalse(own_brand_only({"lattafa": 50, "armaf": 30}, "miamor.se", "Miamor"))
        self.assertFalse(own_brand_only({"lattafa": 50}, "miamor.se", "Miamor"))


if __name__ == "__main__":
    unittest.main()


class ComparisonSite(unittest.TestCase):
    def test_title(self):
        self.assertTrue(fit.is_comparison("Parfumepriser.dk - Sammenlign parfumepriser i Danmark"))
        self.assertTrue(fit.is_comparison("Prisjämförelse parfym"))
        self.assertTrue(fit.is_comparison("Compare perfume prices UK"))
        self.assertFalse(fit.is_comparison("Perfume, Aftershave & Beauty at Great Prices"))
        self.assertFalse(fit.is_comparison("Arabisk parfyme til norske priser | KokkoPris"))


class BeautySignal(unittest.TestCase):
    def test_words(self):
        t = "Hudvård och smink - Kosmetik, hårvård, schampo och serum. Skönhet för alla."
        self.assertGreaterEqual(len(fit.BEAUTY.findall(t)), 5)
        self.assertEqual(fit.BEAUTY.findall("Bilverkstad och däck, service och reservdelar"), [])


class PickCountryTest(__import__("unittest").TestCase):
    def test_non_europe_tie_rejects(self):
        from collections import Counter
        self.assertEqual(fit.pick_country(Counter({"AL": 1, "US": 1}), "SI"), "")      # tie: unclear
        self.assertEqual(fit.pick_country(Counter({"GB": 1, "US": 1}), "GB"), "GB")    # $ selector on a UK shop
        self.assertEqual(fit.pick_country(Counter({"AL": 1, "US": 2}), "SI"), "US")    # "USA" in title adds 1

    def test_european_tie_unclear(self):
        from collections import Counter
        self.assertEqual(fit.pick_country(Counter({"AL": 1, "PT": 1}), "SI"), "")
        self.assertEqual(fit.pick_country(Counter({"SE": 1, "NO": 1}), "SE"), "SE")

    def test_clear_winner(self):
        from collections import Counter
        self.assertEqual(fit.pick_country(Counter({"FR": 3, "BE": 1}), "NL"), "FR")
        self.assertEqual(fit.pick_country(Counter(), "NL"), "")

    def test_usa_words(self):
        self.assertTrue(fit.NON_EU_WORDS.search("Arabic perfumes store near Chicago, USA"))
        self.assertFalse(fit.NON_EU_WORDS.search("Parfums Dubai, livraison France"))



class PhoneCountryTest(__import__("unittest").TestCase):
    def test_noise_is_not_usa(self):
        self.assertFalse(fit.country_hints("order +1 2345678 9"))

    def test_real_numbers(self):
        self.assertEqual(dict(fit.country_hints("tel 0031 20 555 1234")), {"NL": 1})
        self.assertEqual(dict(fit.country_hints("+1 312 555 0123")), {"US": 1})


class FreeMailTest(__import__("unittest").TestCase):
    def test_rules(self):
        from research.emails import free_mail_ok
        self.assertTrue(free_mail_ok("laparfumeriebordelaise@gmail.com"))
        self.assertTrue(free_mail_ok("contacthemain@gmail.com"))
        self.assertFalse(free_mail_ok("torben@gmail.com"))       # placeholder in a contact form
        self.assertFalse(free_mail_ok("your.name@gmail.com"))
        self.assertFalse(free_mail_ok("info@parfum.se"))           # not free-mail
        self.assertTrue(free_mail_ok("apoav@t-online.de"))          # real pharmacy address (short is fine)
        self.assertTrue(free_mail_ok("jane.brandejska@gmail.com"))  # real person-named shop address
        self.assertFalse(free_mail_ok("testparfym@gmail.com"))



class FetchTest(__import__("unittest").TestCase):
    """fit.fetch / fetch_home fallbacks (3 Nov 2026: false site_down rejects)."""

    class R:
        def __init__(self, code, url="https://x.de/"):
            self.status_code, self.url, self.headers, self.encoding = code, url, {"content-type": "text/html"}, "utf-8"

        def iter_content(self, n):
            yield b"<html>ok</html>"

        def close(self):
            pass

    class S:
        def __init__(self, plan):
            self.plan, self.calls = list(plan), []

        def get(self, url, **kw):
            self.calls.append((url, kw.get("verify"), bool(kw.get("headers"))))
            step = self.plan.pop(0)
            if isinstance(step, Exception):
                raise step
            return FetchTest.R(step, url)

    def test_ssl_error_retries_without_verify(self):
        import requests
        s = self.S([requests.exceptions.SSLError("bad cert"), 200])
        self.assertTrue(fit.fetch(s, "https://x.de/")[1])
        self.assertEqual(s.calls[1][1], False)

    def test_403_retries_with_browser_headers(self):
        s = self.S([403, 200])
        self.assertTrue(fit.fetch(s, "https://x.de/")[1])
        self.assertTrue(s.calls[1][2])

    def test_connection_error_retry_only_when_asked(self):
        import requests
        fit.time.sleep, real = (lambda _: None), fit.time.sleep
        try:
            s = self.S([requests.ConnectionError("reset"), 200])
            self.assertTrue(fit.fetch(s, "https://x.de/", retry=True)[1])
            s = self.S([requests.ConnectionError("reset")])
            self.assertFalse(fit.fetch(s, "https://x.de/")[1])
        finally:
            fit.time.sleep = real

    def test_home_tries_www_and_http(self):
        import requests
        s = self.S([404, requests.ConnectionError("Name or service not known"), 404, 200])
        final, html = fit.fetch_home(s, "x.de")
        self.assertTrue(html)
        self.assertEqual([c[0] for c in s.calls], ["https://x.de/", "https://www.x.de/", "http://www.x.de/", "http://x.de/"])


class ReadCapTest(__import__("unittest").TestCase):
    def test_drip_server_stops_at_deadline(self):
        import time

        class Drip:
            encoding = "utf-8"

            def iter_content(self, n):
                while True:
                    time.sleep(0.05)
                    yield b"x"

            def close(self):
                pass
        t = time.time()
        body = fit._read_capped(Drip(), 0.3)
        self.assertLess(time.time() - t, 1.0)
        self.assertTrue(body.startswith("x"))


class EncodingPrefixTest(__import__("unittest").TestCase):
    def test_no_leftovers(self):
        from research.emails import extract_emails
        self.assertEqual(extract_emails('<a href="mailto:%20info@beauty-courage.de">x</a>'), {"info@beauty-courage.de"})
        self.assertEqual(extract_emails("Kontakt&#59;info@vesta-med.de"), {"info@vesta-med.de"})
        self.assertEqual(extract_emails('{"html":"\\u003einfo@shop.se\\u003c"}'), {"info@shop.se"})
        self.assertEqual(extract_emails("mailto%3Ainfo%40shop.fr"), {"info@shop.fr"})
        self.assertEqual(extract_emails("write to 20minutes@news.fr"), {"20minutes@news.fr"})   # real digits stay


class DecodeTest(__import__("unittest").TestCase):
    def test_unknown_charset_and_latin1(self):
        self.assertEqual(fit._decode("Grüße".encode("utf-8"), "none"), "Grüße")       # LookupError case
        self.assertEqual(fit._decode("Grüße".encode("cp1252"), "utf-8"), "Grüße")     # 0xfc in a 'utf-8' page
        self.assertEqual(fit._decode("Grüße".encode("utf-8"), "ISO-8859-1"), "Grüße")

    def test_redirect_unicode_error_is_site_issue(self):
        class S:
            def get(self, url, **kw):
                raise UnicodeDecodeError("utf-8", b"\xfc", 0, 1, "invalid start byte")
        self.assertEqual(fit.fetch(S(), "https://x.de/"), (None, ""))


class ProtectedEmailTest(__import__("unittest").TestCase):
    def test_joomla(self):
        from research.emails import extract_emails
        h = '<joomla-hidden-mail is-link="1" is-email="1" first="aW5mbw==" last="Z3Jvc2NoLWtvc21ldGlrLmRl" text="x">Diese E-Mail</joomla-hidden-mail>'
        self.assertIn("info@grosch-kosmetik.de", extract_emails(h))

    def test_entities(self):
        from research.emails import extract_emails
        h = '<a href="mailto:&#105;n&#102;&#111;&#64;&#109;a&#114;&#105;a&#45;&#102;&#114;is&#101;&#117;rs&#97;lon&#46;de">x</a>'
        self.assertIn("info@maria-friseursalon.de", extract_emails(h))

    def test_typo3(self):
        from research.emails import extract_emails, _typo3_decrypt
        enc = _typo3_decrypt("mailto:info@apotheke-x.de", 1)       # what TYPO3 writes with offset 1
        h = f"<a href=\"javascript:linkTo_UnCryptMailto('{enc}');\">info(at)apotheke-x.de</a>"
        self.assertIn("info@apotheke-x.de", extract_emails(h))
        h2 = f'<a href="#" data-mailto-token="{_typo3_decrypt("mailto:shop@salon-y.de", -3)}" data-mailto-vector="3">x</a>'
        self.assertIn("shop@salon-y.de", extract_emails(h2))

    def test_rtl_and_js_and_punkt(self):
        from research.emails import extract_emails
        self.assertIn("info@parfum-z.de", extract_emails('<span style="unicode-bidi:bidi-override; direction: rtl;">ed.z-mufrap@ofni</span>'))
        self.assertIn("kontakt@duft.de", extract_emails("<script>var a = 'kontakt' + '@' + 'duft.de';</script>"))
        self.assertIn("info@laden.de", extract_emails("E-Mail: info (at) laden (punkt) de"))


class SoftRedirectParkedTest(__import__("unittest").TestCase):
    def test_js_and_meta_redirect(self):
        h = '<html><body><script>window.document.location.href = "http://web218347.web37.dogado.de/joomla/";</script></body></html>'
        self.assertEqual(fit.soft_redirect("https://kosmetik-oase-velbert.de/", h), "http://web218347.web37.dogado.de/joomla/")
        h = '<meta http-equiv="refresh" content="5;URL=index.php"><body>x</body>'
        self.assertEqual(fit.soft_redirect("https://eden-cosmetic-spa.de/", h), "https://eden-cosmetic-spa.de/index.php")
        self.assertIsNone(fit.soft_redirect("https://x.de/", "<p>" + "Wort " * 300 + "</p><script>location.href='/a'</script>"))

    def test_parked(self):
        self.assertTrue(fit.is_parked("<title>Domain Default page</title><p>Plesk is a hosting panel with simple</p>"))
        self.assertFalse(fit.is_parked("<p>" + "Parfum Shop Lattafa " * 50 + "</p>"))



class SameBusinessTest(__import__("unittest").TestCase):
    def test_rules(self):
        from research.contacts_more import same_business, pick
        self.assertTrue(same_business("info@yves-kosmetik.de", "yves-meerbusch.de"))
        self.assertTrue(same_business("elmshorn@staggenborg.com", "staggenborg-apotheke.de"))
        self.assertFalse(same_business("shafi.ghafoor@finance-solutions.net", "beauty-solutions.net"))
        self.assertFalse(same_business("info@allianz.de", "staggenborg-apotheke.de"))
        keep, how = pick({}, {"info@yves-kosmetik.de": "u", "x@gmail.com": "u"}, "yves-meerbusch.de")
        self.assertEqual((set(keep), how), ({"info@yves-kosmetik.de"}, "same_business"))
        keep, how = pick({"info@shop.de": "u"}, {"info@yves-kosmetik.de": "u"}, "shop.de")
        self.assertEqual(how, "own")
        self.assertEqual(pick({}, {"anitawordtmann@googlemail.com": "u"}, "anitawordtmann.de")[1], "free_mail")


class NonSalesInboxTest(__import__("unittest").TestCase):
    def test_bad_locals(self):
        from research.emails import is_role
        for e in ("werbewiderspruch", "lorealparislopd", "datenschutz", "rgpd", "bewerbung", "presse"):
            self.assertTrue(fit.BAD_LOCALS.search(e), e)
        for e in ("info", "einkauf", "loewen", "kontakt", "shop", "verkauf", "buchhaltung-hansa", "amministrazione"):
            self.assertFalse(fit.BAD_LOCALS.search(e), e)
        self.assertTrue(is_role("buchhaltung-hansa@apotheke-heepen.de"))      # ranks after named/info addresses

    def test_phone_glue(self):
        from research.emails import extract_emails
        self.assertEqual(extract_emails("Tel 0316 3164558info@karin-fraenzer.de"), {"info@karin-fraenzer.de"})
        self.assertEqual(extract_emails("2020shop@x.de"), {"2020shop@x.de"})      # 4 digits stay

