import unittest

from research.verify import reacher_verdict


class ReacherVerdict(unittest.TestCase):
    def test_role_address_confirmed_is_valid(self):
        self.assertEqual(reacher_verdict("risky", {"is_catch_all": False, "is_deliverable": True}), "valid")

    def test_catch_all_stays_risky(self):
        self.assertEqual(reacher_verdict("risky", {"is_catch_all": True, "is_deliverable": True}), "risky")

    def test_refused_is_invalid(self):
        self.assertEqual(reacher_verdict("risky", {"is_catch_all": False, "is_deliverable": False}), "invalid")

    def test_full_inbox_not_valid(self):
        self.assertNotEqual(reacher_verdict("risky", {"is_catch_all": False, "is_deliverable": True,
                                                      "has_full_inbox": True}), "valid")

    def test_unknown_catch_all_stays_risky(self):
        self.assertEqual(reacher_verdict("risky", {"is_deliverable": True}), "risky")

    def test_plain_mapping(self):
        self.assertEqual(reacher_verdict("safe", {}), "valid")
        self.assertEqual(reacher_verdict("invalid", {}), "invalid")
        self.assertEqual(reacher_verdict("unknown", {}), "unknown")


if __name__ == "__main__":
    unittest.main()
