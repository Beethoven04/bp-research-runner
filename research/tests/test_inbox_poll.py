import unittest

from research.inbox_poll import parse

RAW = (b"From: Anna Shop <anna@shop.se>\r\nTo: emma@b2bperfume.com\r\nSubject: Re: Wholesale Arabian fragrances\r\n"
       b"Message-ID: <x1@shop.se>\r\nIn-Reply-To: <abc@b2bperfume.com>\r\nReferences: <abc@b2bperfume.com>\r\n"
       b"Auto-Submitted: no\r\nMIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=b\r\n\r\n"
       b"--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHej, skicka prislistan.\r\n"
       b"--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Hej, skicka prislistan.</p>\r\n--b--\r\n")


class TestInboxParse(unittest.TestCase):
    def test_shape_matches_classify_inbound(self):
        m = parse(RAW)
        self.assertEqual(m["from"]["value"][0]["address"], "anna@shop.se")
        self.assertEqual(m["subject"], "Re: Wholesale Arabian fragrances")
        self.assertEqual(m["inReplyTo"], "<abc@b2bperfume.com>")
        self.assertIn("prislistan", m["text"])
        self.assertIn("<p>", m["html"])
        self.assertEqual(m["headers"]["auto-submitted"], "no")

    def test_html_only_gets_text(self):
        m = parse(b"From: a@b.se\r\nSubject: x\r\nContent-Type: text/html\r\n\r\n<b>stop</b>\r\n")
        self.assertIn("stop", m["text"])


if __name__ == "__main__":
    unittest.main()
