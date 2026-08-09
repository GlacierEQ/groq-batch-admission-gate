from __future__ import annotations
import hashlib
import json
import unittest
from pathlib import Path

from src.promotion_authority import (
    LOCAL_OPERATOR_SECRET,
    PromotionAuthority,
    PromotionGrant,
    verify_bound_grant,
)

ROOT = Path(__file__).resolve().parents[1]


class PromotionAuthTests(unittest.TestCase):
    def test_issue_verify(self):
        a = PromotionAuthority(b"test-secret", ttl_s=60)
        g = a.issue("GlacierEQ/x", "abc", "def", now=1000.0)
        ok, r = a.verify(g, now=1001.0)
        self.assertTrue(ok)
        self.assertIsNone(r)

    def test_expired(self):
        a = PromotionAuthority(b"test-secret", ttl_s=10)
        g = a.issue("GlacierEQ/x", "abc", "def", now=1000.0)
        ok, r = a.verify(g, now=2000.0)
        self.assertFalse(ok)
        self.assertEqual(r, "GRANT_EXPIRED")

    def test_bad_mac(self):
        a = PromotionAuthority(b"test-secret", ttl_s=60)
        g = a.issue("GlacierEQ/x", "abc", "def", now=1000.0)
        bad = type(g)(g.repository, g.source_sha, g.proof_receipt_digest, g.not_after, "0" * 64)
        ok, r = a.verify(bad, now=1001.0)
        self.assertFalse(ok)
        self.assertEqual(r, "BAD_MAC")

    def test_real_machine_grant_verifies_against_proof_receipt(self):
        """Drive real shipped path: load machine receipts and verify HMAC binding."""
        grant_path = ROOT / "machine" / "promotion_authority.json"
        proof_path = ROOT / "machine" / "proof_receipt.json"
        self.assertTrue(grant_path.is_file(), "promotion_authority.json missing")
        self.assertTrue(proof_path.is_file(), "proof_receipt.json missing")
        grant = json.loads(grant_path.read_text(encoding="utf-8"))
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        file_digest = hashlib.sha256(proof_path.read_bytes()).hexdigest()
        self.assertEqual(grant["proof_receipt_digest"], file_digest)
        self.assertEqual(grant["source_sha"], proof["source_sha"])
        ok, reason = verify_bound_grant(grant, proof_path, secret=LOCAL_OPERATOR_SECRET)
        self.assertTrue(ok, f"grant verify failed: {reason}")
        # also exercise class path
        g = PromotionGrant.from_dict(grant)
        vok, vreason = PromotionAuthority(LOCAL_OPERATOR_SECRET, ttl_s=10**9).verify(g)
        self.assertTrue(vok, vreason)


if __name__ == "__main__":
    unittest.main()
