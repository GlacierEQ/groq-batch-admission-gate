from __future__ import annotations
import unittest
from src.admission import Admit, BatchAdmissionGate, LoadState

class AdmitTests(unittest.TestCase):
    def test_refuse_over_budget(self):
        g = BatchAdmissionGate(max_inflight=100)
        st = LoadState(inflight=40, service_ms_per_item=2.0, p95_budget_ms=50.0)
        r = g.decide(st, batch_size=20)
        self.assertEqual(r.decision, Admit.REFUSE)

    def test_admit_small(self):
        g = BatchAdmissionGate()
        st = LoadState(inflight=2, service_ms_per_item=1.0, p95_budget_ms=100.0)
        r = g.decide(st, 2)
        self.assertEqual(r.decision, Admit.ADMIT)

if __name__ == "__main__":
    unittest.main()
