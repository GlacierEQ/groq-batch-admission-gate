from __future__ import annotations

import unittest

from src.admission import (
    Admit,
    AdaptiveBatchAdmissionGate,
    AdaptiveLoadState,
    WorkloadClassBudget,
)


class AdaptiveAdmissionTests(unittest.TestCase):
    def make_gate(self) -> AdaptiveBatchAdmissionGate:
        return AdaptiveBatchAdmissionGate(
            {
                "interactive": WorkloadClassBudget(
                    p95_budget_ms=25.0,
                    max_inflight=16,
                    max_batch_size=8,
                    min_samples=3,
                ),
                "bulk": WorkloadClassBudget(
                    p95_budget_ms=250.0,
                    max_inflight=64,
                    max_batch_size=16,
                    min_samples=3,
                ),
            },
            max_inflight=64,
        )

    def warm(self, gate: AdaptiveBatchAdmissionGate, workload_class: str) -> None:
        for service_ms in (1.0, 1.1, 0.9):
            gate.observe(workload_class, service_ms)

    def test_cold_start_refuses_until_class_has_minimum_evidence(self):
        gate = self.make_gate()
        gate.observe("interactive", 1.0)
        gate.observe("interactive", 1.1)
        cold = gate.decide(AdaptiveLoadState(2, 2, "interactive"), 2)
        self.assertEqual(cold.decision, Admit.REFUSE)
        self.assertEqual(cold.reason, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(cold.evidence_samples, 2)

        gate.observe("interactive", 0.9)
        warm = gate.decide(AdaptiveLoadState(2, 2, "interactive"), 2)
        self.assertEqual(warm.decision, Admit.ADMIT)
        self.assertEqual(warm.evidence_samples, 3)

    def test_workload_classes_keep_independent_evidence_and_budgets(self):
        gate = self.make_gate()
        self.warm(gate, "interactive")

        bulk_cold = gate.decide(AdaptiveLoadState(4, 0, "bulk"), 2)
        self.assertEqual(bulk_cold.reason, "INSUFFICIENT_EVIDENCE")

        self.warm(gate, "bulk")
        interactive = gate.decide(AdaptiveLoadState(20, 10, "interactive"), 4)
        bulk = gate.decide(AdaptiveLoadState(20, 10, "bulk"), 4)
        self.assertEqual(interactive.decision, Admit.REFUSE)
        self.assertEqual(interactive.reason, "P95_BUDGET")
        self.assertEqual(bulk.decision, Admit.ADMIT)

    def test_nonstationary_latency_change_increases_safety_and_refuses(self):
        gate = self.make_gate()
        for _ in range(4):
            gate.observe("interactive", 1.0)

        before = gate.model_snapshot("interactive")
        admitted = gate.decide(AdaptiveLoadState(10, 5, "interactive"), 4)
        self.assertEqual(admitted.decision, Admit.ADMIT)

        gate.observe("interactive", 10.0)
        after = gate.model_snapshot("interactive")
        refused = gate.decide(AdaptiveLoadState(10, 5, "interactive"), 4)
        self.assertGreater(after["safety_factor"], before["safety_factor"])
        self.assertGreater(after["change_ratio"], before["change_ratio"])
        self.assertEqual(refused.decision, Admit.REFUSE)
        self.assertEqual(refused.reason, "P95_BUDGET")

    def test_capacity_and_batch_limits_remain_hard_refusals(self):
        gate = self.make_gate()
        self.warm(gate, "interactive")

        class_cap = gate.decide(AdaptiveLoadState(20, 15, "interactive"), 2)
        self.assertEqual(class_cap.reason, "CLASS_INFLIGHT")

        global_cap = gate.decide(AdaptiveLoadState(63, 2, "interactive"), 2)
        self.assertEqual(global_cap.reason, "MAX_INFLIGHT")

        batch_cap = gate.decide(AdaptiveLoadState(2, 2, "interactive"), 9)
        self.assertEqual(batch_cap.reason, "CLASS_BATCH_LIMIT")

    def test_unknown_or_invalid_state_fails_closed(self):
        gate = self.make_gate()
        unknown = gate.decide(AdaptiveLoadState(0, 0, "unknown"), 1)
        self.assertEqual(unknown.decision, Admit.REFUSE)
        self.assertEqual(unknown.reason, "UNKNOWN_WORKLOAD_CLASS")

        invalid = gate.decide(AdaptiveLoadState(1, 2, "interactive"), 1)
        self.assertEqual(invalid.decision, Admit.REFUSE)
        self.assertEqual(invalid.reason, "INVALID_LOAD_STATE")

        with self.assertRaises(ValueError):
            gate.observe("interactive", float("nan"))
        with self.assertRaises(ValueError):
            gate.observe("missing", 1.0)

    def test_receipt_is_deterministic_for_same_model_and_load(self):
        gate = self.make_gate()
        self.warm(gate, "interactive")
        state = AdaptiveLoadState(3, 2, "interactive")
        first = gate.decide(state, 2)
        second = gate.decide(state, 2)
        self.assertEqual(first, second)
        self.assertEqual(len(first.fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
