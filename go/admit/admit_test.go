package admit

import "testing"

func TestRefuseOverBudget(t *testing.T) {
	g := New(100, 2.0, 50.0)
	g.Enter(40)
	d, reason, _ := g.Decide(20)
	if d != Refuse || reason != "P95_BUDGET" {
		t.Fatalf("got %s %s", d, reason)
	}
}

func TestAdmitSmall(t *testing.T) {
	g := New(128, 1.0, 100.0)
	g.Enter(2)
	d, _, _ := g.Decide(2)
	if d != Admit {
		t.Fatal(d)
	}
}
