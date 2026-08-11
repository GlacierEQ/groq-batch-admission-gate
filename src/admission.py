"""Batch admission gates that refuse overload before queue blowup.

The legacy ``BatchAdmissionGate`` remains intentionally stable.  The adaptive
controller is an explicit v2 surface: callers must provide workload-class
budgets and feed observed service-time evidence before it can admit work.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


def digest(obj: object) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
    """Legacy fixed-model admission rule preserved for existing callers."""

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
        # Historical rule: p95 ~= 1.5x mean queueing.
        pred = 1.5 * (state.inflight + batch_size) * state.service_ms_per_item
        if pred > state.p95_budget_ms:
            body = {"d": "REFUSE", "r": "P95_BUDGET", "pred": pred}
            return AdmissionReceipt(Admit.REFUSE, pred, "P95_BUDGET", digest(body))
        body = {"d": "ADMIT", "pred": pred}
        return AdmissionReceipt(Admit.ADMIT, pred, None, digest(body))


@dataclass(frozen=True)
class WorkloadClassBudget:
    """Admission budget owned by one declared workload class."""

    p95_budget_ms: float
    max_inflight: int
    max_batch_size: int
    min_samples: int = 4

    def __post_init__(self) -> None:
        if not math.isfinite(self.p95_budget_ms) or self.p95_budget_ms <= 0:
            raise ValueError("p95_budget_ms must be finite and positive")
        if self.max_inflight < 1:
            raise ValueError("max_inflight must be positive")
        if self.max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")
        if self.min_samples < 1:
            raise ValueError("min_samples must be positive")


@dataclass(frozen=True)
class AdaptiveLoadState:
    """Point-in-time queue state supplied to the adaptive controller."""

    total_inflight: int
    class_inflight: int
    workload_class: str


@dataclass(frozen=True)
class AdaptiveAdmissionReceipt:
    decision: Admit
    predicted_p95_ms: float
    reason: str | None
    workload_class: str
    estimated_service_ms_per_item: float
    safety_factor: float
    change_ratio: float
    evidence_samples: int
    p95_budget_ms: float
    fingerprint: str


@dataclass
class _ServiceEstimator:
    fast_ms: float = 0.0
    slow_ms: float = 0.0
    deviation_ms: float = 0.0
    samples: int = 0


class AdaptiveBatchAdmissionGate:
    """Bounded evidence-driven admission for nonstationary workloads.

    The controller intentionally refuses when it does not have the minimum
    evidence required by a workload class.  Adaptation is bounded by fixed
    EWMA coefficients and safety-factor clamps; it does not learn arbitrary
    policies or infer provider telemetry.
    """

    def __init__(
        self,
        budgets: Mapping[str, WorkloadClassBudget],
        *,
        max_inflight: int = 128,
        alpha_fast: float = 0.50,
        alpha_slow: float = 0.10,
        alpha_deviation: float = 0.25,
        base_safety: float = 1.20,
        min_safety: float = 1.10,
        max_safety: float = 3.00,
    ) -> None:
        if max_inflight < 1:
            raise ValueError("max_inflight must be positive")
        if not budgets:
            raise ValueError("at least one workload budget is required")
        for name in budgets:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("workload class names must be non-empty strings")
        for name, value in {
            "alpha_fast": alpha_fast,
            "alpha_slow": alpha_slow,
            "alpha_deviation": alpha_deviation,
        }.items():
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be in (0, 1]")
        if not 0.0 < alpha_slow <= alpha_fast:
            raise ValueError("alpha_slow must not exceed alpha_fast")
        if not (0.0 < min_safety <= base_safety <= max_safety):
            raise ValueError("safety factors must satisfy 0 < min <= base <= max")

        self.max_inflight = max_inflight
        self._budgets = dict(budgets)
        self._estimators = {name: _ServiceEstimator() for name in budgets}
        self._alpha_fast = alpha_fast
        self._alpha_slow = alpha_slow
        self._alpha_deviation = alpha_deviation
        self._base_safety = base_safety
        self._min_safety = min_safety
        self._max_safety = max_safety

    def observe(self, workload_class: str, service_ms_per_item: float) -> None:
        """Ingest one caller-supplied completed-service observation."""

        if workload_class not in self._budgets:
            raise ValueError("unknown workload class")
        if not math.isfinite(service_ms_per_item) or service_ms_per_item <= 0:
            raise ValueError("service_ms_per_item must be finite and positive")

        est = self._estimators[workload_class]
        if est.samples == 0:
            est.fast_ms = service_ms_per_item
            est.slow_ms = service_ms_per_item
            est.deviation_ms = 0.0
            est.samples = 1
            return

        previous_slow = est.slow_ms
        est.fast_ms += self._alpha_fast * (service_ms_per_item - est.fast_ms)
        est.slow_ms += self._alpha_slow * (service_ms_per_item - est.slow_ms)
        absolute_error = abs(service_ms_per_item - previous_slow)
        est.deviation_ms += self._alpha_deviation * (
            absolute_error - est.deviation_ms
        )
        est.samples += 1

    def model_snapshot(self, workload_class: str) -> dict[str, float | int]:
        if workload_class not in self._budgets:
            raise ValueError("unknown workload class")
        est = self._estimators[workload_class]
        estimate, safety, change_ratio = self._estimate(est)
        return {
            "samples": est.samples,
            "fast_ms": est.fast_ms,
            "slow_ms": est.slow_ms,
            "deviation_ms": est.deviation_ms,
            "estimate_ms": estimate,
            "safety_factor": safety,
            "change_ratio": change_ratio,
        }

    def decide(
        self, state: AdaptiveLoadState, batch_size: int
    ) -> AdaptiveAdmissionReceipt:
        workload_class = state.workload_class
        budget = self._budgets.get(workload_class)
        if budget is None:
            return self._receipt(
                Admit.REFUSE,
                "UNKNOWN_WORKLOAD_CLASS",
                workload_class,
                0.0,
                0.0,
                0.0,
                0,
                0.0,
            )
        if (
            batch_size < 1
            or state.total_inflight < 0
            or state.class_inflight < 0
            or state.class_inflight > state.total_inflight
        ):
            return self._receipt(
                Admit.REFUSE,
                "INVALID_LOAD_STATE",
                workload_class,
                0.0,
                0.0,
                0.0,
                self._estimators[workload_class].samples,
                budget.p95_budget_ms,
            )
        if batch_size > budget.max_batch_size:
            return self._receipt_for_model(
                workload_class, budget, "CLASS_BATCH_LIMIT", state, batch_size
            )
        if state.total_inflight + batch_size > self.max_inflight:
            return self._receipt_for_model(
                workload_class, budget, "MAX_INFLIGHT", state, batch_size
            )
        if state.class_inflight + batch_size > budget.max_inflight:
            return self._receipt_for_model(
                workload_class, budget, "CLASS_INFLIGHT", state, batch_size
            )

        est = self._estimators[workload_class]
        estimate, safety, change_ratio = self._estimate(est)
        predicted = (state.total_inflight + batch_size) * estimate * safety
        if est.samples < budget.min_samples:
            return self._receipt(
                Admit.REFUSE,
                "INSUFFICIENT_EVIDENCE",
                workload_class,
                predicted,
                estimate,
                safety,
                est.samples,
                budget.p95_budget_ms,
                change_ratio,
            )
        if predicted > budget.p95_budget_ms:
            return self._receipt(
                Admit.REFUSE,
                "P95_BUDGET",
                workload_class,
                predicted,
                estimate,
                safety,
                est.samples,
                budget.p95_budget_ms,
                change_ratio,
            )
        return self._receipt(
            Admit.ADMIT,
            None,
            workload_class,
            predicted,
            estimate,
            safety,
            est.samples,
            budget.p95_budget_ms,
            change_ratio,
        )

    def _receipt_for_model(
        self,
        workload_class: str,
        budget: WorkloadClassBudget,
        reason: str,
        state: AdaptiveLoadState,
        batch_size: int,
    ) -> AdaptiveAdmissionReceipt:
        est = self._estimators[workload_class]
        estimate, safety, change_ratio = self._estimate(est)
        predicted = (state.total_inflight + batch_size) * estimate * safety
        return self._receipt(
            Admit.REFUSE,
            reason,
            workload_class,
            predicted,
            estimate,
            safety,
            est.samples,
            budget.p95_budget_ms,
            change_ratio,
        )

    def _estimate(self, est: _ServiceEstimator) -> tuple[float, float, float]:
        if est.samples == 0:
            return 0.0, self._max_safety, 0.0
        estimate = max(est.fast_ms, est.slow_ms)
        denominator = max(est.slow_ms, 1e-12)
        change_ratio = abs(est.fast_ms - est.slow_ms) / denominator
        variability_ratio = est.deviation_ms / denominator
        safety = self._base_safety + change_ratio + 0.5 * variability_ratio
        safety = min(self._max_safety, max(self._min_safety, safety))
        return estimate, safety, change_ratio

    def _receipt(
        self,
        decision: Admit,
        reason: str | None,
        workload_class: str,
        predicted: float,
        estimate: float,
        safety: float,
        samples: int,
        budget_ms: float,
        change_ratio: float = 0.0,
    ) -> AdaptiveAdmissionReceipt:
        body = {
            "decision": decision.value,
            "reason": reason,
            "workload_class": workload_class,
            "predicted_p95_ms": predicted,
            "estimated_service_ms_per_item": estimate,
            "safety_factor": safety,
            "change_ratio": change_ratio,
            "evidence_samples": samples,
            "p95_budget_ms": budget_ms,
        }
        return AdaptiveAdmissionReceipt(
            decision=decision,
            predicted_p95_ms=predicted,
            reason=reason,
            workload_class=workload_class,
            estimated_service_ms_per_item=estimate,
            safety_factor=safety,
            change_ratio=change_ratio,
            evidence_samples=samples,
            p95_budget_ms=budget_ms,
            fingerprint=digest(body),
        )
