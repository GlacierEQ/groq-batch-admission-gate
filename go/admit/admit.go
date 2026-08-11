package admit

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"sync"
	"sync/atomic"
)

type Decision string

const (
	Admit  Decision = "ADMIT"
	Refuse Decision = "REFUSE"
)

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

type WorkloadBudget struct {
	Class            string
	MaxInflight      int64
	SoftP95BudgetMs  float64
	HardLatencyMs    float64
	JitterMultiplier float64
}

func (b WorkloadBudget) Validate() error {
	if b.Class == "" || b.MaxInflight <= 0 || !finite(b.SoftP95BudgetMs) || !finite(b.HardLatencyMs) || !finite(b.JitterMultiplier) {
		return errors.New("WORKLOAD_BUDGET_INVALID")
	}
	if b.SoftP95BudgetMs <= 0 || b.HardLatencyMs < b.SoftP95BudgetMs || b.JitterMultiplier < 0 {
		return errors.New("WORKLOAD_BUDGET_INVALID")
	}
	return nil
}

func (b WorkloadBudget) Fingerprint() string {
	return digest(struct {
		Class            string  `json:"workload_class"`
		MaxInflight      int64   `json:"max_inflight"`
		SoftP95BudgetMs  float64 `json:"soft_p95_budget_ms"`
		HardLatencyMs    float64 `json:"hard_latency_ms"`
		JitterMultiplier float64 `json:"jitter_multiplier"`
	}{b.Class, b.MaxInflight, b.SoftP95BudgetMs, b.HardLatencyMs, b.JitterMultiplier})
}

type ServiceObservation struct {
	Class            string
	ObservedAt       float64
	ServiceMsPerItem float64
}

type EstimatorSnapshot struct {
	Class             string
	ObservationCount  int64
	ServiceEWMAMs     float64
	JitterEWMAMs      float64
	LastObservedAt    *float64
	Fingerprint       string
}

type AdaptiveReceipt struct {
	Decision              Decision
	Class                 string
	BatchSize             int64
	Inflight              int64
	PredictedP95Ms        float64
	PredictedHardMs       float64
	AdaptiveInflightLimit int64
	Reason                string
	BudgetFingerprint     string
	EstimatorFingerprint  string
	Fingerprint           string
}

type estimatorState struct {
	Count          int64
	ServiceEWMAMs  float64
	JitterEWMAMs   float64
	LastObservedAt *float64
}

type AdaptiveGate struct {
	mu                  sync.Mutex
	budgets             map[string]WorkloadBudget
	states              map[string]*estimatorState
	alpha               float64
	coldStartServiceMs  float64
	coldStartJitterMs   float64
}

const EstimatorVersion = "ewma-service-jitter-v1"

func NewAdaptive(budgets []WorkloadBudget, alpha, coldStartServiceMs, coldStartJitterMs float64) (*AdaptiveGate, error) {
	if len(budgets) == 0 || !finite(alpha) || alpha <= 0 || alpha > 1 || !finite(coldStartServiceMs) || coldStartServiceMs <= 0 || !finite(coldStartJitterMs) || coldStartJitterMs < 0 {
		return nil, errors.New("ADAPTIVE_CONFIG_INVALID")
	}
	g := &AdaptiveGate{
		budgets:            map[string]WorkloadBudget{},
		states:             map[string]*estimatorState{},
		alpha:              alpha,
		coldStartServiceMs: coldStartServiceMs,
		coldStartJitterMs:  coldStartJitterMs,
	}
	for _, budget := range budgets {
		if err := budget.Validate(); err != nil {
			return nil, err
		}
		if _, exists := g.budgets[budget.Class]; exists {
			return nil, errors.New("DUPLICATE_WORKLOAD_CLASS")
		}
		g.budgets[budget.Class] = budget
		g.states[budget.Class] = &estimatorState{}
	}
	return g, nil
}

func finite(value float64) bool { return !math.IsNaN(value) && !math.IsInf(value, 0) }

func digest(value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	sum := sha256.Sum256(encoded)
	return hex.EncodeToString(sum[:])
}

