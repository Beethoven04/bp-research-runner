import unittest

from research import brands as B
from research import config

OUR = config.tenant("b2bperfume")["our_brands"]


class TestFind(unittest.TestCase):
    def names(self, text, kind="title", url="https://shop.example/p"):
        return {h["brand"] for h in B.find(OUR, text, kind, url)}

    def test_title_exact(self):
        self.assertEqual(self.names("Lattafa Khamrah EDP 100ml"), {"Lattafa"})

    def test_sub_brand_not_double_counted(self):
        self.assertEqual(self.names("Lattafa Pride Ishq Al Shuyukh 100ml"), {"Lattafa Pride"})

    def test_both_brands(self):
        self.assertEqual(self.names("Lattafa Asad and Lattafa Pride Nebras"), {"Lattafa", "Lattafa Pride"})

    def test_slug(self):
        self.assertEqual(self.names("/products/maison-alhambra-baroque-rouge-540", "slug"), {"Maison Alhambra"})

    def test_word_boundary(self):
        self.assertEqual(self.names("Armafix glue"), set())

    def test_ambiguous_needs_strong_evidence(self):
        text = "Our rave reviews: best perfume shop in town, 100ml bottles"
        self.assertEqual(self.names(text, "context"), set())
        self.assertEqual(self.names("Rave Now Intense EDP 100ml", "title"), {"Rave"})

    def test_context_needs_perfume_words(self):
        self.assertEqual(self.names("Armaf sponsors the local football club", "context"), set())
        self.assertEqual(self.names("New in: Armaf Club de Nuit eau de parfum", "context"), {"Armaf"})

    def test_norm_keeps_digits(self):
        self.assertEqual(B.norm("Asad%20100ml_EDP"), "asad 100ml edp")

    def test_context_ml_counts_as_perfume_word(self):
        self.assertEqual(self.names("Afnan 9pm 100 ml bottle", "context"), {"Afnan"})

    def test_accents_and_case(self):
        self.assertEqual(self.names("SWISS ARABIAN Shaghaf Oud"), {"Swiss Arabian"})


class TestSummarise(unittest.TestCase):
    def test_gap_and_afnan_only_scandinavia(self):
        hits = B.find(OUR, "Lattafa Khamrah EDP", "title", "u1")
        se = B.summarise(OUR, hits, "SE")
        fr = B.summarise(OUR, hits, "FR")
        self.assertIn("Afnan", se["gap"])
        self.assertNotIn("Afnan", fr["gap"])
        self.assertNotIn("Lattafa", se["gap"])

    def test_out_of_stock_brand_never_offered(self):
        stock = {b["name"]: 5 for b in OUR}
        stock["French Avenue"] = 0
        gap = B.summarise(OUR, [], "SE", stock)["gap"]
        self.assertNotIn("French Avenue", gap)
        self.assertIn("Lattafa", gap)

    def test_strongest_evidence_kept(self):
        hits = B.find(OUR, "armaf club de nuit parfum", "context", "u1") + B.find(OUR, "Armaf", "vendor", "u2")
        self.assertEqual(B.summarise(OUR, hits, "NL")["stocked"]["Armaf"]["kind"], "vendor")


if __name__ == "__main__":
    unittest.main()


class TestRangeRules(unittest.TestCase):
    def test_incomplete_read_claims_no_gap(self):
        from research import range_reader as R
        import types
        orig = R.generic
        R.generic = lambda s, base, brands, host: ([], 1)      # search page works, finds nothing
        try:
            out = R.read({"domain": "x.se", "country": "SE"}, "other", OUR, [], {b["name"]: 1 for b in OUR})
        finally:
            R.generic = orig
        self.assertEqual(out["gap"], [])
        self.assertEqual(out["status"], "unknown")
