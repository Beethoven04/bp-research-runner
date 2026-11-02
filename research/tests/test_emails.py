import unittest

from research.emails import extract_emails, is_role


class TestEmails(unittest.TestCase):
    def test_plain_and_obfuscated(self):
        html = 'Mail <a href="mailto:info@parfym.se">info@parfym.se</a> or inkop [at] parfym [dot] se'
        self.assertEqual(extract_emails(html), {"info@parfym.se", "inkop@parfym.se"})

    def test_cloudflare(self):
        # "a@b.se" encoded with key 0x42
        enc = "42" + "".join(f"{ord(c) ^ 0x42:02x}" for c in "a@b.se")
        self.assertEqual(extract_emails(f'<span data-cfemail="{enc}"></span>'), {"a@b.se"})

    def test_junk_dropped(self):
        self.assertEqual(extract_emails("logo@2x.png user@example.com"), set())

    def test_role(self):
        self.assertTrue(is_role("info@shop.se"))
        self.assertFalse(is_role("anna.berg@shop.se"))


if __name__ == "__main__":
    unittest.main()
