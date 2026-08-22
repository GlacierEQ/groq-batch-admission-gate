from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "machine" / "excellence-state.json"
TARGET_PATH = ROOT / "machine" / "target-contract.json"
ROLE_POSITION_PATH = ROOT / "machine" / "role-position.json"
RECEIPT_PATH = (
    ROOT
    / "machine"
    / "evolution-receipts"
    / "2026-08-11-nonstationary-class-admission.json"
)

CONSUMED = (
    "next:nonstationary_queue_estimation_workload_class_budgets_"
    "adaptive_thresholds_hard_latency_refusal"
)
NEXT = (
    "next:authenticated_observation_provenance_model_freshness_"
    "restart_safe_state_and_distributed_budget_coordination"
)
CANDIDATE = "41eb3e157061c4483d813f70575d4ae7d6c95548"
RUN = 31538611693


class EvolutionContractTests(unittest.TestCase):
    def load(self, path: Path) -> dict:
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("<<<<<<<", raw)
        self.assertNotIn("=======", raw)
        self.assertNotIn(">>>>>>>", raw)
        return json.loads(raw)

    def test_state_consumes_exact_cursor_and_advances(self):
        state = self.load(STATE_PATH)
        self.assertEqual(state["principal_state"], "EVOLVING")
        self.assertEqual(state["state"], "EVOLVING")
        self.assertEqual(state["evolution_cursor"], NEXT)
        evolution = state["evolution_history"][-1]
        self.assertEqual(evolution["consumed_cursor"], CONSUMED)
        self.assertEqual(evolution["candidate_source_sha"], CANDIDATE)
        self.assertEqual(evolution["workflow_run"], RUN)
        self.assertEqual(evolution["result"], "PASS")
        self.assertEqual(evolution["next_cursor"], NEXT)

    def test_target_contract_is_evolving_and_truth_bounded(self):
        target = self.load(TARGET_PATH)
        self.assertEqual(
            target["identity"]["repository_id"],
            "GlacierEQ/groq-batch-admission-gate",
        )
        self.assertEqual(target["current"]["state"], "EVOLVING")
        self.assertEqual(target["proof"]["candidate_source_sha"], CANDIDATE)
        self.assertEqual(target["proof"]["workflow_run"], RUN)
        self.assertEqual(target["next_cursor"], NEXT)
        nonclaims = " ".join(target["nonclaims"]).lower()
        self.assertIn("no provider telemetry", nonclaims)
        self.assertIn("no restart-safe", nonclaims)

    def test_receipt_and_role_position_preserve_claim_ceiling(self):
        receipt = self.load(RECEIPT_PATH)
        role_position = self.load(ROLE_POSITION_PATH)
        self.assertEqual(receipt["consumed_cursor"], CONSUMED)
        self.assertEqual(receipt["candidate_source_sha"], CANDIDATE)
        self.assertEqual(receipt["workflow_run"], RUN)
        self.assertEqual(receipt["python"], "PASS")
        self.assertEqual(receipt["go"], "PASS")
        self.assertEqual(receipt["next_cursor"], NEXT)
        boundaries = " ".join(receipt["truth_boundaries"]).lower()
        self.assertIn("caller supplied", boundaries)
        self.assertIn("not distributed", boundaries)
        self.assertIn("no groq affiliation", boundaries)
        self.assertEqual(role_position["position_state"], "EVIDENCE_TRACKED")
        self.assertIn("Authenticate observation provenance", role_position["next_evolution"])


if __name__ == "__main__":
    unittest.main()
