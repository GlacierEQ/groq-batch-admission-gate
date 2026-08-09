"""Promotion authority — short-lived HMAC grant for PROMOTED gate.

Independent reference. Not Helix; local operator authority for this leaf.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path


def _digest(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class PromotionGrant:
    repository: str
    source_sha: str
    proof_receipt_digest: str
    not_after: float
    mac: str

    def fingerprint(self) -> str:
        return _digest({
            "repository": self.repository,
            "source_sha": self.source_sha,
            "proof_receipt_digest": self.proof_receipt_digest,
            "not_after": self.not_after,
            "mac": self.mac,
        })


class PromotionAuthority:
    def __init__(self, secret: bytes, ttl_s: float = 3600.0):
        if not secret:
            raise ValueError("secret required")
        if ttl_s <= 0:
            raise ValueError("ttl")
        self._secret = secret
        self._ttl = ttl_s

    def issue(self, repository: str, source_sha: str, proof_receipt_digest: str, now: float | None = None) -> PromotionGrant:
        t = time.time() if now is None else now
        na = t + self._ttl
        body = f"{repository}|{source_sha}|{proof_receipt_digest}|{na}"
        mac = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return PromotionGrant(repository, source_sha, proof_receipt_digest, na, mac)

    def verify(self, grant: PromotionGrant, now: float | None = None) -> tuple[bool, str | None]:
        t = time.time() if now is None else now
        if t > grant.not_after:
            return False, "GRANT_EXPIRED"
        body = f"{grant.repository}|{grant.source_sha}|{grant.proof_receipt_digest}|{grant.not_after}"
        mac = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, grant.mac):
            return False, "BAD_MAC"
        return True, None
