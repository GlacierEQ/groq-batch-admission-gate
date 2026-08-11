import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = json.loads((ROOT / "machine" / "excellence-state.json").read_text(encoding="utf-8"))
POSITION = json.loads((ROOT / "machine" / "canonical-position.json").read_text(encoding="utf-8"))
CAPABILITIES = json.loads((ROOT / "machine" / "capabilities.json").read_text(encoding="utf-8"))
RECEIPT = json.loads(
    (
        ROOT
        / "machine"
        / "evolution-receipts"
        / "2026-08-11-nonstationary-class-admission.json"
    ).read_text(encoding="utf-8")
)

OLD_CURSOR = (
    "next:nonstationary_queue_estimation_workload_class_budgets_"
    "adaptive_thresholds_hard_latency_refusal"
)
NEW_CURSOR = (
    "next:authenticated_observation_provenance_model_freshness_"
    "restart_safe_state_and_distributed_budget_coordination"
)


class CanonicalPositionContractTests(unittest.TestCase):
    def test_evolving_state_is_gate_complete(self):
        self.assertEqual(STATE["principal_state"], "EVOLVING")
        self.assertEqual(STATE["gates"]["CANONICAL_POSITION_RESOLVED"]["status"], "PASS")
        self.assertEqual(STATE["gates"]["EVOLUTION_CURSOR_DEFINED"]["status"], "PASS")
        self.assertEqual(STATE["canonical_position_ref"], "machine/canonical-position.json")

    def test_identity_and_lineage_are_preserved(self):
        self.assertEqual(POSITION["repository"], STATE["repository"])
        self.assertEqual(POSITION["canonical_identity"], "batch-admission-gate")
        policy = POSITION["integration_policy"]
        self.assertTrue(policy["preserve_repository_identity"])
        self.assertTrue(policy["preserve_lineage"])
        self.assertTrue(policy["presentation_independent"])
        self.assertTrue(policy["absorption_requires_functional_equivalence"])
        self.assertTrue(policy["absorption_requires_proof_equivalence"])

    def test_capabilities_preserve_legacy_names_and_add_adaptive_mechanisms(self):
        self.assertEqual(CAPABILITIES["capability_family"], "latency_budget_admission_control")
        capabilities = set(CAPABILITIES["capabilities"])
        self.assertIn("predictive-p95-batch-admission", capabilities)
        self.assertIn("max-inflight-capacity-refusal", capabilities)
        self.assertIn("explicit-latency-budget-gating", capabilities)
        self.assertIn("deterministic-admission-receipts", capabilities)
        self.assertIn("explicit-workload-class-latency-budget-gating", capabilities)
        self.assertIn("bounded-fast-slow-ewma-service-estimation", capabilities)
        self.assertIn("nonstationary-latency-shock-refusal", capabilities)
        self.assertNotIn("hyper-scaling", capabilities)

    def test_evolution_is_consumed_and_next_boundary_is_material(self):
        self.assertEqual(RECEIPT["consumed_cursor"], OLD_CURSOR)
        self.assertEqual(STATE["evolution_history"][-1]["consumed_cursor"], OLD_CURSOR)
        self.assertEqual(STATE["evolution_cursor"], NEW_CURSOR)
        self.assertNotEqual(STATE["evolution_cursor"], OLD_CURSOR)
        self.assertIn("Authenticate observation provenance", POSITION["next_evolution"])
        self.assertIn("expire stale model evidence", POSITION["next_evolution"])
        self.assertIn("coordinate budgets", POSITION["next_evolution"])
        self.assertIn("no Groq affiliation", POSITION["nonclaims"])
        self.assertIn("No Groq adoption", CAPABILITIES["truth_boundary"])


if __name__ == "__main__":
    unittest.main()
