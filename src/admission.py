"""Batch admission gate — refuse overload before queue blowup."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum


def digest(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Admit(str, Enum):
    ADMIT = "ADMIT"
    REFUSE = "REFUSE"


@dataclass(frozen=True)
class LoadState:
    inflight: int
    service_ms_per_item: float
    p95_budget_ms: float


@dataclass(frozen=True)
class AdmissionReceipt:
    decision: Admit
    predicted_p95_ms: float
    reason: str | None
    fingerprint: str


class BatchAdmissionGate:
    def __init__(self, max_inflight: int = 128):
        self.max_inflight = max_inflight

    def decide(self, state: LoadState, batch_size: int) -> AdmissionReceipt:
        if batch_size < 1:
            body = {"d": "REFUSE", "r": "EMPTY_BATCH"}
            return AdmissionReceipt(Admit.REFUSE, 0.0, "EMPTY_BATCH", digest(body))
        if state.inflight + batch_size > self.max_inflight:
            pred = (state.inflight + batch_size) * state.service_ms_per_item
            body = {"d": "REFUSE", "r": "MAX_INFLIGHT", "pred": pred}
            return AdmissionReceipt(Admit.REFUSE, pred, "MAX_INFLIGHT", digest(body))
        # crude: p95 ~ 1.5x mean queueing
        pred = 1.5 * (state.inflight + batch_size) * state.service_ms_per_item
        if pred > state.p95_budget_ms:
            body = {"d": "REFUSE", "r": "P95_BUDGET", "pred": pred}
            return AdmissionReceipt(Admit.REFUSE, pred, "P95_BUDGET", digest(body))
        body = {"d": "ADMIT", "pred": pred}
        return AdmissionReceipt(Admit.ADMIT, pred, None, digest(body))
