// Copyright 2026 TraceNex Partner OVERLAY
package outbox

import (
	"context"
	"errors"
	"testing"
)

func TestNoopPublisherCounts(t *testing.T) {
	p := &NoopPublisher{}
	for i := 0; i < 5; i++ {
		if err := p.Publish(context.Background(), "cn", "topic", []byte("x")); err != nil {
			t.Fatalf("unexpected: %v", err)
		}
	}
	if p.Sent != 5 {
		t.Fatalf("Sent = %d want 5", p.Sent)
	}
	if string(p.LastBody) != "x" {
		t.Fatalf("LastBody not captured")
	}
}

type errPublisher struct{}

func (errPublisher) Publish(ctx context.Context, region, topic string, payload []byte) error {
	return errors.New("simulated")
}

func TestErrPublisherReturnsError(t *testing.T) {
	if err := (errPublisher{}).Publish(context.Background(), "cn", "t", nil); err == nil {
		t.Fatalf("expected error")
	}
}

func TestRunnerNewDefaults(t *testing.T) {
	r := NewRunner("cn", "test-topic", nil)
	if r.batch != defaultBatchSize {
		t.Errorf("batch default lost")
	}
	if r.leaseTTL != defaultLeaseTTL {
		t.Errorf("leaseTTL default lost")
	}
	if r.interval != defaultInterval {
		t.Errorf("interval default lost")
	}
	if _, ok := r.publisher.(*NoopPublisher); !ok {
		t.Errorf("nil publisher should default to NoopPublisher")
	}
}
