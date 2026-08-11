package admit

import "testing"

func makeAdaptiveGate(t *testing.T) *AdaptiveGate {
	t.Helper()
	g, err := NewAdaptive(64, map[string]WorkloadClassBudget{
		"interactive": {P95BudgetMs: 25, MaxInflight: 16, MaxBatchSize: 8, MinSamples: 3},
		"bulk":        {P95BudgetMs: 250, MaxInflight: 64, MaxBatchSize: 16, MinSamples: 3},
	})
	if err != nil {
		t.Fatal(err)
	}
	return g
}

func warmClass(t *testing.T, g *AdaptiveGate, class string) {
	t.Helper()
	for _, service := range []float64{1.0, 1.1, 0.9} {
		if err := g.Observe(class, service); err != nil {
			t.Fatal(err)
		}
	}
}

func TestAdaptiveColdStartRefusesUntilMinimumEvidence(t *testing.T) {
	g := makeAdaptiveGate(t)
	if err := g.Observe("interactive", 1.0); err != nil {
		t.Fatal(err)
	}
	if err := g.Observe("interactive", 1.1); err != nil {
		t.Fatal(err)
	}
	cold := g.Decide(AdaptiveLoadState{TotalInflight: 2, ClassInflight: 2, WorkloadClass: "interactive"}, 2)
	if cold.Decision != Refuse || cold.Reason != "INSUFFICIENT_EVIDENCE" || cold.EvidenceSamples != 2 {
		t.Fatalf("unexpected cold receipt: %+v", cold)
	}
	if err := g.Observe("interactive", 0.9); err != nil {
		t.Fatal(err)
	}
	warm := g.Decide(AdaptiveLoadState{TotalInflight: 2, ClassInflight: 2, WorkloadClass: "interactive"}, 2)
	if warm.Decision != Admit || warm.EvidenceSamples != 3 {
		t.Fatalf("unexpected warm receipt: %+v", warm)
	}
}

func TestAdaptiveClassesKeepIndependentEvidenceAndBudgets(t *testing.T) {
	g := makeAdaptiveGate(t)
	warmClass(t, g, "interactive")
	bulkCold := g.Decide(AdaptiveLoadState{TotalInflight: 4, ClassInflight: 0, WorkloadClass: "bulk"}, 2)
	if bulkCold.Reason != "INSUFFICIENT_EVIDENCE" {
		t.Fatalf("bulk evidence leaked across classes: %+v", bulkCold)
	}
	warmClass(t, g, "bulk")
	interactive := g.Decide(AdaptiveLoadState{TotalInflight: 20, ClassInflight: 10, WorkloadClass: "interactive"}, 4)
	bulk := g.Decide(AdaptiveLoadState{TotalInflight: 20, ClassInflight: 10, WorkloadClass: "bulk"}, 4)
	if interactive.Decision != Refuse || interactive.Reason != "P95_BUDGET" {
		t.Fatalf("interactive budget failed closed incorrectly: %+v", interactive)
	}
	if bulk.Decision != Admit {
		t.Fatalf("bulk class should remain admissible: %+v", bulk)
	}
}

func TestAdaptiveNonstationaryChangeRaisesSafetyAndRefuses(t *testing.T) {
	g := makeAdaptiveGate(t)
	for i := 0; i < 4; i++ {
		if err := g.Observe("interactive", 1.0); err != nil {
			t.Fatal(err)
		}
	}
	before, err := g.ModelSnapshot("interactive")
	if err != nil {
		t.Fatal(err)
	}
	admitted := g.Decide(AdaptiveLoadState{TotalInflight: 10, ClassInflight: 5, WorkloadClass: "interactive"}, 4)
	if admitted.Decision != Admit {
		t.Fatalf("stable service should admit: %+v", admitted)
	}
	if err := g.Observe("interactive", 10.0); err != nil {
		t.Fatal(err)
	}
	after, err := g.ModelSnapshot("interactive")
	if err != nil {
		t.Fatal(err)
	}
	refused := g.Decide(AdaptiveLoadState{TotalInflight: 10, ClassInflight: 5, WorkloadClass: "interactive"}, 4)
	if after["safety_factor"] <= before["safety_factor"] || after["change_ratio"] <= before["change_ratio"] {
		t.Fatalf("nonstationary change did not tighten model: before=%v after=%v", before, after)
	}
	if refused.Decision != Refuse || refused.Reason != "P95_BUDGET" {
		t.Fatalf("latency shock should refuse: %+v", refused)
	}
}

func TestAdaptiveCapacityLimitsRemainHard(t *testing.T) {
	g := makeAdaptiveGate(t)
	warmClass(t, g, "interactive")
	if r := g.Decide(AdaptiveLoadState{TotalInflight: 20, ClassInflight: 15, WorkloadClass: "interactive"}, 2); r.Reason != "CLASS_INFLIGHT" {
		t.Fatalf("expected class cap: %+v", r)
	}
	if r := g.Decide(AdaptiveLoadState{TotalInflight: 63, ClassInflight: 2, WorkloadClass: "interactive"}, 2); r.Reason != "MAX_INFLIGHT" {
		t.Fatalf("expected global cap: %+v", r)
	}
	if r := g.Decide(AdaptiveLoadState{TotalInflight: 2, ClassInflight: 2, WorkloadClass: "interactive"}, 9); r.Reason != "CLASS_BATCH_LIMIT" {
		t.Fatalf("expected batch cap: %+v", r)
	}
}

func TestAdaptiveUnknownAndInvalidInputsFailClosed(t *testing.T) {
	g := makeAdaptiveGate(t)
	if r := g.Decide(AdaptiveLoadState{WorkloadClass: "unknown"}, 1); r.Decision != Refuse || r.Reason != "UNKNOWN_WORKLOAD_CLASS" {
		t.Fatalf("unknown class did not refuse: %+v", r)
	}
	if r := g.Decide(AdaptiveLoadState{TotalInflight: 1, ClassInflight: 2, WorkloadClass: "interactive"}, 1); r.Decision != Refuse || r.Reason != "INVALID_LOAD_STATE" {
		t.Fatalf("invalid load did not refuse: %+v", r)
	}
	if err := g.Observe("interactive", -1); err == nil {
		t.Fatal("negative observation accepted")
	}
	if err := g.Observe("missing", 1); err == nil {
		t.Fatal("unknown observation class accepted")
	}
}

func TestAdaptiveReceiptDeterministicForStableModel(t *testing.T) {
	g := makeAdaptiveGate(t)
	warmClass(t, g, "interactive")
	state := AdaptiveLoadState{TotalInflight: 3, ClassInflight: 2, WorkloadClass: "interactive"}
	first := g.Decide(state, 2)
	second := g.Decide(state, 2)
	if first != second {
		t.Fatalf("receipts drifted: first=%+v second=%+v", first, second)
	}
	if len(first.Fingerprint) != 64 {
		t.Fatalf("bad fingerprint: %q", first.Fingerprint)
	}
}
