package admit

import "sync/atomic"

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
