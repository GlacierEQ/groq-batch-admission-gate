package admit

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"sync"
	"sync/atomic"
)

type Decision string

const (
	Admit  Decision = "ADMIT"
	Refuse Decision = "REFUSE"
)

// Gate preserves the original fixed admission API for existing callers.
type Gate struct {
	maxInflight int64
	inflight    atomic.Int64
	serviceMs   float64
	p95Budget   float64
}

func New(maxInflight int64, serviceMs, p95Budget float64) *Gate {
	return &Gate{maxInflight: maxInflight, serviceMs: serviceMs, p95Budget: p95Budget}
}

func (g *Gate) Decide(batch int64) (Decision, string, float64) {
	if batch < 1 {
		return Refuse, "EMPTY_BATCH", 0
	}
	cur := g.inflight.Load()
	if cur+batch > g.maxInflight {
		pred := float64(cur+batch) * g.serviceMs
		return Refuse, "MAX_INFLIGHT", pred
	}
	pred := 1.5 * float64(cur+batch) * g.serviceMs
	if pred > g.p95Budget {
		return Refuse, "P95_BUDGET", pred
	}
	return Admit, "", pred
}

func (g *Gate) Enter(n int64) { g.inflight.Add(n) }
func (g *Gate) Leave(n int64) { g.inflight.Add(-n) }

type WorkloadClassBudget struct {
	P95BudgetMs   float64
	MaxInflight   int64
	MaxBatchSize  int64
	MinSamples    int
}

type AdaptiveLoadState struct {
	TotalInflight int64
	ClassInflight int64
	WorkloadClass string
}

type AdaptiveReceipt struct {
	Decision                  Decision
	PredictedP95Ms            float64
	Reason                    string
	WorkloadClass             string
	EstimatedServiceMsPerItem float64
	SafetyFactor              float64
	ChangeRatio               float64
	EvidenceSamples           int
	P95BudgetMs               float64
	Fingerprint               string
}

type serviceEstimator struct {
	fastMs      float64
	slowMs      float64
	deviationMs float64
	samples     int
}

type AdaptiveGate struct {
	mu             sync.Mutex
	maxInflight    int64
	budgets        map[string]WorkloadClassBudget
	estimators     map[string]*serviceEstimator
	alphaFast      float64
	alphaSlow      float64
	alphaDeviation float64
	baseSafety     float64
	minSafety      float64
	maxSafety      float64
}

func NewAdaptive(maxInflight int64, budgets map[string]WorkloadClassBudget) (*AdaptiveGate, error) {
	if maxInflight < 1 {
		return nil, fmt.Errorf("maxInflight must be positive")
	}
	if len(budgets) == 0 {
		return nil, fmt.Errorf("at least one workload budget is required")
	}
	copied := make(map[string]WorkloadClassBudget, len(budgets))
	estimators := make(map[string]*serviceEstimator, len(budgets))
	for name, budget := range budgets {
		if name == "" {
			return nil, fmt.Errorf("workload class names must be non-empty")
		}
		if !finitePositive(budget.P95BudgetMs) || budget.MaxInflight < 1 || budget.MaxBatchSize < 1 || budget.MinSamples < 1 {
			return nil, fmt.Errorf("invalid workload budget for %s", name)
		}
		copied[name] = budget
		estimators[name] = &serviceEstimator{}
	}
	return &AdaptiveGate{
		maxInflight:    maxInflight,
		budgets:        copied,
		estimators:     estimators,
		alphaFast:      0.50,
		alphaSlow:      0.10,
		alphaDeviation: 0.25,
		baseSafety:     1.20,
		minSafety:      1.10,
		maxSafety:      3.00,
	}, nil
}

func (g *AdaptiveGate) Observe(workloadClass string, serviceMsPerItem float64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	est, ok := g.estimators[workloadClass]
	if !ok {
		return fmt.Errorf("unknown workload class")
	}
	if !finitePositive(serviceMsPerItem) {
		return fmt.Errorf("serviceMsPerItem must be finite and positive")
	}
	if est.samples == 0 {
		est.fastMs = serviceMsPerItem
		est.slowMs = serviceMsPerItem
		est.deviationMs = 0
		est.samples = 1
		return nil
	}
	previousSlow := est.slowMs
	est.fastMs += g.alphaFast * (serviceMsPerItem - est.fastMs)
	est.slowMs += g.alphaSlow * (serviceMsPerItem - est.slowMs)
	absoluteError := math.Abs(serviceMsPerItem - previousSlow)
	est.deviationMs += g.alphaDeviation * (absoluteError - est.deviationMs)
	est.samples++
	return nil
}

