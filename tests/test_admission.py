from __future__ import annotations
import math
import unittest
from src.admission import (
    AdaptiveBatchAdmissionGate,
    Admit,
    BatchAdmissionGate,
    LoadState,
    ServiceObservation,
    WorkloadBudget,
)


class AdmissionTests(unittest.TestCase):
    def test_legacy_refuse_high_load(self):
        gate = BatchAdmissionGate(max_inflight=10)
        receipt = gate.decide(LoadState(9, 1.0, 100.0), batch_size=2)
        self.assertEqual(receipt.decision, Admit.REFUSE)

    def test_legacy_admit(self):
        gate = BatchAdmissionGate(max_inflight=10)
        receipt = gate.decide(LoadState(1, 1.0, 100.0), batch_size=2)
        self.assertEqual(receipt.decision, Admit.ADMIT)


class AdaptiveAdmissionTests(unittest.TestCase):
    def make_gate(self):
        return AdaptiveBatchAdmissionGate(
            [
                WorkloadBudget("interactive", 16, 60.0, 80.0, jitter_multiplier=2.0),
                WorkloadBudget("bulk", 64, 300.0, 500.0, jitter_multiplier=1.0),
            ],
            alpha=0.5,
            cold_start_service_ms=2.0,
        )

    def test_workload_classes_have_independent_budgets(self):
        gate = self.make_gate()
        interactive = gate.decide("interactive", inflight=10, batch_size=4)
        bulk = gate.decide("bulk", inflight=10, batch_size=4)
        self.assertEqual(interactive.workload_class, "interactive")
        self.assertEqual(bulk.workload_class, "bulk")
        self.assertNotEqual(interactive.budget_fingerprint, bulk.budget_fingerprint)
        self.assertGreaterEqual(bulk.adaptive_inflight_limit, interactive.adaptive_inflight_limit)

    def test_nonstationary_slowdown_reduces_adaptive_limit(self):
        gate = self.make_gate()
        baseline = gate.decide("interactive", inflight=2, batch_size=2)
        gate.observe(ServiceObservation("interactive", 1.0, 2.0))
        first = gate.snapshot("interactive")
        gate.observe(ServiceObservation("interactive", 2.0, 12.0))
        slowed = gate.snapshot("interactive")
        after = gate.decide("interactive", inflight=2, batch_size=2)
        self.assertGreater(slowed.service_ewma_ms, first.service_ewma_ms)
        self.assertGreater(slowed.jitter_ewma_ms, 0.0)
        self.assertLess(after.adaptive_inflight_limit, baseline.adaptive_inflight_limit)
        self.assertNotEqual(after.estimator_fingerprint, baseline.estimator_fingerprint)

    def test_workload_estimator_updates_are_isolated(self):
        gate = self.make_gate()
        bulk_before = gate.snapshot("bulk")
        gate.observe(ServiceObservation("interactive", 1.0, 20.0))
        bulk_after = gate.snapshot("bulk")
        self.assertEqual(bulk_before, bulk_after)

    def test_hard_latency_limit_is_non_overridable(self):
        gate = AdaptiveBatchAdmissionGate(
            [WorkloadBudget("hard", 1000, 1000.0, 10.0, jitter_multiplier=0.0)],
            cold_start_service_ms=2.0,
        )
        receipt = gate.decide("hard", inflight=5, batch_size=1)
        self.assertEqual(receipt.decision, Admit.REFUSE)
        self.assertEqual(receipt.reason, "HARD_LATENCY_LIMIT")
        self.assertGreater(receipt.predicted_hard_ms, 10.0)

    def test_adaptive_soft_limit_rejects_before_static_class_max(self):
        gate = AdaptiveBatchAdmissionGate(
            [WorkloadBudget("interactive", 100, 30.0, 100.0, jitter_multiplier=0.0)],
            cold_start_service_ms=5.0,
        )
        receipt = gate.decide("interactive", inflight=4, batch_size=1)
        self.assertEqual(receipt.adaptive_inflight_limit, 4)
        self.assertEqual(receipt.decision, Admit.REFUSE)
        self.assertEqual(receipt.reason, "ADAPTIVE_SOFT_LIMIT")

    def test_class_max_is_enforced(self):
        gate = AdaptiveBatchAdmissionGate(
            [WorkloadBudget("bulk", 4, 1000.0, 1000.0)],
            cold_start_service_ms=1.0,
        )
        receipt = gate.decide("bulk", inflight=4, batch_size=1)
        self.assertEqual(receipt.decision, Admit.REFUSE)
        self.assertEqual(receipt.reason, "CLASS_MAX_INFLIGHT")

    def test_receipt_is_deterministic_for_same_state(self):
        gate = self.make_gate()
        a = gate.decide("interactive", inflight=2, batch_size=2)
        b = gate.decide("interactive", inflight=2, batch_size=2)
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.estimator_fingerprint, b.estimator_fingerprint)
        self.assertEqual(len(a.fingerprint), 64)

    def test_observations_must_be_monotonic_and_valid(self):
        gate = self.make_gate()
        gate.observe(ServiceObservation("interactive", 10.0, 2.0))
        with self.assertRaisesRegex(ValueError, "NON_MONOTONIC_OBSERVATION"):
            gate.observe(ServiceObservation("interactive", 10.0, 3.0))
        with self.assertRaises(ValueError):
            ServiceObservation("interactive", 11.0, math.nan)
        with self.assertRaises(ValueError):
            ServiceObservation("interactive", 11.0, 0.0)
        with self.assertRaises(KeyError):
            gate.observe(ServiceObservation("unknown", 1.0, 1.0))

    def test_invalid_configs_fail_closed(self):
        with self.assertRaises(ValueError):
            WorkloadBudget("x", 1, 100.0, 50.0)
        with self.assertRaises(ValueError):
            AdaptiveBatchAdmissionGate(
                [WorkloadBudget("x", 1, 10.0, 20.0)], alpha=0.0
            )
        with self.assertRaises(ValueError):
            AdaptiveBatchAdmissionGate(
                [WorkloadBudget("x", 1, 10.0, 20.0)], cold_start_service_ms=math.inf
            )


if __name__ == "__main__":
    unittest.main()
