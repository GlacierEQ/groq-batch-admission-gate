import json
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
STATE = json.loads((ROOT / "machine" / "excellence-state.json").read_text(encoding="utf-8"))
POSITION = json.loads((ROOT / "machine" / "canonical-position.json").read_text(encoding="utf-8"))
class CanonicalPositionContractTests(unittest.TestCase):
    def test_evolving_state_is_gate_complete(self):
        self.assertEqual(STATE["principal_state"], "EVOLVING")
        self.assertEqual(STATE["gates"]["CANONICAL_POSITION_RESOLVED"]["status"], "PASS")
        self.assertEqual(STATE["gates"]["EVOLUTION_CURSOR_DEFINED"]["status"], "PASS")
        self.assertEqual(STATE["canonical_position_ref"], "machine/canonical-position.json")
    def test_identity_and_lineage_are_preserved(self):
        self.assertEqual(POSITION["repository"], STATE["repository"])
        p = POSITION["integration_policy"]
        self.assertTrue(p["preserve_repository_identity"] and p["preserve_lineage"] and p["presentation_independent"])
        self.assertTrue(p["absorption_requires_functional_equivalence"] and p["absorption_requires_proof_equivalence"])
    def test_evolution_is_material(self):
        self.assertTrue(STATE["evolution_cursor"].startswith("next:"))
        self.assertTrue(POSITION["next_evolution"])
if __name__ == "__main__": unittest.main()