func (g *AdaptiveGate) ModelSnapshot(workloadClass string) (map[string]float64, error) {
	g.mu.Lock()
	defer g.mu.Unlock()

	est, ok := g.estimators[workloadClass]
	if !ok {
		return nil, fmt.Errorf("unknown workload class")
	}
	estimate, safety, change := g.estimate(est)
	return map[string]float64{
		"samples":         float64(est.samples),
		"fast_ms":         est.fastMs,
		"slow_ms":         est.slowMs,
		"deviation_ms":    est.deviationMs,
		"estimate_ms":     estimate,
		"safety_factor":   safety,
		"change_ratio":    change,
	}, nil
}

func (g *AdaptiveGate) Decide(state AdaptiveLoadState, batch int64) AdaptiveReceipt {
	g.mu.Lock()
	defer g.mu.Unlock()

	budget, ok := g.budgets[state.WorkloadClass]
	if !ok {
		return g.receipt(Refuse, "UNKNOWN_WORKLOAD_CLASS", state.WorkloadClass, 0, 0, 0, 0, 0, 0)
	}
	est := g.estimators[state.WorkloadClass]
	if batch < 1 || state.TotalInflight < 0 || state.ClassInflight < 0 || state.ClassInflight > state.TotalInflight {
		return g.receipt(Refuse, "INVALID_LOAD_STATE", state.WorkloadClass, 0, 0, 0, est.samples, budget.P95BudgetMs, 0)
	}
	if batch > budget.MaxBatchSize {
		return g.receiptForModel(state, batch, budget, est, "CLASS_BATCH_LIMIT")
	}
	if state.TotalInflight+batch > g.maxInflight {
		return g.receiptForModel(state, batch, budget, est, "MAX_INFLIGHT")
	}
	if state.ClassInflight+batch > budget.MaxInflight {
		return g.receiptForModel(state, batch, budget, est, "CLASS_INFLIGHT")
	}

	estimate, safety, change := g.estimate(est)
	predicted := float64(state.TotalInflight+batch) * estimate * safety
	if est.samples < budget.MinSamples {
		return g.receipt(Refuse, "INSUFFICIENT_EVIDENCE", state.WorkloadClass, predicted, estimate, safety, est.samples, budget.P95BudgetMs, change)
	}
	if predicted > budget.P95BudgetMs {
		return g.receipt(Refuse, "P95_BUDGET", state.WorkloadClass, predicted, estimate, safety, est.samples, budget.P95BudgetMs, change)
	}
	return g.receipt(Admit, "", state.WorkloadClass, predicted, estimate, safety, est.samples, budget.P95BudgetMs, change)
}

func (g *AdaptiveGate) receiptForModel(state AdaptiveLoadState, batch int64, budget WorkloadClassBudget, est *serviceEstimator, reason string) AdaptiveReceipt {
	estimate, safety, change := g.estimate(est)
	predicted := float64(state.TotalInflight+batch) * estimate * safety
	return g.receipt(Refuse, reason, state.WorkloadClass, predicted, estimate, safety, est.samples, budget.P95BudgetMs, change)
}

func (g *AdaptiveGate) estimate(est *serviceEstimator) (float64, float64, float64) {
	if est.samples == 0 {
		return 0, g.maxSafety, 0
	}
	estimate := math.Max(est.fastMs, est.slowMs)
	denominator := math.Max(est.slowMs, 1e-12)
	change := math.Abs(est.fastMs-est.slowMs) / denominator
	variability := est.deviationMs / denominator
	safety := g.baseSafety + change + 0.5*variability
	if safety < g.minSafety {
		safety = g.minSafety
	}
	if safety > g.maxSafety {
		safety = g.maxSafety
	}
	return estimate, safety, change
}

func (g *AdaptiveGate) receipt(decision Decision, reason, workloadClass string, predicted, estimate, safety float64, samples int, budgetMs, change float64) AdaptiveReceipt {
	body := fmt.Sprintf("%s|%s|%s|%.12g|%.12g|%.12g|%.12g|%d|%.12g", decision, reason, workloadClass, predicted, estimate, safety, change, samples, budgetMs)
	sum := sha256.Sum256([]byte(body))
	return AdaptiveReceipt{
		Decision:                  decision,
		PredictedP95Ms:            predicted,
		Reason:                    reason,
		WorkloadClass:             workloadClass,
		EstimatedServiceMsPerItem: estimate,
		SafetyFactor:              safety,
		ChangeRatio:               change,
		EvidenceSamples:           samples,
		P95BudgetMs:               budgetMs,
		Fingerprint:               hex.EncodeToString(sum[:]),
	}
}

func finitePositive(v float64) bool {
	return !math.IsNaN(v) && !math.IsInf(v, 0) && v > 0
}