func (g *AdaptiveGate) Observe(obs ServiceObservation) (EstimatorSnapshot, error) {
	if obs.Class == "" || !finite(obs.ObservedAt) || !finite(obs.ServiceMsPerItem) || obs.ServiceMsPerItem <= 0 {
		return EstimatorSnapshot{}, errors.New("OBSERVATION_INVALID")
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	state, ok := g.states[obs.Class]
	if !ok {
		return EstimatorSnapshot{}, errors.New("UNKNOWN_WORKLOAD_CLASS")
	}
	if state.LastObservedAt != nil && obs.ObservedAt <= *state.LastObservedAt {
		return EstimatorSnapshot{}, errors.New("NON_MONOTONIC_OBSERVATION")
	}
	if state.Count == 0 {
		state.ServiceEWMAMs = obs.ServiceMsPerItem
		state.JitterEWMAMs = 0
	} else {
		residual := math.Abs(obs.ServiceMsPerItem - state.ServiceEWMAMs)
		state.ServiceEWMAMs = g.alpha*obs.ServiceMsPerItem + (1-g.alpha)*state.ServiceEWMAMs
		state.JitterEWMAMs = g.alpha*residual + (1-g.alpha)*state.JitterEWMAMs
	}
	state.Count++
	last := obs.ObservedAt
	state.LastObservedAt = &last
	return g.snapshotLocked(obs.Class)
}

func (g *AdaptiveGate) Snapshot(class string) (EstimatorSnapshot, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if _, ok := g.states[class]; !ok {
		return EstimatorSnapshot{}, errors.New("UNKNOWN_WORKLOAD_CLASS")
	}
	return g.snapshotLocked(class)
}

func (g *AdaptiveGate) snapshotLocked(class string) (EstimatorSnapshot, error) {
	state, ok := g.states[class]
	if !ok {
		return EstimatorSnapshot{}, errors.New("UNKNOWN_WORKLOAD_CLASS")
	}
	service := state.ServiceEWMAMs
	jitter := state.JitterEWMAMs
	if state.Count == 0 {
		service = g.coldStartServiceMs
		jitter = g.coldStartJitterMs
	}
	body := struct {
		Version           string   `json:"version"`
		Class             string   `json:"workload_class"`
		ObservationCount  int64    `json:"observation_count"`
		ServiceEWMAMs     float64  `json:"service_ewma_ms"`
		JitterEWMAMs      float64  `json:"jitter_ewma_ms"`
		LastObservedAt    *float64 `json:"last_observed_at"`
		Alpha             float64  `json:"alpha"`
	}{EstimatorVersion, class, state.Count, service, jitter, state.LastObservedAt, g.alpha}
	return EstimatorSnapshot{class, state.Count, service, jitter, state.LastObservedAt, digest(body)}, nil
}

func (g *AdaptiveGate) Decide(class string, inflight, batch int64) (AdaptiveReceipt, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	budget, ok := g.budgets[class]
	if !ok {
		return AdaptiveReceipt{}, errors.New("UNKNOWN_WORKLOAD_CLASS")
	}
	if inflight < 0 {
		return AdaptiveReceipt{}, errors.New("INFLIGHT_INVALID")
	}
	snapshot, _ := g.snapshotLocked(class)
	if batch < 1 {
		return g.receipt(budget, snapshot, inflight, batch, 0, 0, 0, Refuse, "EMPTY_BATCH"), nil
	}
	riskService := snapshot.ServiceEWMAMs + budget.JitterMultiplier*snapshot.JitterEWMAMs
	if riskService < 1e-9 {
		riskService = 1e-9
	}
	adaptiveLimit := int64(math.Floor(budget.SoftP95BudgetMs / (1.5 * riskService)))
	if adaptiveLimit < 0 {
		adaptiveLimit = 0
	}
	if adaptiveLimit > budget.MaxInflight {
		adaptiveLimit = budget.MaxInflight
	}
	projected := inflight + batch
	predP95 := 1.5 * float64(projected) * riskService
	predHard := float64(projected) * riskService
	decision, reason := Admit, ""
	switch {
	case predHard > budget.HardLatencyMs:
		decision, reason = Refuse, "HARD_LATENCY_LIMIT"
	case projected > budget.MaxInflight:
		decision, reason = Refuse, "CLASS_MAX_INFLIGHT"
	case projected > adaptiveLimit:
		decision, reason = Refuse, "ADAPTIVE_SOFT_LIMIT"
	case predP95 > budget.SoftP95BudgetMs:
		decision, reason = Refuse, "SOFT_P95_BUDGET"
	}
	return g.receipt(budget, snapshot, inflight, batch, predP95, predHard, adaptiveLimit, decision, reason), nil
}

func (g *AdaptiveGate) receipt(budget WorkloadBudget, snapshot EstimatorSnapshot, inflight, batch int64, predP95, predHard float64, adaptiveLimit int64, decision Decision, reason string) AdaptiveReceipt {
	body := struct {
		Decision             Decision `json:"decision"`
		Class                string   `json:"workload_class"`
		BatchSize            int64    `json:"batch_size"`
		Inflight             int64    `json:"inflight"`
		PredictedP95Ms       float64  `json:"predicted_p95_ms"`
		PredictedHardMs      float64  `json:"predicted_hard_ms"`
		AdaptiveLimit        int64    `json:"adaptive_inflight_limit"`
		Reason               string   `json:"reason"`
		BudgetFingerprint    string   `json:"budget_fingerprint"`
		EstimatorFingerprint string   `json:"estimator_fingerprint"`
	}{decision, budget.Class, batch, inflight, predP95, predHard, adaptiveLimit, reason, budget.Fingerprint(), snapshot.Fingerprint}
	return AdaptiveReceipt{decision, budget.Class, batch, inflight, predP95, predHard, adaptiveLimit, reason, budget.Fingerprint(), snapshot.Fingerprint, digest(body)}
}
