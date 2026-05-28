package controller

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

func TestNextRelayRetryBackoffDelayExponentialAndMax(t *testing.T) {
	cfg := relayRetryBackoffConfig{
		base:  time.Second,
		max:   3 * time.Second,
		total: 20 * time.Second,
	}
	state := &relaycommon.RetryBackoffState{}

	expected := []time.Duration{time.Second, 2 * time.Second, 3 * time.Second, 3 * time.Second}
	for _, want := range expected {
		got, ok := nextRelayRetryBackoffDelay(state, cfg)
		if !ok {
			t.Fatalf("expected delay %s to be allowed", want)
		}
		if got != want {
			t.Fatalf("delay = %s, want %s", got, want)
		}
	}
}

func TestNextRelayRetryBackoffDelayTotalTimeout(t *testing.T) {
	cfg := relayRetryBackoffConfig{
		base:  time.Second,
		max:   10 * time.Second,
		total: 3 * time.Second,
	}
	state := &relaycommon.RetryBackoffState{}

	got, ok := nextRelayRetryBackoffDelay(state, cfg)
	if !ok || got != time.Second {
		t.Fatalf("first delay = %s, %v; want 1s, true", got, ok)
	}

	got, ok = nextRelayRetryBackoffDelay(state, cfg)
	if !ok || got != 2*time.Second {
		t.Fatalf("second delay = %s, %v; want 2s, true", got, ok)
	}

	if got, ok = nextRelayRetryBackoffDelay(state, cfg); ok {
		t.Fatalf("third delay = %s, true; want stopped by total timeout", got)
	}
}

func TestNextRelayRetryBackoffDelayAllowsExactTotalTimeout(t *testing.T) {
	cfg := relayRetryBackoffConfig{
		base:  500 * time.Millisecond,
		max:   time.Second,
		total: 1500 * time.Millisecond,
	}
	state := &relaycommon.RetryBackoffState{}

	got, ok := nextRelayRetryBackoffDelay(state, cfg)
	if !ok || got != 500*time.Millisecond {
		t.Fatalf("first delay = %s, %v; want 500ms, true", got, ok)
	}

	got, ok = nextRelayRetryBackoffDelay(state, cfg)
	if !ok || got != time.Second {
		t.Fatalf("second delay = %s, %v; want 1s, true", got, ok)
	}

	if got, ok = nextRelayRetryBackoffDelay(state, cfg); ok {
		t.Fatalf("third delay = %s, true; want stopped after exact total timeout", got)
	}
}

func TestNextRelayRetryBackoffDelayUnlimitedTotalTimeout(t *testing.T) {
	cfg := relayRetryBackoffConfig{
		base:  time.Second,
		max:   2 * time.Second,
		total: 0,
	}
	state := &relaycommon.RetryBackoffState{}

	expected := []time.Duration{time.Second, 2 * time.Second, 2 * time.Second}
	for _, want := range expected {
		got, ok := nextRelayRetryBackoffDelay(state, cfg)
		if !ok || got != want {
			t.Fatalf("delay = %s, %v; want %s, true", got, ok, want)
		}
	}
}

func TestNextRelayRetryBackoffDelayZeroBase(t *testing.T) {
	cfg := relayRetryBackoffConfig{
		base:  0,
		max:   time.Second,
		total: time.Second,
	}
	state := &relaycommon.RetryBackoffState{}

	for i := 0; i < 3; i++ {
		got, ok := nextRelayRetryBackoffDelay(state, cfg)
		if !ok || got != 0 {
			t.Fatalf("delay = %s, %v; want 0, true", got, ok)
		}
	}
}

func TestNormalizedRelayRetryBackoffConfig(t *testing.T) {
	oldBase := common.RetryBaseIntervalMs
	oldMax := common.RetryMaxIntervalMs
	oldTotal := common.RetryTotalTimeoutMs
	defer func() {
		common.RetryBaseIntervalMs = oldBase
		common.RetryMaxIntervalMs = oldMax
		common.RetryTotalTimeoutMs = oldTotal
	}()

	common.RetryBaseIntervalMs = 2000
	common.RetryMaxIntervalMs = 1000
	common.RetryTotalTimeoutMs = -1

	cfg := normalizedRelayRetryBackoffConfig()
	if cfg.base != 2*time.Second {
		t.Fatalf("base = %s, want 2s", cfg.base)
	}
	if cfg.max != 2*time.Second {
		t.Fatalf("max = %s, want normalized 2s", cfg.max)
	}
	if cfg.total != 0 {
		t.Fatalf("total = %s, want 0", cfg.total)
	}
}

func TestWaitForRelayRetryBackoffContextCanceled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	start := time.Now()
	err := waitForRelayRetryBackoff(ctx, time.Hour)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
	if time.Since(start) > 100*time.Millisecond {
		t.Fatalf("wait did not return quickly after context cancellation")
	}
}
