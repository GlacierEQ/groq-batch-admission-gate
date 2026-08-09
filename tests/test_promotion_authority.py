from __future__ import annotations
import unittest
from src.promotion_authority import PromotionAuthority

class PromotionAuthTests(unittest.TestCase):
    def test_issue_verify(self):
        a = PromotionAuthority(b"test-secret", ttl_s=60)
        g = a.issue("GlacierEQ/x", "abc", "def", now=1000.0)
        ok, r = a.verify(g, now=1001.0)
        self.assertTrue(ok)
    def test_expired(self):
        a = PromotionAuthority(b"test-secret", ttl_s=10)
        g = a.issue("GlacierEQ/x", "abc", "def", now=1000.0)
        ok, r = a.verify(g, now=2000.0)
        self.assertFalse(ok)
        self.assertEqual(r, "GRANT_EXPIRED")
    def test_bad_mac(self):
        a = PromotionAuthority(b"test-secret", ttl_s=60)
        g = a.issue("GlacierEQ/x", "abc", "def", now=1000.0)
        bad = type(g)(g.repository, g.source_sha, g.proof_receipt_digest, g.not_after, "0"*64)
        ok, r = a.verify(bad, now=1001.0)
        self.assertEqual(r, "BAD_MAC")

if __name__ == "__main__":
    unittest.main()
