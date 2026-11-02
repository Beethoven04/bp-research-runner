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
