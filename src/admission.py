"""Batch admission gates — static compatibility path plus adaptive workload budgets."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum


def digest(obj: object) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _finite(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(name)
    return float(value)


def _positive_int(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(name)
    return value


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
    """Legacy static gate retained for compatibility."""

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
        pred = 1.5 * (state.inflight + batch_size) * state.service_ms_per_item
        if pred > state.p95_budget_ms:
            body = {"d": "REFUSE", "r": "P95_BUDGET", "pred": pred}
            return AdmissionReceipt(Admit.REFUSE, pred, "P95_BUDGET", digest(body))
        body = {"d": "ADMIT", "pred": pred}
        return AdmissionReceipt(Admit.ADMIT, pred, None, digest(body))


@dataclass(frozen=True)
class WorkloadBudget:
    workload_class: str
    max_inflight: int
    soft_p95_budget_ms: float
    hard_latency_ms: float
    jitter_multiplier: float = 2.0

    def __post_init__(self) -> None:
        if not isinstance(self.workload_class, str) or not self.workload_class.strip():
            raise ValueError("workload_class")
        _positive_int("max_inflight", self.max_inflight)
        soft = _finite("soft_p95_budget_ms", self.soft_p95_budget_ms)
        hard = _finite("hard_latency_ms", self.hard_latency_ms)
        jitter = _finite("jitter_multiplier", self.jitter_multiplier)
        if soft <= 0 or hard <= 0 or hard < soft or jitter < 0:
            raise ValueError("workload_budget")

    def fingerprint(self) -> str:
        return digest(
            {
                "workload_class": self.workload_class,
                "max_inflight": self.max_inflight,
                "soft_p95_budget_ms": self.soft_p95_budget_ms,
                "hard_latency_ms": self.hard_latency_ms,
                "jitter_multiplier": self.jitter_multiplier,
            }
        )


@dataclass(frozen=True)
class ServiceObservation:
    workload_class: str
    observed_at: float
    service_ms_per_item: float

    def __post_init__(self) -> None:
        if not isinstance(self.workload_class, str) or not self.workload_class.strip():
            raise ValueError("workload_class")
        _finite("observed_at", self.observed_at)
        service = _finite("service_ms_per_item", self.service_ms_per_item)
        if service <= 0:
            raise ValueError("service_ms_per_item")


@dataclass(frozen=True)
class EstimatorSnapshot:
    workload_class: str
    observation_count: int
    service_ewma_ms: float
    jitter_ewma_ms: float
    last_observed_at: float | None
    fingerprint: str


@dataclass(frozen=True)
class AdaptiveAdmissionReceipt:
    decision: Admit
    workload_class: str
    batch_size: int
    inflight: int
    predicted_p95_ms: float
    predicted_hard_ms: float
    adaptive_inflight_limit: int
    reason: str | None
    budget_fingerprint: str
    estimator_fingerprint: str
    fingerprint: str


@dataclass
class _EstimatorState:
    count: int = 0
    service_ewma_ms: float = 0.0
    jitter_ewma_ms: float = 0.0
    last_observed_at: float | None = None


class AdaptiveBatchAdmissionGate:
    """Adaptive workload-class admission with a non-overridable hard latency ceiling.

    The estimator tracks an EWMA of observed service time and absolute residual jitter.
    Adaptation may make admission stricter or looser under the soft budget, but the hard
    latency ceiling is always evaluated independently and can never be relaxed.
    """

    ESTIMATOR_VERSION = "ewma-service-jitter-v1"

    def __init__(
        self,
        budgets: list[WorkloadBudget],
        *,
        alpha: float = 0.25,
        cold_start_service_ms: float = 1.0,
        cold_start_jitter_ms: float = 0.0,
    ) -> None:
        if not budgets:
            raise ValueError("budgets")
        self._budgets: dict[str, WorkloadBudget] = {}
        for budget in budgets:
            if not isinstance(budget, WorkloadBudget):
                raise TypeError("budget")
            if budget.workload_class in self._budgets:
                raise ValueError("DUPLICATE_WORKLOAD_CLASS")
            self._budgets[budget.workload_class] = budget
        self.alpha = _finite("alpha", alpha)
        if not 0 < self.alpha <= 1:
            raise ValueError("alpha")
        self.cold_start_service_ms = _finite("cold_start_service_ms", cold_start_service_ms)
        self.cold_start_jitter_ms = _finite("cold_start_jitter_ms", cold_start_jitter_ms)
        if self.cold_start_service_ms <= 0 or self.cold_start_jitter_ms < 0:
            raise ValueError("cold_start")
        self._state = {name: _EstimatorState() for name in self._budgets}

    def observe(self, observation: ServiceObservation) -> EstimatorSnapshot:
        if not isinstance(observation, ServiceObservation):
            raise TypeError("observation")
        state = self._state.get(observation.workload_class)
        if state is None:
            raise KeyError("UNKNOWN_WORKLOAD_CLASS")
        if state.last_observed_at is not None and observation.observed_at <= state.last_observed_at:
            raise ValueError("NON_MONOTONIC_OBSERVATION")

        sample = observation.service_ms_per_item
        if state.count == 0:
            state.service_ewma_ms = sample
            state.jitter_ewma_ms = 0.0
        else:
            residual = abs(sample - state.service_ewma_ms)
            state.service_ewma_ms = self.alpha * sample + (1.0 - self.alpha) * state.service_ewma_ms
            state.jitter_ewma_ms = self.alpha * residual + (1.0 - self.alpha) * state.jitter_ewma_ms
        state.count += 1
        state.last_observed_at = observation.observed_at
        return self.snapshot(observation.workload_class)

    def snapshot(self, workload_class: str) -> EstimatorSnapshot:
        state = self._state.get(workload_class)
        if state is None:
            raise KeyError("UNKNOWN_WORKLOAD_CLASS")
        service = state.service_ewma_ms if state.count else self.cold_start_service_ms
        jitter = state.jitter_ewma_ms if state.count else self.cold_start_jitter_ms
        body = {
            "version": self.ESTIMATOR_VERSION,
            "workload_class": workload_class,
            "observation_count": state.count,
            "service_ewma_ms": service,
            "jitter_ewma_ms": jitter,
            "last_observed_at": state.last_observed_at,
            "alpha": self.alpha,
        }
        return EstimatorSnapshot(
            workload_class=workload_class,
            observation_count=state.count,
            service_ewma_ms=service,
            jitter_ewma_ms=jitter,
            last_observed_at=state.last_observed_at,
            fingerprint=digest(body),
        )

    def decide(self, workload_class: str, *, inflight: int, batch_size: int) -> AdaptiveAdmissionReceipt:
        if not isinstance(workload_class, str) or not workload_class.strip():
            raise ValueError("workload_class")
        budget = self._budgets.get(workload_class)
        if budget is None:
            raise KeyError("UNKNOWN_WORKLOAD_CLASS")
        if not isinstance(inflight, int) or isinstance(inflight, bool) or inflight < 0:
            raise ValueError("inflight")
        if not isinstance(batch_size, int) or isinstance(batch_size, bool):
            raise ValueError("batch_size")
        snapshot = self.snapshot(workload_class)

        if batch_size < 1:
            return self._receipt(
                budget,
                snapshot,
                inflight,
                batch_size,
                0.0,
                0.0,
                0,
                Admit.REFUSE,
                "EMPTY_BATCH",
            )

        risk_service = snapshot.service_ewma_ms + budget.jitter_multiplier * snapshot.jitter_ewma_ms
        risk_service = max(risk_service, 1e-9)
        adaptive_limit = min(
            budget.max_inflight,
            max(0, int(math.floor(budget.soft_p95_budget_ms / (1.5 * risk_service)))),
        )
        projected = inflight + batch_size
        predicted_p95 = 1.5 * projected * risk_service
        # Hard ceiling uses the same risk-adjusted service but no soft 1.5 queue multiplier.
        predicted_hard = projected * risk_service

        if predicted_hard > budget.hard_latency_ms:
            decision, reason = Admit.REFUSE, "HARD_LATENCY_LIMIT"
        elif projected > budget.max_inflight:
            decision, reason = Admit.REFUSE, "CLASS_MAX_INFLIGHT"
        elif projected > adaptive_limit:
            decision, reason = Admit.REFUSE, "ADAPTIVE_SOFT_LIMIT"
        elif predicted_p95 > budget.soft_p95_budget_ms:
            decision, reason = Admit.REFUSE, "SOFT_P95_BUDGET"
        else:
            decision, reason = Admit.ADMIT, None

        return self._receipt(
            budget,
            snapshot,
            inflight,
            batch_size,
            predicted_p95,
            predicted_hard,
            adaptive_limit,
            decision,
            reason,
        )

    def _receipt(
        self,
        budget: WorkloadBudget,
        snapshot: EstimatorSnapshot,
        inflight: int,
        batch_size: int,
        predicted_p95: float,
        predicted_hard: float,
        adaptive_limit: int,
        decision: Admit,
        reason: str | None,
    ) -> AdaptiveAdmissionReceipt:
        body = {
            "decision": decision.value,
            "workload_class": budget.workload_class,
            "batch_size": batch_size,
            "inflight": inflight,
            "predicted_p95_ms": predicted_p95,
            "predicted_hard_ms": predicted_hard,
            "adaptive_inflight_limit": adaptive_limit,
            "reason": reason,
            "budget_fingerprint": budget.fingerprint(),
            "estimator_fingerprint": snapshot.fingerprint,
        }
        return AdaptiveAdmissionReceipt(
            decision=decision,
            workload_class=budget.workload_class,
            batch_size=batch_size,
            inflight=inflight,
            predicted_p95_ms=predicted_p95,
            predicted_hard_ms=predicted_hard,
            adaptive_inflight_limit=adaptive_limit,
            reason=reason,
            budget_fingerprint=budget.fingerprint(),
            estimator_fingerprint=snapshot.fingerprint,
            fingerprint=digest(body),
        )
