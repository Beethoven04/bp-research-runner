import unittest
from unittest import mock

from research import range_reader as R


class _Page:
    def __init__(self, url):
        self.url = url


class _Tree:
    def __init__(self, urls, invalid=0):
        self.urls, self.invalid = urls, invalid

    def all_pages(self):
        return [_Page(u) for u in self.urls]

    def all_sitemaps(self):
        from usp.objects.sitemap import InvalidSitemap
        return [InvalidSitemap(url="https://x/s.xml", reason="bad")] * self.invalid


BRANDS = [{"name": "Lattafa"}, {"name": "Lattafa Pride"}, {"name": "Maison Alhambra"}, {"name": "Armaf"}]
REF = [f"https://shop.se/products/{b}-edp-100ml" for b in ("dior-sauvage", "chanel-no5", "gucci-bloom", "prada-luna",
                                                             "versace-eros", "armani-code")]
FILLER = [f"https://shop.se/pages/info-{i}" for i in range(30)]


def run(urls, invalid=0, known=(), products=None):
    with mock.patch("usp.tree.sitemap_tree_for_homepage", return_value=_Tree(urls, invalid)):
        return R.sitemap("https://shop.se", BRANDS, "shop.se", products or {}, known)


class Sitemap(unittest.TestCase):
    def test_complete_and_brand_found(self):
        hits, n, complete, _ = run(REF + FILLER + ["https://shop.se/products/lattafa-khamrah-100ml"])
        self.assertTrue(complete)
        self.assertEqual({h["brand"] for h in hits}, {"Lattafa"})

    def test_short_alias(self):
        hits, *_ = run(REF + FILLER + ["https://shop.se/products/alhambra-your-touch-oud"])
        self.assertIn("Maison Alhambra", {h["brand"] for h in hits})

    def test_sub_brand_does_not_prove_parent(self):
        hits, *_ = run(REF + FILLER + ["https://shop.se/products/lattafa-pride-ishq"])
        self.assertEqual({h["brand"] for h in hits}, {"Lattafa Pride"})

    def test_product_name_blocks_gap(self):
        hits, *_ = run(REF + FILLER + ["https://shop.se/products/club-de-nuit-intense-man"],
                       products={"Armaf": ["club de nuit intense"]})
        self.assertEqual([(h["brand"], h["kind"]) for h in hits], [("Armaf", "product_name")])

    def test_urls_without_brand_names_not_complete(self):
        _, _, complete, _ = run(FILLER + [f"https://shop.se/p/{i}" for i in range(50)])
        self.assertFalse(complete)

    def test_broken_sitemap_not_complete(self):
        _, _, complete, _ = run(REF + FILLER, invalid=1)
        self.assertFalse(complete)

    def test_known_brand_missed_not_complete(self):
        _, _, complete, _ = run(REF + FILLER, known=["Armaf"])
        self.assertFalse(complete)


if __name__ == "__main__":
    unittest.main()
