#!/usr/bin/env python3
"""Auditor re-run: verify machine/promotion_authority.json against proof receipt."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from promotion_authority import LOCAL_OPERATOR_SECRET, verify_bound_grant  # noqa: E402


def main() -> int:
    grant = json.loads((ROOT / "machine" / "promotion_authority.json").read_text(encoding="utf-8"))
    proof = ROOT / "machine" / "proof_receipt.json"
    ok, reason = verify_bound_grant(grant, proof, secret=LOCAL_OPERATOR_SECRET)
    out = {
        "ok": ok,
        "reason": reason,
        "repository": grant.get("repository"),
        "source_sha": grant.get("source_sha"),
        "proof_receipt_digest": grant.get("proof_receipt_digest"),
        "secret_ref": "src/promotion_authority.py::LOCAL_OPERATOR_SECRET",
    }
    print(json.dumps(out, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
