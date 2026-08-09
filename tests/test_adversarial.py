from __future__ import annotations
import unittest
from src.admission import Admit, BatchAdmissionGate, LoadState

class Adv(unittest.TestCase):
    def test_empty_batch(self):
        r = BatchAdmissionGate().decide(LoadState(0, 1.0, 100.0), 0)
        self.assertEqual(r.decision, Admit.REFUSE)
        self.assertEqual(r.reason, "EMPTY_BATCH")
    def test_p95_budget(self):
        r = BatchAdmissionGate(max_inflight=1000).decide(
            LoadState(inflight=0, service_ms_per_item=100.0, p95_budget_ms=50.0),
            batch_size=2,
        )
        self.assertEqual(r.decision, Admit.REFUSE)
        self.assertEqual(r.reason, "P95_BUDGET")
    def test_admit_small(self):
        r = BatchAdmissionGate(max_inflight=100).decide(
            LoadState(0, 1.0, 1000.0), 2
        )
        self.assertEqual(r.decision, Admit.ADMIT)

if __name__ == "__main__":
    unittest.main()
