#!/usr/bin/env python3
"""Cold-start: BatchAdmissionGate refuse over budget."""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from admission import Admit, BatchAdmissionGate, LoadState

def main() -> int:
    g = BatchAdmissionGate(max_inflight=10)
    st = LoadState(inflight=9, service_ms_per_item=5.0, p95_budget_ms=100.0)
    r = g.decide(st, batch_size=5)
    out = {
        "decision": r.decision.value,
        "reason": r.reason,
        "expected_decision": Admit.REFUSE.value,
        "expected_reason": "MAX_INFLIGHT",
        "predicted_p95_ms": r.predicted_p95_ms,
        "fingerprint": r.fingerprint,
        "ok": r.decision is Admit.REFUSE and r.reason == "MAX_INFLIGHT",
    }
    print(json.dumps(out, sort_keys=True))
    return 0 if out["ok"] else 1
if __name__ == "__main__":
    raise SystemExit(main())
